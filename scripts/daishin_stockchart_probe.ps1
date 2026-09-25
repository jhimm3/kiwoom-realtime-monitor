param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Z]{6}$')]
    [string]$Code,


    [ValidateSet(1, 5)]
    [int]$Interval = 1,

    [ValidateRange(1, 5000)]
    [int]$Count = 200,

    [ValidateSet('A', 'K', 'N')]
    [string]$Venue = 'K',

    [ValidateSet('regular', 'regular_and_after')]
    [string]$Session = 'regular',

    [ValidateSet('raw', 'adjusted')]
    [string]$Adjustment = 'raw',

    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $OutputEncoding

try {
    if (-not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
        # This probe must run in a 32-bit host because CREON is an in-process COM server.
    }
    else {
        throw 'Run this script with 32-bit Windows PowerShell from SysWOW64.'
    }

    $cybos = New-Object -ComObject CpUtil.CpCybos
    if ([int]$cybos.IsConnect -ne 1) {
        throw 'CREON Plus is not connected. Complete the CREON login first.'
    }

    if ($PreflightOnly) {
        [ordered]@{
            provider = 'daishin_creon'
            connected = $true
            process_bitness = 32
            remaining_quote_requests = [int]$cybos.GetLimitRemainCount(1)
            limit_request_remain_ms = [int]$cybos.LimitRequestRemainTime
            observed_at = [DateTimeOffset]::UtcNow.ToString('o')
        } | ConvertTo-Json -Compress
        return
    }

    $chart = New-Object -ComObject CpSysDib.StockChart
    [int[]]$fields = @(0, 1, 2, 3, 4, 5, 8, 9)
    $chart.SetInputValue(0, "A$Code")
    $chart.SetInputValue(1, [char]'2')
    $chart.SetInputValue(4, $Count)
    $chart.SetInputValue(5, $fields)
    $chart.SetInputValue(6, [char]'m')
    $chart.SetInputValue(7, $Interval)
    $chart.SetInputValue(8, [char]'0')
    $chart.SetInputValue(9, [char]$(if ($Adjustment -eq 'adjusted') { '1' } else { '0' }))
    $chart.SetInputValue(10, [char]$(if ($Session -eq 'regular') { '3' } else { '1' }))
    $chart.SetInputValue(11, [char]'N')
    $chart.SetInputValue(12, [char]$Venue)
    $chart.SetInputValue(13, [char]$(if ($Session -eq 'regular') { '2' } else { '1' }))

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

    [ordered]@{
        provider = 'daishin_creon'
        code = $Code
        interval_seconds = $Interval * 60
        venue = $Venue
        session_scope = $Session
        adjustment_mode = $Adjustment
        requested_count = $Count
        received_count = $received
        continue = [bool]$chart.Continue
        request_return_code = $returnCode
        response_status = $status
        response_message = $message
        remaining_quote_requests = [int]$cybos.GetLimitRemainCount(1)
        limit_request_remain_ms = [int]$cybos.LimitRequestRemainTime
        observed_at = [DateTimeOffset]::UtcNow.ToString('o')
        bar_time_semantics = 'interval_end'
        bars = $bars
    } | ConvertTo-Json -Depth 6 -Compress
}
catch {
    [ordered]@{
        provider = 'daishin_creon'
        error = $_.Exception.Message
        error_type = $_.Exception.GetType().FullName
        observed_at = [DateTimeOffset]::UtcNow.ToString('o')
    } | ConvertTo-Json -Compress
    exit 2
}
