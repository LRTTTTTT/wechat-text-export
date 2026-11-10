$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$setup = Join-Path $project 'release\v0.2.0\WeChatTextExport-Setup-0.2.0.exe'
if (-not (Test-Path -LiteralPath $setup)) { throw 'Build the offline setup package first.' }
$process = Start-Process -FilePath $setup -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Setup failed with exit code $($process.ExitCode)" }
