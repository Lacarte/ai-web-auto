@echo off
title ElevenLabs Downloader and Workspace Cleaner
cd /d "%~dp0..\.."

echo ====================================================
echo   ElevenLabs Downloader and Workspace Cleaner
echo ====================================================
echo.
echo Checking virtual environment...
if not exist "venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found. Run install.bat first.
    pause
    exit /b 1
)

echo Starting ElevenLabs Automation Workflow...
venv\Scripts\python -m pip show httpx >nul 2>&1
if errorlevel 1 (
    echo Installing httpx dependency...
    venv\Scripts\python -m pip install httpx
)

echo Running ElevenLabs Downloader with Force Delete...
venv\Scripts\python "@modules\elevenLabs\elevenlabs_downloader.py" --force-delete %*

echo.
echo Process finished.
pause
