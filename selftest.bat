@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "runtime\python313\python\python.exe" set "PY=runtime\python313\python\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"

echo === unit tests ===
"%PY%" -m unittest discover -s tests -v
echo.
echo === offline simulation ===
"%PY%" main.py --simulate --wav sim_out.wav
echo.
echo Output audio: sim_out.wav
pause
