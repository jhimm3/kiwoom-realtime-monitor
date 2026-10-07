param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = 'Stop'
if ([Environment]::Is64BitProcess) { throw '32-bit PowerShell is required.' }
$cybos = New-Object -ComObject CpUtil.CpCybos
if ([int]$cybos.IsConnect -ne 1) { throw 'CREON Plus is disconnected.' }
$manager = New-Object -ComObject CpUtil.CpCodeMgr
$codes = Get-Content -LiteralPath $InputPath -Raw -Encoding UTF8 | ConvertFrom-Json
$rows = foreach ($item in $codes) {
    $code = [string]$item.code
    $name = ''
    $etnName = ''
    $errorText = ''
    try { $name = [string]$manager.CodeToName("A$code") }
    catch { $errorText = $_.Exception.Message }
    try { $etnName = [string]$manager.CodeToName("Q$code") }
    catch { $errorText += " Q: $($_.Exception.Message)" }
    [ordered]@{ code = $code; current_name = $name; etn_name = $etnName; lookup_error = $errorText }
}
$output = [ordered]@{
    checked_at = [DateTimeOffset]::UtcNow.ToString('o')
    connected = $true
    rows = @($rows)
}
[IO.File]::WriteAllText(
    [IO.Path]::GetFullPath($OutputPath),
    ($output | ConvertTo-Json -Depth 5),
    [Text.UTF8Encoding]::new($false)
)
