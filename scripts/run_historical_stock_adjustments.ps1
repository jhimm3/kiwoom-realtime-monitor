param(
    [ValidateRange(1, 10000)]
    [int]$Jobs = 10000
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$stateRoot = Join-Path $projectRoot 'data\historical_collection'
$stateFile = Join-Path $stateRoot 'stock-adjustment-state.json'
$logRoot = Join-Path $stateRoot 'logs'
$logFile = Join-Path $logRoot "stock-adjustment-$([DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')).log"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.Directory]::CreateDirectory($logRoot) | Out-Null
Set-Location $projectRoot
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = (Join-Path $projectRoot 'src')

function Write-State([string]$status, [string]$phase, [string]$errorText = '') {
    $payload = [ordered]@{
        schema='historical-stock-adjustment-state/v1'; pid=$PID; status=$status
        phase=$phase; requested_jobs=$Jobs
        database=(Join-Path $projectRoot 'data\historical_market_context.sqlite3')
        log=$logFile; error=$errorText; updated_at=[DateTimeOffset]::UtcNow.ToString('o')
    }
    [IO.File]::WriteAllText($stateFile, ($payload | ConvertTo-Json -Depth 4), $utf8)
}

function Invoke-Logged([string[]]$arguments, [string]$phase) {
    Write-State 'running' $phase
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $python @arguments 2>&1 | ForEach-Object {
        $line = [string]$_
        [IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine, $utf8)
        Write-Output $line
        Write-State 'running' $phase
    }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) { throw "$phase exited with code $code. See $logFile" }
}

try {
    Invoke-Logged -arguments @('scripts\collect_historical_market_context.py', 'stock_adjustment', '--jobs', [string]$Jobs) -phase 'creon'
    Invoke-Logged -arguments @('scripts\classify_historical_stock_adjustments.py', '--jobs', [string]$Jobs) -phase 'dart'
    Write-State 'complete' 'complete'
}
catch {
    Write-State 'failed' 'failed' $_.Exception.Message
    [IO.File]::AppendAllText($logFile, "pipeline failed: $($_.Exception.Message)`n", $utf8)
    exit 2
}
