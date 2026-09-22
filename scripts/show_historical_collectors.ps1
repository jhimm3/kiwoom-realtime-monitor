param(
    [string]$NasProject = "X:\kiwoom-monitor"
)

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stateRoot = Join-Path $projectRoot 'data\historical_collection'

function Show-Collector([string]$Name, [string]$StateName) {
    $path = Join-Path $stateRoot $StateName
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Host "$Name : state file missing"
        return
    }
    $state = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    $updated = [DateTimeOffset]::Parse([string]$state.updated_at)
    $age = [math]::Round(([DateTimeOffset]::Now - $updated).TotalSeconds)
    $jobHeartbeatFresh = $false
    if ($StateName -eq 'news-collector-state.json') {
        $jobHeartbeatPath = Join-Path $stateRoot 'news-job-heartbeat.json'
        if (Test-Path -LiteralPath $jobHeartbeatPath) {
            $jobHeartbeat = Get-Content -LiteralPath $jobHeartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $jobHeartbeatUpdated = [DateTimeOffset]::Parse([string]$jobHeartbeat.updated_at)
            $jobHeartbeatFresh = (
                ([DateTimeOffset]::Now - $jobHeartbeatUpdated).TotalSeconds -lt 120 -and
                [bool](Get-Process -Id $jobHeartbeat.pid -ErrorAction SilentlyContinue)
            )
        }
    }
    $health = if ($process -and $state.status -eq 'running' -and ($age -lt 120 -or $jobHeartbeatFresh)) {
        'RUNNING'
    } elseif ($process) {
        'STALE (process exists)'
    } else {
        'STOPPED'
    }
    Write-Host "$Name : $health"
    Write-Host "  PID=$($state.pid) state=$($state.status) updated=$($updated.ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss')) age=${age}s"
    if ($null -ne $state.finished_this_run) {
        Write-Host "  attempts_this_run=$($state.finished_this_run)"
    }
    if ([string]$state.error) { Write-Host "  error=$($state.error)" }
    Write-Host "  log=$($state.log)"
}

Show-Collector 'NEWS' 'news-collector-state.json'
$heartbeatPath = Join-Path $stateRoot 'news-job-heartbeat.json'
if (Test-Path -LiteralPath $heartbeatPath) {
    $heartbeat = Get-Content -LiteralPath $heartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $heartbeatUpdated = [DateTimeOffset]::Parse([string]$heartbeat.updated_at)
    $heartbeatAge = [math]::Round(([DateTimeOffset]::Now - $heartbeatUpdated).TotalSeconds)
    $heartbeatAlive = [bool](Get-Process -Id $heartbeat.pid -ErrorAction SilentlyContinue)
    $jobLabel = if ($heartbeatAlive) { 'active_job' } else { 'last_job' }
    Write-Host "  $jobLabel=$($heartbeat.code) $($heartbeat.target_date) $($heartbeat.query)"
    Write-Host "  phase=$($heartbeat.phase) page=$($heartbeat.page) article=$($heartbeat.article) heartbeat_age=${heartbeatAge}s"
}
Show-Collector 'DAISHIN' 'daishin-collector-state.json'

$statusPath = [IO.Path]::Combine(
    $NasProject, 'deploy\synology\server-data\historical-intelligence\v1\STATUS.md'
)
if (Test-Path -LiteralPath $statusPath) {
    $item = Get-Item -LiteralPath $statusPath
    $age = [math]::Round(((Get-Date) - $item.LastWriteTime).TotalSeconds)
    Write-Host "NAS STATUS : updated=$($item.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')) age=${age}s"
    Write-Host "  $statusPath"
} else {
    Write-Host "NAS STATUS : not visible in this PowerShell session"
    Write-Host "  $statusPath"
}
