@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage:
  echo   set_jimeng_default_account.bat ACCOUNT_NAME
  echo   set_jimeng_default_account.bat current
  exit /b 1
)

if /i "%~1"=="current" (
  findstr /b /c:"DEFAULT_JIMENG_ACCOUNT=" ".env"
  exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$envPath = Join-Path (Get-Location) '.env'; $name = '%~1'; $text = Get-Content -LiteralPath $envPath -Raw; if ($text -match '(?m)^DEFAULT_JIMENG_ACCOUNT=') { $text = $text -replace '(?m)^DEFAULT_JIMENG_ACCOUNT=.*$', ('DEFAULT_JIMENG_ACCOUNT=' + $name) } else { $text = $text.TrimEnd() + \"`r`nDEFAULT_JIMENG_ACCOUNT=$name`r`n\" }; Set-Content -LiteralPath $envPath -Value $text -Encoding UTF8; Write-Output ('DEFAULT_JIMENG_ACCOUNT=' + $name)"
exit /b %ERRORLEVEL%
