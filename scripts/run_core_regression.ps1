$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pythonCandidates = [System.Collections.Generic.List[string]]::new()
$pythonCandidates.Add((Join-Path $repositoryRoot ".venv\Scripts\python.exe"))

# Codex worktree에는 .venv가 복제되지 않는다. 공통 Git 디렉터리의 부모가
# 원본 프로젝트이므로 그곳의 개발 가상환경을 두 번째 후보로 사용한다.
$gitCommonDirectory = & git -C $repositoryRoot rev-parse --path-format=absolute --git-common-dir 2>$null
if ($LASTEXITCODE -eq 0 -and $gitCommonDirectory) {
    $originalRepository = Split-Path -Parent $gitCommonDirectory.Trim()
    $pythonCandidates.Add((Join-Path $originalRepository ".venv\Scripts\python.exe"))
}

function Test-PythonExecutable([string]$candidate) {
    if (-not (Test-Path -LiteralPath $candidate)) {
        return $false
    }
    try {
        & $candidate --version *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$pythonExecutable = $pythonCandidates | Where-Object { Test-PythonExecutable $_ } | Select-Object -First 1
if (-not $pythonExecutable) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand -and (Test-PythonExecutable $pythonCommand.Source)) {
        $pythonExecutable = $pythonCommand.Source
    } else {
        $userPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
        if (Test-PythonExecutable $userPython) {
            $pythonExecutable = $userPython
        }
    }
}
if (-not $pythonExecutable) {
    throw "Python could not run in this environment. Check access restrictions with an approved run before concluding the installation is missing. See DEVELOPMENT_GUARDRAILS.md."
}

& $pythonExecutable (Join-Path $PSScriptRoot "run_regression.py") --profile core @args
exit $LASTEXITCODE
