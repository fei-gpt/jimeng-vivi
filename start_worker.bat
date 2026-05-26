@echo off
setlocal
cd /d "%~dp0"

echo Starting Okivivi workflow...
echo This will sync Windows config/files into Ubuntu and restart the Feishu worker.
echo.

call "%~dp0sync_env_restart_worker.bat"

endlocal
