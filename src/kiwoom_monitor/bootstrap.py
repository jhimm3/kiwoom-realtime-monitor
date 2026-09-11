from __future__ import annotations

import sys
import logging
import ctypes
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig, DataSourceSettings
from kiwoom_monitor.infrastructure.central_server_process import LocalCentralServerProcess
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
from kiwoom_monitor.infrastructure.central_content_sync import CentralContentSyncService
from kiwoom_monitor.infrastructure.central_theme_sync import CentralThemeSyncDispatcher
from kiwoom_monitor.infrastructure.central_settings_sync import CentralSettingsSyncService
from kiwoom_monitor.infrastructure.logging_config import configure_logging
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.client import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.central_realtime_worker import CentralRealtimeWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.validation_client import RealtimeValidationRecorder
from kiwoom_monitor.infrastructure.kiwoom_rest.client_factory import create_query_client
from kiwoom_monitor.infrastructure.kiwoom_rest.minute_history_worker import MinuteHistoryWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.fundamentals_worker import FundamentalsWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.daily_high_worker import DailyHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.historical_high_worker import HistoricalHighWorker
from kiwoom_monitor.infrastructure.kiwoom_rest.nxt_eligibility_worker import NxtEligibilityWorker
from kiwoom_monitor.application import RankingService
from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.application.stock_fundamentals_service import StockFundamentalsService
from kiwoom_monitor.application.daily_high_service import DailyHighService
from kiwoom_monitor.application.historical_high_service import HistoricalHighService
from kiwoom_monitor.application.nxt_eligibility_service import NxtEligibilityService
from kiwoom_monitor.application.investor_flow_service import InvestorFlowService
from kiwoom_monitor.application.program_trade_service import ProgramTradeService
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository as DatabaseThemeRepository
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncService
from kiwoom_monitor.infrastructure.persistence.news_database import migrate_legacy_news_database
from kiwoom_monitor.presentation.main_window import APP_DISPLAY_NAME, MainWindow


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
    _set_taskbar_app_id()
    paths = AppPaths.for_current_user()
    configure_logging(paths.log_dir)

    database = Database(paths.database_path)
    database.initialize()
    local_central_server: LocalCentralServerProcess | None = None
    configured_data_source = DataSourceSettings()
    try:
        configured_data_source = DataSourceConfig(paths.data_dir / "data_source.json").load()
        if configured_data_source.mode == "local_server":
            local_central_server = LocalCentralServerProcess(
                configured_data_source, paths.data_dir / "api.env", paths.database_path,
            )
            local_central_server.start()
    except (ValueError, RuntimeError, TimeoutError, OSError) as error:
        logging.getLogger(__name__).warning("로컬 중앙 서버를 시작하지 못했습니다: %s", error)
        if configured_data_source.mode == "local_server":
            if local_central_server is not None:
                local_central_server.stop()
            local_central_server = None
    migrate_legacy_news_database(paths.database_path, paths.news_database_path)
    central_theme_sync: CentralThemeSyncDispatcher | None = None
    central_settings_sync: CentralSettingsSyncService | None = None
    if configured_data_source.mode in {"local_server", "personal_server"} and not (
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
                # 이전 실행의 로컬 대기 변경과 NAS 완료본의 수정 시각을 비교해
                # 더 최신인 테마를 먼저 확정한 뒤 나머지 콘텐츠를 내려받는다.
                if central_theme_sync is not None and central_theme_sync.has_pending \
                        and not central_theme_sync.flush_pending():
                    return
                pulled = central_content_service.pull(paths.database_path, paths.news_database_path)
                seed_marker = paths.data_dir / ".central_content_seeded"
                if seed_marker.exists():
                    logging.getLogger(__name__).info(
                        "중앙 콘텐츠 시작 동기화 완료: 내려받기 %s건 · 초기 업로드 생략", pulled.total,
                    )
                else:
                    pushed = central_content_service.push(paths.database_path, paths.news_database_path)
                    seed_marker.write_text("1\n", encoding="utf-8")
                    logging.getLogger(__name__).info(
                        "중앙 콘텐츠 최초 이전 완료: 내려받기 %s건, 보존 %s건", pulled.total, pushed.total,
                    )
            except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
                logging.getLogger(__name__).warning("중앙 콘텐츠 이전을 건너뜁니다: %s", error)

        threading.Thread(target=sync_central_content, name="central-content-sync", daemon=True).start()
        central_settings_sync = CentralSettingsSyncService(CentralContentClient(
            configured_data_source.server_url, configured_data_source.access_token,
        ))
    minute_bar_repository = MinuteBarRepository(paths.database_path)
    daily_bar_repository = DailyBarRepository(paths.database_path)
    google_drive_sync = GoogleDriveSyncService(paths.database_path, paths.news_database_path)
    local_changed_at = database.settings.get("google_drive_local_changed_at")
    last_upload_at = database.settings.get("google_drive_last_upload_success_at")
    local_changes_are_newer = database.settings.get("google_drive_unsynced_changes") == "1" or (
        bool(local_changed_at) and (not last_upload_at or local_changed_at > last_upload_at)
    )
    # 시작 시에는 내용 다운로드 전에 Drive 수정 시각만 먼저 확인한다. 로컬 변경이
    # 남은 경우에도 원격 변경과 충돌인지 판별해야 하므로 자동 업로드 대상이면 확인한다.
    initial_google_drive_download = google_drive_sync.connected and (
        database.settings.get("google_drive_auto_download") == "1"
        or (local_changes_are_newer and database.settings.get("google_drive_auto_upload") == "1")
    )

    def build_api_runtime() -> dict[str, object]:
        """현재 저장된 API 설정으로 작업 객체 묶음을 새로 만든다."""
        local_api = paths.data_dir / "api.env"
        # API 설정 창에서 페일오버를 바꾼 경우 앱 전체를 다시 켜지 않아도
        # 새 실행 객체에 반영되도록 PC 전용 연결 설정을 다시 읽는다.
        source = DataSourceConfig(paths.data_dir / "data_source.json").load()
        # 로컬 서버 시작에 실패한 경우 이번 실행은 기존 직접 연결로 복구한다.
        if source.mode == "local_server" and local_central_server is None:
            source = DataSourceSettings()
        client = create_query_client(
            local_api, paths.data_dir / "data_source.json", source_settings=source,
        )
        if source.mode == "local":
            settings = LocalApiConfig(local_api).load()
            realtime_factory = lambda codes: RealtimeTradeWorker(
                client.get_access_token, settings.environment, codes, client.server_now,
            )
        elif source.mode == "personal_server" and (
            source.local_fallback_enabled or source.parallel_validation_enabled
        ):
            local_settings = LocalApiConfig(local_api).load()
            if local_settings.app_key and local_settings.secret_key:
                # 중앙 서버와 로컬 WebSocket을 동시에 열지 않는다. 중앙 연결이
                # 연속 실패한 동안에만 전용 직접 연결 객체를 잠시 실행한다.
                direct_client = KiwoomRestClient(local_settings)

                def create_direct_realtime(codes, nxt_codes=()):
                    return RealtimeTradeWorker(
                        direct_client.get_access_token,
                        local_settings.environment,
                        codes,
                        direct_client.server_now,
                        nxt_codes,
                    )

                realtime_recorder = (
                    RealtimeValidationRecorder(paths.data_dir / "central_local_realtime_validation.jsonl")
                    if source.parallel_validation_enabled else None
                )
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
        return {
            "ranking_loader": RankingService(client, stocks=StockRepository(paths.database_path), query_type=database.settings.get("rank_query_type")),
            "realtime_worker_factory": realtime_factory,
            "minute_history_worker_factory": lambda codes: MinuteHistoryWorker(
                MinuteChartService(client, include_nxt=True), codes, client.server_now
            ),
            "fundamentals_worker_factory": lambda codes: FundamentalsWorker(StockFundamentalsService(client), codes),
            "daily_high_worker_factory": lambda codes: DailyHighWorker(
                DailyHighService(
                    client,
                    include_nxt=True,
                    cached_high_250_loader=StockRepository(paths.database_path).load_high_250_price,
                ),
                codes,
            ),
            "historical_high_worker_factory": lambda codes: HistoricalHighWorker(
                HistoricalHighService(
                    client, include_nxt=True,
                    # 계산 기준이 바뀐 릴리즈에서는 기존 근거를 사용하지 않고
                    # 전 종목을 오늘 기준 수정주가로 한 번 다시 계산한다.
                    cache_loader=lambda code: (
                        StockRepository(paths.database_path).load_historical_high_cache(code)
                        if database.settings.get("historical_high_adjusted_basis_version") == "5"
                        else None
                    ),
                    high_250_loader=StockRepository(paths.database_path).load_high_250_price,
                ), codes
            ),
            "nxt_eligibility_worker_factory": lambda codes: NxtEligibilityWorker(NxtEligibilityService(client), codes),
            "entry_investor_loader": InvestorFlowService(client).load,
            "program_trade_loader": ProgramTradeService(client).load_day,
        }

    api_runtime: dict[str, object] = {}
    try:
        api_runtime = build_api_runtime()
    except ValueError as error:
        logging.getLogger(__name__).warning("키움 REST 설정을 불러오지 못했습니다: %s", error)

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
    theme_store = DatabaseThemeRepository(paths.database_path, database.settings.get("theme_active_profile"))
    if central_theme_sync is not None:
        theme_store.set_change_callback(central_theme_sync.notify)
    themes = theme_store.all_by_name()
    window = MainWindow(
        settings=database.settings,
        ranking_loader=api_runtime.get("ranking_loader"),
        realtime_worker_factory=api_runtime.get("realtime_worker_factory"),
        minute_history_worker_factory=api_runtime.get("minute_history_worker_factory"),
        fundamentals_worker_factory=api_runtime.get("fundamentals_worker_factory"),
        daily_high_worker_factory=api_runtime.get("daily_high_worker_factory"),
        historical_high_worker_factory=api_runtime.get("historical_high_worker_factory"),
        nxt_eligibility_worker_factory=api_runtime.get("nxt_eligibility_worker_factory"),
        minute_aggregator=MinuteTradeValueAggregator(),
        minute_bar_repository=minute_bar_repository,
        daily_bar_repository=daily_bar_repository,
        themes=themes,
        columns=ColumnSettingsRepository(paths.database_path),
        stock_lookup=StockRepository(paths.database_path),
        theme_store=theme_store,
        google_drive_sync=google_drive_sync,
        initial_google_drive_download=initial_google_drive_download,
        api_runtime_factory=build_api_runtime,
        news_config_path=paths.data_dir / "naver_news.dat",
        news_database_path=paths.news_database_path,
        journal_database_path=paths.journal_database_path,
        monitor_database_path=paths.database_path,
        entry_investor_loader=api_runtime.get("entry_investor_loader"),
        program_trade_loader=api_runtime.get("program_trade_loader"),
    )
    window.show()

    def retain_market_bars_after_startup() -> None:
        def run() -> None:
            try:
                minute_bar_repository.purge_before(datetime.now().date() - timedelta(days=30))
            except sqlite3.Error as error:
                logging.getLogger(__name__).warning("시작 후 분봉 보존 정리를 건너뜁니다: %s", error)
            try:
                daily_bar_repository.retain_latest(250)
            except sqlite3.Error as error:
                logging.getLogger(__name__).warning("시작 후 일봉 보존 정리를 건너뜁니다: %s", error)

        threading.Thread(target=run, name="market-bar-retention", daemon=True).start()

    # 실제 크기 DB에서 분봉/일봉 정리가 각각 약 0.65초/0.12초까지 걸렸다.
    # 첫 화면과 최초 순위 처리를 먼저 시작한 뒤 한 번 실행해 보존 정책은 유지한다.
    QTimer.singleShot(30_000, retain_market_bars_after_startup)
    central_settings_running = threading.Event()
    central_settings_timer: QTimer | None = None
    if central_settings_sync is not None:
        def schedule_central_settings_sync() -> None:
            if central_settings_running.is_set():
                return
            central_settings_running.set()

            def run() -> None:
                try:
                    central_settings_sync.sync(paths.database_path)
                    database.settings.clear_cache()
                except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
                    logging.getLogger(__name__).warning("공통 설정 중앙 동기화 실패(로컬 설정 유지): %s", error)
                finally:
                    central_settings_running.clear()

            threading.Thread(target=run, name="central-app-settings-sync", daemon=True).start()

        central_settings_timer = QTimer()
        central_settings_timer.setInterval(60_000)
        central_settings_timer.timeout.connect(schedule_central_settings_sync)
        central_settings_timer.start()
        QTimer.singleShot(1_000, schedule_central_settings_sync)
    try:
        exit_code = app.exec()
    finally:
        if central_settings_timer is not None:
            central_settings_timer.stop()
        if central_theme_sync is not None:
            central_theme_sync.close()
        if local_central_server is not None:
            local_central_server.stop()
    sys.exit(exit_code)
