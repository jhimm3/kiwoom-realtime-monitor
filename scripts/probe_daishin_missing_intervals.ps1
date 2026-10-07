param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

# One read-only StockChart request per missing-range candidate. Never imports bars.
$ErrorActionPreference = 'Stop'
if ([Environment]::Is64BitProcess) { throw '32-bit PowerShell is required.' }
$cybos = New-Object -ComObject CpUtil.CpCybos
if ([int]$cybos.IsConnect -ne 1) { throw 'CREON Plus is disconnected.' }
$items = Get-Content -LiteralPath $InputPath -Raw -Encoding UTF8 | ConvertFrom-Json
$target = [IO.Path]::GetFullPath($OutputPath)
if ([IO.File]::Exists($target)) { throw "OutputPath already exists: $target" }
$utf8 = [Text.UTF8Encoding]::new($false)
foreach ($item in $items) {
    $code = [string]$item.code
    $interval = [int]$item.interval
    $toDate = [string]$item.to_date
    if ($code -notmatch '^[0-9A-Z]{6}$' -or $interval -notin @(1, 5) -or
        $toDate -notmatch '^[0-9]{8}$') { throw "Invalid input item: $code/$interval/$toDate" }
    $result = [ordered]@{
        code = $code; interval = $interval; to_date = $toDate
        checked_at = [DateTimeOffset]::UtcNow.ToString('o')
        status = ''; received = 0; newest = ''; oldest = ''; has_more = $false; error = ''
    }
    $chart = $null
    try {
        $chart = New-Object -ComObject CpSysDib.StockChart
        [int[]]$fields = @(0, 1, 2, 3, 4, 5, 8, 9)
        $chart.SetInputValue(0, "A$code")
        $chart.SetInputValue(1, [char]'1')
        $chart.SetInputValue(2, [int]$toDate)
        $chart.SetInputValue(3, [int]19000101)
        $chart.SetInputValue(5, $fields)
        $chart.SetInputValue(6, [char]'m')
        $chart.SetInputValue(7, $interval)
        $chart.SetInputValue(8, [char]'0')
        $chart.SetInputValue(9, [char]'0')
        $chart.SetInputValue(10, [char]'3')
        $chart.SetInputValue(11, [char]'N')
        $chart.SetInputValue(12, [char]'K')
        $chart.SetInputValue(13, [char]'2')
        if ([int]$cybos.GetLimitRemainCount(1) -le 0) {
            Start-Sleep -Milliseconds ([Math]::Max(100, [int]$cybos.LimitRequestRemainTime + 100))
        }
        $returnCode = [int]$chart.BlockRequest()
        if ($returnCode -ne 0) { throw "BlockRequest return code $returnCode" }
        $status = [int]$chart.GetDibStatus()
        if ($status -ne 0) { throw "StockChart response status ${status}: $($chart.GetDibMsg1())" }
        $received = [int]$chart.GetHeaderValue(3)
        $result.received = $received
        $result.has_more = [bool]$chart.Continue
        if ($received -gt 0) {
            $result.newest = "$(($chart.GetDataValue(0, 0)).ToString('00000000'))$(($chart.GetDataValue(1, 0)).ToString('0000'))"
            $last = $received - 1
            $result.oldest = "$(($chart.GetDataValue(0, $last)).ToString('00000000'))$(($chart.GetDataValue(1, $last)).ToString('0000'))"
        }
        $result.status = if ($received -gt 0) { 'bars_returned' } else { 'no_bars' }
    }
    catch {
        $result.status = 'rejected_or_error'
        $result.error = $_.Exception.Message
    }
    finally {
        if ($null -ne $chart) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($chart)
        }
    }
    [IO.File]::AppendAllText($target, (($result | ConvertTo-Json -Compress) + [Environment]::NewLine), $utf8)
}
