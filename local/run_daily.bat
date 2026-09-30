@echo off
REM ---------------------------------------------------------------
REM Local daily runner for wens-tonghu-auto (Windows Task Scheduler).
REM
REM Why local: GitHub-hosted runners sit outside mainland China and
REM cannot even open a TCP connection to cq.wens.com.cn
REM (DNS resolves fine, TCP connect times out at 45s).
REM So THIS machine has to be the executor.
REM
REM Keep this file ASCII-only: non-ASCII bytes in .bat get garbled
REM under some console code pages.
REM ---------------------------------------------------------------

setlocal
cd /d "%~dp0.."

REM Force UTF-8 end to end. Without this the redirected log comes out
REM as mojibake: Python writes UTF-8 while the console default is GBK.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM ---- run settings ----
set FAST_MODE=1
set FAST_METHOD=seek
set HEADLESS=1
set MAX_MINUTES=180

REM Credentials: no env var needed locally.
REM src/settings.py falls back to login_state/storage_state.json,
REM which local/export_login.py produces.

set "PYEXE=C:\Users\mrzhang\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo [ERROR] python not found: %PYEXE%
  exit /b 1
)

if not exist "local\logs" mkdir "local\logs"

echo [start] %date% %time% >> "local\logs\run.log"
"%PYEXE%" -u -m src.main >> "local\logs\run.log" 2>&1
set RC=%ERRORLEVEL%
echo [end]   %date% %time% exit=%RC% >> "local\logs\run.log"

exit /b %RC%
