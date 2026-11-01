@echo off
title ImageFX Automation
echo ============================================
echo   ImageFX Image Generator Automation
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
echo USAGE:
echo   Discover mode (first run):  --discover
echo   Generate image:             --prompt "your prompt here"
echo   Multiple images:            --prompt "..." --iterations 3
echo.

if "%~1"=="" (
    echo [MODE] Discover + Generate with demo prompt
    echo [INFO] Make sure Chrome extension is loaded and ImageFX is open.
    echo.
    python -m ai_web_auto_backend.tasks.imagefx_auto --prompt "A cyberpunk city at sunset with neon lights reflecting on wet streets"
) else (
    python -m ai_web_auto_backend.tasks.imagefx_auto %*
)

echo.
pause
