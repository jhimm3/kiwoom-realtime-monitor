param([string]$OutputPath = 'data\historical_collection\creon-connection-check.json')

$ErrorActionPreference = 'Stop'
$target = [IO.Path]::GetFullPath($OutputPath)
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
$result = [ordered]@{
    checked_at = [DateTimeOffset]::UtcNow.ToString('o')
    pid = $PID
    is_64_bit = [Environment]::Is64BitProcess
    is_admin = [Security.Principal.WindowsPrincipal]::new(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    is_connected = $false
    error = ''
}
try {
    $cybos = New-Object -ComObject CpUtil.CpCybos
    $result.is_connected = [int]$cybos.IsConnect -eq 1
}
catch {
    $result.error = $_.Exception.Message
}
[IO.File]::WriteAllText($target, ($result | ConvertTo-Json -Depth 3), [Text.UTF8Encoding]::new($false))
