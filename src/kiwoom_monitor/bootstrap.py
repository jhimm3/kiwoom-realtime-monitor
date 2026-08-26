from __future__ import annotations

import sys
import logging
import ctypes
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from kiwoom_monitor.infrastructure.app_paths import AppPaths
from kiwoom_monitor.infrastructure.logging_config import configure_logging
from kiwoom_monitor.infrastructure.persistence.database import Database
from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomRestClient
from kiwoom_monitor.infrastructure.kiwoom_rest.local_config import LocalApiConfig
from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_worker import RealtimeTradeWorker
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
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository as DatabaseThemeRepository
from kiwoom_monitor.infrastructure.persistence.minute_bar_repository import MinuteBarRepository
from kiwoom_monitor.infrastructure.persistence.daily_bar_repository import DailyBarRepository
from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncService
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
    _set_taskbar_app_id()
    paths = AppPaths.for_current_user()
    configure_logging(paths.log_dir)

    database = Database(paths.database_path)
    database.initialize()
    minute_bar_repository = MinuteBarRepository(paths.database_path)
    minute_bar_repository.purge_before(datetime.now().date() - timedelta(days=30))
    daily_bar_repository = DailyBarRepository(paths.database_path)
    daily_bar_repository.purge_before(datetime.now().date() - timedelta(days=30))
    google_drive_sync = GoogleDriveSyncService(paths.database_path)
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
        settings = LocalApiConfig(local_api).load()
        if not settings.app_key or not settings.secret_key:
            raise ValueError("API 키가 아직 설정되지 않았습니다.")
        client = KiwoomRestClient(settings)
        return {
            "ranking_loader": RankingService(client, stocks=StockRepository(paths.database_path), query_type=database.settings.get("rank_query_type")),
            "realtime_worker_factory": lambda codes: RealtimeTradeWorker(client.get_access_token, settings.environment, codes, client.server_now),
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
    )
    window.show()
    sys.exit(app.exec())
