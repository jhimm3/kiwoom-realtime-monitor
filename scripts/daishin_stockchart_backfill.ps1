param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Z]{6}$')]
    [string]$Code,

    [Parameter(Mandatory = $true)]
    [ValidateSet(1, 5)]
    [int]$Interval,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [ValidatePattern('^[0-9]{8}$')]
    [string]$FromDate = '19000101',

    [ValidatePattern('^[0-9]{8}$')]
    [string]$ToDate = (Get-Date).ToString('yyyyMMdd'),

    [ValidateSet('A', 'K', 'N')]
    [string]$Venue = 'K',

    [ValidateSet('regular', 'regular_and_after')]
    [string]$Session = 'regular',

    [ValidateSet('raw', 'adjusted')]
    [string]$Adjustment = 'raw',

    [ValidateRange(0, 10000)]
    [int]$MaxPages = 0
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)

function Write-NdjsonRecord([object]$Value) {
    $line = $Value | ConvertTo-Json -Depth 7 -Compress
    [IO.File]::AppendAllText($resolvedOutput, $line + [Environment]::NewLine, $utf8)
}

try {
    if ([Environment]::Is64BitProcess) {
        throw 'Run this script with 32-bit Windows PowerShell from SysWOW64.'
    }
    $parent = [IO.Path]::GetDirectoryName($resolvedOutput)
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        [IO.Directory]::CreateDirectory($parent) | Out-Null
    }
    if ([IO.File]::Exists($resolvedOutput)) {
        throw "OutputPath already exists: $resolvedOutput"
    }

    $cybos = New-Object -ComObject CpUtil.CpCybos
    if ([int]$cybos.IsConnect -ne 1) {
        throw 'CREON Plus is not connected in this Windows privilege context.'
    }

    $chart = New-Object -ComObject CpSysDib.StockChart
    [int[]]$fields = @(0, 1, 2, 3, 4, 5, 8, 9)
    $chart.SetInputValue(0, "A$Code")
    $chart.SetInputValue(1, [char]'1')
    $chart.SetInputValue(2, [int]$ToDate)
    $chart.SetInputValue(3, [int]$FromDate)
    $chart.SetInputValue(5, $fields)
    $chart.SetInputValue(6, [char]'m')
    $chart.SetInputValue(7, $Interval)
    $chart.SetInputValue(8, [char]'0')
    $chart.SetInputValue(9, [char]$(if ($Adjustment -eq 'adjusted') { '1' } else { '0' }))
    $chart.SetInputValue(10, [char]$(if ($Session -eq 'regular') { '3' } else { '1' }))
    $chart.SetInputValue(11, [char]'N')
    $chart.SetInputValue(12, [char]$Venue)
    $chart.SetInputValue(13, [char]$(if ($Session -eq 'regular') { '2' } else { '1' }))

    $runId = [Guid]::NewGuid().ToString('N')
    $pageNumber = 0
    $totalBars = 0L
    $oldest = ''
    $newest = ''
    $priorOldest = ''
    do {
        if ([int]$cybos.GetLimitRemainCount(1) -le 0) {
            $waitMs = [Math]::Max(100, [int]$cybos.LimitRequestRemainTime + 100)
            Start-Sleep -Milliseconds $waitMs
        }
        $returnCode = [int]$chart.BlockRequest()
        if ($returnCode -ne 0) {
            throw "StockChart BlockRequest failed with code $returnCode."
        }
        $status = [int]$chart.GetDibStatus()
        $message = [string]$chart.GetDibMsg1()
        if ($status -ne 0) {
            throw "StockChart response status $status`: $message"
        }

        $received = [int]$chart.GetHeaderValue(3)
        $bars = New-Object System.Collections.Generic.List[object]
        for ($row = 0; $row -lt $received; $row++) {
            $rawDate = [int]$chart.GetDataValue(0, $row)
            $rawTime = [int]$chart.GetDataValue(1, $row)
            $dateText = $rawDate.ToString('00000000')
            $timeText = $rawTime.ToString('0000')
            $barTime = '{0}-{1}-{2}T{3}:{4}:00+09:00' -f (
                $dateText.Substring(0, 4), $dateText.Substring(4, 2), $dateText.Substring(6, 2),
                $timeText.Substring(0, 2), $timeText.Substring(2, 2)
            )
            $bars.Add([ordered]@{
                bar_time = $barTime
                raw_date = $rawDate
                raw_time = $rawTime
                open = [long]$chart.GetDataValue(2, $row)
                high = [long]$chart.GetDataValue(3, $row)
                low = [long]$chart.GetDataValue(4, $row)
                close = [long]$chart.GetDataValue(5, $row)
                volume = [long]$chart.GetDataValue(6, $row)
                trading_value = [long]$chart.GetDataValue(7, $row)
            })
        }
        $pageNumber += 1
        $totalBars += $received
        if ($received -gt 0) {
            $pageNewest = [string]$bars[0].bar_time
            $pageOldest = [string]$bars[$received - 1].bar_time
            if (-not $newest) { $newest = $pageNewest }
            if ($priorOldest -and $pageOldest -eq $priorOldest) {
                throw "StockChart continuation did not advance beyond $pageOldest."
            }
            $priorOldest = $pageOldest
            $oldest = $pageOldest
        }
        Write-NdjsonRecord ([ordered]@{
            record_type = 'page'
            run_id = $runId
            provider = 'daishin_creon'
            code = $Code
            interval_seconds = $Interval * 60
            venue = $Venue
            session_scope = $Session
            adjustment_mode = $Adjustment
            bar_time_semantics = 'interval_end'
            page = $pageNumber
            received_count = $received
            continue = [bool]$chart.Continue
            observed_at = [DateTimeOffset]::UtcNow.ToString('o')
            bars = $bars
        })
        $continue = [bool]$chart.Continue -and $received -gt 0
        if ($MaxPages -gt 0 -and $pageNumber -ge $MaxPages) {
            $continue = $false
        }
    } while ($continue)

    Write-NdjsonRecord ([ordered]@{
        record_type = 'summary'
        run_id = $runId
        provider = 'daishin_creon'
        code = $Code
        interval_seconds = $Interval * 60
        pages = $pageNumber
        total_bars = $totalBars
        newest = $newest
        oldest = $oldest
        provider_has_more = [bool]$chart.Continue
        stopped_by_max_pages = ($MaxPages -gt 0 -and $pageNumber -ge $MaxPages -and [bool]$chart.Continue)
        completed_at = [DateTimeOffset]::UtcNow.ToString('o')
    })
}
catch {
    Write-NdjsonRecord ([ordered]@{
        record_type = 'error'
        provider = 'daishin_creon'
        code = $Code
        interval_seconds = $Interval * 60
        error = $_.Exception.Message
        error_type = $_.Exception.GetType().FullName
        observed_at = [DateTimeOffset]::UtcNow.ToString('o')
    })
    exit 2
}
