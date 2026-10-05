param(
    [ValidateRange(1, 1000000)][int]$Jobs = 1000000,
    [ValidateRange(1, 100)][int]$BatchSize = 100,
    [ValidateRange(1, 32)][int]$ArticleWorkers = 16,
    [ValidateRange(1, 8)][int]$PrepareWorkers = 4,
    [ValidateRange(0.0, 60.0)][double]$ArticleDelay = 0.2,
    [string]$Database = 'data\historical_intelligence.sqlite3'
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$stateRoot = Join-Path $projectRoot 'data\historical_collection'
$logRoot = Join-Path $stateRoot 'logs'
[IO.Directory]::CreateDirectory($logRoot) | Out-Null
$stopFile = Join-Path $stateRoot 'STOP_NEWS_ARTICLES'
$stateFile = Join-Path $stateRoot 'news-article-collector-state.json'
$logFile = Join-Path $logRoot ("news-articles-{0}.log" -f [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss'))
$diagnosticLog = Join-Path $logRoot ("news-article-timing-{0}.jsonl" -f [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss'))
$lockFile = Join-Path $stateRoot 'news-article-collector.lock'
try {
    $singleInstance = [IO.File]::Open($lockFile, [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
}
catch [IO.IOException] {
    Write-Output 'Article collector is already running; duplicate launch skipped.'
    exit 0
}

function Write-State([string]$Status, [int]$Completed, [string]$ErrorText = '') {
    $document = [ordered]@{
        schema = 'historical-news-article-collector-state/v1'
        pid = $PID
        article_workers = $ArticleWorkers
        prepare_workers = $PrepareWorkers
        status = $Status
        completed_this_run = $Completed
        log = $logFile
        diagnostic_log = $diagnosticLog
        error = $ErrorText
        updated_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json
    $temporary = "$stateFile.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    $backup = "$temporary.bak"
    $lastError = $null
    try {
        [IO.File]::WriteAllText($temporary, $document, [System.Text.UTF8Encoding]::new($false))
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            try {
                if ([IO.File]::Exists($stateFile)) {
                    [IO.File]::Replace($temporary, $stateFile, $backup)
                }
                else {
                    [IO.File]::Move($temporary, $stateFile)
                }
                return
            }
            catch {
                $lastError = $_.Exception.Message
                if ($attempt -lt 19) { Start-Sleep -Milliseconds 100 }
            }
        }
    }
    catch {
        $lastError = $_.Exception.Message
    }
    finally {
        try { [IO.File]::Delete($temporary) } catch { }
        try { [IO.File]::Delete($backup) } catch { }
    }
    try { Write-Log "state update failed after retry: $lastError" } catch { }
}

function Write-Log([string]$Message) {
    [IO.File]::AppendAllText($logFile,
        ("{0} {1}{2}" -f [DateTimeOffset]::Now.ToString('o'), $Message,
            [Environment]::NewLine))
}

Set-Location $projectRoot
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'
$completed = 0
Write-State 'running' $completed
try {
    # Reject a network-restricted launcher before it can resolve queue rows as failures.
    $networkProbe = & $python -c "import socket; socket.getaddrinfo('s.search.naver.com', 443)" 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Article collector DNS preflight failed; queue not processed: $networkProbe"
    }
    Write-Log "collector config: article_workers=$ArticleWorkers prepare_workers=$PrepareWorkers; DNS preflight passed"
    $recovered = & $python scripts\probe_historical_backfill.py article-finalize --output $Database 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "article-finalize failed: $recovered"
    }
    foreach ($line in $recovered) { Write-Log ([string]$line) }
    for ($index = 1; $index -le $Jobs; $index++) {
        if (Test-Path -LiteralPath $stopFile) {
            Write-Log 'stop file observed at article batch boundary'
            Write-State 'stopped' $completed
            exit 0
        }
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = & $python scripts\probe_historical_backfill.py article-run `
            --limit $BatchSize --article-workers $ArticleWorkers `
            --article-delay $ArticleDelay --prepare-workers $PrepareWorkers `
            --diagnostic-log $diagnosticLog --output $Database 2>&1
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorAction
        foreach ($line in $output) { Write-Log ([string]$line) }
        if ($exitCode -ne 0) {
            Write-State 'retrying' $completed ((($output | ForEach-Object { [string]$_ }) -join ' ') | Select-Object -First 1)
            Start-Sleep -Seconds 30
            continue
        }
        $completed++
        Write-State 'running' $completed
        $detail = (($output | ForEach-Object { [string]$_ }) -join ' ')
        if ($detail -match '"claimed_articles"\s*:\s*0') {
            $searchStateFile = Join-Path $stateRoot 'news-collector-state.json'
            if ((Test-Path -LiteralPath $searchStateFile) -and
                $detail -match '"unfinished_articles"\s*:\s*0') {
                $searchState = Get-Content -LiteralPath $searchStateFile -Raw | ConvertFrom-Json
                if ($searchState.status -eq 'complete') {
                    Write-Log 'search and article queues are complete'
                    break
                }
            }
            Start-Sleep -Seconds 5
        }
    }
    Write-State 'complete' $completed
}
catch {
    Write-Log "collector failed: $($_.Exception.Message)"
    Write-State 'failed' $completed $_.Exception.Message
    exit 2
}
finally {
    $singleInstance.Dispose()
}
