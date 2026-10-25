@echo off
rem Troubleshooting launcher: keeps the console open, shows logs and environment check.
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "runtime\python313\python\python.exe" set "PY=runtime\python313\python\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"

if /I "%PY%"=="python" (
  echo [WARN] No runtime\python313 and no .venv found. Falling back to system Python.
  echo        If the window fails to open, run:  install.ps1 -Portable
  echo.
)

"%PY%" main.py %*
if errorlevel 1 goto failed
goto end

:failed
echo.
echo ============================================================
echo  VoxCall failed to start. Environment check below.
echo  Please screenshot this window and send it back.
echo ============================================================
echo.
"%PY%" main.py --doctor
echo.
echo Crash log (if any): logs\error.log
echo.
pause
exit /b 1

:end
