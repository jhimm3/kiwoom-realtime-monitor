from __future__ import annotations

from kiwoom_monitor.domain.time_basis import as_kst

import sys
import argparse
import logging
import ctypes
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.central_server_process import LocalCentralServerProcess
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient
from kiwoom_monitor.infrastructure.central_operational_settings import CentralOperationalSettingsClient
from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
from kiwoom_monitor.infrastructure.central_theme_sync import CentralThemeSyncDispatcher
from kiwoom_monitor.infrastructure.central_settings_sync import CentralSettingsSyncService
from kiwoom_monitor.infrastructure.logging_config import configure_logging
from kiwoom_monitor.infrastructure.persistence.database import DEFAULT_SETTINGS, Database
from kiwoom_monitor.infrastructure.persistence.settings_repository import SettingsRepository
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomApiError
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker import CentralRealtimeWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.client_factory import create_query_client
from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import MinuteHistoryWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.fundamentals_worker import FundamentalsWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.historical_high_worker import HistoricalHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.nxt_eligibility_worker import NxtEligibilityWorker
from kiwoom_monitor.application.ranking_service import RankingService
from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.application.stock_fundamentals_service import StockFundamentalsService
from kiwoom_monitor.application.daily_high_service import DailyHighService
from kiwoom_monitor.application.daily_bar_coverage import daily_query_date
from kiwoom_monitor.application.historical_high_service import (
    HISTORICAL_HIGH_BASIS_VERSION, HistoricalHighService,
)
from kiwoom_monitor.application.nxt_eligibility_service import NxtEligibilityService
from kiwoom_monitor.application.investor_flow_service import InvestorFlowService
from kiwoom_monitor.application.program_trade_service import ProgramTradeService
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository as DatabaseThemeRepository
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
from kiwoom_monitor.infrastructure.persistence.google_drive_sync import (
    CURRENT_BACKUP_SET, GoogleDriveSyncService, has_pending_google_drive_changes,
)
from kiwoom_monitor.infrastructure.persistence.strict_restore import (
    RestoreOutcome, StrictRestoreCoordinator, StrictRestoreError,
)
from kiwoom_monitor.infrastructure.persistence.news_database import migrate_legacy_news_database
from kiwoom_monitor.presentation.main_window import APP_DISPLAY_NAME, MainWindow
from kiwoom_monitor.presentation.settings_request_worker import SettingsRequestWorker


def _application_icon_path() -> Path:
    """실행 환경에 맞는 공통 프로그램 아이콘 위치를 반환한다."""
    if getattr(sys, "frozen", False):
        # PyInstaller 폴더형 배포에서는 포함 데이터가 _internal 아래에 놓인다.
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return bundle_root / "resources" / "app_icon.png"
    return Path(__file__).resolve().parents[2] / "resources" / "app_icon.png"


def _set_taskbar_app_id() -> None:
    """Keep the development/test app separate from the installed app on Windows."""
    if sys.platform != "win32":
        return
    app_id = "Kuni.KiwoomRealtimeMonitor" if getattr(sys, "frozen", False) else "Kuni.KiwoomRealtimeMonitor.Test"
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        # 작업 표시줄 분류 실패는 프로그램 실행에 영향을 주지 않는다.
        pass


def main() -> None:
    if "--central-server" in sys.argv:
        from kiwoom_monitor.central_server.__main__ import main as central_server_main
        index = sys.argv.index("--central-server")
        original = sys.argv
        try:
            sys.argv = [original[0], *original[index + 1:]]
            raise SystemExit(central_server_main())
        finally:
            sys.argv = original
    if "--journal-process" in sys.argv:
        from kiwoom_monitor.journal_process import main as journal_main
        index = sys.argv.index("--journal-process")
        raise SystemExit(journal_main(sys.argv[index + 1:]))
    if "--news-process" in sys.argv:
        from kiwoom_monitor.news_process import main as news_main
        index = sys.argv.index("--news-process")
        raise SystemExit(news_main(sys.argv[index + 1:]))
    if "--research-process" in sys.argv:
        from kiwoom_monitor.research_process import main as research_main
        index = sys.argv.index("--research-process")
        raise SystemExit(research_main(sys.argv[index + 1:]))
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--query-data-dir", type=Path)
    parser.add_argument("--query-settings-role")
    parser.add_argument("--full-data-dir")
    parser.add_argument("--full-settings-role")
    options, _ = parser.parse_known_args(sys.argv[1:])
    selected = options.query_data_dir is not None or options.query_settings_role is not None
    full_selected = options.full_data_dir is not None or options.full_settings_role is not None
    if full_selected and (selected or "--query-only" in sys.argv or not options.full_data_dir
                          or not options.full_data_dir.strip() or not options.full_settings_role
                          or not options.full_settings_role.strip()):
        parser.error("--full-data-dir and --full-settings-role require full desktop mode together")
    if selected and ("--query-only" not in sys.argv or options.query_data_dir is None
                     or options.query_settings_role is None):
        parser.error("--query-data-dir and --query-settings-role require --query-only together")
    if full_selected:
        _main_with_restore_gate(full_data_dir=Path(options.full_data_dir), settings_role=options.full_settings_role)
    elif selected:
        _main_with_restore_gate(query_only=True, query_data_dir=options.query_data_dir,
                                settings_role=options.query_settings_role)
    elif "--query-only" in sys.argv:
        _main_with_restore_gate(query_only=True)
    else:
        _main_with_restore_gate()


