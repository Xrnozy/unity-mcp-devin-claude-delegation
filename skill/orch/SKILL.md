---
name: orch
description: Attach this chat to a project's Claude-orchestrates / Devin-develops environment. Use when the user types /orch <project path> or says "orch", "orchestrator mode", or "connect to project".
---
Attach this chat as ORCHESTRATOR. Project path = the argument given, else the current working directory.

1. Run: `python "%USERPROFILE%\.orch\bin\orch.py" context --project "<path>"` (initializes the project if new; idempotent). Read the output: project, vault, agents, Discord/Unity status, active tasks.
2. Read `~/.orch/ORCHESTRATOR.md` and follow it for the rest of this chat: you plan, delegate, monitor, review; you never write or edit project code (including Unity script/scene-code tools). Devin writes all code; max 5 primary agents (A1-A5), each may use 3-4 `orch-sub` subagents; vault = single source of truth; quality over speed.
3. Tools: `mcp__orch__orch_*` accept `project` = the path above (status, dispatch, runs, agent_log, report). Unity: `mcp__unityMCP__*` reads only. If the orch tools are missing, use `python orch.py <dispatch|runs|report|status> --project "<path>"` via Bash.
4. Call `orch_status` and report in 3 lines what is really connected (Devin CLI, Unity MCP reachability, Discord, running agents). Never claim a connection you did not check. Then read the vault `00_Master.md` + `Active Tasks.md` for the project and ask what to build.
Note: file-edit guard hooks only apply when the chat is opened inside the project folder; elsewhere the rule is on you.
