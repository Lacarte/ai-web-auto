@echo off
REM AI Web-Auto - build-deals check. Windows Task Scheduler task "AI Web-Auto build deals" runs this at 06:00, 12:00
REM and 18:00 (every 6 hours, never midnight-6 AM). Double-click to run it now (or "Run now" in the deal monitor).
REM   1) one price-watch cycle: eBay (Buy It Now + auctions), Amazon, Best Buy, PC Server and Parts
REM      -> dashboard with the opportunity banner (offers at or under each deal price get a check mark + chime)
REM   2) config_check: selectable eBay listings priced with the 64GB options SELECTED on the listing page
REM   3) Claude (read/write/search files only - no shell, no web) analyzes and writes the HTML report, which then opens
REM Read-only: nothing is bought, bid on, carted or messaged. The deal monitor page reads the STEP lines of the log.
REM Needs: Chrome open with AI Web-Auto, and the Chrome window not hidden (eBay drops typing in a hidden tab).
setlocal
cd /d "%~dp0..\.."
set PYTHONIOENCODING=utf-8
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd-HHmm"') do set TS=%%i
set D=@modules\price_watch\@downloads
set W=%D%\watch-build-deals
set OUTDIR=%D%\build-deals-reports
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
set LOG=%OUTDIR%\run-%TS%.log
set REPORT=%OUTDIR%\report-%TS%.html
echo [%date% %time%] build-deals run %TS% > "%LOG%"

echo STEP 1/3 price watch: eBay, Amazon, Best Buy, PC Server and Parts >> "%LOG%"
venv\Scripts\python @modules\price_watch\price_watch.py @modules\price_watch\watches\build-deals.json --once >> "%LOG%" 2>&1
echo price_watch exit code %errorlevel% >> "%LOG%"
echo STEP 2/3 config check: selectable eBay listings priced with the 64GB options selected >> "%LOG%"
venv\Scripts\python @modules\price_watch\config_check.py @modules\price_watch\watches\build-deals.json >> "%LOG%" 2>&1
echo config_check exit code %errorlevel% >> "%LOG%"
if exist "%W%\compare.json" copy /y "%W%\compare.json" "%OUTDIR%\compare-%TS%.json" > nul

echo STEP 3/3 Claude analysis and report >> "%LOG%"
(type @modules\price_watch\build-deals-analysis.md & echo. & echo REPORT_PATH=%REPORT% & echo RUN_LOG=%LOG%) | call "%APPDATA%\npm\claude.cmd" -p --permission-mode dontAsk --allowedTools "Read Write Glob Grep" >> "%LOG%" 2>&1
echo claude exit code %errorlevel% >> "%LOG%"

if exist "%REPORT%" (start "" "%REPORT%") else (start "" "%W%\dashboard.html")
echo DONE %date% %time% >> "%LOG%"
endlocal
