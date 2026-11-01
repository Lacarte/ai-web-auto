@echo off
title SKOOL-DOWN-COURSE Backend
cd /d "%~dp0..\..\..\"

:: Try local backend venv first, fall back to project root venv
if exist "created-extensions\skool-down-course\skool-down-course-backend\venv\Scripts\activate.bat" (
    call created-extensions\skool-down-course\skool-down-course-backend\venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [ERROR] No venv found. Run install.bat first.
    pause
    exit /b 1
)

echo ============================================
echo   Skool Down Course - Backend Server
echo ============================================
echo.
echo [INFO] Starting backend on http://localhost:9876
echo.

python created-extensions\skool-down-course\skool-down-course-backend\sdc_backend.py

echo.
echo [INFO] Backend stopped.
pause
