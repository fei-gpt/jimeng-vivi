@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_worker_ubuntu.ps1"

echo.
echo Press any key to close...
pause >nul
endlocal
