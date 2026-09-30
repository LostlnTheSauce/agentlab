@echo off
title Agent Lab
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Setting up for the first time. This takes a minute...
  python -m venv .venv || (echo Python was not found. Install Python 3.13 from python.org and try again. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || (echo Installing packages failed. & pause & exit /b 1)
)

".venv\Scripts\python.exe" -c "from lab.config import get_settings; from lab.db import DB; import sys; s=get_settings(); sys.exit(0 if DB(s.db_path).get('password_hash') else 1)"
if errorlevel 1 (
  echo.
  echo No login password yet. Choose one now ^(at least 10 characters; nothing shows while you type^).
  ".venv\Scripts\python.exe" -m lab set-password || (pause & exit /b 1)
)

echo.
echo Agent Lab is running at http://127.0.0.1:8000
echo Keep this window open while you use it. Close it (or press Ctrl+C) to stop.
echo.
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m lab serve --port 8000
pause
