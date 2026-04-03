@echo off
title Flow Learner
echo ============================================
echo   Self-Learning Flow Automation Builder
echo ============================================
echo.
echo Loop: screenshot - extract HTML - identify elements
echo       highlight - interact - verify - save good step
echo.

if not exist "venv" (
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo.
if "%~1"=="" (
    python learner.py
) else (
    python learner.py --prompt "%*"
)

echo.
pause
