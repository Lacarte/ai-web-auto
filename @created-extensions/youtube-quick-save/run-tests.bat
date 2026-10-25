@echo off
rem Unit tests + browser tests against the local fake YouTube. Add --mutations for the planted-bug check.
setlocal
cd /d "%~dp0"

echo == Unit tests ==
node --test "tests/unit/*.test.mjs"
if errorlevel 1 goto :fail

echo == Browser tests (Playwright Chromium, local fake YouTube) ==
if not exist "tests\e2e\node_modules" (
  pushd tests\e2e
  call npm install --no-audit --no-fund
  popd
)
node tests\e2e\run-e2e.mjs
if errorlevel 1 goto :fail

if /i "%~1"=="--mutations" (
  echo == Planted-bug check ==
  node tests\mutation\run-mutations.mjs
  if errorlevel 1 goto :fail
)

echo All tests passed.
exit /b 0

:fail
echo TESTS FAILED
exit /b 1
