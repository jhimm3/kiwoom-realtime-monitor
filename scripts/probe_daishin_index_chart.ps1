param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [switch]$HistoricalDailyOnly
)

$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$resolvedOutput = [IO.Path]::GetFullPath($OutputPath)
if ([Environment]::Is64BitProcess) {
    throw 'Run with 32-bit Windows PowerShell from SysWOW64.'
}
if ([IO.File]::Exists($resolvedOutput)) {
    throw "OutputPath already exists: $resolvedOutput"
}
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($resolvedOutput)) | Out-Null

$cybos = New-Object -ComObject CpUtil.CpCybos
if ([int]$cybos.IsConnect -ne 1) {
    throw 'CREON Plus is not connected in this Windows privilege context.'
}

foreach ($index in @(@{ code = 'U001'; market = 'KOSPI' }, @{ code = 'U201'; market = 'KOSDAQ' })) {
    $periods = if ($HistoricalDailyOnly) {
        @(@{ kind = 'historical_daily'; cycle = 'D'; interval = 1 })
    }
    else {
        @(@{ kind = 'minute'; cycle = 'm'; interval = 1 },
            @{ kind = 'five_minute'; cycle = 'm'; interval = 5 },
            @{ kind = 'daily'; cycle = 'D'; interval = 1 })
    }
    foreach ($period in $periods) {
        $result = [ordered]@{
            provider = 'daishin_creon'
            market = $index.market
            index_code = $index.code
            kind = $period.kind
            requested_count = 5
            observed_at = [DateTimeOffset]::UtcNow.ToString('o')
        }
        try {
            if ([int]$cybos.GetLimitRemainCount(1) -le 0) {
                Start-Sleep -Milliseconds ([Math]::Max(100, [int]$cybos.LimitRequestRemainTime + 100))
            }
            $chart = New-Object -ComObject CpSysDib.StockChart
            [int[]]$fields = if ($period.cycle -eq 'D') { @(0, 2, 3, 4, 5, 8, 9) }
                else { @(0, 1, 2, 3, 4, 5, 8, 9) }
            $chart.SetInputValue(0, [string]$index.code)
            if ($HistoricalDailyOnly) {
                $chart.SetInputValue(1, [char]'1')
                $chart.SetInputValue(2, 20190110)
                $chart.SetInputValue(3, 20190102)
            }
            else {
                $chart.SetInputValue(1, [char]'2')
                $chart.SetInputValue(4, 5)
            }
            $chart.SetInputValue(5, $fields)
            $chart.SetInputValue(6, [char]$period.cycle)
            $chart.SetInputValue(7, [int]$period.interval)
            $chart.SetInputValue(8, [char]'0')
            $chart.SetInputValue(9, [char]'0')
            $requestCode = [int]$chart.BlockRequest()
            $status = [int]$chart.GetDibStatus()
            $result.request_code = $requestCode
            $result.status = $status
            $result.message = [string]$chart.GetDibMsg1()
            $result.continue = [bool]$chart.Continue
            $result.field_names = @($chart.GetHeaderValue(2))
            $rows = New-Object System.Collections.Generic.List[object]
            if ($requestCode -eq 0 -and $status -eq 0) {
                $received = [int]$chart.GetHeaderValue(3)
                for ($row = 0; $row -lt $received; $row++) {
                    $values = New-Object System.Collections.Generic.List[object]
                    for ($column = 0; $column -lt $fields.Count; $column++) {
                        $values.Add($chart.GetDataValue($column, $row))
                    }
                    $rows.Add($values.ToArray())
                }
            }
            $result.fields = $fields
            $result.rows = $rows.ToArray()
        }
        catch {
            $result.error = $_.Exception.Message
        }
        [IO.File]::AppendAllText($resolvedOutput,
            (($result | ConvertTo-Json -Depth 6 -Compress) + [Environment]::NewLine), $utf8)
    }
}
