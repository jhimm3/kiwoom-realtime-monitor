param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$payloadDir = Join-Path $projectRoot "build\installer_payload"
$installerSource = Join-Path $projectRoot "installer\kiwoom-monitor.iss"
$innoCompiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"

if (-not (Test-Path $python)) { throw ".venv Python was not found." }
if (-not (Test-Path $innoCompiler)) { throw "Inno Setup 6 was not found." }

Push-Location $projectRoot
try {
    if (-not $SkipTests) {
        & $python -m compileall -q src tests
        & $python -m unittest discover -s tests -q
    }

    # The one-directory distribution never extracts a _MEI temporary directory.
    if (Test-Path $payloadDir) { Remove-Item -LiteralPath $payloadDir -Recurse -Force }
    & $python -m PyInstaller --noconfirm --clean --windowed --onedir `
        --name "kiwoom-monitor" `
        --paths src `
        --distpath (Join-Path $projectRoot "build") `
        --workpath (Join-Path $projectRoot "build\pyinstaller_work") `
        "src\kiwoom_monitor\bootstrap.py"
    Move-Item -LiteralPath (Join-Path $projectRoot "build\kiwoom-monitor") -Destination $payloadDir
    & $innoCompiler $installerSource
}
finally {
    Pop-Location
}
