#!/usr/bin/env python3
"""Claude-orchestrates / Devin-develops toolkit. Stdlib only.
Subcommands: init, report, flush, hook, guard, dispatch, runs, status, webhook-rename
"""
import argparse, hashlib, json, os, re, subprocess, sys, time, datetime as dt
import urllib.request, urllib.error
from pathlib import Path

HOME = Path(os.environ.get("ORCH_HOME") or Path.home() / ".orch")
STATE = HOME / "state"
MAX_PRIMARY = 5
STATUS = {  # key: (emoji, label, color)
    "completed": ("🟢", "Completed", 0x2ECC71), "progress": ("🔵", "In Progress", 0x3498DB),
    "waiting": ("🟡", "Waiting", 0xF1C40F), "failed": ("🔴", "Failed", 0xE74C3C),
    "review": ("🟣", "Review Required", 0x9B59B6),
}


# ---------- config / secrets ----------
def secret(name):
    v = os.environ.get(name)
    if v:
        return v
    f = HOME / "secrets.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return ""


def find_project(start):
    p = Path(start).resolve()
    for d in [p, *p.parents]:
        if (d / ".orch" / "project.json").exists():
            return d
    return None


def load_project(d):
    return json.loads((d / ".orch" / "project.json").read_text(encoding="utf-8-sig"))


def vault_dir(d, cfg):
    return Path(cfg["vault"]) / "Orch" / cfg["name"]


# ---------- detection / init ----------
MARKERS = [("unity", "ProjectSettings/ProjectVersion.txt"), ("kotlin", "build.gradle.kts"),
           ("kotlin", "settings.gradle.kts"), ("cpp", "CMakeLists.txt"), ("rust", "Cargo.toml"),
           ("go", "go.mod"), ("javascript", "package.json"), ("python", "pyproject.toml"),
           ("python", "requirements.txt"), ("dotnet", "*.sln")]


def detect_type(d):
    found = []
    for t, m in MARKERS:
        if list(d.glob(m)):
            found.append(t)
    if not found:  # one level down (e.g. Unity project nested in a workspace folder)
        for sub in [x for x in d.iterdir() if x.is_dir() and not x.name.startswith(".")]:
            for t, m in MARKERS:
                if list(sub.glob(m)):
                    found.append(t)
    return sorted(set(found)) or ["generic"]


def find_vault(d):
    """An Obsidian vault registered in Obsidian that lives inside the project dir, else None."""
    ob = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
    try:
        vaults = [Path(v["path"]) for v in json.loads(ob.read_text(encoding="utf-8-sig"))["vaults"].values()]
    except Exception:
        return None
    for v in vaults:
        try:
            v.resolve().relative_to(d.resolve())
            return v
        except ValueError:
            pass
    return None


SCAFFOLD = {
    "00_Master.md": "# {n} — Master\n\nType: {t} · Root: `{r}`\n\n## Read order (token-efficient)\n1. This file → 2. [[Active Tasks]] → 3. [[Pending]] → only then topic notes.\n\n## Index\n- [[Architecture]] · [[Plan]] · [[Active Tasks]] · [[Completed Tasks]]\n- [[Bugs]] · [[Decisions]] · [[Dependencies]] · [[Assignments]] · [[History]] · [[Testing]] · [[Pending]]\n- Agent logs: `Agents/Logs/YYYY-MM-DD.md`\n\n## Current status\n_(orchestrator updates this block)_\n",
    "Architecture.md": "# {n} — Architecture\n\n_Fill from real inspection; link systems with [[wikilinks]]._\n",
    "Plan.md": "# {n} — Development Plan\n",
    "Active Tasks.md": "# Active Tasks\n\n_One line per task: `- [[Tasks/T-001]] agent:A1 status:progress`_\n",
    "Completed Tasks.md": "# Completed Tasks\n",
    "Bugs.md": "# Known Bugs\n",
    "Decisions.md": "# Technical Decisions\n\n_Format: date — decision — why — alternatives._\n",
    "Dependencies.md": "# System Dependencies\n\n_Every new dependency: source, maintainer, license, vuln check, approval._\n",
    "Assignments.md": "# Agent Assignments\n\n_Max 5 primary agents. One owner per file area._\n",
    "History.md": "# Implementation History\n",
    "Testing.md": "# Testing Reports\n",
    "Pending.md": "# Pending Issues\n",
}

UNITY_WRITE = {"mcp__unityMCP__" + n for n in "create_script delete_script apply_text_edits script_apply_edits manage_script manage_shader execute_code execute_custom_tool batch_execute".split()}
BEGIN, END = "<!-- ORCH:BEGIN (managed by ~/.orch; edit outside this block) -->", "<!-- ORCH:END -->"


def write_if_absent(p, text):
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return True
    return False


