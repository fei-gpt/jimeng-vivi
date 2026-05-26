@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage:
  echo   generate_scripts.bat COUNT [DURATION] [BRIEF]
  echo Example:
  echo   generate_scripts.bat 5 15 "宿舍和通勤场景多一些"
  exit /b 1
)

where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] wsl.exe not found. Please install/enable WSL first.
  exit /b 1
)

set "WSL_DISTRO=Ubuntu-22.04"
for /f "tokens=1,* delims==" %%A in ('findstr /b /c:"WSL_DISTRO=" ".env" 2^>nul') do set "WSL_DISTRO=%%B"
if "%WSL_DISTRO%"=="" set "WSL_DISTRO=Ubuntu-22.04"

set "COUNT=%~1"
set "DURATION=%~2"
if "%DURATION%"=="" set "DURATION=15"
set "BRIEF=%~3"

echo Generating scripts and creating tasks in WSL distro: %WSL_DISTRO%
wsl.exe -d "%WSL_DISTRO%" -- bash -lc "cd ~/okivivi && cp /mnt/c/Users/aaa/Documents/okivivi/worker/create_task.py ~/okivivi/worker/create_task.py && cp /mnt/c/Users/aaa/Documents/okivivi/worker/generate_scripts.py ~/okivivi/worker/generate_scripts.py && cp /mnt/c/Users/aaa/Documents/okivivi/.env ~/okivivi/.env && python3 worker/generate_scripts.py --count '%COUNT%' --duration '%DURATION%' --brief '%BRIEF%'"
if errorlevel 1 (
  echo wsl.exe failed; trying ubuntu2204.exe fallback...
  ubuntu2204.exe run bash -lc "cd ~/okivivi && cp /mnt/c/Users/aaa/Documents/okivivi/worker/create_task.py ~/okivivi/worker/create_task.py && cp /mnt/c/Users/aaa/Documents/okivivi/worker/generate_scripts.py ~/okivivi/worker/generate_scripts.py && cp /mnt/c/Users/aaa/Documents/okivivi/.env ~/okivivi/.env && python3 worker/generate_scripts.py --count '%COUNT%' --duration '%DURATION%' --brief '%BRIEF%'"
)
exit /b %ERRORLEVEL%
