@echo off
REM Deal Watch page (http://127.0.0.1:8791): schedule, runs with their steps, live log, opportunities (chime when a new
REM one appears), best price per product, "Run now". Starts the local server in the background if it isn't running yet,
REM then opens the page. Stop it: close it from Task Manager (pythonw.exe) or reboot - it holds no state of its own.
cd /d "%~dp0..\.."
start "" venv\Scripts\pythonw.exe @modules\price_watch\webapp\server.py --open
