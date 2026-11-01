@echo off
rem Viral research: run.bat <niche> [langs]      e.g.  run.bat ai-music en,es
rem The niche needs niches\<niche>.json. Everything is cached: run it again to continue an interrupted run.
setlocal
set "HERE=%~dp0"
set "PY=%HERE%..\..\venv\Scripts\python.exe"
if "%~1"=="" (
  echo usage: run.bat ^<niche^> [langs]
  echo available niches:
  for %%f in ("%HERE%niches\*.json") do echo   %%~nf
  exit /b 2
)
if not exist "%HERE%niches\%~1.json" (
  echo No niche file: niches\%~1.json  - create it first ^(see INSTRUCTIONS.md^).
  exit /b 2
)
set "LANGS=%~2"
if "%LANGS%"=="" set "LANGS=en,es"
"%PY%" -u "%HERE%viral_research.py" run --niche %~1 --langs %LANGS%
exit /b %errorlevel%
