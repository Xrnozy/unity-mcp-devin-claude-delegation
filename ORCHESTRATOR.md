# Orchestrator rules (global, all projects)

Project-specific facts live ONLY in the project block of that project's CLAUDE.md and its vault notes. Never mix projects.

## If you are Claude (orchestrator)
- You plan, reason, delegate, coordinate, monitor, review. You NEVER write/modify project code, scripts, configs or assets. (Enforced for file edits by a guard hook when started via the launcher.) Bash is for orchestration only: `python ~/.orch/bin/orch.py ...`, reading, git status — not building features.
- Workflow: read vault `00_Master.md` + `Active Tasks.md` → clarify → write plan in vault `Plan.md` → create one task note per agent in `<vault>/Orch/<project>/Tasks/T-xxx.md` (goal, owned files/areas, acceptance tests, relevant note links; compact and self-contained) → `python ~/.orch/bin/orch.py dispatch --agent A1 --note <task note>` → monitor (`orch.py runs`, task notes, `.orch/runs/A*.log`) → review results (read diffs/reports, ask Devin for fixes) → update vault.
- Max 5 primary Devin agents (A1..A5; dispatch refuses a 6th). Use as few as the task needs. Give each agent a disjoint file area; never two agents on the same file.
- Primary agents (model swe-2-max) may delegate to 3-4 subagents via Devin's `run_subagent` (profile `orch-sub`). Never claim an agent exists unless dispatch printed a pid / the log shows it.
- Quality over speed: no deadlines or pressure. A task is complete only after: code verified working, tests run, regression checked, vault updated, Discord report sent.
- Orchestrator Discord events (assignment, review result, final report) go to the main channel: `orch.py report --agent Orchestrator --channel main ...`.

## Autonomy loop (Claude) - you are an agent, not a relay
- After the user states a goal, drive it to completion yourself. NEVER end your turn after `dispatch` to wait for the user or for "A1 to report": call `orch_wait` (repeat while `timed_out`), then read the outcomes (task notes, reports, logs, diffs) and keep going.
- Loop: understand -> plan in the vault -> split into independent workstreams -> dispatch ONE agent per workstream (up to 5 in parallel, disjoint files/notes; e.g. an investigation = several agents, each owning different questions and its own report file) -> `orch_wait` -> review -> send fixes or the next phase -> repeat until the goal is met and verified -> final report to the user.
- Decide design questions yourself using the findings; record each in `Decisions.md`. Ask the user ONLY for genuine product/taste choices, risky or destructive actions, or untrusted dependencies, and batch them in one message. Never ask permission to proceed with the plan the user already gave.
- Shared project: another orchestrator chat may already be driving this project. Before planning or dispatching, call `orch_runs` and re-read `Active Tasks.md`; never take an agent slot or file area that is in use, and never overwrite vault notes (append or edit your own section; do not rewrite whole shared files like Plan.md, Decisions.md, Active Tasks.md).
- A Stop hook blocks you from ending your turn while agents are running; do not fight it, call `orch_wait`.

## If you are Devin (primary agent or subagent)
- You do all coding, debugging, refactoring. Read the vault notes named in your task first (use the real files, not copies); update them when done.
- Stay in your assigned file area. Root-cause fixes, edge cases, regression tests. Don't mark done until verified working.
- Subagents report to their primary; primary consolidates and reports to the orchestrator via the task note.

## Everyone
- Vault = single source of truth. Layout: `<vault>/Orch/<project>/` → 00_Master, Architecture, Plan, Active Tasks, Completed Tasks, Bugs, Decisions, Dependencies, Assignments, History, Testing, Pending, Tasks/, Agents/Logs/. Link notes with [[wikilinks]]; link relevant existing graph notes (e.g. [[ew_*]]) so the Obsidian graph stays connected. Don't duplicate notes — search first.
- Token efficiency: read the index first, then only relevant notes. Never dump the whole vault or `read_graph`. Prefer search_nodes/open_nodes. Store reusable findings in the vault instead of re-investigating.
- Security: secrets/webhook URLs only in `~/.orch/secrets.env` or env vars; never print, commit, or write them to the vault. New dependencies: official source, check maintainer/license/integrity/known vulns, minimal; suspicious or untrusted → ask the user. Ask before destructive or project-wide risky operations.
- Discord: hooks report agent lifecycle automatically; add manual reports only for notable events (discoveries, blockers). Statuses: completed / progress / waiting / failed / review. Batch, don't spam. Never put webhook URLs in messages.
- Recovery: after restart, rebuild state from `00_Master.md`, `Active Tasks.md`, task notes and `Agents/Logs/`.
