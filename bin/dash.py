#!/usr/bin/env python3
"""Local read-only dashboard for the Claude/Devin orchestration environment. Stdlib only, 127.0.0.1 only.
GET only; no endpoint takes a file path (projects must be registered, agents/sessions are validated by regex)."""
import argparse, glob, json, os, re, sqlite3, sys, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))
import orch  # noqa: E402  (reuses find/alive/redact helpers; orch.main is guarded)

HOME = orch.HOME
STATIC = HOME / "dash" / "index.html"
SKIP_PREFIX = ("<system-reminder", "<command-", "<local-command", "Caveat:", "[Request interrupted", "<task-notification", "<ide_")
_cache = {}


def cached(key, ttl, fn):
    t, v = _cache.get(key, (0, None))
    if time.time() - t > ttl:
        v = fn()
        _cache[key] = (time.time(), v)
    return v


def registry():
    f = HOME / "projects.json"
    r = json.loads(f.read_text(encoding="utf-8-sig")) if f.exists() else {}
    return [{"path": p, **v} for p, v in r.items() if (Path(p) / ".orch" / "project.json").exists()]


def get_proj(q):
    want = (q.get("project") or [""])[0]
    reg = registry()
    if not want:
        return Path(reg[0]["path"]) if reg else None
    for r in reg:
        if os.path.normcase(os.path.abspath(r["path"])) == os.path.normcase(os.path.abspath(want)):
            return Path(r["path"])
    return None


def rel(d, p):
    if not p:
        return ""
    try:
        return str(Path(p).resolve().relative_to(d.resolve()))
    except Exception:
        return str(p)


def read_lines(f):
    return f.read_text(encoding="utf-8", errors="replace").splitlines() if f.exists() else []