def init_project(d, vault_override=None):
    d = Path(d).resolve()
    orch = d / ".orch"
    orch.mkdir(exist_ok=True)
    cfgp = orch / "project.json"
    created = not cfgp.exists()
    if created:
        types = detect_type(d)
        v = Path(vault_override) if vault_override else (find_vault(d) or orch / "vault")
        cfg = {"name": d.name, "root": str(d), "types": types, "vault": str(v),
               "vault_is_default": not (vault_override or find_vault(d)),
               "webhooks": {"dev": "DISCORD_WEBHOOK_DEV", "main": "DISCORD_WEBHOOK_MAIN"},
               "mcp": {"unity": "http://127.0.0.1:8080/mcp"} if "unity" in types else {}}
        cfgp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    cfg = load_project(d)
    vd = vault_dir(d, cfg)
    for fn, body in SCAFFOLD.items():
        write_if_absent(vd / fn, body.format(n=cfg["name"], t="/".join(cfg["types"]), r=cfg["root"]))
    for sub in ("Tasks", "Agents/Logs", "Reports"):
        (vd / sub).mkdir(parents=True, exist_ok=True)
    (orch / "runs").mkdir(exist_ok=True)
    (orch / ".gitignore").write_text("runs/\nbackups/\nclaude-settings.json\n", encoding="utf-8")
    # CLAUDE.md managed block (backup first, never touch text outside the block)
    block = (f"{BEGIN}\n@~/.orch/ORCHESTRATOR.md\n\n## Project: {cfg['name']}\n"
             f"- Types: {', '.join(cfg['types'])}\n- Root: {cfg['root']}\n- Vault notes: {vd}\n"
             f"- On session start read `{vd / '00_Master.md'}` then `Active Tasks.md`. Nothing else until needed.\n"
             f"- Project config: .orch/project.json\n{END}\n")
    cm = d / "CLAUDE.md"
    old = cm.read_text(encoding="utf-8-sig") if cm.exists() else ""
    if BEGIN in old:
        new = re.sub(re.escape(BEGIN) + ".*?" + re.escape(END) + r"\n?", lambda m: block, old, flags=re.S)
    else:
        new = (old.rstrip() + "\n\n" if old else "") + block
    if new != old:
        if old:
            bk = orch / "backups"
            bk.mkdir(exist_ok=True)
            (bk / f"CLAUDE.md.{int(time.time())}.bak").write_text(old, encoding="utf-8")
        cm.write_text(new, encoding="utf-8")
    # global registry
    reg = HOME / "projects.json"
    r = json.loads(reg.read_text(encoding="utf-8-sig")) if reg.exists() else {}
    r[str(d)] = {"name": cfg["name"], "types": cfg["types"], "vault": cfg["vault"]}
    reg.write_text(json.dumps(r, indent=2), encoding="utf-8")
    # Claude hooks in project .claude/settings.json (read by Claude Desktop AND CLI): merge, back up, never clobber
    me = f'python "{Path(__file__).resolve()}"'
    want = {"SessionStart": ("", f"{me} context"), "PreToolUse": ("Edit|Write|NotebookEdit|mcp__unityMCP__(create_script|delete_script|apply_text_edits|script_apply_edits|manage_script|manage_shader|execute_code|execute_custom_tool|batch_execute)", f"{me} guard"), "Stop": ("", f"{me} stop-guard")}
    cs = d / ".claude" / "settings.json"
    old_txt = cs.read_text(encoding="utf-8-sig") if cs.exists() else ""
    try:
        cj = json.loads(old_txt) if old_txt else {}
    except ValueError:
        raise SystemExit(f"{cs} is not valid JSON; fix it or move it, then re-run (I won't overwrite it)")
    hk = cj.setdefault("hooks", {})
    for ev, (matcher, cmd) in want.items():
        lst = hk.setdefault(ev, [])
        hit = [e for e in lst for h in e.get("hooks", []) if cmd in h.get("command", "")]
        if hit:
            hit[0]["matcher"] = matcher
        else:
            lst.append({"matcher": matcher, "hooks": [{"type": "command", "command": cmd}]})
    en = cj.setdefault("enabledMcpjsonServers", [])
    for n in ("orch", "unityMCP"):
        if n not in en:
            en.append(n)
    new_txt = json.dumps(cj, indent=2)
    if new_txt != old_txt.strip():
        if old_txt:
            (orch / "backups").mkdir(exist_ok=True)
            (orch / "backups" / f"settings.json.{int(time.time())}.bak").write_text(old_txt, encoding="utf-8")
        cs.parent.mkdir(exist_ok=True)
        cs.write_text(new_txt, encoding="utf-8")
    mp = d / ".mcp.json"
    mtxt = mp.read_text(encoding="utf-8-sig") if mp.exists() else ""
    try:
        mj = json.loads(mtxt) if mtxt else {}
    except ValueError:
        raise SystemExit(f"{mp} is not valid JSON; fix it, then re-run (I won't overwrite it)")
    srv = mj.setdefault("mcpServers", {})
    entry = {"command": "python", "args": [str(Path(__file__).with_name("orch_mcp.py").resolve())]}
    if srv.get("orch") != entry:
        srv["orch"] = entry
        if mtxt:
            (orch / "backups").mkdir(exist_ok=True)
            (orch / "backups" / f".mcp.json.{int(time.time())}.bak").write_text(mtxt, encoding="utf-8")
        mp.write_text(json.dumps(mj, indent=2), encoding="utf-8")
    return d, cfg, created


