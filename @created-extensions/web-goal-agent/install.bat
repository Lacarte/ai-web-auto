@echo off
title Web Goal Agent - Install
cd /d "%~dp0"
echo ============================================
echo   Web Goal Agent - Install
echo ============================================
echo.

set "PY=%~dp0..\..\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] ai-web-auto venv not found. Run install.bat in the project root first.
    pause
    exit /b 1
)

echo [INFO] Registering the native messaging host (lets the extension start the backend)...
"%PY%" web-goal-agent-backend\native_host\install_host.py
if errorlevel 1 (
    echo [ERROR] Native host registration failed. See the output above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Load the extension
echo ============================================
echo   1. Open chrome://extensions  (Developer mode ON)
echo   2. Load unpacked:  %~dp0web-goal-agent-extension
echo   3. Its ID must be  lfejjmliceaahhcnbdlmafhklbehnpab  (fixed by the manifest key)
echo   4. Click the extension icon: it starts the backend and connects by itself.
echo.
echo   Try it on the bundled demo site:  serve-demo-site.bat
echo   Optional AI planner for free-text goals: set ANTHROPIC_API_KEY
echo ============================================
pause
