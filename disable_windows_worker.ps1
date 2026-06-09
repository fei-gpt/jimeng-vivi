$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "OkiviviFeishuWorker"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$logDir = Join-Path $root "logs"

if (!(Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "disable_windows_worker_$stamp.log"

function Write-Log {
    param([string]$Message)
    Write-Host $Message
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value $Message
}

Write-Log "Disabling Okivivi Windows/WSL Feishu worker..."

try {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Log "Scheduled task removed: $taskName"
} catch {
    Write-Log "Scheduled task cleanup skipped: $($_.Exception.Message)"
}

try {
    Remove-ItemProperty -Path $runKey -Name $taskName -ErrorAction SilentlyContinue
    Write-Log "HKCU Run entry removed: $taskName"
} catch {
    Write-Log "HKCU Run cleanup skipped: $($_.Exception.Message)"
}

$stopCommand = @'
ps -ef | grep '[f]eishu_worker.py' | awk '{print $2}' | xargs -r kill || true
pkill -f worker/feishu_worker.py || true
mkdir -p "$HOME/okivivi"
if [ -f "$HOME/okivivi/.env" ]; then
  grep -v '^ACTIVE_WORKER_ID=' "$HOME/okivivi/.env" > "$HOME/okivivi/.env.tmp" || true
  printf '\nACTIVE_WORKER_ID=macdemacbook-pro-8bed8dfe\n' >> "$HOME/okivivi/.env.tmp"
  mv "$HOME/okivivi/.env.tmp" "$HOME/okivivi/.env"
fi
echo "Windows WSL Feishu worker stopped."
'@

$launchers = @(
    @{ Name = "ubuntu2204.exe"; Args = @("run", "bash", "-lc", $stopCommand) },
    @{ Name = "$env:LOCALAPPDATA\Microsoft\WindowsApps\ubuntu2204.exe"; Args = @("run", "bash", "-lc", $stopCommand) },
    @{ Name = "wsl.exe"; Args = @("-d", "Ubuntu-22.04", "--", "bash", "-lc", $stopCommand) },
    @{ Name = "wsl.exe"; Args = @("-d", "Ubuntu", "--", "bash", "-lc", $stopCommand) },
    @{ Name = "wsl.exe"; Args = @("--", "bash", "-lc", $stopCommand) }
)

foreach ($launcher in $launchers) {
    try {
        Write-Log "Trying WSL launcher: $($launcher.Name) $($launcher.Args -join ' ')"
        & $launcher.Name @($launcher.Args) 2>&1 | Tee-Object -FilePath $logPath -Append
        if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
            Write-Log "WSL worker stopped by launcher: $($launcher.Name)"
            break
        }
    } catch {
        Write-Log "Launcher failed: $($_.Exception.Message)"
    }
}

Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -match "python|ubuntu|wsl" -and $_.Path -match "okivivi|Ubuntu|WindowsApps" } |
    Format-Table Id, ProcessName, Path -AutoSize |
    Out-String |
    Tee-Object -FilePath $logPath -Append

Write-Log "Done. Keep Windows worker disabled; Mac worker should be the only active Feishu long-connection worker."
Write-Log "Log: $logPath"
