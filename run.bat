@echo off
rem Launch VoxCall without leaving a console window behind.
rem With arguments (e.g. --doctor) it keeps the console so you can read the output.
chcp 65001 >nul
cd /d "%~dp0"

set "PYW="
if exist "runtime\python313\python\pythonw.exe" set "PYW=runtime\python313\python\pythonw.exe"
if not defined PYW if exist ".venv\Scripts\pythonw.exe" set "PYW=.venv\Scripts\pythonw.exe"

if not "%~1"=="" goto console
if not defined PYW goto nopython

start "" "%PYW%" "main.py"
exit /b 0

:console
set "PY="
if exist "runtime\python313\python\python.exe" set "PY=runtime\python313\python\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"
"%PY%" main.py %*
pause
exit /b %errorlevel%

:nopython
echo [ERROR] runtime\python313 and .venv are both missing.
echo         Run:  install.ps1 -Portable
echo.
pause
exit /b 1
