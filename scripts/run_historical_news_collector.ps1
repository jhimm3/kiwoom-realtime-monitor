param(
    [ValidateRange(1, 10000)]
    [int]$Jobs = 50,

    [ValidateRange(1, 100)]
    [int]$MaxPages = 100,

    [ValidateRange(0.0, 60.0)]
    [double]$RequestDelay = 0.5,

    [ValidateRange(0.0, 60.0)]
    [double]$ArticleDelay = 0.2,

    [string]$Database = "data\historical_intelligence.sqlite3",

    [string]$NasProject = "X:\kiwoom-monitor"
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$logRoot = Join-Path $projectRoot 'data\historical_collection\logs'
$stateRoot = Join-Path $projectRoot 'data\historical_collection'
$stopFile = Join-Path $stateRoot 'STOP_NEWS'
$stateFile = Join-Path $stateRoot 'news-collector-state.json'
$stamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
$logFile = Join-Path $logRoot "news-$stamp.log"

[IO.Directory]::CreateDirectory($logRoot) | Out-Null
if (Test-Path -LiteralPath $stopFile) {
    Remove-Item -LiteralPath $stopFile -Force
}
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'

function Write-State([string]$Status, [int]$Completed, [string]$ErrorText = '') {
    [ordered]@{
        schema = 'historical-news-collector-state/v1'
        pid = $PID
        status = $Status
        requested_jobs = $Jobs
        finished_this_run = $Completed
        database = [IO.Path]::GetFullPath((Join-Path $projectRoot $Database))
        log = $logFile
        error = $ErrorText
        updated_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
}

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f [DateTimeOffset]::Now.ToString('o'), $Message
    [IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine)
}

Set-Location $projectRoot
$completed = 0
Write-State 'running' $completed
Write-Log "collector started jobs=$Jobs max_pages=$MaxPages"
try {
    for ($index = 1; $index -le $Jobs; $index++) {
        if (Test-Path -LiteralPath $stopFile) {
            Write-Log 'stop file observed'
            break
        }
        $output = & $python scripts\probe_historical_backfill.py news-run `
            --jobs 1 --max-pages $MaxPages --request-delay $RequestDelay `
            --article-delay $ArticleDelay --output $Database 2>&1
        foreach ($line in $output) { Write-Log ([string]$line) }
        if ($LASTEXITCODE -ne 0) {
            throw "news-run exited with code $LASTEXITCODE"
        }
        $completed += 1
        Write-State 'running' $completed
        try {
            $importOutput = & $python scripts\import_historical_news_to_nas.py `
                --database $Database --batch-size 100 2>&1
            foreach ($line in $importOutput) { Write-Log ([string]$line) }
            if ($LASTEXITCODE -ne 0) {
                Write-Log "NAS news import exited with code $LASTEXITCODE"
            }
            $statusOutput = & $python scripts\report_historical_collection_status.py `
                --database $Database --nas-project $NasProject 2>&1
            foreach ($line in $statusOutput) { Write-Log ([string]$line) }
        }
        catch {
            Write-Log "status publish failed: $($_.Exception.Message)"
        }
    }
    Write-State 'publishing' $completed
    $publishOutput = & $python scripts\publish_historical_intelligence_to_nas.py `
        --database $Database --nas-project $NasProject 2>&1
    foreach ($line in $publishOutput) { Write-Log ([string]$line) }
    if ($LASTEXITCODE -ne 0) {
        throw "NAS snapshot publish exited with code $LASTEXITCODE"
    }
    & $python scripts\report_historical_collection_status.py `
        --database $Database --nas-project $NasProject 2>&1 |
        ForEach-Object { Write-Log ([string]$_) }
    Write-State 'complete' $completed
    Write-Log "collector finished completed=$completed"
}
catch {
    Write-State 'failed' $completed $_.Exception.Message
    Write-Log "collector failed: $($_.Exception.Message)"
    exit 2
}
