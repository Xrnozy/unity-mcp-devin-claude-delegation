# Unity MCP + Devin + Claude delegation

Claude plans and reviews. Devin writes all the code. An Obsidian vault is the shared memory. Discord shows live progress. Works in any project folder (Unity, Python, JS, ...), with Unity MCP when the project is Unity.

- **Claude = orchestrator.** Plans, delegates, monitors, reviews. A guard hook blocks it from editing project code (including Unity's script-writing MCP tools).
- **Devin = developer.** Up to 5 primary agents (`swe-2-max`), each can use `orch-sub` subagents via Devin's `run_subagent`. `dispatch` refuses a 6th.
- **Obsidian vault = brain.** Per project: master note, tasks, bugs, decisions, history, agent logs. Agents read the real files, not copies.
- **Discord embeds.** Devin lifecycle hooks send compact status embeds automatically; failed sends are queued and retried without duplicates.
- **Per-project isolation.** Each project has its own `.orch/project.json`, vault notes, and `CLAUDE.md` block. Global rules live in `~/.orch`.

## Requirements
Windows, Python 3, Claude Desktop or CLI, Devin CLI (signed in). Optional: Obsidian, Unity + a Unity MCP server (default `http://127.0.0.1:8080/mcp`), Discord webhooks.

## Install
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```
Copies the toolkit to `~/.orch`, adds the `/orch` command, registers the `orch` MCP tools, and merges hooks into your Claude and Devin configs (existing files are backed up). Webhooks are asked once and stored only in `~/.orch/secrets.env`.

## Use
1. Copy `~/.orch/ORCH-START.bat` into any project folder and double-click it. It detects the project, creates its vault notes, checks Devin/Unity/Discord, and copies a connect code to your clipboard.
2. Open that folder in Claude Desktop (loads automatically), or paste `/orch "<project path>"` into any chat.
3. Describe what you want. Claude writes task notes in the vault and dispatches Devin agents.

## Dashboard and control panel
`ORCH-START.bat` is an interactive control panel (arrow keys). It checks every connection, starts ONE background dashboard server (127.0.0.1 only, read-only) if none is running, and then offers: open the dashboard, a live terminal view, re-check connections, copy the `/orch` code, start a Claude CLI session, stop the dashboard. It never touches your clipboard, opens a browser, starts Claude or disturbs running agents unless you choose it.

The dashboard (`http://127.0.0.1:8765`) shows every Devin agent and every run it ever had (read from Devin's own session store, `%APPDATA%\devin\cli\sessions.db`, opened read-only), with each run's subagents nested under it, the full chat history rendered as markdown, per-file diffs, the Claude chat with its real Desktop session names, and a light/dark theme. It shows every Devin agent, its subagents (attributed by time span, so overlapping background subagents cannot be told apart), a live activity timeline (edits with diffs, commands, reads), per-file changes, the agent's output, its task note, and the Claude chat of the project. Webhook URLs are redacted from everything it serves.

## Autonomy and safety
- Claude drives the goal itself: `orch_wait` blocks until agents finish, and a Stop hook keeps the chat that dispatched agents working until they end.
- **Devin agents run headless in `dangerous` mode** (all tools auto-approved), because headless Devin otherwise dies on the first command it won't auto-approve. A PreToolUse hook blocks `rm -rf`, `git push`, `git reset --hard`, recursive deletes and `pip`/`npm install`, and reports them as "waiting". This is a blocklist, not a sandbox: commands it does not list still run. Change the default in `orch.py` (`--permission`) if you want it stricter.
- Use ONE orchestrator chat per project; two chats will collide on agents and vault notes.

## Known limits
- Tested on one Windows machine (Elderwood Unity project + a throwaway Python project). `install.ps1` is lightly tested. Not tested: 5 agents in parallel, a real context compaction, Claude Desktop UI (CLI only).
- The code-edit guard only applies to chats opened inside the project folder; elsewhere it is an instruction.
- Claude can still use Unity scene tools (`manage_scene`, `manage_gameobject`); only code-writing tools are blocked.
- Graph Mind sync is not implemented; notes link to existing `[[wikilinks]]` only.
- Never commit `~/.orch/secrets.env`.
