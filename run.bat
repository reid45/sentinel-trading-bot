@echo off
cd /d "%~dp0"
:loop
echo Starting Sentinel on http://localhost:8765
start "" http://localhost:8765
py server.py
echo Server stopped - restarting in 5s (Ctrl+C to quit)
timeout /t 5 >nul
goto loop
