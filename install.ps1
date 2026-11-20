# One-time installer. Run: powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
$dst = Join-Path $env:USERPROFILE ".orch"
function Need($c) { if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { throw "$c not found on PATH - install it first." } }
Need python; Need claude
if (-not (Get-Command devin -ErrorAction SilentlyContinue)) { Write-Warning "Devin CLI not found: delegation will not work until it is installed and signed in." }

New-Item -ItemType Directory -Force "$dst\bin", "$dst\state", "$env:USERPROFILE\.claude\skills\orch" | Out-Null
Copy-Item "$PSScriptRoot\bin\*.py" "$dst\bin" -Force
Copy-Item "$PSScriptRoot\launch.ps1", "$PSScriptRoot\ORCHESTRATOR.md", "$PSScriptRoot\ORCH-START.bat" $dst -Force
Copy-Item "$PSScriptRoot\skill\orch\SKILL.md" "$env:USERPROFILE\.claude\skills\orch" -Force

$sec = "$dst\secrets.env"
if (-not (Test-Path $sec)) {
  Write-Host "Discord webhooks are optional (press Enter to skip). They are stored only in $sec."
  $dev = Read-Host "Webhook for agent updates"; $main = Read-Host "Webhook for orchestrator updates"
  "DISCORD_WEBHOOK_DEV=$dev`nDISCORD_WEBHOOK_MAIN=$main`n" | Set-Content $sec -Encoding ascii
  icacls $sec /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
}

$py = "$dst\bin\orch.py"
if (Get-Command devin -ErrorAction SilentlyContinue) { Write-Host "Devin:  $(python $py ensure-devin)" }
Write-Host "Claude: $(python $py ensure-claude)"
claude mcp get orch *> $null
if ($LASTEXITCODE -ne 0) { claude mcp add -s user orch python "$dst\bin\orch_mcp.py" | Out-Null; Write-Host "MCP:    orch registered" } else { Write-Host "MCP:    orch already registered" }
Write-Host "`nDone. Copy $dst\ORCH-START.bat into a project folder, double-click it, then open that folder in Claude Desktop (or paste the /orch code it prints)."