# ---------- Devin agents ----------
def note_status(path):
    try:
        m = re.search(r"^status:\s*(\S+)", Path(path).read_text(encoding="utf-8-sig"), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def mkdiff(ti):
    if "old_string" in ti or "new_string" in ti:
        old, new = str(ti.get("old_string", "")), str(ti.get("new_string", ""))
        return "\n".join(["- " + l for l in old.splitlines()[:60]] + ["+ " + l for l in new.splitlines()[:60]])
    if "content" in ti:
        return "\n".join("+ " + l for l in str(ti["content"]).splitlines()[:80])
    for k in ("patch", "diff", "edits", "new_content"):
        if k in ti:
            return json.dumps(ti[k], ensure_ascii=False)[:3000] if not isinstance(ti[k], str) else ti[k][:3000]
    return ""


def describe(d, tool, ti, out=None):
    """One tool call -> {cat, title, file?, diff?, detail?}. Shared by hook events and Devin session history."""
    tool = (tool or "").lower()
    if tool == "todo_write":
        todos = ti.get("todos") if isinstance(ti.get("todos"), list) else []
        return {"cat": "todo", "title": f"Todo list updated ({len(todos)} items)",
                "detail": "\n".join(f"[{t.get('status', '?')}] {t.get('content', '')}" for t in todos if isinstance(t, dict))[:3000]}
    if tool == "mcp_call_tool":
        return {"cat": "tool", "title": f"MCP {ti.get('server_name', '')}: {ti.get('tool_name', '')}",
                "detail": json.dumps(ti.get("arguments"), ensure_ascii=False)[:1500]}
    if tool in ("edit", "write", "multi_edit", "multiedit", "apply_patch", "patch", "str_replace", "notebook_edit"):
        p = rel(d, ti.get("file_path") or ti.get("path") or "")
        return {"cat": "edit", "title": "Edited " + (p or "(unknown file)"), "file": p or "(unknown file)", "diff": mkdiff(ti)}
    if tool in ("exec", "bash", "shell"):
        return {"cat": "exec", "title": "$ " + str(ti.get("command", ""))[:240], "detail": str(out or "")[:2000]}
    if tool == "read":
        return {"cat": "read", "title": "Read " + rel(d, ti.get("file_path") or ti.get("path") or "")}
    if tool in ("grep", "glob", "search", "web_search", "find"):
        return {"cat": "search", "title": f"{tool} " + str(ti.get("pattern") or ti.get("query") or ti.get("glob") or "")[:160]}
    if tool == "run_subagent":
        return {"cat": "sub_end", "title": "Subagent: " + str(ti.get("title", "")), "detail": str(out or "")[:3000]}
    return {"cat": "tool", "title": tool or "tool", "detail": json.dumps(ti, ensure_ascii=False)[:400]}


def norm(d, rec, sub, subid):
    raw, kind = rec["raw"], rec["kind"]
    ti, tr = raw.get("tool_input") or {}, raw.get("tool_response") or {}
    tool = (raw.get("tool_name") or "").lower()
    out = tr.get("output") if isinstance(tr, dict) else None
    e = {"t": rec["t"], "kind": kind, "sub": sub, "subid": subid, "tool": tool, "cat": "other", "title": kind, "detail": "",
         "ok": bool(tr.get("success", True)) if isinstance(tr, dict) else True}
    if kind == "PostToolUse":
        e.update(describe(d, tool, ti, out))
        if e["cat"] == "sub_end":
            e["title"] = "Subagent finished: " + str(ti.get("title", ""))
    elif kind == "SubStart":
        e.update(cat="sub_start", title="Subagent started: " + str(ti.get("title", "")), detail=str(ti.get("task", ""))[:3000])
    elif kind == "Stop":
        e.update(cat="msg", title="Subagent report" if sub else "Agent final message", detail=str(raw.get("last_assistant_message") or "")[:4000])
    elif kind == "Blocked":
        e.update(cat="blocked", title="Blocked: " + str(ti.get("command", ""))[:200])
    elif kind in ("SessionStart", "SessionEnd"):
        e.update(cat="sys", title="Session " + ("started" if kind == "SessionStart" else "ended"))
    return e


def events(d, agent, since, setid=None):
    lines = read_lines(orch.runs_dir(d, setid) / f"{agent}.events.jsonl")
    out, stack, n = [], [], 0
    for i, l in enumerate(lines):
        try:
            rec = json.loads(l)
        except ValueError:
            continue
        subid = 0
        if rec["kind"] == "SubStart":
            n += 1
            stack.append(n)
            subid, sub = n, (stack[-2] if len(stack) > 1 else 0)
        elif rec["kind"] == "PostToolUse" and (rec["raw"].get("tool_name") or "").lower() == "run_subagent" and stack:
            subid = stack.pop()
            sub = stack[-1] if stack else 0
        else:
            sub = stack[-1] if stack else 0
        if i >= since:
            e = norm(d, rec, sub, subid)
            e["i"] = i
            out.append(e)
    return out, len(lines)


# ---------- Devin sessions (%APPDATA%/devin/cli/sessions.db, opened read-only) ----------
def devin_db():
    p = Path(os.environ.get("APPDATA", "")) / "devin" / "cli" / "sessions.db"
    if not p.exists():
        return None
    c = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=3)
    c.execute("PRAGMA query_only=1")
    return c


def agent_runs(d, agent):
    """Every Devin session of this project that was started as <agent> (newest first), so past runs keep their history."""
    def load():
        c = devin_db()
        if not c:
            return []
        try:
            like, out = f"%You are {agent}%", []
            for sid, title, c0, c1 in c.execute("select id,title,created_at,last_activity_at from sessions where working_directory=? and hidden=0 "
                                                 "order by created_at desc limit 150", (str(d),)).fetchall():
                if c.execute("select 1 from message_nodes where session_id=? and node_id<=8 and chat_message like ? limit 1", (sid, like)).fetchone():
                    out.append({"id": sid, "title": title or sid, "created": c0, "last": c1})
            return out
        except sqlite3.Error:
            return []
        finally:
            c.close()
    return cached(f"runs:{d}:{agent}", 8, load)


def current_run(d, agent, st):
    """The Devin session of the run that A<n>.json describes: recorded by the hook, else the closest start time."""
    runs = agent_runs(d, agent)
    if st.get("session") and any(r["id"] == st["session"] for r in runs):
        return st["session"]
    near = [r for r in runs if abs(r["created"] - st.get("start", 0)) <= 120]
    return min(near, key=lambda r: abs(r["created"] - st.get("start", 0)))["id"] if near else (runs[0]["id"] if runs else None)


