@echo off
rem ---------------------------------------------------------------
rem  gd_restore.cmd  --  double-click wrapper for tools/gd_restore.py
rem  (ASCII only on purpose: non-ASCII in a .cmd gets mangled by the
rem   OEM code page and can break the whole script)
rem ---------------------------------------------------------------
chcp 65001 >nul
setlocal
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0gd_restore.py" %*
set RC=%ERRORLEVEL%
echo.
if "%~1"=="" pause
if not "%RC%"=="0" pause
exit /b %RC%
