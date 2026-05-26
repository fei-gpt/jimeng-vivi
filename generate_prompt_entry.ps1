param(
  [string]$Count,
  [string]$Duration,
  [string]$Brief,
  [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not $Count) {
  $Count = Read-Host "Script count, default 1"
}
if ([string]::IsNullOrWhiteSpace($Count)) { $Count = "1" }
while ($Count -notmatch '^\d+$') {
  Write-Host "Please enter a number, for example: 1"
  $Count = Read-Host "Script count, default 1"
  if ([string]::IsNullOrWhiteSpace($Count)) { $Count = "1" }
}

if (-not $Duration) {
  $Duration = Read-Host "Duration seconds 4-15, default 15"
}
if ([string]::IsNullOrWhiteSpace($Duration)) { $Duration = "15" }
while ($Duration -notmatch '^\d+$') {
  Write-Host "Please enter a number from 4 to 15, for example: 15"
  $Duration = Read-Host "Duration seconds 4-15, default 15"
  if ([string]::IsNullOrWhiteSpace($Duration)) { $Duration = "15" }
}

if (-not $Brief) {
  $Brief = Read-Host "Generation brief, press Enter for default"
}
if ($null -eq $Brief) { $Brief = "" }

$distro = "Ubuntu-22.04"
if (Test-Path ".env") {
  $line = Get-Content ".env" | Where-Object { $_ -match "^WSL_DISTRO=" } | Select-Object -First 1
  if ($line) { $distro = ($line -replace "^WSL_DISTRO=", "").Trim() }
}

$localRequests = Join-Path $Root "script_requests"
New-Item -ItemType Directory -Force -Path $localRequests | Out-Null
$payloadName = "local-entry-{0}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss")
$payloadPath = Join-Path $localRequests $payloadName
$payload = @{
  count = $Count
  duration = $Duration
  brief = $Brief
  source = "local_text_entry"
} | ConvertTo-Json -Compress
Set-Content -LiteralPath $payloadPath -Value $payload -Encoding UTF8

$wslPayload = "/mnt/c/Users/aaa/Documents/okivivi/script_requests/$payloadName"
$bash = 'cd ~/okivivi; ' +
  'cp /mnt/c/Users/aaa/Documents/okivivi/.env ~/okivivi/.env; ' +
  'cp /mnt/c/Users/aaa/Documents/okivivi/worker/create_task.py ~/okivivi/worker/create_task.py; ' +
  'cp /mnt/c/Users/aaa/Documents/okivivi/worker/generate_scripts.py ~/okivivi/worker/generate_scripts.py; ' +
  'cp /mnt/c/Users/aaa/Documents/okivivi/worker/submit_script_request.py ~/okivivi/worker/submit_script_request.py; ' +
  '. .venv/bin/activate; ' +
  'python3 worker/submit_script_request.py --payload-file ' + "'" + $wslPayload + "'"

Write-Host ""
Write-Host "Submitting script generation request..."
Write-Host "Count: $Count"
Write-Host "Duration: $Duration"
Write-Host "Brief: $(if ($Brief) { $Brief } else { 'default' })"
Write-Host ""

& "$env:WINDIR\System32\wsl.exe" -d $distro -- bash -lc $bash
$code = $LASTEXITCODE
if ($null -eq $code) { $code = 1 }

Write-Host ""
if ($code -eq 0) {
  Write-Host "Submitted. The worker will write scripts to Feishu and send review cards."
} else {
  Write-Host "Submit failed. Exit code: $code"
}
if (-not $NoPause) {
  Write-Host "Press any key to close..."
  $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
exit $code
