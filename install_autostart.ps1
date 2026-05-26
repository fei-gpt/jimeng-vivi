$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "OkiviviFeishuWorker"
$scriptPath = Join-Path $root "start_worker_ubuntu.ps1"
$logDir = Join-Path $root "logs"

if (!(Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

if (!(Test-Path -LiteralPath $scriptPath)) {
    throw "Missing startup script: $scriptPath"
}

$runCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""

try {
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`"" `
        -WorkingDirectory $root

    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Start Okivivi Feishu long-connection worker after Windows user logon." `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $taskName
    Write-Host "OK: scheduled task registered and started: $taskName"
    Write-Host "Check with: Get-ScheduledTask -TaskName $taskName"
} catch {
    Write-Host "WARN: scheduled task failed, falling back to HKCU Run autostart."
    Write-Host "Reason: $($_.Exception.Message)"
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    New-Item -Path $runKey -Force | Out-Null
    Set-ItemProperty -Path $runKey -Name $taskName -Value $runCommand
    Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`"" -WorkingDirectory $root
    Write-Host "OK: HKCU Run autostart registered and worker started: $taskName"
    Write-Host "Check with: Get-ItemProperty -Path '$runKey' -Name '$taskName'"
}

Write-Host "Log: $(Join-Path $logDir 'start_worker_ubuntu.log')"
