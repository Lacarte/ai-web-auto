@echo off
title ElevenLabs Downloader - SIMULATION MODE (No Delete)
cd /d "%~dp0..\.."

echo ====================================================
echo   ElevenLabs Downloader - SIMULATION MODE
echo   Status: DRY RUN ONLY (Will NOT delete anything)
echo ====================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)

venv\Scripts\python "@modules\elevenLabs\elevenlabs_downloader.py" --dry-run %*

echo.
echo [SIMULATION FINISHED] No files were removed from the website.
pause
