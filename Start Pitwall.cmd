@echo off
rem Horizon Pitwall - start (double-click). Extra arguments are passed on,
rem e.g.  "Start Pitwall.cmd" --demo
setlocal
cd /d "%~dp0"
rem The path may contain spaces: keep the quotes inside PY.
if exist "%~dp0.venv\Scripts\python.exe" (
  set PY="%~dp0.venv\Scripts\python.exe"
) else (
  where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or newer was not found.
  echo Install it from https://www.python.org/downloads/ and run "Install Pitwall.cmd".
  pause
  exit /b 1
)
%PY% -c "import fastapi, uvicorn, websockets" >nul 2>&1
if errorlevel 1 (
  echo Pitwall is not installed yet. Run "Install Pitwall.cmd" first.
  pause
  exit /b 1
)
echo Starting Horizon Pitwall. Close this window or press Ctrl+C to stop it.
%PY% "%~dp0forza_horizon_6_telemetry.py" --open %*
if errorlevel 1 pause
