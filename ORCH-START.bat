@echo off
rem Universal Claude/Devin orchestration launcher. Copy into any project folder and double-click.
rem Project root = folder containing this file. Global config lives in %ORCH_HOME% (default %USERPROFILE%\.orch).
setlocal
if not defined ORCH_HOME set "ORCH_HOME=%USERPROFILE%\.orch"
if not exist "%ORCH_HOME%\launch.ps1" (
  echo [FAIL] Global orchestrator config not found: %ORCH_HOME%\launch.ps1
  pause & exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ORCH_HOME%\launch.ps1" -ProjectDir "%~dp0." %*
if errorlevel 1 pause
endlocal
