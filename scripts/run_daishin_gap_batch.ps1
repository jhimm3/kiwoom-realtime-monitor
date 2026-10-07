param(
    [string]$PlanPath = 'data\historical_collection\daishin-retry-plan-20260923.json'
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$resolvedPlan = [IO.Path]::GetFullPath((Join-Path $projectRoot $PlanPath))
if (-not $resolvedPlan.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Retry plan must stay inside the project.'
}
$plan = Get-Content -LiteralPath $resolvedPlan -Raw | ConvertFrom-Json
if ($plan.schema -ne 'daishin-gap-retry-plan/v1') { throw 'Unexpected retry plan schema.' }
$codes = @($plan.codes | ForEach-Object { [string]$_.code })
if ($codes.Count -lt 1 -or $codes.Count -gt 30 -or ($codes | Select-Object -Unique).Count -ne $codes.Count) {
    throw 'Retry plan must have 1-30 distinct codes.'
}
foreach ($stockCode in $codes) {
    if ($stockCode -cnotmatch '^[0-9A-Z]{6}$') { throw "Invalid retry code: $stockCode" }
}
if (-not [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run the batch in the administrator CREON login session.'
}

$python = 'C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$logRoot = Join-Path $projectRoot 'data\historical_collection\logs'
$stateFile = Join-Path $projectRoot 'data\historical_collection\daishin-retry-batch-state.json'
$stamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
$logFile = Join-Path $logRoot "daishin-retry-batch-$stamp.log"
$errorFile = Join-Path $logRoot "daishin-retry-batch-$stamp.err.log"
[IO.Directory]::CreateDirectory($logRoot) | Out-Null
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $projectRoot

function Write-State([string]$status, [string]$currentCode, [int]$completed, [int]$failed) {
    [ordered]@{
        schema = 'daishin-gap-retry-batch/v1'
        pid = $PID
        status = $status
        current_code = $currentCode
        completed = $completed
        failed = $failed
        total = $codes.Count
        log = $logFile
        error_log = $errorFile
        updated_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
}

$completed = 0
$failed = 0
try {
    Write-State 'running' '' $completed $failed
    $preflight = & 'C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe' `
        -NoProfile -ExecutionPolicy Bypass -File 'scripts\daishin_stockchart_probe.ps1' `
        -Code $codes[0] -PreflightOnly 2>&1
    if ($LASTEXITCODE -ne 0) { throw "CREON preflight failed: $($preflight -join ' ')" }
    [IO.File]::AppendAllText($logFile, "CREON preflight: $($preflight -join ' ')`n")
    foreach ($stockCode in $codes) {
        Write-State 'running' $stockCode $completed $failed
        [IO.File]::AppendAllText($logFile, "retry started $stockCode $([DateTimeOffset]::Now.ToString('o'))`n")
        $previousErrorAction = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $python -u scripts\retry_daishin_missing_5m.py --code $stockCode `
            1>> $logFile 2>> $errorFile
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorAction
        if ($exitCode -eq 0) { $completed += 1 } else { $failed += 1 }
        [IO.File]::AppendAllText($logFile, "retry exited code=$stockCode exit=$exitCode`n")
    }
    Write-State 'complete' '' $completed $failed
    exit $(if ($failed -eq 0) { 0 } else { 1 })
}
catch {
    [IO.File]::AppendAllText($errorFile, "batch launcher failed: $($_.Exception.Message)`n")
    Write-State 'failed' '' $completed ($failed + 1)
    exit 2
}
