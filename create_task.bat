@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage:
  echo   create_task.bat PROMPT_TXT [DURATION]
  echo   create_task.bat PROMPT_TXT IMAGE1 IMAGE2 [DURATION]
  echo Optional account can be set with DEFAULT_JIMENG_ACCOUNT in .env.
  echo Example:
  echo   create_task.bat "prompts\vivi_prompt.txt" 15
  echo   create_task.bat "prompts\sunny_prompt.txt" "C:\path\image1.png" "C:\path\image2.png" 15
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

set "PROMPT_WIN=%~f1"
set "IMAGE1_WIN="
set "IMAGE2_WIN="
set "DURATION=15"

if not "%~2"=="" (
  if exist "%~2" (
    set "IMAGE1_WIN=%~f2"
    if not "%~3"=="" set "IMAGE2_WIN=%~f3"
    if not "%~4"=="" set "DURATION=%~4"
  ) else (
    set "DURATION=%~2"
  )
)

call :to_wsl "%PROMPT_WIN%" PROMPT_WSL

set "IMAGE_ARGS="
if not "%IMAGE1_WIN%"=="" (
  call :to_wsl "%IMAGE1_WIN%" IMAGE1_WSL
  set "IMAGE_ARGS=--image '%IMAGE1_WSL%'"
)
if not "%IMAGE2_WIN%"=="" (
  call :to_wsl "%IMAGE2_WIN%" IMAGE2_WSL
  set "IMAGE_ARGS=%IMAGE_ARGS% --image '%IMAGE2_WSL%'"
)

echo Creating task in WSL distro: %WSL_DISTRO%
wsl.exe -d "%WSL_DISTRO%" -- bash -lc "cd ~/okivivi && cp /mnt/c/Users/aaa/Documents/okivivi/worker/create_task.py ~/okivivi/worker/create_task.py && cp /mnt/c/Users/aaa/Documents/okivivi/.env ~/okivivi/.env && python3 worker/create_task.py --prompt '%PROMPT_WSL%' %IMAGE_ARGS% --duration '%DURATION%'"
if errorlevel 1 (
  echo wsl.exe failed; trying ubuntu2204.exe fallback...
  ubuntu2204.exe run bash -lc "cd ~/okivivi && cp /mnt/c/Users/aaa/Documents/okivivi/worker/create_task.py ~/okivivi/worker/create_task.py && cp /mnt/c/Users/aaa/Documents/okivivi/.env ~/okivivi/.env && python3 worker/create_task.py --prompt '%PROMPT_WSL%' %IMAGE_ARGS% --duration '%DURATION%'"
)
exit /b %ERRORLEVEL%

:to_wsl
set "p=%~1"
set "drive=%p:~0,1%"
set "rest=%p:~2%"
set "rest=%rest:\=/%"
set "%~2=/mnt/%drive%/%rest%"
exit /b 0
