@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where wsl.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] wsl.exe not found.
  exit /b 1
)

set "WSL_DISTRO=Ubuntu-22.04"
for /f "tokens=1,* delims==" %%A in ('findstr /b /c:"WSL_DISTRO=" ".env" 2^>nul') do set "WSL_DISTRO=%%B"
if "%WSL_DISTRO%"=="" set "WSL_DISTRO=Ubuntu-22.04"

echo Uploading Ubuntu image library to remote server...
wsl.exe -d "%WSL_DISTRO%" -- bash -lc "cd ~/okivivi && cp /mnt/c/Users/aaa/Documents/okivivi/upload_images_to_server.sh ~/okivivi/upload_images_to_server.sh && cp /mnt/c/Users/aaa/Documents/okivivi/.env ~/okivivi/.env && bash ~/okivivi/upload_images_to_server.sh"
if not "%ERRORLEVEL%"=="0" (
  echo wsl.exe failed; trying ubuntu2204.exe fallback...
  ubuntu2204.exe run bash -lc "cd ~/okivivi && cp /mnt/c/Users/aaa/Documents/okivivi/upload_images_to_server.sh ~/okivivi/upload_images_to_server.sh && cp /mnt/c/Users/aaa/Documents/okivivi/.env ~/okivivi/.env && bash ~/okivivi/upload_images_to_server.sh"
)
exit /b %ERRORLEVEL%
