@echo off
title AI Web-Auto - Install
cd /d "%~dp0"

echo ============================================
echo   AI Web-Auto - Install
echo ============================================
echo.

:: Check for Python 3.10+
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)
python -c "import sys; sys.exit(sys.version_info < (3, 10))"
if errorlevel 1 (
    echo [ERROR] Python 3.10+ required. Found:
    python --version
    pause
    exit /b 1
)

:: Create venv if it doesn't exist
if not exist "venv\Scripts\python.exe" (
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

:: Install via the venv's python directly - activate.bat and pip.exe
:: hardcode the venv's original path and break if the folder is moved
echo.
echo [INFO] Installing dependencies...
venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed. See the pip output above.
    pause
    exit /b 1
)
echo [OK] Dependencies installed.

echo.
echo ============================================
echo   Install the Chrome extension
echo ============================================
echo   1. Open chrome://extensions in Chrome
echo   2. Turn on "Developer mode" (top right)
echo   3. Click "Load unpacked" and select:
echo        %~dp0ai-web-auto-extension
echo   4. The extension auto-connects to ws://localhost:8765
echo      with token local-dev-token once the server is running
echo.
echo   Optional: set ANTHROPIC_API_KEY to use AI "task" runs
echo.
echo ============================================
echo   Install complete!
echo   Run: runner.bat to start the server
echo ============================================
pause