# ---------- discord ----------
def post(url, payload, method="POST"):
    req = urllib.request.Request(url + ("?wait=true" if method == "POST" else ""), method=method,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "orch-reporter/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def vault_log(d, cfg, line):
    f = vault_dir(d, cfg) / "Agents" / "Logs" / f"{dt.date.today()}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def build_embed(a, proj):
    emoji, label, color = STATUS[a["status"]]
    fields = [("Agent", a["agent"], True), ("Status", f"{emoji} {label}", True)]
    for k, key in (("Task", "task"), ("Files", "files"), ("Changes", "changes"), ("Tests", "tests"),
                   ("Knowledge Base", "kb")):
        if a.get(key):
            fields.append((k, str(a[key])[:1000], k in ("Files", "Tests", "Knowledge Base")))
    return {"title": f"{emoji} {a.get('title') or 'Task Update'}"[:250],
            "description": (a.get("note") or "")[:500] or None, "color": color,
            "fields": [{"name": n, "value": v, "inline": i} for n, v, i in fields],
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "footer": {"text": proj}}


def send(a, d=None, cfg=None):
    """Deliver one update. Dedupe by content hash; queue on failure. Returns 'sent'|'dup'|'queued'|'no-webhook'."""
    STATE.mkdir(parents=True, exist_ok=True)
    proj = cfg["name"] if cfg else "orch"
    key = hashlib.sha1(json.dumps([a.get(k) for k in ("agent", "task", "status", "changes", "files", "title")],
                                  sort_keys=False).encode()).hexdigest()
    sent = STATE / "sent.json"
    seen = json.loads(sent.read_text()) if sent.exists() else {}
    if key in seen and not a.get("force"):
        return "dup"
    chan = a.get("channel", "dev")
    targets = [chan] + (["main"] if a["status"] == "failed" and chan != "main" else [])
    ok = True
    wh_any = False
    for t in targets:
        url = secret("DISCORD_WEBHOOK_DEV" if t == "dev" else "DISCORD_WEBHOOK_MAIN")
        if not url:
            continue
        wh_any = True
        try:
            post(url, {"embeds": [build_embed(a, proj)]})
        except Exception as e:  # never log the URL
            ok = False
            with (STATE / "queue.jsonl").open("a", encoding="utf-8") as q:
                q.write(json.dumps({"a": {**a, "channel": t}, "d": str(d) if d else None,
                                    "err": type(e).__name__, "t": time.time()}) + "\n")
    if d and cfg:
        e, lab, _ = STATUS[a["status"]]
        vault_log(d, cfg, f"- {dt.datetime.now():%H:%M:%S} {e} **{a['agent']}** · {a.get('task','')} · {lab}"
                  f"{' · ' + a['changes'] if a.get('changes') else ''}{' · files: ' + str(a['files']) if a.get('files') else ''}"
                  f"{' · tests: ' + a['tests'] if a.get('tests') else ''}"
                  f"{' · discord:' + ('ok' if ok else 'queued') if wh_any else ' · discord:off'}")
    if not wh_any:
        return "no-webhook"
    if ok:
        seen[key] = time.time()
        sent.write_text(json.dumps(dict(list(seen.items())[-500:])))
        return "sent"
    return "queued"


def flush():
    q = STATE / "queue.jsonl"
    if not q.exists():
        return 0
    items = [json.loads(l) for l in q.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    q.unlink()
    n = 0
    for it in items:
        d = Path(it["d"]) if it["d"] else None
        cfg = load_project(d) if d and (d / ".orch" / "project.json").exists() else None
        if send({**it["a"], "force": True}, d, None) == "sent":  # d=None: no 2nd vault line
            n += 1
        else:
            with q.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(it) + "\n")
    return n


def cmd_report(a):
    d = find_project(a.project or os.environ.get("ORCH_PROJECT_DIR") or os.getcwd())
    cfg = load_project(d) if d else None
    flush()
    r = send({"agent": a.agent, "status": a.status, "task": a.task, "files": a.files, "changes": a.changes,
              "tests": a.tests, "kb": a.kb, "title": a.title, "note": a.note, "channel": a.channel,
              "force": a.force}, d, cfg)
    print(r)


# ---------- Devin hooks (stdin JSON) ----------
REDACT = re.compile("https://discord(?:app)?[.]com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+")


def redact(t):
    return REDACT.sub("[webhook redacted]", t)


def cut(v, n=3000):
    if isinstance(v, str):
        return redact(v)[:n]
    if isinstance(v, dict):
        return {k: cut(x, n) for k, x in v.items()}
    if isinstance(v, list):
        return [cut(x, n) for x in v[:50]]
    return v


def log_event(d, agent, kind, ev):
    """Append one trimmed, redacted hook event to .orch/runs/<agent>.events.jsonl (feeds the dashboard)."""
    f = d / ".orch" / "runs" / f"{agent}.events.jsonl"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.time(), "kind": kind, "raw": cut(ev)}, ensure_ascii=False) + chr(10))


DENY = re.compile(r"(\brm\s+-\w*[rf]\w*\s|\bgit\s+(reset\s+--hard|clean\s+-\w*f|push|restore|checkout\s+--)|\bRemove-Item\b.*-Recurse|\brmdir\s+/s|\bdel\s+/[sq]|\bformat\s+[a-z]:|\b(npm|pnpm|yarn)\s+(i|install|add)\b|\bpip3?\s+install\b|\bcurl\b.*\|\s*(sh|bash)|\bInvoke-Expression\b|\biex\b)", re.I)
BLOCK_MSG = "Blocked by orchestrator safety policy (destructive command or dependency install). Do not retry. Set your task note status to 'waiting', explain what you need and why, and finish; the orchestrator will ask the user."


