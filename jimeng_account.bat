@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage:
  echo   jimeng_account.bat list
  echo   jimeng_account.bat current
  echo   jimeng_account.bat save ACCOUNT_NAME
  echo   jimeng_account.bat use ACCOUNT_NAME
  echo   jimeng_account.bat clear
  echo   jimeng_account.bat checklogin [DEVICE_CODE]
  exit /b 1
)

set "WSL_DISTRO=Ubuntu-22.04"
for /f "tokens=1,* delims==" %%A in ('findstr /b /c:"WSL_DISTRO=" ".env" 2^>nul') do set "WSL_DISTRO=%%B"
if "%WSL_DISTRO%"=="" set "WSL_DISTRO=Ubuntu-22.04"

wsl.exe -d "%WSL_DISTRO%" -- bash -lc "cd ~/okivivi && cp /mnt/c/Users/aaa/Documents/okivivi/jimeng_account.sh ~/okivivi/jimeng_account.sh && chmod +x ~/okivivi/jimeng_account.sh && ~/okivivi/jimeng_account.sh %*"
if not "%ERRORLEVEL%"=="0" (
  echo wsl.exe failed; trying ubuntu2204.exe fallback...
  ubuntu2204.exe run bash -lc "cd ~/okivivi && cp /mnt/c/Users/aaa/Documents/okivivi/jimeng_account.sh ~/okivivi/jimeng_account.sh && chmod +x ~/okivivi/jimeng_account.sh && ~/okivivi/jimeng_account.sh %*"
)
exit /b %ERRORLEVEL%