def _main_with_restore_gate(
    *, query_only: bool = False, query_data_dir: Path | None = None,
    settings_role: str | None = None, full_data_dir: Path | None = None,
) -> None:
    _set_taskbar_app_id()
    try:
        if full_data_dir is not None and (query_only or query_data_dir is not None):
            raise ValueError("전체 앱 역할과 조회 전용 역할을 함께 선택할 수 없습니다.")
        selected_root = full_data_dir if full_data_dir is not None else query_data_dir
        if (selected_root is None) != (settings_role is None) or (query_data_dir is not None and not query_only):
            raise ValueError("조회 역할 폴더와 설정 역할을 함께 명시해야 합니다.")
        paths = (AppPaths.for_full_role(full_data_dir) if full_data_dir is not None else
                 AppPaths.for_query_role(query_data_dir) if query_data_dir is not None else AppPaths.for_current_user())
        configure_logging(paths.log_dir)
        coordinator = StrictRestoreCoordinator(
            paths.database_path, paths.news_database_path,
            **({"expected_backup_set": CURRENT_BACKUP_SET} if full_data_dir is not None else {}),
        )
        if full_data_dir is not None:
            lease, restore_outcome = coordinator.enter_main(
                startup_preparation=lambda: paths.desktop_startup_preparation(copy_legacy_personal=False),
                restore_target_check=lambda: Database(paths.database_path).initialize_full_role(
                    role=settings_role, allow_create=False,
                ),
            )
        elif query_data_dir is not None:
            # A role root shares the existing folder lifetime protection, but
            # cannot apply a legacy full-app backup into its fresh cache schema.
            lease, restore_outcome = coordinator.enter_main(
                startup_preparation=lambda: paths.desktop_startup_preparation(query_only=True),
                allow_restore=False,
            )
        elif paths.desktop_startup_preparation(query_only=query_only) is None:
            lease, restore_outcome = coordinator.enter_main()
        else:
            lease, restore_outcome = coordinator.enter_main(
                startup_preparation=lambda: paths.desktop_startup_preparation(query_only=query_only),
            )
    except (StrictRestoreError, OSError, sqlite3.Error, ValueError) as error:
        logging.getLogger(__name__).error("시작 전 복원 경계 확인 실패: %s", error)
        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName(APP_DISPLAY_NAME)
        QMessageBox.critical(None, "Google Drive 복원 중단", str(error))
        return
    try:
        if full_data_dir is not None:
            _run_desktop(paths, restore_outcome, settings_role=settings_role, settings_layout="full")
        elif query_data_dir is not None:
            _run_desktop(paths, restore_outcome, query_only=True, settings_role=settings_role)
        elif query_only:
            _run_desktop(paths, restore_outcome, query_only=True)
        else:
            _run_desktop(paths, restore_outcome)
    finally:
        lease.close()


