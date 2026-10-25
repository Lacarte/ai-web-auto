@echo off
title Web Goal Agent - Backend (manual)
cd /d "%~dp0web-goal-agent-backend"
rem Normally the extension starts the backend through the native host. Use this to run it
rem in a console instead (live logs). A backend started here is never stopped by the extension.
set "PY=%~dp0..\..\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] ai-web-auto venv not found. Run install.bat in the project root first.
    pause
    exit /b 1
)
"%PY%" -m goal_agent_backend --started-by manual %*
echo.
echo [INFO] Backend stopped.
pause
