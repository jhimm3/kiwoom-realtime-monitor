param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('index_1m', 'index_1m_latest', 'index_5m', 'index_daily', 'stock_daily', 'stock_adjustment')]
    [string]$Kind,
    [ValidateRange(1, 10000)]
    [int]$Jobs = 1
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$stateRoot = Join-Path $projectRoot 'data\historical_collection'
$logRoot = Join-Path $stateRoot 'logs'
$stateFile = Join-Path $stateRoot "context-$Kind-state.json"
$logFile = Join-Path $logRoot "context-$Kind-$([DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')).log"
$utf8 = New-Object System.Text.UTF8Encoding($false)
[IO.Directory]::CreateDirectory($logRoot) | Out-Null
Set-Location $projectRoot
$env:PYTHONIOENCODING = 'utf-8'
$completedJobs = 0
$environmentUnavailable = $false

function Write-State([string]$status, [string]$errorText = '') {
    $payload = [ordered]@{
        schema='historical-market-context-state/v1'; pid=$PID; kind=$Kind
        status=$status; requested_jobs=$Jobs; completed_jobs=$completedJobs
        database=(Join-Path $projectRoot 'data\historical_market_context.sqlite3')
        log=$logFile; error=$errorText
        updated_at=[DateTimeOffset]::UtcNow.ToString('o')
    }
    [IO.File]::WriteAllText($stateFile, ($payload | ConvertTo-Json -Depth 4), $utf8)
}

Write-State 'running'
try {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $python scripts\collect_historical_market_context.py $Kind --jobs $Jobs 2>&1 | ForEach-Object {
        $line = [string]$_
        [IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine, $utf8)
        Write-Output $line
        if ($line -match '"event":\s*"complete"') {
            $completedJobs += 1
            Write-State 'running'
        }
        if ($line -match '"event":\s*"environment_unavailable"') {
            $environmentUnavailable = $true
        }
    }
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    if ($environmentUnavailable) {
        Write-State 'environment_unavailable' 'CREON Plus connection was lost. Reconnect CREON Plus before restarting the collector.'
        exit 3
    }
    if ($code -ne 0) { throw "Context collector exited with code $code. See $logFile" }
    Write-State 'complete'
}
catch {
    Write-State 'failed' $_.Exception.Message
    [IO.File]::AppendAllText($logFile, "collector failed: $($_.Exception.Message)`n", $utf8)
    exit 2
}
