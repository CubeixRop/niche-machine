@echo off
setlocal
call "%~dp0scripts\nm.cmd" %*
exit /b %errorlevel%
