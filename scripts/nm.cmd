@echo off
setlocal
set PYTHONUTF8=1
set "DIR=%~dp0"
set "NMHOME=%NICHE_HOME%"
if not defined NMHOME set "NMHOME=%USERPROFILE%\.niche-machine"
set "PY=%NMHOME%\venv\Scripts\python.exe"
if not exist "%PY%" goto bootstrap
set PYTHONDONTWRITEBYTECODE=1
"%PY%" "%DIR%nm.py" %*
exit /b %errorlevel%
:bootstrap
if /I "%~1"=="setup" goto setup
if /I "%~1"=="doctor" goto setup
echo Niche Machine is not set up. Run: nm.cmd setup 1>&2
exit /b 1
:setup
py -3.12 "%DIR%nm.py" %*
exit /b %errorlevel%


