param(
    [int]$Jobs = 1,
    [string]$StartDate = "2019-01-01",
    [string]$EndDate = "2026-09-22",
    [double]$RequestDelay = 0.7,
    [ValidateRange(1, 8)]
    [int]$PrepareWorkers = 4,
    [int]$PublishEveryDays = 30,
    [int]$StatusEveryDays = 1,
    [string]$NasProject = 'X:\kiwoom-monitor'
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$logRoot = Join-Path $projectRoot 'data\historical_collection\logs'
$stamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
$logFile = Join-Path $logRoot "market-news-$stamp.log"
$errorLog = Join-Path $logRoot "market-news-$stamp.err.log"
$stateFile = Join-Path $projectRoot 'data\historical_collection\market-news-state.json'

[IO.Directory]::CreateDirectory($logRoot) | Out-Null
try {
    [IO.File]::AppendAllText($logFile, "collector launcher started $([DateTimeOffset]::Now.ToString('o'))`n")
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python executable is missing: $python"
    }
    if (-not (Test-Path -LiteralPath $NasProject -PathType Container)) {
        $NasProject = '\\192.168.0.5\docker\kiwoom-monitor'
    }
    if (-not (Test-Path -LiteralPath $NasProject -PathType Container)) {
        throw "NAS project is unavailable at both X: and UNC path"
    }
    Set-Location $projectRoot
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $python -u scripts\run_naver_stock_market_news.py `
        --start-date $StartDate --end-date $EndDate `
        --delay $RequestDelay --publish-every-days $PublishEveryDays `
        --status-every-days $StatusEveryDays --nas-project $NasProject `
        --prepare-workers $PrepareWorkers `
        1>> $logFile 2>> $errorLog
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    [IO.File]::AppendAllText($logFile, "collector exited code=$code $([DateTimeOffset]::Now.ToString('o'))`n")
    if ($code -ne 0) {
        $detail = "Market news collector exited with code $code. See $errorLog"
        if (Test-Path -LiteralPath $stateFile) {
            $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
            if ($state.status -eq 'running') {
                $state.status = 'failed'
                $state.error = $detail
                $state.updated_at = [DateTimeOffset]::UtcNow.ToString('o')
                $state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $stateFile -Encoding utf8
            }
        }
    }
    exit $code
}
catch {
    $detail = "Market news launcher failed: $($_.Exception.Message)"
    [IO.File]::AppendAllText($errorLog, "$detail`n")
    [ordered]@{
        schema = 'naver-stock-market-news-collector/v1'
        pid = $PID
        updated_at = [DateTimeOffset]::UtcNow.ToString('o')
        status = 'failed'
        error = $detail
    } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
    exit 2
}
