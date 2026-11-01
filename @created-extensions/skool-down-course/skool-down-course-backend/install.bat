@echo off
title SKOOL-DOWN-COURSE - Install
cd /d "%~dp0"

echo ============================================
echo   Skool Down Course - Backend Install
echo ============================================
echo.

:: Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

:: Create venv if it doesn't exist
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [OK] Virtual environment already exists.
)

:: Activate and install
echo.
echo [INFO] Installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt

echo.
echo ============================================
echo   Install complete!
echo   Run: runner.bat to start the backend
echo ============================================
pause
