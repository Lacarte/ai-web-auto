@echo off
rem Native messaging entry point - Chrome launches this. Nothing may print to stdout
rem except the protocol frames host.py writes, so keep this file free of echo output.
setlocal
set "PY=%~dp0..\..\..\..\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -u "%~dp0host.py" %*
