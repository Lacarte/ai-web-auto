@echo off
title AI Web Automation Controller
cd /d "%~dp0"
echo ============================================
echo   AI-Powered Web Automation Platform
echo ============================================
echo.

:: Dependencies are set up by install.bat
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)

echo.
echo [INFO] Starting WebSocket server on ws://localhost:8765
echo [INFO] Load the Chrome extension from: ai-web-auto-extension\
echo [INFO] Token: local-dev-token (pre-configured)
echo.
echo COMMANDS:
echo   navigate ^<url^>       - Open a URL
echo   screenshot           - Take a screenshot
echo   discover             - Discover interactive elements
echo   type ^<text^>          - Type into focused input (CDP)
echo   click ^<selector^>     - Click element
echo   extract              - Extract page text
echo   observe [selector]   - Start DOM observation (default: body)
echo   diff                 - Show DOM changes since last snapshot
echo   unobserve            - Stop DOM observation
echo   task ^<objective^>     - Run AI automation task
echo   quit                 - Exit
echo.

venv\Scripts\python -m ai_web_auto_backend.automation_controller

echo.
echo [INFO] Server stopped.
pause
