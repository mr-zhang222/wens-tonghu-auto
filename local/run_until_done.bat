@echo off
REM ---------------------------------------------------------------
REM Run rounds back-to-back until the platform has no pending
REM courses left (or a safety cap is hit).
REM
REM Round ends with "budget exhausted" markers  -> start next round.
REM Round ends with everything done / a failure -> stop the loop.
REM
REM Kill switches (any one of these ends the loop):
REM   * create an empty file  local\STOP
REM   * 6 rounds max (6 x 100 min = 10 h)
REM   * Task Scheduler ExecutionTimeLimit = 12 h
REM
REM Keep this file ASCII-only: non-ASCII bytes in .bat get garbled
REM under some console code pages. Must stay CRLF.
REM ---------------------------------------------------------------

setlocal
cd /d "%~dp0.."

chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set FAST_MODE=1
set FAST_METHOD=seek
set HEADLESS=1
set MAX_MINUTES=100

set "PYEXE=C:\Users\mrzhang\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo [ERROR] python not found: %PYEXE%
  exit /b 1
)
if not exist "local\logs" mkdir "local\logs"

set ROUND=0

:loop
set /a ROUND+=1
if %ROUND% GTR 6 goto :maxrounds
if exist "local\STOP" goto :stopped

echo [round %ROUND%] start %date% %time% >> "local\logs\run.log"
"%PYEXE%" -u -m src.main > "local\logs\round.log" 2>&1
set RC=%ERRORLEVEL%
echo [round %ROUND%] end   %date% %time% exit=%RC% >> "local\logs\run.log"
type "local\logs\round.log" >> "local\logs\run.log"

if not "%RC%"=="0" goto :fatal

"%PYEXE%" local\check_more.py "local\logs\round.log" | findstr /C:"MORE" >nul
if errorlevel 1 goto :alldone

echo [round %ROUND%] budget exhausted with courses pending, next round >> "local\logs\run.log"
goto :loop

:alldone
echo [all-done] %date% %time% no pending courses left >> "local\logs\run.log"
exit /b 0

:stopped
echo [stopped] %date% %time% local\STOP marker found >> "local\logs\run.log"
exit /b 0

:fatal
echo [fatal] %date% %time% round %ROUND% exited with code %RC%, aborting loop >> "local\logs\run.log"
exit /b %RC%

:maxrounds
echo [max-rounds] %date% %time% reached 6 rounds cap >> "local\logs\run.log"
exit /b 0
