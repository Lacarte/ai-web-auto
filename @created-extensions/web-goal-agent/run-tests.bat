@echo off
title Web Goal Agent - Tests
cd /d "%~dp0"
set "PY=%~dp0..\..\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] ai-web-auto venv not found. Run install.bat in the project root first.
    exit /b 1
)

echo === Unit tests (engine, protocol/server, native host, specs, safety) ===
"%PY%" -m unittest discover -s tests\unit -t tests\unit
if errorlevel 1 set FAILED=1

echo.
echo === End-to-end tests (real Chromium with the extension loaded) ===
pushd tests\e2e
if not exist node_modules (
    call npm install --no-audit --no-fund || (popd & exit /b 1)
    call npx playwright install chromium || (popd & exit /b 1)
)
node run-e2e.mjs
if errorlevel 1 set FAILED=1
popd

if defined FAILED (
    echo.
    echo [FAILED] See test-results\e2e-results.json and the output above.
    exit /b 1
)
echo.
echo [OK] All tests passed. Evidence: test-results\
