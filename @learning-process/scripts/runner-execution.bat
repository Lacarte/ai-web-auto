@echo off
title ImageFX Execution Runner
echo ============================================
echo   Self-Correcting Execution Runner
echo ============================================
echo.

if not exist "venv" (
    echo [SETUP] Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo.
echo [INFO] Make sure:
echo   1. Chrome extension is loaded and reloaded
echo   2. No other runner.bat is running (port 8765)
echo.

if "%~1"=="" (
    python runner-execution.py
) else (
    python runner-execution.py --prompt "%*"
)

echo.
pause