def _run_desktop(
    paths: AppPaths, restore_outcome: RestoreOutcome | None, *, query_only: bool = False,
    settings_role: str | None = None, settings_layout: str = "query",
) -> None:
    # UI-owned research children inherit the same data-folder lease boundary.
    if not query_only:
        os.environ["KIWOOM_DESKTOP_DATA_DIR"] = str(paths.data_dir.resolve())

    if settings_role is None:
        database = Database(paths.database_path)
        database.initialize()
        settings = database.settings
        columns = ColumnSettingsRepository(paths.database_path)
    else:
        if (settings_layout == "query" and not query_only) or (settings_layout == "full" and query_only):
            raise ValueError("명시한 조회 설정 역할은 조회 전용 기동에서만 사용할 수 있습니다.")
        if settings_layout not in {"query", "full"}:
            raise ValueError("알 수 없는 데스크톱 저장 역할입니다.")
        # Reuse the native owners before any cache, API or UI worker can start.
        stage = "monitor.sqlite3" if settings_layout == "full" else "query-role.sqlite3"
        role_label = "전체 앱" if settings_layout == "full" else "조회"
        try:
            if settings_layout == "full":
                Database(paths.database_path).initialize_full_role(role=settings_role)
            else:
                Database(paths.database_path).initialize_query_role(role=settings_role)
            settings = SettingsRepository(paths.database_path, strict_text=True)
            stage = "rank_query_type"
            RankingService.validate_query_type(settings.get("rank_query_type"))
            stage = "column_settings"
            columns = ColumnSettingsRepository(
                paths.database_path,
                column_names=tuple(name for name, _ in MainWindow.COLUMNS), strict_values=True,
            )
            # One startup-only safety read prevents a partial MainWindow
            # constructor from owning an unreturned Controller/writer on failure.
            columns.list()
        except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as error:
            logging.getLogger(__name__).error("%s 역할 %s 미수락: %s", role_label, stage, error)
            app = QApplication.instance() or QApplication(sys.argv)
            app.setApplicationName(APP_DISPLAY_NAME)
            QMessageBox.critical(None, f"{role_label} 설정 미적용",
                                 f"{stage} 원자료를 적용할 수 없습니다. 저장된 원자료를 보존합니다.\n{error}")
            return
    local_central_server: LocalCentralServerProcess | None = None
    connection_path = DataSourceConfig.connection_path(paths.data_dir / "api.env", query_only=query_only)
    configured_data_source = DataSourceSettings()
    try:
        configured_data_source = DataSourceConfig(connection_path).load()
        if configured_data_source.mode == "local_server" and not query_only:
            # Owned full schemas exclude central tables. Keep the existing
            # combined legacy store until that default path is transitioned.
            local_server_database = (
                paths.central_database_path if settings_role is not None and settings_layout == "full"
                else paths.database_path
            )
            local_central_server = LocalCentralServerProcess(
                configured_data_source, paths.data_dir / "api.env", local_server_database,
            )
            local_central_server.start()
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        logging.getLogger(__name__).warning("로컬 중앙 서버를 시작하지 못했습니다: %s", error)
        if configured_data_source.mode == "local_server":
            if local_central_server is not None:
                local_central_server.stop()
            local_central_server = None
    if not query_only:
        migrate_legacy_news_database(paths.database_path, paths.news_database_path)
    central_content_thread: threading.Thread | None = None
    central_theme_sync: CentralThemeSyncDispatcher | None = None
    central_settings_sync: CentralSettingsSyncService | None = None
    candidate_client: CentralContentClient | None = None
    candidate_settings_client: CentralOperationalSettingsClient | None = None
    mock_automation_credentials_client: CentralCredentialsClient | None = None
    if not query_only and configured_data_source.mode in {"local_server", "personal_server"} and not (
        configured_data_source.mode == "local_server" and local_central_server is None
    ):
        central_content_service = CentralContentSyncService(CentralContentClient(
            configured_data_source.server_url, configured_data_source.access_token,
        ))
        central_theme_sync = CentralThemeSyncDispatcher(
            central_content_service, paths.database_path,
        )

        def sync_central_content() -> None:
            try:
                sync_news_catalog = configured_data_source.mode != "personal_server"
                # Theme publication/recovery has one owner. A theme conflict
                # preserves its pending material while independent news can sync.
                if central_theme_sync is not None and not central_theme_sync.flush_pending(force=True):
                    logging.getLogger(__name__).warning("테마 수락 동기화 대기: 로컬 자료를 유지합니다.")
                seed_marker = paths.data_dir / ".central_content_seeded"
                needs_manifest_seed = (
                    seed_marker.exists()
                    and not central_content_service.push_manifest_exists(paths.news_database_path)
                )
                pulled = central_content_service.pull(
                    paths.database_path, paths.news_database_path,
                    sync_news_catalog=sync_news_catalog,
                )
                if seed_marker.exists():
                    if needs_manifest_seed:
                        central_content_service.seed_push_manifest(
                            paths.database_path, paths.news_database_path, allow_existing=True,
                            sync_news_catalog=sync_news_catalog,
                        )
                    logging.getLogger(__name__).info(
                        "중앙 콘텐츠 시작 동기화 완료: 내려받기 %s건 · 초기 업로드 생략", pulled.total,
                    )
                else:
                    pushed = central_content_service.push(
                        paths.database_path, paths.news_database_path,
                        sync_news_catalog=sync_news_catalog,
                    )
                    seed_marker.write_text("1\n", encoding="utf-8")
                    logging.getLogger(__name__).info(
                        "중앙 콘텐츠 최초 이전 완료: 내려받기 %s건, 보존 %s건", pulled.total, pushed.total,
                    )
            except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
                logging.getLogger(__name__).warning("중앙 콘텐츠 이전을 건너뜁니다: %s", error)

        central_content_thread = threading.Thread(
            target=sync_central_content, name="central-content-sync", daemon=True,
        )
        central_content_thread.start()
        central_settings_sync = CentralSettingsSyncService(CentralContentClient(
            configured_data_source.server_url, configured_data_source.access_token,
        ))
        candidate_client = CentralContentClient(
            configured_data_source.server_url, configured_data_source.access_token,
            timeout_seconds=5.0,
        )
        candidate_settings_client = CentralOperationalSettingsClient(configured_data_source)
        mock_automation_credentials_client = CentralCredentialsClient(
            configured_data_source, provider="kiwoom_mock",
        )
    minute_bar_repository = MinuteBarRepository(paths.database_path)
    daily_bar_repository = DailyBarRepository(paths.database_path)
    google_drive_sync = None
    local_changes_are_newer = False
    if not query_only:
        google_drive_sync = GoogleDriveSyncService(
            paths.database_path, paths.news_database_path, backup_set=CURRENT_BACKUP_SET,
        )
        local_changed_at = settings.get("google_drive_local_changed_at")
        last_upload_at = settings.get("google_drive_last_upload_success_at")
        local_changes_are_newer = has_pending_google_drive_changes(
            local_changed_at, last_upload_at,
            unsynced=settings.get("google_drive_unsynced_changes") == "1",
        )
    # 시작 시에는 내용 다운로드 전에 Drive 수정 시각만 먼저 확인한다. 로컬 변경이
    # 남은 경우에도 원격 변경과 충돌인지 판별해야 하므로 자동 업로드 대상이면 확인한다.
    initial_google_drive_download = google_drive_sync is not None and google_drive_sync.connected and (
        settings.get("google_drive_auto_download") == "1"
        or (local_changes_are_newer and settings.get("google_drive_auto_upload") == "1")
    )

    def _close_unpublished_api_observers(
        error: BaseException, owned_observers: tuple[object, ...],
        owned_completions: tuple[tuple[object, object], ...],
    ) -> BaseException | None:
        # These exact fresh observers have no published callbacks or inputs.
        # Attempt every owned close and preserve each separate fact/error.
        cleanup_results = []
        first_cleanup_error = None
        for observer in owned_observers:
            close_error = None
            lifecycle = None
            observation_error = None
            try:
                observer.close()
            except BaseException as caught:
                close_error = caught
                if first_cleanup_error is None:
                    first_cleanup_error = caught
            try:
                lifecycle = observer.comparison_lifecycle_state()
            except BaseException as caught:
                observation_error = caught
            cleanup_results.append((observer, close_error, lifecycle, observation_error))
        setattr(error, "api_runtime_observer_cleanup", tuple(cleanup_results))
        completion_results = []
        first_completion_error = None
        for observer, finish in owned_completions:
            completion = None
            completion_error = None
            try:
                # This exact normal native capability was acquired with its
                # observer, independently of legacy close and fact getters.
                completion = finish()
                if (completion.owner is not observer or completion.native_completed is not True
                        or (completion.wait_error is not None and
                            not isinstance(completion.wait_error, BaseException))):
                    raise RuntimeError("comparison native completion is unconfirmed")
            except BaseException as caught:
                completion_error = caught
                if first_completion_error is None:
                    first_completion_error = caught
            completion_results.append((observer, completion, completion_error))
            if first_cleanup_error is None:
                first_cleanup_error = completion_error or (
                    completion.wait_error if completion is not None else None
                )
        setattr(error, "api_runtime_observer_completion", tuple(completion_results))
        if first_completion_error is not None:
            # A corrupted/unsupported capability has supplied no native proof.
            # Do not reclassify it or busy-loop on an unchanged invalid result.
            raise error from first_completion_error
        return first_cleanup_error

    def build_api_runtime() -> dict[str, object]:
        """현재 저장된 API 설정으로 작업 객체 묶음을 새로 만든다."""
        local_api = paths.data_dir / "api.env"
        # API 설정 창에서 페일오버를 바꾼 경우 앱 전체를 다시 켜지 않아도
        # 새 실행 객체에 반영되도록 PC 전용 연결 설정을 다시 읽는다.
        source = DataSourceConfig(connection_path).load()
        if query_only:
            if source.mode == "local":
                raise ValueError("조회 전용 실행에는 공통 데이터 공급 연결이 필요합니다. 조회 연결 설정에서 주소와 접속 토큰을 입력하세요.")
            # Query owns only this runtime projection; the saved full policy stays intact.
            source = replace(source, local_fallback_enabled=False, parallel_validation_enabled=False)
        # 로컬 서버 시작에 실패한 경우 이번 실행은 기존 직접 연결로 복구한다.
        if source.mode == "local_server" and local_central_server is None and not query_only:
            source = DataSourceSettings()
        local_settings = (
            LocalApiConfig(local_api).load()
            if source.mode == "local" or (
                source.mode == "personal_server" and (
                    source.local_fallback_enabled or source.parallel_validation_enabled
                )
            ) else None
        )
        client = create_query_client(
            local_api, connection_path, source_settings=source, local_settings=local_settings,
        )
        owned_observers: tuple[object, ...] = (
            (client,) if source.mode == "personal_server" and source.parallel_validation_enabled
            and local_settings is not None and local_settings.app_key and local_settings.secret_key
            else ()
        )
        owned_completions = tuple(
            (observer, observer.finish_comparison_shutdown) for observer in owned_observers
        )
        try:
            if query_only:
                try:
                    client.require_query_access()
                except KiwoomApiError as error:
                    raise ValueError(f"조회 연결 권한을 확인하지 못했습니다: {error}") from error
            if source.mode == "local":
                realtime_factory = lambda codes: RealtimeTradeWorker(
                    client.get_access_token, local_settings.environment, codes, client.server_now,
                )
            elif source.mode == "personal_server" and (
                source.local_fallback_enabled or source.parallel_validation_enabled
            ):
                if local_settings.app_key and local_settings.secret_key:
                    # 중앙 서버와 로컬 WebSocket을 동시에 열지 않는다. 중앙 연결이
                    # 연속 실패한 동안에만 직접 연결을 잠시 실행한다. 조회 fallback과
                    # 같은 로컬 client를 써야 OAuth 토큰과 요청 잠금도 한 벌로 유지된다.

                    def create_direct_realtime(codes, nxt_codes=()):
                        return RealtimeTradeWorker(
                            client.get_access_token,
                            local_settings.environment,
                            codes,
                            client.server_now,
                            nxt_codes,
                        )

                    if source.parallel_validation_enabled:
                        from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import RealtimeValidationRecorder
                        realtime_recorder = RealtimeValidationRecorder(paths.data_dir / "central_local_realtime_validation.jsonl")
                        if all(realtime_recorder is not owned for owned in owned_observers):
                            owned_observers += (realtime_recorder,)
                            owned_completions += ((realtime_recorder, realtime_recorder.finish_comparison_shutdown),)
                    else:
                        realtime_recorder = None
                    realtime_factory = lambda codes: CentralRealtimeWorker(
                        source,
                        codes,
                        fallback_factory=create_direct_realtime if source.local_fallback_enabled else None,
                        validation_factory=create_direct_realtime if source.parallel_validation_enabled else None,
                        validation_recorder=realtime_recorder,
                    )
                else:
                    logging.getLogger(__name__).warning(
                        "로컬 자동 전환이 켜져 있지만 이 PC에 키움 API 키가 없어 중앙 실시간만 사용합니다."
                    )
                    realtime_factory = lambda codes: CentralRealtimeWorker(source, codes)
            else:
                realtime_factory = lambda codes: CentralRealtimeWorker(source, codes)
            stock_repository = StockRepository(paths.database_path, now_provider=getattr(client, "server_now", None))
            nxt_eligibility = NxtEligibilityService(
                client, catalog_settings=settings if source.mode == "local" else None,
                catalog_store=stock_repository,
            )
            return {
                "source_mode": source.mode,
                "market_data_client": client,
                "owned_observers": owned_observers,
                "owned_observer_completions": owned_completions,
                "ranking_loader": RankingService(client, stocks=stock_repository, query_type=settings.get("rank_query_type"),
                                                 stock_only_enrichment=True),
                "realtime_worker_factory": realtime_factory,
                "minute_history_worker_factory": lambda codes: MinuteHistoryWorker(
                    MinuteChartService(client, include_nxt=True), codes, client.server_now
                ),
                "fundamentals_worker_factory": lambda codes: FundamentalsWorker(StockFundamentalsService(client), codes),
                "daily_high_worker_factory": lambda codes: DailyHighWorker(
                    DailyHighService(
                        client,
                        include_nxt=True,
                        now_provider=client.server_now,
                        cached_high_250_loader=stock_repository.load_high_250_price,
                        nxt_enabled_loader=(lambda code: nxt_eligibility.stored_flag(code, client.server_now))
                        if source.mode == "local" else (lambda code: stock_repository.load_nxt_enabled(
                            (code,), daily_query_date(client.server_now()).isoformat(),
                        ).get(code)),
                    ),
                    codes,
                ),
                "historical_high_worker_factory": lambda codes: HistoricalHighWorker(
                    HistoricalHighService(
                        client, include_nxt=True,
                        # 계산 기준이 바뀐 릴리즈에서는 기존 근거를 사용하지 않고
                        # 전 종목을 오늘 기준 수정주가로 한 번 다시 계산한다.
                        cache_loader=lambda code: (
                            stock_repository.load_historical_high_cache(code)
                            if settings.get(
                                "historical_high_adjusted_basis_version",
                                DEFAULT_SETTINGS["historical_high_adjusted_basis_version"],
                            ) == HISTORICAL_HIGH_BASIS_VERSION
                            else None
                        ),
                        high_250_loader=stock_repository.load_high_250_price,
                    ), codes
                ),
                "nxt_eligibility_worker_factory": lambda codes: NxtEligibilityWorker(nxt_eligibility, codes, client.server_now),
                "nxt_catalog_due": nxt_eligibility.preparation_due,
                "entry_investor_loader": InvestorFlowService(client).load,
                "program_trade_loader": ProgramTradeService(client, now_provider=client.server_now).load_day,
            }
        except BaseException as error:
            first_cleanup_error = _close_unpublished_api_observers(error, owned_observers, owned_completions)
            if first_cleanup_error is not None:
                raise error from first_cleanup_error
            raise

    def _close_desktop_sources() -> tuple[BaseException | None, tuple]:
        first_cleanup_error = None
        cleanup_results = []
        for owner, action in (
            (central_theme_sync, "close"),
            (central_content_thread, "join"),
            (local_central_server, "stop"),
        ):
            if owner is None:
                continue
            cleanup_error = None
            # Retain the existing public child until terminal exit is confirmed;
            # the server also keeps ownership through its bounded stop.
            process = local_central_server.process if owner is local_central_server else None
            try:
                getattr(owner, action)()
            except BaseException as caught:
                cleanup_error = caught
                if first_cleanup_error is None:
                    first_cleanup_error = caught
            if owner is central_content_thread:
                # Thread.join has no timeout. Defer interruptions while this
                # exact native source is still owned by the enclosing lease.
                while True:
                    try:
                        central_content_thread.join()
                        break
                    except BaseException as caught:
                        if cleanup_error is None:
                            cleanup_error = caught
                        if first_cleanup_error is None:
                            first_cleanup_error = caught
            elif process is not None:
                while True:
                    try:
                        process.wait()
                        break
                    except BaseException as caught:
                        if cleanup_error is None:
                            cleanup_error = caught
                        if first_cleanup_error is None:
                            first_cleanup_error = caught
            cleanup_results.append((owner, action, cleanup_error))
        return first_cleanup_error, tuple(cleanup_results)

    api_runtime: dict[str, object] = {}
    unpublished_observer_error: BaseException | None = None
    try:
        api_runtime = build_api_runtime()
    except ValueError as error:
        unpublished_observer_error = error
        logging.getLogger(__name__).warning("키움 REST 설정을 불러오지 못했습니다: %s", error)

    try:
        app = QApplication(sys.argv)
        app.setApplicationName(APP_DISPLAY_NAME)
        app.setWindowIcon(QIcon(str(_application_icon_path())))
        reported_errors: set[str] = set()

        def report_unhandled_error(error_type: type[BaseException], error: BaseException, traceback: object) -> None:
            signature = f"{error_type.__name__}: {error}"
            if signature in reported_errors:
                return
            reported_errors.add(signature)
            logging.getLogger(__name__).error("처리되지 않은 오류", exc_info=(error_type, error, traceback))
            QMessageBox.critical(None, "프로그램 오류", f"처리 중 오류가 발생했습니다.\n{error}\n\n로그 열기에서 자세한 내용을 확인할 수 있습니다.")

        sys.excepthook = report_unhandled_error
        theme_store = None
        try:
            theme_profile = settings.get("theme_active_profile")
        except (KeyError, ValueError):
            if settings_role is None:
                raise
            logging.getLogger(__name__).warning("조회 역할의 테마 선택 자료가 없어 테마 편집을 적용하지 않습니다. 원자료를 보존합니다.")
        else:
            if settings_role is None:
                theme_store = DatabaseThemeRepository(paths.database_path, theme_profile)
            else:
                theme_store = DatabaseThemeRepository.open_existing(paths.database_path, theme_profile)
                if theme_store is None:
                    logging.getLogger(__name__).warning("조회 역할 테마 원자료 미준비: 원래 선택을 보존하고 테마를 적용하지 않습니다.")
        if central_theme_sync is not None and theme_store is not None:
            theme_store.set_change_callback(central_theme_sync.notify)
        central_settings_timer: QTimer | None = None
        window: MainWindow | None = None
        themes = theme_store.all_by_name() if theme_store is not None else {}
    except BaseException as error:
        # Runtime success has not published either observer to a Controller.
        first_cleanup_error = _close_unpublished_api_observers(
            error, api_runtime.get("owned_observers", ()),
            api_runtime.get("owned_observer_completions", ()),
        )
        source_cleanup_error, setup_cleanup = _close_desktop_sources()
        setattr(error, "desktop_setup_cleanup", setup_cleanup)
        if first_cleanup_error is None:
            first_cleanup_error = source_cleanup_error
        if first_cleanup_error is not None:
            raise error from first_cleanup_error
        raise
    desktop_error: BaseException | None = None
    try:
        try:
            window = MainWindow(
                settings=settings,
                ranking_loader=api_runtime.get("ranking_loader"),
                realtime_worker_factory=api_runtime.get("realtime_worker_factory"),
                minute_history_worker_factory=api_runtime.get("minute_history_worker_factory"),
                fundamentals_worker_factory=api_runtime.get("fundamentals_worker_factory"),
                daily_high_worker_factory=api_runtime.get("daily_high_worker_factory"),
                historical_high_worker_factory=api_runtime.get("historical_high_worker_factory"),
                nxt_eligibility_worker_factory=api_runtime.get("nxt_eligibility_worker_factory"),
                nxt_catalog_due=api_runtime.get("nxt_catalog_due"),
                minute_aggregator=MinuteTradeValueAggregator(),
                minute_bar_repository=minute_bar_repository,
                daily_bar_repository=daily_bar_repository,
                themes=themes,
                columns=columns,
                stock_lookup=StockRepository(paths.database_path),
                theme_store=theme_store,
                google_drive_sync=None if query_only else google_drive_sync,
                api_runtime_factory=build_api_runtime,
                news_config_path=paths.data_dir / "naver_news.dat",
                news_database_path=paths.news_database_path,
                journal_database_path=paths.journal_database_path,
                monitor_database_path=paths.database_path,
                entry_investor_loader=api_runtime.get("entry_investor_loader"),
                program_trade_loader=api_runtime.get("program_trade_loader"),
                candidate_client=candidate_client,
                candidate_settings_client=candidate_settings_client,
                mock_automation_credentials_client=mock_automation_credentials_client,
                market_data_client=api_runtime.get("market_data_client"),
                owned_observers=api_runtime.get("owned_observers", ()),
                unpublished_observer_error=unpublished_observer_error,
                research_data_dir=paths.data_dir / "research",
                query_only=query_only,
                data_dir=paths.data_dir if settings_role is not None else None,
                settings_role=settings_role,
            )
        except BaseException as error:
            # Construction has not activated core writers/dialog work. The
            # unpublished runtime tuple is still held by this assembly owner.
            cleanup_error = _close_unpublished_api_observers(
                error, api_runtime.get("owned_observers", ()),
                api_runtime.get("owned_observer_completions", ()),
            )
            if cleanup_error is not None:
                raise error from cleanup_error
            raise
        window.activate_startup(initial_google_drive_download=initial_google_drive_download)
        window.show()
        if restore_outcome is not None:
            dialog = QMessageBox.warning if restore_outcome.state == "ROLLED_BACK" else QMessageBox.information
            QTimer.singleShot(0, lambda: None if window._app_controller.closing else dialog(window, "Google Drive 복원", restore_outcome.message))

        def retain_market_bars_after_startup() -> None:
            if window._app_controller.closing:
                return

            def run() -> None:
                try:
                    minute_bar_repository.purge_before(as_kst(window._app_controller.ranking_now()).date() - timedelta(days=30))
                except sqlite3.Error as error:
                    logging.getLogger(__name__).warning("시작 후 분봉 보존 정리를 건너뜁니다: %s", error)
                try:
                    daily_bar_repository.retain_latest(250)
                except sqlite3.Error as error:
                    logging.getLogger(__name__).warning("시작 후 일봉 보존 정리를 건너뜁니다: %s", error)

            worker = SettingsRequestWorker(run, window._app_controller)
            worker.setObjectName("startup-market-bar-retention")
            worker.start()

        # 실제 크기 DB에서 분봉/일봉 정리가 각각 약 0.65초/0.12초까지 걸렸다.
        # 첫 화면과 최초 순위 처리를 먼저 시작한 뒤 한 번 실행해 보존 정책은 유지한다.
        QTimer.singleShot(30_000, retain_market_bars_after_startup)
        central_settings_worker: SettingsRequestWorker | None = None
        if central_settings_sync is not None:
            def schedule_central_settings_sync() -> None:
                nonlocal central_settings_worker
                if window._app_controller.closing or central_settings_worker is not None:
                    return

                def run() -> None:
                    try:
                        central_settings_sync.sync(paths.database_path)
                        settings.clear_cache()
                    except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
                        logging.getLogger(__name__).warning("공통 설정 중앙 동기화 실패(로컬 설정 유지): %s", error)

                worker = SettingsRequestWorker(run, window._app_controller)
                worker.setObjectName("central-settings-sync")

                def finished() -> None:
                    nonlocal central_settings_worker
                    if central_settings_worker is worker:
                        central_settings_worker = None

                worker.finished.connect(finished)
                central_settings_worker = worker
                worker.start()

            central_settings_timer = QTimer(window._app_controller)
            central_settings_timer.setInterval(60_000)
            central_settings_timer.timeout.connect(schedule_central_settings_sync)
            window._app_controller.add_shutdown_timer(central_settings_timer)
            central_settings_timer.start()
            QTimer.singleShot(1_000, schedule_central_settings_sync)
        exit_code = app.exec()
    except BaseException as error:
        desktop_error = error
        raise
    finally:
        cleanup_errors = []
        if window is not None:
            try:
                # The published window keeps its existing producer/queued
                # results/writer ACK/native observer shutdown owner after exec.
                window.finish_shutdown()
            except BaseException as error:
                # finish_shutdown raises deferred errors only after ready.
                cleanup_errors.append((window, "finish_shutdown", error))
        if central_settings_timer is not None:
            try:
                central_settings_timer.stop()
            except BaseException as error:
                cleanup_errors.append((central_settings_timer, "stop", error))
        source_cleanup_error, source_cleanup = _close_desktop_sources()
        cleanup_errors.extend(item for item in source_cleanup if item[2] is not None)
        if cleanup_errors:
            first_cleanup_error = cleanup_errors[0][2]
            error = desktop_error if desktop_error is not None else first_cleanup_error
            setattr(error, "desktop_source_cleanup", source_cleanup)
            setattr(error, "desktop_shutdown_cleanup", tuple(cleanup_errors))
            if desktop_error is not None and desktop_error.__cause__ is None:
                raise desktop_error from first_cleanup_error
            if desktop_error is None:
                raise first_cleanup_error
    sys.exit(exit_code)