def cmd_hook(a):
    try:
        ev = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    except Exception:
        ev = {}
    d = find_project(os.environ.get("ORCH_PROJECT_DIR") or ev.get("cwd") or os.getcwd())
    if not d or not os.environ.get("ORCH_AGENT"):  # only agents launched via `dispatch`
        return
    cfg = load_project(d)
    agent, task = os.environ["ORCH_AGENT"], os.environ.get("ORCH_TASK", "")
    run = d / ".orch" / "runs" / f"{agent}.json"
    st = json.loads(run.read_text()) if run.exists() else {}
    if a.event != "PreToolUse":
        log_event(d, agent, a.event, ev)
    if a.event == "PreToolUse":
        if (ev.get("tool_name") or "").lower() == "run_subagent":  # span start: events until its PostToolUse are the subagent's
            log_event(d, agent, "SubStart", ev)
            st["sub_open"] = st.get("sub_open", 0) + 1
            run.write_text(json.dumps(st))
            return
        cmd = (ev.get("tool_input") or {}).get("command") or ""
        if (ev.get("tool_name") or "").lower() in ("exec", "bash", "shell") and DENY.search(cmd):
            send({"agent": agent, "task": task, "status": "waiting", "title": "Blocked risky command",
                  "note": cmd[:200]}, d, cfg)
            log_event(d, agent, "Blocked", ev)
            print(json.dumps({"decision": "block", "reason": BLOCK_MSG}))
        return
    if a.event == "PostToolUse":
        tool = (ev.get("tool_name") or "").lower()
        if tool == "run_subagent":
            st["sub_open"] = max(0, st.get("sub_open", 0) - 1)
            st["subagents"] = st.get("subagents", 0) + 1
            run.write_text(json.dumps(st))
        if any(k in tool for k in ("edit", "write", "patch")):
            p = (ev.get("tool_input") or {}).get("file_path") or (ev.get("tool_input") or {}).get("path")
            if p:
                st.setdefault("files", [])
                if p not in st["files"]:
                    st["files"].append(p)
                run.write_text(json.dumps(st))
    elif a.event == "Stop":
        if st.get("sub_open", 0) > 0:  # a subagent's own Stop: no enforcement, no Discord report
            return
        # enforce: task note must have been touched by the agent before it may stop (once)
        tn = Path(st.get("task_note", ""))
        if tn.exists() and tn.stat().st_mtime < st.get("start", 0) and not ev.get("stop_hook_active"):
            print(json.dumps({"decision": "block", "reason": f"Update your task note {tn} (status, results, tests, "
                              "files) and Decisions/Bugs/History notes if relevant, then finish."}))
            return
        done = tn.exists() and tn.stat().st_mtime >= st.get("start", 0)
        send({"agent": agent, "task": task, "status": "completed" if done else "failed",
              "files": f"{len(st.get('files', []))} modified", "kb": "Updated" if done else "NOT updated",
              "changes": st.get("summary", ""), "title": "Agent turn finished",
              "note": "" if done else "Stopped without updating its task note (blocked by a permission prompt?). Orchestrator must check the log."}, d, cfg)
    elif a.event == "SessionEnd":
        send({"agent": agent, "task": task, "status": "review", "files": f"{len(st.get('files', []))} modified",
              "title": "Agent session ended", "note": "Orchestrator review required"}, d, cfg)
    elif a.event == "SessionStart":
        if ev.get("session_id") and st.get("session") != ev["session_id"]:  # lets the dashboard find this run's Devin chat
            st["session"] = ev["session_id"]
            run.write_text(json.dumps(st))
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext":
              f"You are {agent} on project {cfg['name']}. Read {st.get('task_note','your task note')} first; use the vault, "
              "not copied docs. Report via your task note; hooks send Discord updates automatically."}}))


# ---------- Claude guard: Claude may not edit project code ----------
def cmd_guard(a):
    ev = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    tn = ev.get("tool_name") or ""
    if os.environ.get("ORCH_AGENT") or not (tn in ("Edit", "Write", "NotebookEdit") or tn in UNITY_WRITE):
        return  # Devin agents (and Devin's own tool names) are the coders
    if tn in UNITY_WRITE:  # Unity code-writing tools: orchestrator may not use them
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
              "permissionDecisionReason": "Orchestrator role: code-writing Unity tools are for Devin agents. Delegate via task note + orch.py dispatch."}}))
        return
    p = (ev.get("tool_input") or {}).get("file_path") or (ev.get("tool_input") or {}).get("notebook_path") or ""
    d = find_project(os.environ.get("CLAUDE_PROJECT_DIR") or ev.get("cwd") or os.getcwd())
    if not d or not p:
        return
    cfg = load_project(d)
    t = Path(p).resolve()
    if tn in ("Edit", "Write", "NotebookEdit") and d.resolve() not in t.parents:
        return  # outside this project (e.g. the global ~/.orch toolkit): not project code
    allowed = [Path(cfg["vault"]).resolve(), (d / ".orch").resolve(), (d / "CLAUDE.md").resolve()]
    if any(t == x or x in t.parents for x in allowed):
        return
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
          "permissionDecisionReason": "Orchestrator role: Claude may not edit project code. Write a task note in the "
          "vault and `orch.py dispatch` it to a Devin agent."}}))


# ---------- dispatch ----------
def alive(pid):
    """True while the process exists and has not exited (Win32 call; tasklist took ~0.8s per check)."""
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def active_runs(d):
    out = []
    for f in (d / ".orch" / "runs").glob("A*.json"):
        s = json.loads(f.read_text())
        if s.get("pid") and alive(s["pid"]):
            out.append(f.stem)
    return out


