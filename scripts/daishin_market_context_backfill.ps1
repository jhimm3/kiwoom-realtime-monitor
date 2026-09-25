param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(U001|U201|[0-9A-Z]{6})$')]
    [string]$Code,
    [Parameter(Mandatory = $true)]
    [ValidateSet('index_1m', 'index_1m_latest', 'index_5m', 'index_daily', 'stock_daily', 'stock_adjustment')]
    [string]$Kind,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [ValidatePattern('^[0-9]{8}$')]
    [string]$FromDate = '19000101',
    [ValidatePattern('^[0-9]{8}$')]
    [string]$ToDate = (Get-Date).ToString('yyyyMMdd')
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$output = [IO.Path]::GetFullPath($OutputPath)

function Write-Record([object]$record) {
    [IO.File]::AppendAllText($output, (($record | ConvertTo-Json -Depth 8 -Compress) + [Environment]::NewLine), $utf8)
}

try {
    if ([Environment]::Is64BitProcess) { throw '32-bit Windows PowerShell is required.' }
    if ([IO.File]::Exists($output)) { throw "OutputPath already exists: $output" }
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($output)) | Out-Null
    $cybos = New-Object -ComObject CpUtil.CpCybos
    if ([int]$cybos.IsConnect -ne 1) {
        throw 'CREON Plus is not connected in this Windows privilege context.'
    }
    $chart = New-Object -ComObject CpSysDib.StockChart
    $isDaily = $Kind.EndsWith('daily') -or $Kind -eq 'stock_adjustment'
    $isIndex = $Kind.StartsWith('index_')
    $isRecent = $Kind -eq 'index_1m_latest'
    [int[]]$fields = if ($Kind -eq 'stock_daily') { @(0, 5, 8, 9, 12, 13) }
        elseif ($Kind -eq 'stock_adjustment') { @(0, 18, 19) }
        elseif ($isDaily) { @(0, 2, 3, 4, 5, 8, 9) }
        else { @(0, 1, 2, 3, 4, 5, 8, 9) }
    $chart.SetInputValue(0, $(if ($isIndex) { $Code } else { "A$Code" }))
    $chart.SetInputValue(1, [char]$(if ($isRecent) { '2' } else { '1' }))
    if ($isRecent) {
        $chart.SetInputValue(4, 1500)
    }
    else {
        $chart.SetInputValue(2, [int]$ToDate)
        $chart.SetInputValue(3, [int]$FromDate)
    }
    $chart.SetInputValue(5, $fields)
    $chart.SetInputValue(6, [char]$(if ($isDaily) { 'D' } else { 'm' }))
    $chart.SetInputValue(7, $(if ($Kind -eq 'index_5m') { 5 } else { 1 }))
    $chart.SetInputValue(8, [char]'0')
    $chart.SetInputValue(9, [char]'0')
    if (-not $isIndex) {
        $chart.SetInputValue(10, [char]'3')
        $chart.SetInputValue(12, [char]'K')
        $chart.SetInputValue(13, [char]'2')
    }
    $runId = [Guid]::NewGuid().ToString('N')
    $pages = 0
    $total = 0L
    $oldest = ''
    $newest = ''
    $previousOldest = ''
    do {
        if ([int]$cybos.GetLimitRemainCount(1) -le 0) {
            Start-Sleep -Milliseconds ([Math]::Max(100, [int]$cybos.LimitRequestRemainTime + 100))
        }
        $requestCode = [int]$chart.BlockRequest()
        if ($requestCode -ne 0) { throw "StockChart BlockRequest failed: $requestCode" }
        $status = [int]$chart.GetDibStatus()
        if ($status -ne 0) { throw "StockChart status $status`: $($chart.GetDibMsg1())" }
        $received = [int]$chart.GetHeaderValue(3)
        $bars = New-Object System.Collections.Generic.List[object]
        for ($row = 0; $row -lt $received; $row++) {
            $rawDate = [int]$chart.GetDataValue(0, $row)
            $day = $rawDate.ToString('00000000')
            $date = '{0}-{1}-{2}' -f $day.Substring(0,4), $day.Substring(4,2), $day.Substring(6,2)
            if ($Kind -eq 'stock_daily') {
                $bars.Add([ordered]@{
                    date=$date; raw_date=$rawDate
                    close=[double]$chart.GetDataValue(1,$row)
                    volume=[long]$chart.GetDataValue(2,$row)
                    trading_value=[long]$chart.GetDataValue(3,$row)
                    shares=[long]$chart.GetDataValue(4,$row)
                    market_cap=[long]$chart.GetDataValue(5,$row)
                })
            }
            elseif ($Kind -eq 'stock_adjustment') {
                $bars.Add([ordered]@{
                    date=$date; raw_date=$rawDate
                    raw_adjustment_date=[int]$chart.GetDataValue(1,$row)
                    adjustment_rate=[double]$chart.GetDataValue(2,$row)
                })
            }
            else {
                $offset = if ($isDaily) { 0 } else { 1 }
                $rawTime = if ($isDaily) { 0 } else { [int]$chart.GetDataValue(1,$row) }
                $time = $rawTime.ToString('0000')
                $barTime = if ($isDaily) { $date } else {
                    '{0}T{1}:{2}:00+09:00' -f $date,$time.Substring(0,2),$time.Substring(2,2)
                }
                $bars.Add([ordered]@{
                    bar_time=$barTime; raw_date=$rawDate; raw_time=$rawTime
                    open=[double]$chart.GetDataValue((1+$offset),$row)
                    high=[double]$chart.GetDataValue((2+$offset),$row)
                    low=[double]$chart.GetDataValue((3+$offset),$row)
                    close=[double]$chart.GetDataValue((4+$offset),$row)
                    volume=[long]$chart.GetDataValue((5+$offset),$row)
                    trading_value=[long]$chart.GetDataValue((6+$offset),$row)
                })
            }
        }
        $pages += 1
        $total += $received
        if ($received -gt 0) {
            $first = if (-not $isIndex) { [string]$bars[0].date } else { [string]$bars[0].bar_time }
            $last = if (-not $isIndex) { [string]$bars[$received-1].date } else { [string]$bars[$received-1].bar_time }
            if (-not $newest) { $newest = $first }
            if ($previousOldest -and $previousOldest -eq $last) { throw "Continuation did not advance: $last" }
            $previousOldest = $last
            $oldest = $last
        }
        Write-Record ([ordered]@{
            record_type='page'; run_id=$runId; provider='daishin_creon'; code=$Code; kind=$Kind
            page=$pages; field_ids=$fields; received_count=$received; continue=[bool]$chart.Continue
            observed_at=[DateTimeOffset]::UtcNow.ToString('o'); bars=$bars
        })
        $continue = [bool]$chart.Continue -and $received -gt 0 -and (-not $isRecent -or $total -lt 1500)
    } while ($continue)
    Write-Record ([ordered]@{
        record_type='summary'; run_id=$runId; code=$Code; kind=$Kind; pages=$pages
        total_bars=$total; oldest=$oldest; newest=$newest
        provider_has_more=$(if ($isRecent) { $continue } else { [bool]$chart.Continue })
        requested_count=$(if ($isRecent) { 1500 } else { 0 })
        completed_at=[DateTimeOffset]::UtcNow.ToString('o')
    })
}
catch {
    Write-Record ([ordered]@{
        record_type='error'; code=$Code; kind=$Kind; error=$_.Exception.Message
        observed_at=[DateTimeOffset]::UtcNow.ToString('o')
    })
    exit 2
}