def sub_count(sid):
    def load():
        c = devin_db()
        try:
            return c.execute("select count(*) from subagent_heads where session_id=?", (sid,)).fetchone()[0] if c else 0
        except sqlite3.Error:
            return 0
        finally:
            c and c.close()
    return cached("subs:" + sid, 5, load)


def calls_of(m):
    cl = m.get("tool_calls") or []
    if isinstance(cl, str):
        try:
            cl = json.loads(cl)
        except ValueError:
            cl = []
    return [x for x in cl if isinstance(x, dict)]


def args_of(cl):
    a = cl.get("arguments") or {}
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except ValueError:
            a = {}
    return a if isinstance(a, dict) else {}


def history(d, sid, since, before=0):
    """Chat history of one Devin session: the main agent chain plus every subagent chain, as UI items."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", sid or ""):
        return {"error": "bad session"}
    c = devin_db()
    if not c:
        return {"error": "Devin session database not found"}
    try:
        s = c.execute("select main_chain_id,title,created_at,last_activity_at from sessions where id=? and working_directory=?", (sid, str(d))).fetchone()
        if not s:
            return {"error": "unknown session"}
        leaf, title, c0, c1 = s
        parents = dict(c.execute("select node_id,parent_node_id from message_nodes where session_id=?", (sid,)).fetchall())

        def chain(n):
            out = []
            while n is not None and n in parents and len(out) < 200000:
                out.append(n)
                n = parents[n]
            return out
        owner = {n: "main" for n in chain(leaf)} if leaf is not None else {}
        heads = c.execute("select agent_id,chain_node_id from subagent_heads where session_id=? order by updated_at", (sid,)).fetchall()
        sizes = {}
        for aid, hn in heads:
            ch = chain(hn)
            sizes[aid] = len(ch)
            for n in ch:
                owner.setdefault(n, aid)
        if before:  # "load earlier": the 700 rows before the oldest one the page already has
            rows = c.execute("select row_id,node_id,chat_message,created_at from message_nodes where session_id=? and row_id<? order by row_id desc limit 700", (sid, before)).fetchall()[::-1]
        elif since < 0:
            rows = c.execute("select row_id,node_id,chat_message,created_at from message_nodes where session_id=? order by row_id desc limit 700", (sid,)).fetchall()[::-1]
        else:
            rows = c.execute("select row_id,node_id,chat_message,created_at from message_nodes where session_id=? and row_id>? order by row_id limit 1200", (sid, since)).fetchall()
        items, seen, nxt = [], set(), (0 if before else max(since, 0))
        oldest = c.execute("select min(row_id) from message_nodes where session_id=?", (sid,)).fetchone()[0] or 0
        first = rows[0][0] if rows else 0
        for rid, nid, msg, ts in rows:
            who = owner.get(nid)
            if who is None:
                continue  # dead branches and system prefixes
            nxt = max(nxt, rid)
            try:
                m = json.loads(msg)
            except ValueError:
                continue
            role, mid = m.get("role"), m.get("message_id")
            if role == "system" or (mid and mid in seen):
                continue
            seen.add(mid)
            it = {"i": rid, "chain": who, "t": ts, "role": role, "mid": mid}
            if role == "user":
                it["text"] = str(m.get("content") or "")[:6000]
            elif role == "assistant":
                it["text"] = str(m.get("content") or "").strip()[:8000]
                it["calls"] = [dict(describe(d, cl.get("name", ""), args_of(cl)), id=cl.get("id"), tool=cl.get("name", "")) for cl in calls_of(m)]
                if not it["text"] and not it["calls"]:
                    continue
            elif role == "tool":
                it.update(call=m.get("tool_call_id"), text=str(m.get("content") or "")[:4000])
            else:
                continue
            items.append(it)
        return {"session": {"id": sid, "title": title, "created": c0, "last": c1}, "items": items, "next": nxt,
                "first": first, "more_before": bool(first) and first > oldest + 3,
                "subagents": cached(f"subinfo:{sid}:{c1}", 10, lambda: sub_info(c, sid, heads, sizes))}
    except sqlite3.Error as e:
        return {"error": "database busy: " + type(e).__name__}
    finally:
        c.close()


def sub_info(c, sid, heads, sizes):
    calls, results = {}, {}
    for (msg,) in c.execute("select chat_message from message_nodes where session_id=? and chat_message like '%run_subagent%'", (sid,)):
        try:
            m = json.loads(msg)
        except ValueError:
            continue
        if m.get("role") == "assistant":
            for cl in calls_of(m):
                if cl.get("name") == "run_subagent":
                    calls[cl.get("id")] = args_of(cl)
        elif m.get("role") == "tool":
            results[m.get("tool_call_id")] = str(m.get("content") or "")
    out = []
    for cid, a in calls.items():
        r = results.get(cid, "")
        mm = re.search(r"agent_id=([0-9a-f]+)", r)
        aid = mm.group(1) if mm else None
        out.append({"id": aid, "title": str(a.get("title", ""))[:120], "task": str(a.get("task", ""))[:3000], "result": r[:3000],
                    "done": bool(r), "messages": sizes.get(aid, 0)})
    return out


def in_set(d, setid, runs):
    lo, hi = orch.set_window(d, setid or orch.current_set(d))
    return [r for r in runs if lo <= r["created"] < hi]


def roster(d, setid=None):
    """Every Devin run of one agent set per slot (A1..A5, newest first) with its subagent count."""
    runs = {a: in_set(d, setid, agent_runs(d, a)) for a in ("A1", "A2", "A3", "A4", "A5")}
    sids = [r["id"] for rs in runs.values() for r in rs]
    counts = {}
    c = devin_db() if sids else None
    if c:
        try:
            counts = dict(c.execute(f"select session_id,count(*) from subagent_heads where session_id in ({','.join('?' * len(sids))}) group by session_id", sids).fetchall())
        except sqlite3.Error:
            pass
        finally:
            c.close()
    return {a: [dict(r, subs=counts.get(r["id"], 0)) for r in rs] for a, rs in runs.items() if rs}


def subs_of(d, sid):
    """Subagents (id, title, task, result) of one run of this project."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", sid or ""):
        return []

    def load():
        c = devin_db()
        if not c:
            return []
        try:
            if not c.execute("select 1 from sessions where id=? and working_directory=?", (sid, str(d))).fetchone():
                return []
            heads = c.execute("select agent_id,chain_node_id from subagent_heads where session_id=?", (sid,)).fetchall()
            return sub_info(c, sid, heads, {})
        except sqlite3.Error:
            return []
        finally:
            c.close()
    return cached("subs-of:" + sid, 8, load)


