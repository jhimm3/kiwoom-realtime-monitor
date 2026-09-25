param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Z]{6}$')]
    [string]$Code
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$logRoot = Join-Path $projectRoot 'data\historical_collection\logs'
$stamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
$logFile = Join-Path $logRoot "daishin-retry-$Code-$stamp.log"
$errorFile = Join-Path $logRoot "daishin-retry-$Code-$stamp.err.log"
[IO.Directory]::CreateDirectory($logRoot) | Out-Null
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $projectRoot

try {
    if (-not [Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run the retry in the administrator CREON login session.'
    }
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $python -u scripts\retry_daishin_missing_5m.py --code $Code `
        1>> $logFile 2>> $errorFile
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    [IO.File]::AppendAllText($logFile, "retry exited code=$exitCode $([DateTimeOffset]::Now.ToString('o'))`n")
    exit $exitCode
}
catch {
    [IO.File]::AppendAllText($errorFile, "retry launcher failed: $($_.Exception.Message)`n")
    exit 2
}
