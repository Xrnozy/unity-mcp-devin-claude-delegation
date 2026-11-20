#!/usr/bin/env python3
"""Minimal stdio MCP server: gives Claude real tools to orchestrate Devin. Stdlib only.
Delegates to orch.py so there is one implementation. Project = nearest ancestor of cwd with .orch/project.json."""
import json, os, subprocess, sys
from pathlib import Path

ORCH = str(Path(__file__).with_name("orch.py"))
STAT = ["completed", "progress", "waiting", "failed", "review"]
TOOLS = [
    {"name": "orch_status", "description": "LIVE check of the orchestration environment: project, Devin CLI, Unity MCP reachability, Discord webhooks configured (booleans only), running primary agents, queued Discord updates.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string", "description": "Project root (optional; defaults to cwd)"}}}},
    {"name": "orch_dispatch", "description": "Start a primary Devin agent (SWE-2 Max, headless) on a task note in the vault. Max 5 concurrent; refuses a 6th or a busy agent id. The agent may spawn 3-4 `orch-sub` subagents itself. Returns pid and log path only if it really started.",
     "inputSchema": {"type": "object", "required": ["agent", "note"], "properties": {"project": {"type": "string"},
         "agent": {"type": "string", "enum": ["A1", "A2", "A3", "A4", "A5"]},
         "note": {"type": "string", "description": "Absolute path of the task note (vault Tasks/T-xxx.md)"},
         "task": {"type": "string", "description": "Short task title"}}}},
    {"name": "orch_runs", "description": "How many primary Devin agents are running right now, and which.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string", "description": "Project root (optional; defaults to cwd)"}}}},
    {"name": "orch_wait", "description": "BLOCK until all primary Devin agents finish (or timeout_s, default 240, max 540), then return each agent's outcome: alive, note status, files modified, log tail. Call this after dispatch instead of ending your turn; if timed_out is true call it again.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "timeout_s": {"type": "integer", "default": 240}}}},
    {"name": "orch_agent_log", "description": "Tail of a Devin agent's run log (.orch/runs/<agent>.log).",
     "inputSchema": {"type": "object", "required": ["agent"], "properties": {"project": {"type": "string"}, "agent": {"type": "string"}, "chars": {"type": "integer", "default": 3000}}}},
    {"name": "orch_report", "description": "Send a Discord embed + vault log line. channel 'main' for orchestrator-level events, 'dev' for agent activity.",
     "inputSchema": {"type": "object", "required": ["agent", "status"], "properties": {"project": {"type": "string"},
         "agent": {"type": "string"}, "status": {"type": "string", "enum": STAT}, "title": {"type": "string"},
         "task": {"type": "string"}, "files": {"type": "string"}, "changes": {"type": "string"}, "tests": {"type": "string"},
         "kb": {"type": "string"}, "note": {"type": "string"}, "channel": {"type": "string", "enum": ["dev", "main"]}}}},
]


def run(args, timeout=60):
    r = subprocess.run([sys.executable, ORCH, *args], capture_output=True, text=True, cwd=os.getcwd(), timeout=timeout)
    return (r.stdout + r.stderr).strip() or "(no output)", r.returncode != 0


def status():
    out, _ = run(["status"])
    try:
        st = json.loads(out)
    except ValueError:
        return out
    dv = subprocess.run("devin --version", capture_output=True, text=True, shell=True)
    st["devin_cli"] = dv.stdout.strip() or "NOT FOUND"
    st["unity_mcp"] = run(["unity"])[0]
    return json.dumps(st, indent=1)


def call(name, a):
    if a.get("project"):
        os.chdir(a.pop("project"))  # attach to the project named by /orch
    if name == "orch_status":
        return status(), False
    if name == "orch_runs":
        return run(["runs"])
    if name == "orch_wait":
        tmo = min(int(a.get("timeout_s", 240)), 540)
        return run(["wait", "--timeout", str(tmo)], timeout=tmo + 30)
    if name == "orch_dispatch":
        args = ["dispatch", "--agent", a["agent"], "--note", a["note"]]
        return run(args + (["--task", a["task"]] if a.get("task") else []))
    if name == "orch_agent_log":
        root = os.getcwd()
        f = Path(root) / ".orch" / "runs" / f"{a['agent']}.log"
        return (f.read_text(encoding="utf-8", errors="replace")[-int(a.get("chars", 3000)):] if f.exists() else "no log"), False
    if name == "orch_report":
        args = ["report"]
        for k, v in a.items():
            args += [f"--{k}", str(v)]
        return run(args)
    return f"unknown tool {name}", True


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        m = json.loads(line)
        mid, meth = m.get("id"), m.get("method")
        if mid is None:
            continue  # notification
        if meth == "initialize":
            res = {"protocolVersion": m["params"].get("protocolVersion", "2025-03-26"), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "orch", "version": "1.0"}}
        elif meth == "tools/list":
            res = {"tools": TOOLS}
        elif meth == "tools/call":
            try:
                text, err = call(m["params"]["name"], m["params"].get("arguments") or {})
            except Exception as e:
                text, err = f"{type(e).__name__}: {e}", True
            res = {"content": [{"type": "text", "text": text}], "isError": err}
        elif meth == "ping":
            res = {}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no such method"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