def classify(alive, ns, log_tail):
    if alive:
        return "progress"
    known = {"done": "completed", "complete": "completed", "completed": "completed", "passed": "completed", "accepted": "completed",
             "waiting": "waiting", "blocked": "waiting", "review": "review", "failed": "failed"}
    if ns.lower() in known:
        return known[ns.lower()]
    return "failed" if "rejected a tool call" in log_tail else "finished"


def agents(d, setid=None):
    rows = []
    for f in sorted(orch.runs_dir(d, setid).glob("A?.json")):
        st = json.loads(f.read_text(encoding="utf-8-sig"))
        name, alive = f.stem, bool(st.get("pid")) and orch.alive(st["pid"])
        ev = f.with_suffix(".events.jsonl")
        last = ev.stat().st_mtime if ev.exists() else f.stat().st_mtime
        ns = note_status(st.get("task_note", ""))
        log = f.with_suffix(".log")
        status = classify(alive, ns, log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else "")
        sid = current_run(d, name, st)
        lines = read_lines(ev)
        tail = []
        for l in lines[-8:]:  # only the tail is normalised here; full parsing happens in /api/events
            try:
                tail.append(norm(d, json.loads(l), 0, 0))
            except ValueError:
                pass
        rows.append({"name": name, "alive": alive, "status": status, "task": st.get("task"), "note": rel(d, st.get("task_note", "")),
                     "note_status": ns, "files": [rel(d, x) for x in st.get("files", [])],
                     "subagents": sub_count(sid) if sid else st.get("subagents", 0) + st.get("sub_open", 0), "session": sid,
                     "start": st.get("start"), "last": last, "elapsed": (time.time() if alive else last) - st.get("start", last),
                     "event_count": len(lines), "recent": [e["title"] for e in tail if e["cat"] != "sys"][-3:]})
    return rows


