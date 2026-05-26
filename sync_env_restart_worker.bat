@echo off
chcp 65001 >nul
cd /d C:\Users\aaa\Documents\okivivi
echo Syncing .env and restarting Okivivi Feishu worker...
echo.
set SCRIPT=/mnt/c/Users/aaa/Documents/okivivi/sync_env_restart_worker.sh

echo [try] default WSL distro
C:\Windows\System32\wsl.exe -- bash %SCRIPT%
if not errorlevel 1 goto done

echo.
echo [try] Ubuntu-22.04 distro name
C:\Windows\System32\wsl.exe -d Ubuntu-22.04 -- bash %SCRIPT%
if not errorlevel 1 goto done

echo.
echo [try] Ubuntu distro name
C:\Windows\System32\wsl.exe -d Ubuntu -- bash %SCRIPT%
if not errorlevel 1 goto done

echo.
echo [try] ubuntu2204.exe launcher
ubuntu2204.exe run bash %SCRIPT%
if not errorlevel 1 goto done

echo.
echo All automatic Ubuntu launch methods failed.

:done
echo.
echo Done. This window will stay open so you can see errors.
pause
