@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_autostart.ps1"

echo.
echo Press any key to close...
pause >nul
endlocal
