$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $project
$python = Join-Path $project '.venv\Scripts\python.exe'
$pyinstaller = Join-Path $project '.venv\Scripts\pyinstaller.exe'
$compiler = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Missing project Python environment' }
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Missing Inno Setup compiler' }
& $python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed' }
& $pyinstaller --noconfirm --clean WeChatTextExport.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
& $python tools\release.py build
if ($LASTEXITCODE -ne 0) { throw 'Release signing failed' }
& $compiler setup.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed' }
