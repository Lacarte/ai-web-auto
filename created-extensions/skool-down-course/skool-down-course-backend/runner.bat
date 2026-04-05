@echo off
title SKOOL-DOWN-COURSE Backend
cd /d "%~dp0..\..\..\"

call venv\Scripts\activate.bat

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
