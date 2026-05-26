$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

if (!(Test-Path -LiteralPath "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $PSScriptRoot "logs\start_worker_ubuntu_$stamp`_$PID.log"
$latestPath = Join-Path $PSScriptRoot "logs\start_worker_ubuntu_latest.txt"
"$logPath" | Set-Content -LiteralPath $latestPath -Encoding UTF8
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] start_worker_ubuntu.ps1 launched" | Set-Content -LiteralPath $logPath -Encoding UTF8

function Write-Both {
    param([string]$Message)
    Write-Host $Message
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value $Message
}

Write-Both "Starting Ubuntu worker..."
Write-Both "This window must stay open while the Feishu worker is running."
Write-Both "Realtime output is also saved to $logPath"
Write-Both ""

$ubuntuCommand = "bash /mnt/c/Users/aaa/Documents/okivivi/start_worker_ubuntu.sh"

$candidates = @(
    @{ Name = "ubuntu2204.exe"; Args = @("run", "bash", "-lc", $ubuntuCommand) },
    @{ Name = "$env:LOCALAPPDATA\Microsoft\WindowsApps\ubuntu2204.exe"; Args = @("run", "bash", "-lc", $ubuntuCommand) },
    @{ Name = "wsl.exe"; Args = @("-d", "Ubuntu-22.04", "--", "bash", "-lc", $ubuntuCommand) },
    @{ Name = "wsl.exe"; Args = @("-d", "Ubuntu", "--", "bash", "-lc", $ubuntuCommand) }
)

foreach ($candidate in $candidates) {
    Write-Both "Trying launcher: $($candidate.Name) $($candidate.Args[0]) ..."
    try {
        & $candidate.Name @($candidate.Args) 2>&1 | Tee-Object -FilePath $logPath -Append
        $exitCode = $LASTEXITCODE
        Write-Both "Launcher exited with code $exitCode"
        if ($exitCode -eq 0 -or $null -eq $exitCode) {
            exit 0
        }
    } catch {
        Write-Both "Launcher failed: $($_.Exception.Message)"
    }
}

Write-Both "All Ubuntu launchers failed. Open Ubuntu manually and run:"
Write-Both "cd ~/okivivi && . .venv/bin/activate && python3 -u worker/feishu_worker.py"
exit 1
