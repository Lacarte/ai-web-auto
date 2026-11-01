@echo off
rem Price watch: eBay + Amazon, every interval_minutes, until Ctrl+C. Needs the AI Web-Auto extension in Chrome.
rem Stop runner.bat (or anything else on port 8765) first.
cd /d "%~dp0"
set WATCH=%1
if "%WATCH%"=="" set WATCH=watches\llm-parts.json
"%~dp0..\..\venv\Scripts\python.exe" price_watch.py %WATCH%
pause
