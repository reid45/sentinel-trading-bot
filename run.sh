#!/usr/bin/env bash
# Starts Sentinel on macOS / Linux. Needs Python 3.7+. Usage: ./run.sh
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then echo "Sentinel needs Python 3.7 or newer: https://www.python.org/downloads/"; exit 1; fi
echo "Sentinel is starting. Dashboard: http://localhost:8765  (Ctrl+C to stop)"
( sleep 3; (command -v xdg-open >/dev/null && xdg-open "http://localhost:8765/?r=$RANDOM") || (command -v open >/dev/null && open "http://localhost:8765/?r=$RANDOM") ) >/dev/null 2>&1 &
while true; do "$PY" server.py; echo "Server stopped. Restarting in 5s (Ctrl+C to quit)"; sleep 5; done