PRIMARY_PROMPT = """You are {agent}, a PRIMARY Devin agent on project "{name}" (root: {root}). You write all code.
1. Read ONLY: {note} (your task), then notes it links. Vault: {vault}. Check Bugs/Decisions before changing design.
2. Own ONLY the files/areas named in your task. Do not touch others' areas.
3. SUBAGENTS: unless the task is trivial (a single small edit), first split it into 2-4 independent slices (disjoint files or disjoint questions) and
   run each slice as its own subagent with run_subagent (profile `orch-sub`; run independent slices in the background so they work in parallel).
   Give each a compact self-contained brief and a disjoint file set. Do NOT do every slice serially yourself. Only the Unity Editor is a shared
   resource: never let two subagents compile or run Unity tests at once. You integrate, verify and stay responsible for the result.
4. Work carefully, root-cause fixes, test every meaningful change, regression-check. Quality over speed. Verify it actually works.
5. Dependencies: only official/trusted sources, check license + maintainer + known vulns; if suspicious STOP and report 'waiting'.
5b. Shell: you run headless, so any command that writes OUTSIDE the project root (e.g. /tmp) is refused and kills your run. Put temp files in {root}\.orch\scratch (create it) and keep commands simple.
6. Never print or store secrets/webhook URLs. No destructive/project-wide risky ops without approval (report 'waiting').
7. Before finishing: update your task note (status, summary, files, test results), and append to Decisions/Bugs/History/Testing notes if relevant.
   Discord reporting is automatic via hooks; (manual `orch.py report` calls may be denied in headless mode; the hooks suffice).
Unity MCP (if relevant): http://127.0.0.1:8080/mcp is configured as `unityMCP`.
"""


def cmd_dispatch(a):
    d = find_project(a.project or os.getcwd())
    cfg = load_project(d)
    act = active_runs(d)
    if len(act) >= MAX_PRIMARY:
        sys.exit(f"REFUSED: {len(act)}/{MAX_PRIMARY} primary agents already running: {act}")
    if a.agent in act:
        sys.exit(f"REFUSED: {a.agent} already running")
    note = Path(a.note).resolve()
    if not note.exists():
        sys.exit(f"task note not found: {note}")
    prompt = PRIMARY_PROMPT.format(agent=a.agent, name=cfg["name"], root=cfg["root"], note=note,
                                   vault=vault_dir(d, cfg), orch=Path(__file__).resolve(), task=a.task or note.stem)
    runs = d / ".orch" / "runs"
    pf = runs / f"{a.agent}.prompt.md"
    pf.write_text(prompt, encoding="utf-8")
    log = runs / f"{a.agent}.log"
    env = {**os.environ, "ORCH_AGENT": a.agent, "ORCH_TASK": a.task or note.stem, "ORCH_PROJECT_DIR": str(d)}
    cmd = ["devin", "-p", "--model", a.model, "--permission-mode", a.permission, "--respect-workspace-trust", "false",
           "--prompt-file", str(pf)]
    lf = open(log, "wb")
    p = subprocess.Popen(cmd, cwd=a.cwd or str(d), env=env, stdout=lf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=0x08000000)  # CREATE_NO_WINDOW
    (runs / f"{a.agent}.events.jsonl").unlink(missing_ok=True)
    (runs / f"{a.agent}.json").write_text(json.dumps({"pid": p.pid, "start": time.time(), "task": a.task or note.stem,
                                                       "task_note": str(note), "files": []}))
    send({"agent": a.agent, "task": a.task or note.stem, "status": "progress", "title": "Work started",
          "note": f"model {a.model}", "channel": "dev"}, d, cfg)
    print(f"started {a.agent} pid={p.pid} log={log}")


def cmd_runs(a):
    d = find_project(a.project or os.getcwd())
    act = active_runs(d)
    print(f"{len(act)}/{MAX_PRIMARY} primary agents running: {act}")


