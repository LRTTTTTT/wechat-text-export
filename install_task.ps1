param([Parameter(Mandatory = $true)][string]$Program)

$ErrorActionPreference = 'Stop'
$taskName = 'WeChatTextExport'
$localRoot = Join-Path $env:LOCALAPPDATA 'WeChatTextExport'
$logPath = Join-Path $localRoot 'install.log'
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null

function Write-InstallEvent([string]$eventName) {
    Add-Content -LiteralPath $logPath -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $eventName) -Encoding UTF8
}

try {
    $programPath = (Resolve-Path -LiteralPath $Program).Path
    $expectedBase = (Resolve-Path -LiteralPath (Join-Path $localRoot 'versions')).Path
    if (-not $programPath.StartsWith($expectedBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Program is outside the application versions directory'
    }
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $oldAction = if ($existing) { $existing.Actions[0] } else { $null }

    # An existing logged-in user may need to select the data root during installation only.
    $rootProbe = Start-Process -FilePath $programPath -ArgumentList 'root-status' -Wait -PassThru -WindowStyle Hidden
    if ($rootProbe.ExitCode -ne 0 -and (Get-Process Weixin -ErrorAction SilentlyContinue)) {
        $repair = Start-Process -FilePath $programPath -ArgumentList 'repair' -Wait -PassThru -WindowStyle Hidden
        Write-InstallEvent ('data_root_repair_exit=' + $repair.ExitCode)
    }

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $programPath -Argument 'watch' -WorkingDirectory (Split-Path -Parent $programPath)
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
    $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

    $previousPid = $null
    $statusPath = Join-Path $localRoot 'status.json'
    if (Test-Path -LiteralPath $statusPath) {
        try { $previousPid = (Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json).pid } catch { }
    }
    if ($existing) { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue }
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    $startTime = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Start-ScheduledTask -TaskName $taskName
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path -LiteralPath $statusPath) {
            try {
                $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $version = Split-Path -Leaf (Split-Path -Parent $programPath)
                $watcher = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$status.pid) -ErrorAction SilentlyContinue
                if ($status.version -eq $version -and $status.started_unix -ge $startTime -and
                    $status.pid -ne $previousPid -and $watcher -and
                    $watcher.ExecutablePath -eq $programPath -and
                    (Get-ScheduledTask -TaskName $taskName).State -eq 'Running') {
                    $healthy = $true
                    break
                }
            } catch { }
        }
    }
    if (-not $healthy) { throw 'New watcher did not start' }
    Write-InstallEvent ('install_ok version=' + $version)
    exit 0
} catch {
    Write-InstallEvent ('install_failed error=' + $_.Exception.GetType().Name + ' detail=' + ($_.Exception.Message -replace '[\r\n]', ' '))
    if ($oldAction) {
        try {
            $restore = New-ScheduledTaskAction -Execute $oldAction.Execute -Argument $oldAction.Arguments -WorkingDirectory $oldAction.WorkingDirectory
            Set-ScheduledTask -TaskName $taskName -Action $restore | Out-Null
            Start-ScheduledTask -TaskName $taskName
            Write-InstallEvent 'rollback_ok'
        } catch { Write-InstallEvent 'rollback_failed' }
    }
    exit 1
}
