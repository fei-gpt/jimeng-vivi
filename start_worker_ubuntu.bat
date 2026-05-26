@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_worker_ubuntu.ps1"

echo.
echo Worker launcher exited with code %ERRORLEVEL%.
echo See logs\start_worker_ubuntu.log for details.
pause
endlocal