# ---------- Claude (orchestrator) transcripts ----------
def claude_dir(d):
    return Path.home() / ".claude" / "projects" / re.sub("[^A-Za-z0-9]", "-", str(d))


def msg_text(c):
    if isinstance(c, str):
        return c
    return "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")


def clean(t):
    return re.sub(r"<system-reminder>.*?</system-reminder>", "", t, flags=re.S).strip()


def desktop_titles():
    """Claude Desktop keeps the real session names in claude-code-sessions/*/*/local_*.json (keyed by the CLI transcript id)."""
    def load():
        m = {}
        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Claude-Data"
        for f in glob.glob(str(base / "*" / "claude-code-sessions" / "*" / "*" / "local_*.json")):
            try:
                j = json.loads(Path(f).read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            for k in ("cliSessionId", "lastAssistantCliSessionId"):
                if j.get(k) and j.get("title"):
                    m[j[k]] = j["title"]
        return m
    return cached("desktop-titles", 15, load)


def claude_sessions(d, limit=30):
    fs = sorted(claude_dir(d).glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    names, out = desktop_titles(), []
    for f in fs:
        title = ""
        with f.open(encoding="utf-8", errors="replace") as fh:
            for _, l in zip(range(80), fh):
                try:
                    o = json.loads(l)
                except ValueError:
                    continue
                if o.get("type") == "user" and not o.get("isMeta"):
                    t = clean(msg_text((o.get("message") or {}).get("content", "")))
                    if t and not t.startswith(SKIP_PREFIX):
                        title = t[:90]
                        break
        name = names.get(f.stem)
        if not name and not title:
            continue  # empty transcript (opened, nothing said): not a conversation worth listing
        out.append({"id": f.stem, "title": orch.redact(name or title), "named": bool(name), "mtime": f.stat().st_mtime,
                    "active": time.time() - f.stat().st_mtime < 90})
    return out


def tool_arg(i):
    if isinstance(i, dict):
        for k in ("file_path", "command", "pattern", "path", "query", "description", "agent", "title"):
            if i.get(k):
                return orch.redact(str(i[k]))[:140]
    return ""


def claude_msgs(d, sid, since):
    if not re.fullmatch(r"[0-9a-f-]{36}", sid):
        return [], 0
    lines = read_lines(claude_dir(d) / f"{sid}.jsonl")
    total = len(lines)
    start = max(0, total + since) if since < 0 else since
    out = []
    for i in range(start, total):
        try:
            o = json.loads(lines[i])
        except ValueError:
            continue
        if o.get("isSidechain") or o.get("type") not in ("user", "assistant"):
            continue
        c = (o.get("message") or {}).get("content", "")
        text, tools = clean(msg_text(c)), []
        if isinstance(c, list):
            tools = [{"name": b.get("name", ""), "arg": tool_arg(b.get("input"))} for b in c if isinstance(b, dict) and b.get("type") == "tool_use"]
        if o["type"] == "user" and (not text or text.startswith(SKIP_PREFIX) or o.get("isMeta")):
            continue
        if not text and not tools:
            continue
        out.append({"i": i, "t": o.get("timestamp"), "role": o["type"], "text": orch.redact(text[:4000]), "tools": tools[:10]})
    return out[-200:], total


# ---------- status ----------
def unity_ok(url):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "dash", "version": "1"}}}
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def state(d, setid=None):
    sets = orch.load_sets(d)
    sid = setid if setid in [s["id"] for s in sets["sets"]] else sets["current"]
    cfg = orch.load_project(d)
    vd = orch.vault_dir(d, cfg)
    at = vd / "Active Tasks.md"
    tasks = [l[2:].strip() for l in read_lines(at) if l.startswith("- ")]
    q = orch.STATE / "queue.jsonl"
    unity = cfg.get("mcp", {}).get("unity")
    return {"now": time.time(), "project": {"name": cfg["name"], "path": str(d), "types": cfg["types"]},
            "agents": agents(d, sid), "tasks": tasks, "claude": claude_sessions(d),
            "set": sid, "current": sets["current"], "other_running": orch.other_running(d, sid),
            "sets": [{"id": s["id"], "start": s["start"], "label": s["label"], "agents": len(list(orch.runs_dir(d, s["id"]).glob("A?.json"))),
                      "running": len(orch.active_runs(d, s["id"]))} for s in sets["sets"]][::-1],
            "integrations": {"unity": None if not unity else cached("unity", 15, lambda: unity_ok(unity)),
                             "discord_dev": bool(orch.secret("DISCORD_WEBHOOK_DEV")), "discord_main": bool(orch.secret("DISCORD_WEBHOOK_MAIN")),
                             "queued": len(read_lines(q))}}


