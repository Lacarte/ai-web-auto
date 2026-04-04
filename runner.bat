@echo off
title AI Web Automation Controller
echo ============================================
echo   AI-Powered Web Automation Platform
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

:: Install dependencies if needed
if not exist "venv" (
    echo [SETUP] Creating virtual environment...
    python -m venv venv
    echo [SETUP] Installing dependencies...
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo.
echo [INFO] Starting WebSocket server on ws://localhost:8765
echo [INFO] Load the Chrome extension from: chrome-extension\
echo [INFO] Token: local-dev-token (pre-configured)
echo.
echo COMMANDS:
echo   navigate ^<url^>       - Open a URL
echo   screenshot           - Take a screenshot
echo   discover             - Discover interactive elements
echo   type ^<text^>          - Type into focused input (CDP)
echo   click ^<selector^>     - Click element
echo   extract              - Extract page text
echo   task ^<objective^>     - Run AI automation task
echo.

python -m ai_web_auto_python.automation_controller

echo.
echo [INFO] Server stopped.
pause
