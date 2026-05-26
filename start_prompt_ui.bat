@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if not exist logs mkdir logs

echo Starting OKIVIVI prompt UI...
echo URL: http://127.0.0.1:8765
echo Log: %CD%\logs\prompt_ui.log
echo.

C:\Windows\System32\wsl.exe -d Ubuntu-22.04 -- bash -lc "bash /mnt/c/Users/aaa/Documents/okivivi/start_prompt_ui_daemon.sh" > logs\prompt_ui_start.log 2>&1
if errorlevel 1 (
  echo Ubuntu-22.04 launch failed, trying default WSL distro... >> logs\prompt_ui_start.log
  C:\Windows\System32\wsl.exe -- bash -lc "bash /mnt/c/Users/aaa/Documents/okivivi/start_prompt_ui_daemon.sh" >> logs\prompt_ui_start.log 2>&1
)

start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process 'http://127.0.0.1:8765'"

echo.
echo Prompt UI started. See logs\prompt_ui_start.log and logs\prompt_ui.log for details.
pause