def cmd_webhook_rename(a):
    url = secret("DISCORD_WEBHOOK_DEV")  # only webhook 1; webhook 2 is never modified
    req = urllib.request.Request(url, method="PATCH", data=json.dumps({"name": a.name}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "orch-reporter/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        print("renamed ->", json.loads(r.read())["name"])


SUB_PROFILE = """---
name: orch-sub
description: Devin subagent for a small independent implementation task delegated by a primary orchestrated agent.
model: swe-2-max
---
You are an implementation subagent. Your primary agent gave you a self-contained brief with a disjoint file set.
- Read the vault notes it names before coding; stay within your files.
- Root-cause fixes, careful and tested; verify it works. No secrets in output. No risky/destructive ops or suspicious dependencies without reporting back instead.
- Finish with a compact report: files changed, what/why, tests run + results, open issues. Your primary updates the vault and task note.
"""


def cmd_ensure_devin(a):
    """Idempotent: orch-sub profile + reporting hooks in the user-level Devin config (backed up once)."""
    base = Path(os.environ["APPDATA"]) / "devin"
    prof = base / "agents" / "orch-sub" / "AGENT.md"
    msgs = []
    if not prof.exists():
        prof.parent.mkdir(parents=True, exist_ok=True)
        prof.write_text(SUB_PROFILE, encoding="utf-8")
        msgs.append("profile installed")
    cfgp = base / "config.json"
    cfg = json.loads(cfgp.read_text(encoding="utf-8-sig"))
    cmdline = f'python "{Path(__file__).resolve()}" hook'
    hooks = cfg.setdefault("hooks", {})
    changed = False
    for ev in ("SessionStart", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"):
        lst = hooks.setdefault(ev, [])
        if not any(cmdline in h.get("command", "") for e in lst for h in e.get("hooks", [])):
            lst.append({"matcher": "", "hooks": [{"type": "command", "command": f"{cmdline} {ev}", "timeout": 20}]})
            changed = True
    if changed:
        bak = cfgp.with_name("config.json.pre-orch.bak")
        if not bak.exists():
            bak.write_text(cfgp.read_text(encoding="utf-8-sig"), encoding="utf-8")
        cfgp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        msgs.append("hooks installed")
    print(", ".join(msgs) or "ok (already installed)")


def cmd_ensure_claude(a):
    """Idempotent: user-level Claude hooks (re-inject rules after compaction; remember /orch attachments). Backed up once."""
    cp = Path.home() / ".claude" / "settings.json"
    txt = cp.read_text(encoding="utf-8-sig") if cp.exists() else ""
    cj = json.loads(txt) if txt else {}
    me = f'python "{Path(__file__).resolve()}"'
    hk, changed = cj.setdefault("hooks", {}), False
    for ev, m, c in (("SessionStart", "compact|resume", f"{me} context"), ("PostToolUse", "Bash|mcp__orch__orch_dispatch", f"{me} mark"), ("Stop", "", f"{me} stop-guard")):
        lst = hk.setdefault(ev, [])
        hit = [e for e in lst for h in e.get("hooks", []) if c in h.get("command", "")]
        if hit:
            if hit[0].get("matcher") != m:
                hit[0]["matcher"], changed = m, True
        else:
            lst.append({"matcher": m, "hooks": [{"type": "command", "command": c}]})
            changed = True
    if changed:
        cp.parent.mkdir(exist_ok=True)
        bak = cp.with_name("settings.json.pre-orch.bak")
        if txt and not bak.exists():
            bak.write_text(txt, encoding="utf-8")
        cp.write_text(json.dumps(cj, indent=2), encoding="utf-8")
    print("hooks installed" if changed else "ok (already installed)")


def summarize_runs(d):
    rows = []
    for f in sorted((d / ".orch" / "runs").glob("A?.json")):
        st = json.loads(f.read_text(encoding="utf-8-sig"))
        note, status = Path(st.get("task_note", "")), ""
        if note.is_file():
            m = re.search(r"^status:\s*(\S+)", note.read_text(encoding="utf-8-sig"), re.M)
            status = m.group(1) if m else ""
        log = f.with_suffix(".log")
        rows.append({"agent": f.stem, "alive": bool(st.get("pid")) and alive(st["pid"]), "task": st.get("task"),
                     "note_status": status, "files_modified": len(st.get("files", [])),
                     "log_tail": log.read_text(encoding="utf-8", errors="replace")[-400:].strip() if log.exists() else ""})
    return rows


def cmd_wait(a):
    """Block until no primary agent is running (or timeout), then print every agent's outcome."""
    d = find_project(a.project or os.getcwd())
    t0 = time.time()
    while active_runs(d) and time.time() - t0 < a.timeout:
        time.sleep(5)
    still = active_runs(d)
    print(json.dumps({"still_running": still, "timed_out": bool(still), "agents": summarize_runs(d)}, indent=1))


def cmd_stop_guard(a):
    """Claude Stop hook: the orchestrator may not end its turn while Devin agents are still running."""
    if os.environ.get("ORCH_AGENT"):
        return  # Devin loads the same settings files; it is not the orchestrator
    ev = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    sid = ev.get("session_id", "?")
    d = find_project(os.environ.get("CLAUDE_PROJECT_DIR") or ev.get("cwd") or os.getcwd())
    if not d and attached().get(sid):
        d = find_project(attached()[sid])
    if not d:
        return
    f = STATE / "stopblocks.json"
    c = json.loads(f.read_text(encoding="utf-8-sig")) if f.exists() else {}
    o = STATE / "owners.json"
    owners = json.loads(o.read_text(encoding="utf-8-sig")) if o.exists() else {}
    running = [x for x in active_runs(d) if owners.get(f"{d}|{x}") == sid]
    if not running:
        if sid in c:
            c.pop(sid)
            f.write_text(json.dumps(c), encoding="utf-8")
        return
    if c.get(sid, 0) >= 60:  # safety valve against an endless loop
        return
    c[sid] = c.get(sid, 0) + 1
    STATE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(c), encoding="utf-8")
    print(json.dumps({"decision": "block", "reason": f"Devin agents still running: {running}. Do not end your turn. "
                      "Call orch_wait, review the results (task notes, logs, changed files), then continue the plan: "
                      "dispatch the next tasks or give the final report."}))


def dash_info():
    try:
        return json.loads((STATE / "dash.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def dash_ping(port):
    """Returns the dashboard server's pid if one of ours answers on this port, else None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read()).get("orch_dash")
    except Exception:
        return None


def dash_start():
    """Start the single global dashboard unless it is already running (never a second one). Returns its port."""
    import socket
    for port in [dash_info().get("port", 8765), *range(8765, 8775)]:
        if dash_ping(port):
            return port
    for port in range(8765, 8775):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue  # taken by something that is not our dashboard
        finally:
            s.close()
        STATE.mkdir(parents=True, exist_ok=True)
        log = open(STATE / "dash.log", "ab")
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("dash.py")), "--port", str(port)], stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, creationflags=0x208, close_fds=True)  # DETACHED_PROCESS | NEW_PROCESS_GROUP: no window, survives the launcher
        for _ in range(40):
            if dash_ping(port):
                return port
            time.sleep(0.25)
        raise SystemExit("dashboard did not start; see ~/.orch/state/dash.log")
    raise SystemExit("no free port in 8765-8774")


def cmd_dash(a):
    port = dash_info().get("port", 8765)
    if a.action == "start":
        port = dash_start()
        print(f"http://127.0.0.1:{port}")
    elif a.action == "status":
        pid = dash_ping(port)
        print(f"running http://127.0.0.1:{port} pid={pid}" if pid else "stopped")
    elif a.action == "stop":
        pid = dash_ping(port)
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            print("stopped")
        else:
            print("not running")
    elif a.action == "open":
        import webbrowser
        from urllib.parse import quote
        port = dash_start()
        d = find_project(a.project or os.getcwd())
        webbrowser.open(f"http://127.0.0.1:{port}/" + (f"?project={quote(str(d))}" if d else ""))
        print(f"opened http://127.0.0.1:{port}")


def cmd_watch(a):
    """Live terminal view of agents, subagent counts, recent activity and the latest Claude messages (q = quit)."""
    import dash, msvcrt, shutil
    d = find_project(a.project or os.getcwd())
    os.system("")  # enables ANSI colours in the Windows console
    sys.stdout.reconfigure(errors="replace")  # agent/chat text can hold characters the console codepage lacks
    ESC = chr(27)
    col = lambda c, t: f"{ESC}[{c}m{t}{ESC}[0m"
    DOT = {"progress": "34", "completed": "32", "waiting": "33", "failed": "31", "review": "35"}
    mins = lambda s: f"{int(s) // 60}m{int(s) % 60:02d}s"
    while True:
        st, w = dash.state(d), shutil.get_terminal_size().columns - 2
        i = st["integrations"]
        L = [col("1;36", f" ORCHESTRA  {st['project']['name']}") + col("90", "   " + time.strftime("%H:%M:%S")), "",
             " Unity MCP " + (col("32", "online") if i["unity"] else col("31", "offline") if i["unity"] is False else col("90", "n/a"))
             + "   Discord " + (col("32", "on") if i["discord_dev"] and i["discord_main"] else col("33", "partial"))
             + (f"   queued {i['queued']}" if i["queued"] else ""), "", col("1", " Devin agents")]
        for ag in st["agents"] or []:
            L.append(f" {col(DOT[ag['status']], chr(9679))} {ag['name']}  {ag['status']:<9} {mins(ag['elapsed']):>7}  {len(ag['files'])} files  {ag['subagents']} sub  {(ag['task'] or '')[:46]}")
            L += [col("90", "      " + t) for t in ag["recent"][-3:]]
        if not st["agents"]:
            L.append(col("90", "  none dispatched yet"))
        L += ["", col("1", " Claude (latest session)")]
        if st["claude"]:
            msgs, _ = dash.claude_msgs(d, st["claude"][0]["id"], -40)
            for m in msgs[-5:]:
                txt = (m["text"] or "[" + ", ".join(t["name"] for t in m["tools"]) + "]").replace(chr(10), " ")
                L.append(f" {col('36' if m['role'] == 'user' else '35', 'you' if m['role'] == 'user' else 'claude')} {txt}")
        L += ["", col("90", " q = back")]
        out = chr(10).join(l[:w + 14] if ESC not in l else l for l in L)
        sys.stdout.write(f"{ESC}[H{ESC}[J{out}{chr(10)}")
        sys.stdout.flush()
        for _ in range(15):
            time.sleep(0.1)
            if msvcrt.kbhit() and msvcrt.getwch().lower() in ("q", ESC):
                return


def cmd_unity(a):
    """Probe Unity MCP (streamable HTTP initialize). Exit 0 if reachable."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26",
            "capabilities": {}, "clientInfo": {"name": "orch", "version": "1"}}}
    req = urllib.request.Request(a.url, method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            print("reachable", r.status)
    except Exception as e:
        print("unreachable", type(e).__name__)
        sys.exit(1)


def attached():
    f = STATE / "attached.json"
    return json.loads(f.read_text(encoding="utf-8-sig")) if f.exists() else {}


def cmd_mark(a):
    """PostToolUse(Bash) hook: remember which project a chat attached to via /orch, keyed by session_id."""
    ev = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
    ti = ev.get("tool_input") or {}
    import shlex
    try:
        tok = shlex.split(ti.get("command", ""), posix=False)
    except ValueError:
        tok = []
    agent = ti.get("agent") if ev.get("tool_name", "").endswith("orch_dispatch") else (
        tok[tok.index("--agent") + 1].strip(chr(34)) if tok[:1] and "dispatch" in tok[1:3] and tok[0].strip(chr(34)).endswith(("python", "python.exe")) and "--agent" in tok[:-1] else None)
    dproj = find_project(ti.get("project") or ev.get("cwd") or os.getcwd())
    if agent and dproj and ev.get("session_id"):  # remember who dispatched, so only that chat is held by the stop guard
        o = STATE / "owners.json"
        owners = json.loads(o.read_text(encoding="utf-8-sig")) if o.exists() else {}
        owners[f"{dproj}|{agent}"] = ev["session_id"]
        STATE.mkdir(parents=True, exist_ok=True)
        o.write_text(json.dumps(owners), encoding="utf-8")
    cmd = ti.get("command", "")
    m = re.search(r'orch\.py"?\s+context\s+--project\s+"([^"]+)"', cmd)
    if m and ev.get("session_id"):
        STATE.mkdir(parents=True, exist_ok=True)
        r = attached()
        r[ev["session_id"]] = m.group(1)
        (STATE / "attached.json").write_text(json.dumps(dict(list(r.items())[-200:])), encoding="utf-8")


def cmd_context(a):
    """SessionStart hook for Claude: inject compact project state so a fresh Desktop/CLI session knows what is connected."""
    if a.project:  # attach mode (/orch): init if new, then print plain text
        d = find_project(a.project)
        if not d:
            d, _, _ = init_project(a.project)
        ev = {}
    else:
        try:
            ev = json.loads(sys.stdin.buffer.read().decode("utf-8-sig") or "{}")
        except ValueError:
            ev = {}
        d = find_project(os.environ.get("CLAUDE_PROJECT_DIR") or ev.get("cwd") or os.getcwd())
        if not d and ev.get("session_id"):  # chat attached via /orch from another folder
            d = find_project(attached().get(ev["session_id"], "") or ".") if attached().get(ev["session_id"]) else None
    if not d:
        return
    cfg = load_project(d)
    vd = vault_dir(d, cfg)
    NL = chr(10)

    def body(fn, n=12):
        f = vd / fn
        if not f.exists():
            return "-"
        lines = [l for l in f.read_text(encoding="utf-8-sig").splitlines() if l.strip() and not l.startswith(("#", "_"))]
        return NL.join(lines[-n:]) or "-"

    mt = (vd / "00_Master.md").read_text(encoding="utf-8-sig") if (vd / "00_Master.md").exists() else ""
    status = mt.split("## Current status")[-1].strip()[:600] if "## Current status" in mt else "-"
    unity = cfg.get("mcp", {}).get("unity")
    parts = [
        "ORCHESTRATOR CONTEXT (auto-loaded)",
        f"Project: {cfg['name']} [{', '.join(cfg['types'])}] root={cfg['root']}",
        f"Vault notes: {vd}",
        f"Primary Devin agents running: {len(active_runs(d))}/{MAX_PRIMARY}",
        f"Discord: dev={'on' if secret('DISCORD_WEBHOOK_DEV') else 'off'} main={'on' if secret('DISCORD_WEBHOOK_MAIN') else 'off'} | Unity MCP: {unity or 'n/a'}",
        "Status:", status,
        "Active tasks:", body("Active Tasks.md"),
        "Pending:", body("Pending.md", 6),
        "Tools: mcp__orch__orch_status (live check), orch_dispatch, orch_runs, orch_agent_log, orch_report; Unity via mcp__unityMCP__*. Call orch_status to verify connections before claiming anything.",
        "Role: you orchestrate only; Devin writes all code (see ~/.orch/ORCHESTRATOR.md).",
    ]
    if a.project:
        print(NL.join(parts)[:3000])
        return
    txt = NL.join(parts)[:3000]
    if not find_project(ev.get("cwd") or os.getcwd()):  # no CLAUDE.md import here: add the rules text
        txt += NL + (HOME / "ORCHESTRATOR.md").read_text(encoding="utf-8-sig")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": txt}}))


def cmd_init(a):
    d, cfg, created = init_project(a.dir, a.vault)
    print(json.dumps({"root": str(d), "name": cfg["name"], "types": cfg["types"], "vault": cfg["vault"],
                      "vault_is_default": cfg.get("vault_is_default"), "created": created}))


def cmd_status(a):
    d = find_project(a.project or os.getcwd())
    q = STATE / "queue.jsonl"
    print(json.dumps({"project": load_project(d)["name"] if d else None, "running": active_runs(d) if d else [],
                      "webhook_dev": bool(secret("DISCORD_WEBHOOK_DEV")), "webhook_main": bool(secret("DISCORD_WEBHOOK_MAIN")),
                      "queued": len(q.read_text().splitlines()) if q.exists() else 0}))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("init"); s.add_argument("dir"); s.add_argument("--vault"); s.set_defaults(f=cmd_init)
    s = sp.add_parser("report"); s.set_defaults(f=cmd_report)
    s.add_argument("--agent", required=True); s.add_argument("--status", required=True, choices=list(STATUS))
    for k in ("task", "files", "changes", "tests", "kb", "title", "note", "project"):
        s.add_argument("--" + k)
    s.add_argument("--channel", default="dev", choices=["dev", "main"]); s.add_argument("--force", action="store_true")
    s = sp.add_parser("flush"); s.set_defaults(f=lambda a: print("flushed", flush()))
    s = sp.add_parser("hook"); s.add_argument("event"); s.set_defaults(f=cmd_hook)
    s = sp.add_parser("mark"); s.set_defaults(f=cmd_mark)
    s = sp.add_parser("context"); s.add_argument("--project"); s.set_defaults(f=cmd_context)
    s = sp.add_parser("guard"); s.set_defaults(f=cmd_guard)
    s = sp.add_parser("dispatch"); s.set_defaults(f=cmd_dispatch)
    s.add_argument("--agent", required=True, choices=[f"A{i}" for i in range(1, MAX_PRIMARY + 1)])
    s.add_argument("--note", required=True); s.add_argument("--task"); s.add_argument("--project"); s.add_argument("--cwd")
    s.add_argument("--model", default="swe-2-max"); s.add_argument("--permission", default="dangerous")
    s = sp.add_parser("runs"); s.add_argument("--project"); s.set_defaults(f=cmd_runs)
    s = sp.add_parser("ensure-devin"); s.set_defaults(f=cmd_ensure_devin)
    s = sp.add_parser("ensure-claude"); s.set_defaults(f=cmd_ensure_claude)
    s = sp.add_parser("wait"); s.add_argument("--project"); s.add_argument("--timeout", type=int, default=300); s.set_defaults(f=cmd_wait)
    s = sp.add_parser("stop-guard"); s.set_defaults(f=cmd_stop_guard)
    s = sp.add_parser("dash"); s.add_argument("action", choices=["start", "stop", "status", "open"]); s.add_argument("--project"); s.set_defaults(f=cmd_dash)
    s = sp.add_parser("watch"); s.add_argument("--project"); s.set_defaults(f=cmd_watch)
    s = sp.add_parser("unity"); s.add_argument("--url", default="http://127.0.0.1:8080/mcp"); s.set_defaults(f=cmd_unity)
    s = sp.add_parser("status"); s.add_argument("--project"); s.set_defaults(f=cmd_status)
    s = sp.add_parser("webhook-rename"); s.add_argument("name"); s.set_defaults(f=cmd_webhook_rename)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
