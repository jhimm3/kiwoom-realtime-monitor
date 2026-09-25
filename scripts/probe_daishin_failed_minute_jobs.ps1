param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = 'Stop'
if ([Environment]::Is64BitProcess) { throw '32-bit PowerShell is required.' }
$cybos = New-Object -ComObject CpUtil.CpCybos
if ([int]$cybos.IsConnect -ne 1) { throw 'CREON Plus is disconnected.' }
$codes = Get-Content -LiteralPath $InputPath -Raw -Encoding UTF8 | ConvertFrom-Json
$target = [IO.Path]::GetFullPath($OutputPath)
if ([IO.File]::Exists($target)) { throw "OutputPath already exists: $target" }
$utf8 = [Text.UTF8Encoding]::new($false)
$toDate = [int](Get-Date).ToString('yyyyMMdd')
foreach ($item in $codes) {
    $code = [string]$item.code
    $result = [ordered]@{
        code = $code
        checked_at = [DateTimeOffset]::UtcNow.ToString('o')
        status = ''
        received = 0
        has_more = $false
        error = ''
    }
    try {
        $chart = New-Object -ComObject CpSysDib.StockChart
        [int[]]$fields = @(0, 1, 2, 3, 4, 5, 8, 9)
        $chart.SetInputValue(0, "A$code")
        $chart.SetInputValue(1, [char]'1')
        $chart.SetInputValue(2, $toDate)
        $chart.SetInputValue(3, [int]19000101)
        $chart.SetInputValue(5, $fields)
        $chart.SetInputValue(6, [char]'m')
        $chart.SetInputValue(7, [int]1)
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
        $dibStatus = [int]$chart.GetDibStatus()
        if ($dibStatus -ne 0) { throw "StockChart response status ${dibStatus}: $($chart.GetDibMsg1())" }
        $result.received = [int]$chart.GetHeaderValue(3)
        $result.has_more = [bool]$chart.Continue
        $result.status = if ($result.received -gt 0) { 'bars_returned' } else { 'no_bars' }
    }
    catch {
        $result.status = 'rejected_or_error'
        $result.error = $_.Exception.Message
    }
    finally {
        if ($null -ne $chart) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($chart)
            $chart = $null
        }
    }
    [IO.File]::AppendAllText($target, (($result | ConvertTo-Json -Compress) + [Environment]::NewLine), $utf8)
}
