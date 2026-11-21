param([Parameter(Mandatory)][string]$ProjectDir, [switch]$NoMenu, [switch]$NoLaunch, [switch]$Cli, [string]$Session = "")
$ErrorActionPreference = "Stop"
$OrchHome = if ($env:ORCH_HOME) { $env:ORCH_HOME } else { Join-Path $env:USERPROFILE ".orch" }
$py = Join-Path $OrchHome "bin\orch.py"
$root = (Resolve-Path -LiteralPath $ProjectDir).Path.TrimEnd('\')
function Ok($m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Orch { & python $py @args }

Write-Host "`n== Claude/Devin orchestration control panel ==" -ForegroundColor Cyan
if (-not (Test-Path $py)) { Bad "Global config missing: $py (set ORCH_HOME or reinstall ~/.orch)"; exit 1 }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Bad "python not found on PATH"; exit 1 }

# 1. detect + initialize project (idempotent; writes only what is missing or changed; CLAUDE.md is backed up first)
$initOut = Orch init $root
if ($LASTEXITCODE -ne 0 -or -not $initOut) { Bad "Project init failed (see error above)"; exit 1 }
$info = $initOut | ConvertFrom-Json
Ok "Project: $($info.name)  [$($info.types -join ', ')]"
Ok "Root:    $($info.root)"
if ($info.vault_is_default) { Warn "No Obsidian vault found inside project; using $($info.vault)." }
else { Ok "Vault:   $($info.vault)" }
if ($info.created) { Warn "First-time setup: created .orch\project.json, vault notes, CLAUDE.md block (backup in .orch\backups if it existed)" }

# 2. Devin
if (Get-Command devin -ErrorAction SilentlyContinue) {
  Ok "Devin CLI: $((& devin --version) -join ' ')"
  Ok "Devin integration: $(Orch ensure-devin)"
} else { Warn "Devin CLI not found - delegation unavailable" }

# 3. Unity MCP (only for Unity projects)
$cfg = Get-Content (Join-Path $root ".orch\project.json") -Raw | ConvertFrom-Json
if ($cfg.mcp.unity) {
  $r = Orch unity --url $cfg.mcp.unity
  if ($LASTEXITCODE -eq 0) {
    Ok "Unity MCP $($cfg.mcp.unity): $r"
    $mj = Join-Path $root ".mcp.json"
    if (-not ((Test-Path $mj) -and ((Get-Content $mj -Raw) -match "unityMCP"))) {
      Push-Location $root; & claude mcp add -s project -t http unityMCP $cfg.mcp.unity *> $null; Pop-Location
      Ok "Registered unityMCP for Claude in .mcp.json (approve it on first use)"
    }
  } else { Warn "Unity MCP not reachable at $($cfg.mcp.unity) - start the Unity Editor/MCP bridge; continuing without it" }
} else { Ok "Unity MCP: not applicable" }

# 4. Discord (booleans only, never URLs) + retry queued updates
$st = Orch status --project $root | ConvertFrom-Json
if ($st.webhook_dev -and $st.webhook_main) { Ok "Discord webhooks: dev=configured main=configured" }
else { Warn "Discord webhook(s) missing in ~/.orch/secrets.env (dev=$($st.webhook_dev) main=$($st.webhook_main))" }
if ($st.queued -gt 0) { Ok "Retried queued Discord updates: $(Orch flush)" }

# 5. running agents are only reported, never touched
if ($st.running.Count -gt 0) { Warn "Devin agents already running (left alone): $($st.running -join ', ')" } else { Ok "No Devin agents running" }

# 6. dashboard: one global background server, started only if not already running. Opens nothing.
$dashUrl = ""
try { $dashUrl = (Orch dash start | Select-Object -Last 1); Ok "Dashboard server: $dashUrl (background; choose 'Open dashboard' to view)" }
catch { Warn "Dashboard server could not start (see ~/.orch/state/dash.log)" }

if ($NoMenu -or $NoLaunch) { Ok "Setup complete (no menu)"; exit 0 }

# 7. every interactive launch opens a fresh agent set (new A1-A5 slots, its own dashboard view).
#    Agents still running in earlier sets are reported and left alone.
try {
  $ns = Orch newset --project $root | ConvertFrom-Json
  if ($ns.created) { Ok "New agent set $($ns.set): fresh A1-A5 slots (earlier sets stay viewable in the dashboard)" }
  else { Ok "Agent set $($ns.set): reused, no agent has been started in it yet" }
  if ($ns.other_running.Count -gt 0) { Warn "Agents still running in earlier sets (left alone): $($ns.other_running -join ', ')" }
} catch { Warn "Could not open a new agent set: $($_.Exception.Message)" }

function Select-Menu($title, $items) {
  Write-Host "`n$title" -ForegroundColor Cyan
  Write-Host "  (Up/Down arrows, Enter = select, Esc = quit)"
  1..$items.Count | ForEach-Object { Write-Host "" }
  $top = [Console]::CursorTop - $items.Count; $i = 0
  while ($true) {
    [Console]::SetCursorPosition(0, $top)
    for ($n = 0; $n -lt $items.Count; $n++) {
      $line = ("  " + $(if ($n -eq $i) { "> " } else { "  " }) + $items[$n]).PadRight([Console]::WindowWidth - 1)
      if ($n -eq $i) { Write-Host $line -ForegroundColor Black -BackgroundColor Cyan } else { Write-Host $line }
    }
    switch (([Console]::ReadKey($true)).Key) {
      'UpArrow'   { $i = ($i + $items.Count - 1) % $items.Count }
      'DownArrow' { $i = ($i + 1) % $items.Count }
      'Enter'     { return $i }
      'Escape'    { return -1 }
    }
  }
}

function Start-ClaudeHere {
  if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Bad "claude CLI not found"; return }
  $m = Select-Menu "Claude CLI session in $($info.name)" @("New session", "Continue last session", "Pick a previous session (Claude's own list)", "Enter a session name or ID", "Back")
  if ($m -lt 0 -or $m -eq 4) { return }
  $boot = "Orchestrator boot: read the vault 00_Master.md and Active Tasks.md for this project, then summarize current state, active agents and pending work in max 10 lines. Do not write project code."
  Push-Location $root
  try {
    if ($m -eq 0) { & claude $boot } elseif ($m -eq 1) { & claude --continue } elseif ($m -eq 2) { & claude --resume }
    else { $s = Read-Host "Session name or ID"; if ($s) { & claude --resume $s } }
  } finally { Pop-Location }
}

