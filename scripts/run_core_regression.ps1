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
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        & $candidate --version *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousErrorAction
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

$testModules = @(
    "tests.unit.test_kiwoom_rest_client",
    "tests.unit.test_central_rest_broker",
    "tests.unit.test_ranking_service",
    "tests.unit.test_ranking_schedule",
    "tests.unit.test_ranking_execution",
    "tests.unit.test_ranking_worker_controller",
    "tests.unit.test_market_session_schedule",
    "tests.unit.test_realtime_subscription",
    "tests.unit.test_realtime_worker_controller",
    "tests.unit.test_market_data_finalization",
    "tests.unit.test_daily_high_worker_controller",
    "tests.unit.test_fundamentals_worker_controller",
    "tests.unit.test_google_drive_worker_controller",
    "tests.unit.test_historical_high_worker_controller",
    "tests.unit.test_image_theme_ocr_worker_controller",
    "tests.unit.test_krx_stock_catalog_worker_controller",
    "tests.unit.test_minute_history_worker_controller",
    "tests.unit.test_new_high_worker_controller",
    "tests.unit.test_nxt_eligibility_worker_controller",
    "tests.unit.test_secondary_data_schedule",
    "tests.unit.test_realtime",
    "tests.unit.test_central_realtime_hub",
    "tests.unit.test_central_realtime_collector",
    "tests.unit.test_central_server_app",
    "tests.unit.test_central_server_database",
    "tests.unit.test_central_database_codec",
    "tests.unit.test_central_schema",
    "tests.unit.test_central_schema_migrations",
    "tests.unit.test_minute_trade_value",
    "tests.unit.test_top20_trade_value_collector",
    "tests.unit.test_top20_market_repair_worker_controller",
    "tests.unit.test_update_worker_controller",
    "tests.unit.test_autonomous_top20",
    "tests.unit.test_external_market_collector",
    "tests.unit.test_minute_bar_repository",
    "tests.unit.test_theme_matching",
    "tests.unit.test_theme_ranking",
    "tests.unit.test_theme_dialogs",
    "tests.unit.test_main_table_formatting",
    "tests.unit.test_main_table_column_controller",
    "tests.unit.test_main_window_layout",
    "tests.unit.test_theme_color_repository",
    "tests.unit.test_journal_database",
    "tests.unit.test_snapshot_provenance",
    "tests.unit.test_market_data_contract",
    "tests.unit.test_market_data_coverage",
    "tests.unit.test_market_data_metadata_repository",
    "tests.unit.test_journal_snapshot_service",
    "tests.unit.test_trade_snapshot_context",
    "tests.unit.test_trade_review_formatting",
    "tests.unit.test_trade_review_view_model",
    "tests.unit.test_strategy_review_context",
    "tests.unit.test_trade_strategy_coordinator",
    "tests.unit.test_trade_analysis_preparation_service",
    "tests.unit.test_trade_episode_analysis_service",
    "tests.unit.test_trade_history_query_service",
    "tests.unit.test_trade_group_edit_service",
    "tests.unit.test_journal_schema",
    "tests.unit.test_schema_migrations",
    "tests.unit.test_sqlite_connections",
    "tests.unit.test_trade_chart",
    "tests.unit.test_journal_chart_style",
    "tests.unit.test_detached_chart_window",
    "tests.unit.test_journal_detached_flow",
    "tests.unit.test_journal_workers",
    "tests.unit.test_personal_trade_rules",
    "tests.unit.test_strategy_pack",
    "tests.unit.test_strategy_pack_extraction",
    "tests.unit.test_strategy_pack_dialog",
    "tests.unit.test_journal_settings_dialogs",
    "tests.unit.test_news_ai_repository",
    "tests.unit.test_news_database",
    "tests.unit.test_stock_news_repository",
    "tests.unit.test_news_auto_analysis",
    "tests.unit.test_news_execution",
    "tests.unit.test_news_view_model",
    "tests.unit.test_central_news_service",
    "tests.unit.test_central_ai_service",
    "tests.unit.test_central_content_sync",
    "tests.unit.test_central_journal_sync",
    "tests.unit.test_central_settings_sync",
    "tests.unit.test_central_theme_sync",
    "tests.unit.test_central_sync_utils",
    "tests.unit.test_process_control"
)

$env:PYTHONPATH = Join-Path $repositoryRoot "src"
$journalBatchStart = [Array]::IndexOf($testModules, "tests.unit.test_theme_color_repository")
if ($journalBatchStart -le 0) {
    throw "Core regression batch boundary was not found."
}

# 수백 개 Qt 테스트를 한 인터프리터에 누적하면 각 묶음은 성공해도 Windows의
# QApplication 종료 단계가 간헐적으로 코드 1로 끝난다. 제품 프로세스 경계를
# 따라 메인/NAS와 매매일지/뉴스 묶음을 별도 인터프리터에서 검증한다.
$mainBatch = $testModules[0..($journalBatchStart - 1)]
$journalBatch = $testModules[$journalBatchStart..($testModules.Count - 1)]

& $pythonExecutable -m unittest @mainBatch
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $pythonExecutable -m unittest @journalBatch
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
