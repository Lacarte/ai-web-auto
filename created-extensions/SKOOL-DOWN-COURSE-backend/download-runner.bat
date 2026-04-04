@echo off
title SKOOL-DOWN-COURSE Downloader
cd /d "%~dp0..\.."

if "%~1"=="" (
    set URL=https://www.skool.com/ai-video-creators/classroom
) else (
    set URL=%~1
)

echo Downloading from: %URL%
echo Make sure Chrome is open with the AI Web-Auto extension loaded.
echo.

venv\Scripts\python created-extensions\SKOOL-DOWN-COURSE-backend\sdc_downloader.py "%URL%"
pause
