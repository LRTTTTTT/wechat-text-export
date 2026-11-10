param(
    [Parameter(Mandatory = $true)][int]$OldPid,
    [Parameter(Mandatory = $true)][string]$NewExe,
    [Parameter(Mandatory = $true)][string]$NewVersion,
    [Parameter(Mandatory = $true)][string]$OldVersion,
    [Parameter(Mandatory = $true)][string]$LocalRoot
)

$ErrorActionPreference = 'Stop'
$taskName = 'WeChatTextExport'
$logPath = Join-Path $LocalRoot 'update.log'

function Write-UpdateEvent([string]$EventName) {
    Add-Content -LiteralPath $logPath -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $EventName) -Encoding UTF8
}

try {
    Wait-Process -Id $OldPid -Timeout 120 -ErrorAction SilentlyContinue
    if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) {
        throw 'Old watcher did not exit'
    }
    for ($i = 0; $i -lt 30; $i++) {
        if ((Get-ScheduledTask -TaskName $taskName).State -ne 'Running') { break }
        Start-Sleep -Seconds 1
    }
    $oldAction = (Get-ScheduledTask -TaskName $taskName).Actions[0]
    $newAction = New-ScheduledTaskAction -Execute $NewExe -Argument 'watch' -WorkingDirectory (Split-Path -Parent $NewExe)
    Set-ScheduledTask -TaskName $taskName -Action $newAction | Out-Null
    $startTime = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Start-ScheduledTask -TaskName $taskName
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        $statusPath = Join-Path $LocalRoot 'status.json'
        if (Test-Path -LiteralPath $statusPath) {
            try {
                $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $watcher = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$status.pid) -ErrorAction SilentlyContinue
                if ($status.version -eq $NewVersion -and $status.started_unix -ge $startTime -and
                    $watcher -and $watcher.ExecutablePath -eq $NewExe -and
                    (Get-ScheduledTask -TaskName $taskName).State -eq 'Running') {
                    $healthy = $true
                    break
                }
            } catch { }
        }
    }
    if (-not $healthy) {
        throw 'New watcher did not report healthy startup'
    }
    Write-UpdateEvent ('update_ok version=' + $NewVersion)
} catch {
    Write-UpdateEvent ('update_failed version=' + $NewVersion + ' error=' + $_.Exception.GetType().Name)
    try {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($oldAction) {
            $restore = New-ScheduledTaskAction -Execute $oldAction.Execute -Argument $oldAction.Arguments -WorkingDirectory $oldAction.WorkingDirectory
            Set-ScheduledTask -TaskName $taskName -Action $restore | Out-Null
            Start-ScheduledTask -TaskName $taskName
            Write-UpdateEvent ('rollback_ok version=' + $OldVersion)
        }
    } catch {
        Write-UpdateEvent 'rollback_failed'
    }
}
