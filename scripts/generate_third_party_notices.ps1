<#
  Build a redistributable third-party notice bundle from the locked runtime
  environment. Run after installing requirements.lock.txt into .venv.

  Output:
    licenses/THIRD_PARTY_NOTICE_INDEX.txt
    licenses/third_party/<distribution>/<original license file>
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..'))
)

$ErrorActionPreference = 'Stop'
$sitePackages = Join-Path $ProjectRoot '.venv\Lib\site-packages'
$requirementsPath = Join-Path $ProjectRoot 'requirements.lock.txt'
$licensesRoot = Join-Path $ProjectRoot 'licenses'
$outputRoot = Join-Path $licensesRoot 'third_party'
$indexPath = Join-Path $licensesRoot 'THIRD_PARTY_NOTICE_INDEX.txt'

if (-not (Test-Path -LiteralPath $sitePackages)) { throw ".venv site-packages를 찾을 수 없습니다: $sitePackages" }
if (-not (Test-Path -LiteralPath $requirementsPath)) { throw "잠금 목록을 찾을 수 없습니다: $requirementsPath" }

function Normalize-DistributionName([string]$Name) { return ($Name.ToLowerInvariant() -replace '[-_.]+', '-') }
function Get-MetadataValue([string]$MetadataPath, [string]$Field) {
    $match = Select-String -LiteralPath $MetadataPath -Pattern ("^{0}:\s*(.+)$" -f [regex]::Escape($Field)) | Select-Object -First 1
    if ($null -eq $match) { return '' }
    return $match.Matches[0].Groups[1].Value.Trim()
}

$required = @{}
Get-Content -LiteralPath $requirementsPath | ForEach-Object {
    if ($_ -match '^([A-Za-z0-9_.-]+)==([^\s#]+)') { $required[(Normalize-DistributionName $Matches[1])] = $Matches[2] }
}

$resolved = @{}
Get-ChildItem -LiteralPath $sitePackages -Directory -Filter '*.dist-info' | ForEach-Object {
    $metadataPath = Join-Path $_.FullName 'METADATA'
    if (-not (Test-Path -LiteralPath $metadataPath)) { return }
    $name = Get-MetadataValue $metadataPath 'Name'
    $normalized = Normalize-DistributionName $name
    if ($name -and $required.ContainsKey($normalized)) {
        $resolved[$normalized] = [PSCustomObject]@{ Name=$name; Version=(Get-MetadataValue $metadataPath 'Version'); License=(Get-MetadataValue $metadataPath 'License'); Directory=$_.FullName }
    }
}

if (Test-Path -LiteralPath $outputRoot) { Remove-Item -LiteralPath $outputRoot -Recurse -Force }
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$missingPackages = [System.Collections.Generic.List[string]]::new()
$packagesWithoutFiles = [System.Collections.Generic.List[string]]::new()
$index = [System.Collections.Generic.List[string]]::new()
$index.Add('키움 실시간 종목 모니터 - 제3자 라이선스 원문 묶음')
$index.Add('생성 기준: requirements.lock.txt 및 현재 .venv 설치 환경')
$index.Add(('생성 시각: {0:yyyy-MM-dd HH:mm:ss zzz}' -f (Get-Date)))
$index.Add('')
$index.Add('이 폴더는 설치본에 포함된 런타임 의존성의 원본 LICENSE / NOTICE / COPYING / AUTHORS 파일을 가능한 한 그대로 복사합니다.')
$index.Add('패키지 메타데이터에 원문 파일이 없는 항목은 아래 누락 목록에 남습니다. 배포 전 해당 프로젝트의 라이선스를 별도로 확인하십시오.')
$index.Add('')

foreach ($normalized in ($required.Keys | Sort-Object)) {
    if (-not $resolved.ContainsKey($normalized)) { $missingPackages.Add("$normalized==$($required[$normalized]) (현재 .venv에서 찾지 못함)"); continue }
    $package = $resolved[$normalized]
    $packageFolderName = "{0}-{1}" -f $package.Name, $package.Version
    $packageFolder = Join-Path $outputRoot $packageFolderName
    $candidateFiles = Get-ChildItem -LiteralPath $package.Directory -Recurse -File | Where-Object {
        $_.Name -match '^(LICENSE|LICENCE|NOTICE|COPYING|AUTHORS)([._-].*)?$' -or $_.DirectoryName -match '[\\/]licenses?$'
    }
    $index.Add(('## {0} {1}' -f $package.Name, $package.Version))
    $index.Add(('Declared license: {0}' -f $(if ($package.License) { $package.License } else { 'not declared in METADATA' })))
    if ($candidateFiles.Count -eq 0) {
        $apacheLicense = Join-Path $licensesRoot 'Apache-2.0.txt'
        if ($package.License -match '(?i)apache' -and (Test-Path -LiteralPath $apacheLicense)) {
            New-Item -ItemType Directory -Path $packageFolder -Force | Out-Null
            Copy-Item -LiteralPath $apacheLicense -Destination (Join-Path $packageFolder 'LICENSE') -Force
            $index.Add('License text: third_party/{0}/LICENSE (standard Apache-2.0 text; this installed distribution did not ship a separate file)' -f $packageFolderName)
            $index.Add('')
            continue
        }
        $packagesWithoutFiles.Add("$($package.Name)==$($package.Version)")
        $index.Add('Original notice file: not present in this installed distribution')
        $index.Add('')
        continue
    }
    New-Item -ItemType Directory -Path $packageFolder -Force | Out-Null
    foreach ($file in $candidateFiles) {
        $relative = $file.FullName.Substring($package.Directory.Length).TrimStart('\\')
        $target = Join-Path $packageFolder $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
        $index.Add(('Original notice file: third_party/{0}/{1}' -f $packageFolderName, $relative))
    }
    $index.Add('')
}

$qtLgplPath = Join-Path $licensesRoot 'GNU_LGPL-3.0.txt'
$index.Add('## Qt for Python / PySide6 추가 고지')
$index.Add('PySide6, PySide6_Addons, PySide6_Essentials, shiboken6은 LGPL-3.0-only 또는 GPL/상용 라이선스 선택지로 배포된다.')
if (Test-Path -LiteralPath $qtLgplPath) {
    $index.Add('LGPL-3.0 전문: GNU_LGPL-3.0.txt')
} else {
    $index.Add('LGPL-3.0 전문: 누락됨 — GNU_LGPL-3.0.txt를 추가해야 함')
}
$index.Add('')

$index.Add('## 현재 환경에서 찾지 못한 잠금 패키지')
if ($missingPackages.Count -eq 0) { $index.Add('없음') } else { $missingPackages | ForEach-Object { $index.Add("- $_") } }
$index.Add('')
$index.Add('## 원문 파일이 제공되지 않은 설치 패키지')
if ($packagesWithoutFiles.Count -eq 0) { $index.Add('없음') } else { $packagesWithoutFiles | ForEach-Object { $index.Add("- $_") } }
Set-Content -LiteralPath $indexPath -Value $index -Encoding utf8
Write-Host "완료: $indexPath"