# ---------- http ----------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(b)

    def js(self, obj, code=200):
        self.send(code, orch.redact(json.dumps(obj, ensure_ascii=False)))

    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):  # blocks DNS-rebinding
            return self.send(403, "forbidden", "text/plain")
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path in ("/", "/index.html"):
                return self.send(200, STATIC.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/ping":
                return self.js({"ok": True, "orch_dash": os.getpid()})
            if u.path == "/api/projects":
                return self.js(registry())
            d = get_proj(q)
            if d is None:
                return self.js({"error": "unknown project"}, 404)
            agent = (q.get("agent") or [""])[0]
            setid = (q.get("set") or [None])[0]
            if setid and setid not in [s["id"] for s in orch.load_sets(d)["sets"]]:
                setid = None
            if u.path in ("/api/events", "/api/log", "/api/note") and not re.fullmatch(r"A[1-5]", agent):
                return self.js({"error": "bad agent"}, 400)
            if u.path == "/api/state":
                return self.js(state(d, setid))
            if u.path == "/api/events":
                ev, nxt = events(d, agent, int((q.get("since") or ["0"])[0]), setid)
                return self.js({"events": ev, "next": nxt})
            if u.path == "/api/log":
                f = orch.runs_dir(d, setid) / f"{agent}.log"
                return self.js({"text": f.read_text(encoding="utf-8", errors="replace")[-20000:] if f.exists() else ""})
            if u.path == "/api/note":
                rj = orch.runs_dir(d, setid) / f"{agent}.json"
                note = Path(json.loads(rj.read_text(encoding="utf-8-sig")).get("task_note", "")) if rj.exists() else None
                ok = note and note.is_file() and (d.resolve() in note.resolve().parents)
                return self.js({"path": rel(d, note) if ok else "", "text": note.read_text(encoding="utf-8-sig")[:30000] if ok else ""})
            if u.path == "/api/roster":
                return self.js(roster(d, setid))
            if u.path == "/api/subs":
                return self.js(subs_of(d, (q.get("session") or [""])[0]))
            if u.path == "/api/runs":
                a = (q.get("agent") or [""])[0]
                return self.js(in_set(d, setid, agent_runs(d, a)) if re.fullmatch(r"A[1-5]", a) else [])
            if u.path == "/api/history":
                return self.js(history(d, (q.get("session") or [""])[0], int((q.get("since") or ["-1"])[0]), int((q.get("before") or ["0"])[0])))
            if u.path == "/api/claude":
                msgs, nxt = claude_msgs(d, (q.get("session") or [""])[0], int((q.get("since") or ["-60"])[0]))
                return self.js({"messages": msgs, "next": nxt})
            self.js({"error": "not found"}, 404)
        except Exception as e:  # never leak a traceback to the page
            self.js({"error": type(e).__name__}, 500)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.daemon_threads = True
    orch.STATE.mkdir(parents=True, exist_ok=True)
    (orch.STATE / "dash.json").write_text(json.dumps({"pid": os.getpid(), "port": a.port}), encoding="utf-8")
    srv.serve_forever()


if __name__ == "__main__":
    main()
