param(
    [ValidateRange(1, 10000)]
    [int]$Jobs = 3,

    [string]$Reference = "data\nas_reference_inspect_20260922\historical_reference.sqlite3",

    [string]$Database = "data\historical_intelligence.sqlite3",

    [string]$NasProject = "X:\kiwoom-monitor"
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = 'C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$stateRoot = Join-Path $projectRoot 'data\historical_collection'
$logRoot = Join-Path $stateRoot 'logs'
$stateFile = Join-Path $stateRoot 'daishin-collector-state.json'
$stopFile = Join-Path $stateRoot 'STOP_DAISHIN'
$stamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
$logFile = Join-Path $logRoot "daishin-$stamp.log"
[IO.Directory]::CreateDirectory($logRoot) | Out-Null
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'

function Write-State([string]$Status, [string]$ErrorText = '') {
    [ordered]@{
        schema = 'daishin-collector-state/v1'
        pid = $PID
        status = $Status
        requested_jobs = $Jobs
        database = [IO.Path]::GetFullPath((Join-Path $projectRoot $Database))
        log = $logFile
        error = $ErrorText
        updated_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
}

function Append-Output([object[]]$Lines) {
    foreach ($line in $Lines) {
        [IO.File]::AppendAllText($logFile, ([string]$line) + [Environment]::NewLine, $utf8)
    }
}

Set-Location $projectRoot
if (Test-Path -LiteralPath $stopFile) {
    Remove-Item -LiteralPath $stopFile -Force
}
Write-State 'running'
try {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $output = & $python scripts\run_daishin_candidate_collection.py `
        --reference $Reference --database $Database --jobs $Jobs 2>&1
    $collectorExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorAction
    Append-Output $output
    if ($collectorExitCode -ne 0) {
        $detail = (($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine).Trim()
        if (-not $detail) {
            $detail = "Daishin candidate collector exited with code $collectorExitCode"
        }
        throw $detail
    }
    # Elevated CREON sessions do not reliably inherit the user's X: mapping.
    # Publish the closed DB snapshot from the ordinary user session afterward.
    Append-Output (& $python scripts\report_historical_collection_status.py `
        --database $Database --local-only 2>&1)
    Write-State 'complete'
}
catch {
    $ErrorActionPreference = 'Stop'
    Write-State 'failed' $_.Exception.Message
    Append-Output @("collector failed: $($_.Exception.Message)")
    exit 2
}
