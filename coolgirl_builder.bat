@echo off
chcp 65001 >nul
setlocal

set "PYTHON=python\python.exe"
set "ROOT=%~dp0"

if not exist "%ROOT%main.py" (
    echo [ERROR] main.py not found in %ROOT%
    pause
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%ROOT%;%PYTHONPATH%"

cd /d "%ROOT%"
"%PYTHON%" "%ROOT%main.py"
pause