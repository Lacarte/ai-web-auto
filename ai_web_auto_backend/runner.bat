@echo off
title AI Web-Auto Backend
cd /d "%~dp0\.."

echo ============================================
echo   AI Web-Auto Backend Server
echo ============================================
echo.
echo [INFO] Starting WebSocket server on ws://localhost:8765
echo.

venv\Scripts\python.exe -m ai_web_auto_backend.automation_controller

echo.
echo [INFO] Server stopped.
pause