if ($Cli) { Start-ClaudeHere; exit 0 }

$items = @("Open dashboard in browser", "Live view here (agents, subagents, activity, Claude)", "Re-run connection checks",
           "Copy /orch connect code to clipboard", "Start a Claude CLI session here", "Stop dashboard server", "Quit (leaves dashboard running)")
$msg = ""; $first = $true
while ($true) {
  if (-not $first) { Clear-Host; Write-Host "== $($info.name) control panel ==" -ForegroundColor Cyan }
  $first = $false
  $now = Orch status --project $root | ConvertFrom-Json
  Write-Host ("`n  Agent set {3} | running: {0}/5 {1}   Dashboard: {2}" -f $now.running.Count, $(if ($now.running.Count) { "(" + ($now.running -join ", ") + ")" } else { "" }), $dashUrl, $now.set)
  if ($msg) { Write-Host "  $msg" -ForegroundColor Green; $msg = "" }
  $c = Select-Menu "What do you want to do?" $items
  switch ($c) {
    0 { $msg = (Orch dash open --project $root | Select-Object -Last 1); $dashUrl = $msg -replace '^opened ', '' }
    1 { Orch watch --project $root }
    2 { & $PSCommandPath -ProjectDir $root -NoMenu; Write-Host "`n  Press any key to return..."; [void][Console]::ReadKey($true) }
    3 { Set-Clipboard -Value ('/orch "' + $root + '"'); $msg = "Copied: /orch `"$root`"  (paste it into any Claude chat)" }
    4 { Start-ClaudeHere }
    5 { $msg = (Orch dash stop | Select-Object -Last 1); $dashUrl = "(stopped)" }
    default { exit 0 }
  }
}
