param([Parameter(Mandatory = $true)][string]$TargetExe)

$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'WeChatTextExport' -ErrorAction SilentlyContinue
if (-not $task) { exit 0 }
$oldExe = $task.Actions[0].Execute
$versions = Join-Path $env:LOCALAPPDATA 'WeChatTextExport\versions'
if (-not $oldExe.StartsWith($versions + '\', [StringComparison]::OrdinalIgnoreCase)) { exit 0 }
if (-not $oldExe.Equals($TargetExe, [StringComparison]::OrdinalIgnoreCase)) { exit 0 }

Stop-ScheduledTask -TaskName 'WeChatTextExport'
for ($i = 0; $i -lt 30; $i++) {
    $running = Get-CimInstance Win32_Process -Filter "Name='WeChatTextExport.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $oldExe }
    if (-not $running) { exit 0 }
    Start-Sleep -Milliseconds 500
}
exit 1
