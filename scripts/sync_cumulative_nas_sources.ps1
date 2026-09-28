param(
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$nasRoot = [IO.Path]::GetFullPath('X:\kiwoom-monitor')
$backupBase = [IO.Path]::GetFullPath('X:\kiwoom-monitor-backups')
if (-not (Test-Path -LiteralPath (Join-Path $nasRoot 'deploy\synology\docker-compose.yml'))) {
    throw "NAS project root is unavailable: $nasRoot"
}
if (-not (Test-Path -LiteralPath $backupBase)) {
    throw "NAS backup root is unavailable: $backupBase"
}

$excluded = @(
    '^(\.git|\.codex-backup|\.venv|__pycache__|node_modules|build|dist|release|outputs|runs|tmp|logs|htmlcov|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.local-news-audit)(/|$)',
    '^data(/|$)',
    '^deploy/synology/(\.env$|postgres-data/|server-data/|server-secrets/|release/)',
    '(^|/)([^/]+\.egg-info|\.idea|\.vscode)(/|$)',
    '(^|/)([^/]+\.db|[^/]+\.sqlite3?|[^/]+\.py[cod]|\.coverage|Thumbs\.db|\.DS_Store)$',
    '(^|/)\.env(\..*)?$',
    '^reports/.*\.json$',
    '^(scripts/extract_texty_lecture_pdfs\.py|scripts/ocr_lecture_pdfs\.ps1|resources/UpdateHelper\.exe|pyinstaller-build\.log)$',
    '\.inspect\.ndjson$'
)
$paths = [Collections.Generic.List[string]]::new()
$pendingDirectories = [Collections.Generic.Stack[string]]::new()
$pendingDirectories.Push($sourceRoot)
while ($pendingDirectories.Count -gt 0) {
    $directory = $pendingDirectories.Pop()
    try {
        $entries = @(Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop)
    } catch [System.UnauthorizedAccessException] {
        continue
    } catch [System.IO.IOException] {
        continue
    }
    foreach ($entry in $entries) {
        $relative = $entry.FullName.Substring($sourceRoot.Length + 1).Replace('\', '/')
        if ($entry.PSIsContainer) {
            if (-not ($excluded | Where-Object { $relative -match $_ } | Select-Object -First 1)) {
                $pendingDirectories.Push($entry.FullName)
            }
            continue
        }
        $isExampleEnv = $relative -eq '.env.example' -or $relative -eq 'deploy/synology/.env.example'
        if ($isExampleEnv -or -not ($excluded | Where-Object { $relative -match $_ } | Select-Object -First 1)) {
            $paths.Add($relative)
        }
    }
}
$paths = @($paths | Sort-Object -Unique)
$changes = [Collections.Generic.List[object]]::new()
foreach ($relative in $paths) {
    $local = [IO.Path]::GetFullPath((Join-Path $sourceRoot $relative))
    if (-not [IO.File]::Exists($local)) { continue }
    $target = [IO.Path]::GetFullPath((Join-Path $nasRoot $relative))
    if (-not $target.StartsWith($nasRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source path escapes NAS project: $relative"
    }
    $sourceHash = (Get-FileHash -LiteralPath $local -Algorithm SHA256).Hash
    $targetHash = if ([IO.File]::Exists($target)) {
        (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
    } else { $null }
    if ($sourceHash -ne $targetHash) {
        $changes.Add([pscustomobject]@{
            Relative = $relative
            Source = $local
            Target = $target
            SourceHash = $sourceHash
            ExistingHash = $targetHash
        })
    }
}
$preview = [pscustomobject]@{
    mode = if ($Apply) { 'apply' } else { 'preview' }
    compared = $paths.Count
    changed = $changes.Count
    existing_changed = @($changes | Where-Object ExistingHash).Count
    new_files = @($changes | Where-Object { -not $_.ExistingHash }).Count
    sample = @($changes | Select-Object -First 20 -ExpandProperty Relative)
}
$preview | ConvertTo-Json -Depth 4
if (-not $Apply -or $changes.Count -eq 0) { return }

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = [IO.Path]::GetFullPath((Join-Path $backupBase "${stamp}-diagnostic-session-control-v1"))
if (-not $backupRoot.StartsWith($backupBase + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "Backup path escapes backup root: $backupRoot"
}
foreach ($change in $changes) {
    if (-not $change.ExistingHash) { continue }
    $saved = [IO.Path]::GetFullPath((Join-Path $backupRoot $change.Relative))
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($saved)) | Out-Null
    [IO.File]::Copy($change.Target, $saved, $false)
    if ((Get-FileHash -LiteralPath $saved -Algorithm SHA256).Hash -ne $change.ExistingHash) {
        throw "Existing source backup did not verify: $($change.Relative)"
    }
}
foreach ($change in $changes) {
    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($change.Target)) | Out-Null
    [IO.File]::Copy($change.Source, $change.Target, $true)
    if ((Get-FileHash -LiteralPath $change.Target -Algorithm SHA256).Hash -ne $change.SourceHash) {
        throw "NAS source copy did not verify: $($change.Relative)"
    }
}
[pscustomobject]@{ state = 'verified'; changed = $changes.Count; backup = $backupRoot } |
    ConvertTo-Json -Depth 3
