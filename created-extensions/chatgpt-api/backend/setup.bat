@echo off
echo ========================================
echo  ChatGPT API Bridge - Setup
echo ========================================
echo.

cd /d "%~dp0"

:: Create venv
if not exist "venv" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
) else (
    echo [1/3] Venv already exists, skipping...
)

:: Activate and install
echo [2/3] Installing dependencies...
call venv\Scripts\activate.bat
pip install fastapi uvicorn websockets --quiet

echo [3/3] Done!
echo.
echo ========================================
echo  Next steps:
echo   1. Load extension in chrome://extensions
echo      (Developer mode ON ^> Load unpacked ^> select this folder)
echo   2. Open https://chatgpt.com/ in Chrome
echo   3. Run: run.bat
echo ========================================
pause
