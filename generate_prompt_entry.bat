@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not exist logs mkdir logs

echo Starting OKIVIVI text prompt entry...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0generate_prompt_entry.ps1"
set "CODE=%ERRORLEVEL%"

echo Exit code: %CODE%
echo Press any key to close...
pause >nul
exit /b %CODE%
