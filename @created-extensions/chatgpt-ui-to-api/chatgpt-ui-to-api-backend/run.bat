@echo off
echo ========================================
echo  ChatGPT API Bridge - Server
echo  REST API: http://localhost:8767
echo  WebSocket: ws://localhost:8766
echo ========================================
echo.

cd /d "%~dp0"
call venv\Scripts\activate.bat
python api_server.py
