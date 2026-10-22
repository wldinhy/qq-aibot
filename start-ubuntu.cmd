@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-ubuntu.ps1"
if errorlevel 1 (
    echo Startup failed. See the error above.
    pause
    exit /b 1
)
exit /b 0
