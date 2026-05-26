$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "Stopping old Feishu worker in Ubuntu..."
$stopCommand = "ps -ef | grep '[f]eishu_worker.py' | awk '{print `$2}' | xargs -r kill || true"

$launchers = @(
    @{ Name = "ubuntu2204.exe"; Args = @("run", "bash", "-lc", $stopCommand) },
    @{ Name = "$env:LOCALAPPDATA\Microsoft\WindowsApps\ubuntu2204.exe"; Args = @("run", "bash", "-lc", $stopCommand) },
    @{ Name = "wsl.exe"; Args = @("-d", "Ubuntu-22.04", "--", "bash", "-lc", $stopCommand) },
    @{ Name = "wsl.exe"; Args = @("-d", "Ubuntu", "--", "bash", "-lc", $stopCommand) }
)

foreach ($launcher in $launchers) {
    try {
        & $launcher.Name @($launcher.Args) | Out-Null
        if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) {
            break
        }
    } catch {
    }
}

Start-Sleep -Seconds 2
Write-Host "Starting updated Feishu worker..."
Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSScriptRoot\start_worker_ubuntu.ps1`"" -WorkingDirectory $PSScriptRoot
Write-Host "Done. Check logs\start_worker_ubuntu_latest.txt for the latest log path."
