@echo off
title Web Goal Agent - Uninstall
cd /d "%~dp0"
set "PY=%~dp0..\..\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" web-goal-agent-backend\native_host\install_host.py --uninstall
echo Remove the extension itself from chrome://extensions.
pause
