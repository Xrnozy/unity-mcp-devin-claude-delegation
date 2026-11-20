param([Parameter(Mandatory)][string]$ProjectDir, [switch]$NoLaunch, [switch]$Cli, [string]$Session = "")
$ErrorActionPreference = "Stop"
$OrchHome = if ($env:ORCH_HOME) { $env:ORCH_HOME } else { Join-Path $env:USERPROFILE ".orch" }
$py = Join-Path $OrchHome "bin\orch.py"
$root = (Resolve-Path -LiteralPath $ProjectDir).Path.TrimEnd('\')
function Ok($m)   { Write-Host "  [ OK ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Orch { & python $py @args }

Write-Host "`n== Claude/Devin orchestration launcher ==" -ForegroundColor Cyan
if (-not (Test-Path $py)) { Bad "Global config missing: $py (set ORCH_HOME or reinstall ~/.orch)"; exit 1 }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Bad "python not found on PATH"; exit 1 }

# 1. detect + initialize project (idempotent; never overwrites, backs up CLAUDE.md before editing)
$initOut = Orch init $root
if ($LASTEXITCODE -ne 0 -or -not $initOut) { Bad "Project init failed (see error above)"; exit 1 }
$info = $initOut | ConvertFrom-Json
Ok "Project: $($info.name)  [$($info.types -join ', ')]"
Ok "Root:    $($info.root)"
if ($info.vault_is_default) { Warn "No Obsidian vault found inside project; using $($info.vault). Re-run 'python `"$py`" init `"$root`" --vault <path>' after deleting .orch\project.json to choose another." }
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

# 5. already-running agents (no duplicate processes are started by this launcher)
if ($st.running.Count -gt 0) { Warn "Devin agents already running: $($st.running -join ', ')" } else { Ok "No Devin agents running" }

if (-not $Cli) {
  $code = '/orch "' + $root + '"'
  try { Set-Clipboard -Value $code; Ok "Connect code copied to clipboard" } catch {}
  Write-Host "`n  ALL CONNECTED. In any Claude Desktop chat, paste:" -ForegroundColor Cyan
  Write-Host "    $code" -ForegroundColor White
  Write-Host "  That chat becomes the orchestrator for this project (Devin codes, vault remembers).`n"
  if (-not $NoLaunch) { Pause }
  exit 0
}
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { Bad "claude CLI not found"; exit 1 }

# 6. session menu (arrow keys + Enter). claude.ai session links cannot be attached to CLI sessions.
function Select-Menu($title, $items) {
  $i = 0
  while ($true) {
    Clear-Host; Write-Host $title -ForegroundColor Cyan; Write-Host "  (Up/Down arrows, Enter to select, Esc to quit)`n"
    for ($n = 0; $n -lt $items.Count; $n++) {
      if ($n -eq $i) { Write-Host "  > $($items[$n])" -ForegroundColor Black -BackgroundColor Cyan } else { Write-Host "    $($items[$n])" }
    }
    $k = [Console]::ReadKey($true)
    switch ($k.Key) {
      'UpArrow'   { if ($i -gt 0) { $i-- } else { $i = $items.Count - 1 } }
      'DownArrow' { if ($i -lt $items.Count - 1) { $i++ } else { $i = 0 } }
      'Enter'     { Clear-Host; return $i }
      'Escape'    { Clear-Host; return -1 }
    }
  }
}
$boot = "Orchestrator boot: read the vault 00_Master.md and Active Tasks.md for this project, then summarize current state, active agents and pending work in max 10 lines. Do not write project code."
$mode = 0
if ($Session -eq "") {
  $mode = Select-Menu "$($info.name) - start Claude session" @("New session", "Continue last session", "Pick a previous session (Claude's own list)", "Enter a session name or ID")
  if ($mode -eq -1) { exit 0 }
  if ($mode -eq 3) { $Session = Read-Host "Session name or ID" }
}
Set-Location $root
if ($Session) { & claude --resume $Session }
elseif ($mode -eq 1) { & claude --continue }
elseif ($mode -eq 2) { & claude --resume }
else { & claude $boot }
