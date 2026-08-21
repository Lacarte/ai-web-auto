@echo off
REM Start the backend in orchestrated mode: WebSocket server + command-file bridge.
REM The brain (Claude) drives the browser by writing orchestrator\inbox\<n>.json
REM and reading orchestrator\outbox\<n>.json. Keep this window open.
cd /d "%~dp0"
venv\Scripts\python -m ai_web_auto_backend.run_orchestrated %*
pause
