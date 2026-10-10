@echo off
setlocal
cd /d "%~dp0"
title Sentinel trading bot - keep this window open
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo.
  echo  Sentinel needs Python 3.7 or newer, and it was not found on this computer.
  echo  1. Download it from https://www.python.org/downloads/
  echo  2. During install, tick "Add python.exe to PATH"
  echo  3. Run this file again.
  echo.
  pause
  exit /b 1
)
echo.
echo  Sentinel is starting. Your dashboard opens in your browser at http://localhost:8765
echo  Keep this window open - closing it stops the bot. Press Ctrl+C to stop it.
echo.
start "" /min cmd /c "timeout /t 3 >nul & start http://localhost:8765/?r=%RANDOM%"
:loop
%PY% server.py
echo.
echo  The server stopped. Restarting in 5 seconds (close this window to quit)...
timeout /t 5 >nul
goto loop
