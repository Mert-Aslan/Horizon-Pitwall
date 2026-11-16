@echo off
rem Horizon Pitwall - install into a private folder (.venv) next to this file.
rem Nothing outside this folder is changed. Offline if a "wheels" folder is present.
setlocal
cd /d "%~dp0"
where py >nul 2>&1 && (set "BASE=py -3") || (set "BASE=python")
%BASE% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 or newer was not found.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and run this again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating the private Python environment in "%~dp0.venv" ...
  %BASE% -m venv .venv || goto :fail
)
if exist "wheels\" (
  echo Installing from the bundled "wheels" folder ^(no internet needed^) ...
  ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheels -r requirements.txt || goto :fail
) else (
  echo Downloading the required packages from pypi.org ^(about 10 MB^) ...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, websockets; print('Installed.')" || goto :fail
echo.
echo Done. Start Pitwall with "Start Pitwall.cmd".
echo Your recordings and logs are kept in %LOCALAPPDATA%\HorizonPitwall ^(not in this folder^).
pause
exit /b 0
:fail
echo.
echo Installation failed. Nothing outside "%~dp0" was changed. See docs\INSTALL.md.
pause
exit /b 1
