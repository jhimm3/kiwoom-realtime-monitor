param(
    [Parameter(Mandatory = $true)][string]$Archive,
    [Parameter(Mandatory = $true)][string]$Target,
    [Parameter(Mandatory = $true)][string]$Executable,
    [int]$WaitProcessId = 0
)

$ErrorActionPreference = 'Stop'
$updates = Split-Path -Parent $Archive
$staging = Join-Path $updates 'test-staging'
$log = Join-Path $updates 'test-apply-update.log'

function Write-UpdateLog([string]$Message) {
    ('{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message) | Add-Content -Path $log -Encoding UTF8
}

Write-UpdateLog '테스트 업데이트 도우미 시작'
try {
    Add-Type -AssemblyName PresentationFramework
    Add-Type -AssemblyName System.Windows.Forms
    $window = New-Object Windows.Window
    $window.Title = '키움 실시간 모니터 업데이트 테스트'
    $window.Width = 420
    $window.Height = 145
    $window.ResizeMode = 'NoResize'
    $window.WindowStartupLocation = 'CenterScreen'
    $panel = New-Object Windows.Controls.StackPanel
    $panel.Margin = '22'
    $status = New-Object Windows.Controls.TextBlock
    $status.Text = '앱 종료를 기다리고 있습니다…'
    $bar = New-Object Windows.Controls.ProgressBar
    $bar.Height = 20
    $bar.Margin = '0,14,0,0'
    $bar.Minimum = 0
    $bar.Maximum = 100
    $panel.Children.Add($status) | Out-Null
    $panel.Children.Add($bar) | Out-Null
    $window.Content = $panel
    $window.Show() | Out-Null
    [Windows.Forms.Application]::DoEvents()

    while ($WaitProcessId -gt 0 -and (Get-Process -Id $WaitProcessId -ErrorAction SilentlyContinue)) {
        Start-Sleep -Milliseconds 100
        [Windows.Forms.Application]::DoEvents()
    }
    Write-UpdateLog '앱 종료 확인'
    $status.Text = '업데이트 파일을 준비하고 있습니다…'
    [Windows.Forms.Application]::DoEvents()
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $Archive -DestinationPath $staging -Force
    $manifest = Get-Content (Join-Path $staging 'update_manifest.json') -Raw | ConvertFrom-Json
    $total = ($manifest.changed | ForEach-Object { (Get-Item (Join-Path $staging $_)).Length } | Measure-Object -Sum).Sum
    $done = 0
    foreach ($file in $manifest.changed) {
        $source = Join-Path $staging $file
        $destination = Join-Path $Target $file
        $status.Text = '파일을 교체하고 있습니다…'
        New-Item -ItemType Directory -Force -Path (Split-Path $destination) | Out-Null
        Copy-Item $source $destination -Force
        Write-UpdateLog ('교체 완료: ' + $file)
        $done += (Get-Item $source).Length
        $bar.Value = [Math]::Min(100, [Math]::Round(100 * $done / [Math]::Max(1, $total)))
        [Windows.Forms.Application]::DoEvents()
    }
    foreach ($file in $manifest.deleted) {
        Remove-Item (Join-Path $Target $file) -Force -ErrorAction SilentlyContinue
        Write-UpdateLog ('삭제 처리: ' + $file)
    }
    $bar.Value = 100
    $status.Text = '업데이트를 마무리하고 있습니다…'
    [Windows.Forms.Application]::DoEvents()
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
    Write-UpdateLog '테스트 업데이트 완료'
    Start-Process -FilePath $Executable
    $window.Close()
} catch {
    $message = '테스트 업데이트에 실패했습니다. ' + $_.Exception.Message
    Write-UpdateLog $message
    [Windows.Forms.MessageBox]::Show($message, '키움 실시간 모니터 업데이트 테스트') | Out-Null
}
