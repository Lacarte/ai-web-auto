@echo off
title Web Goal Agent - Demo site on http://127.0.0.1:8777
cd /d "%~dp0"
set "PY=%~dp0..\..\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo Demo CRM:   http://127.0.0.1:8777/crm.html
echo Edge cases: http://127.0.0.1:8777/edge.html
echo The demo-* goals in web-goal-agent-backend\goals target these pages.
"%PY%" -m http.server 8777 --bind 127.0.0.1 --directory tests\fixtures\site
