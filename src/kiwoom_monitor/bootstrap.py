from __future__ import annotations

import sys
import logging
from pathlib import Path

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
from kiwoom_monitor.infrastructure.kiwoom_rest.nxt_eligibility_worker import NxtEligibilityWorker
from kiwoom_monitor.application import RankingService
from kiwoom_monitor.application.minute_trade_value import MinuteTradeValueAggregator
from kiwoom_monitor.application.minute_chart_service import MinuteChartService
from kiwoom_monitor.application.stock_fundamentals_service import StockFundamentalsService
from kiwoom_monitor.application.daily_high_service import DailyHighService
from kiwoom_monitor.application.nxt_eligibility_service import NxtEligibilityService
from kiwoom_monitor.infrastructure.excel.theme_repository import ThemeRepository
from kiwoom_monitor.infrastructure.persistence.stock_repository import StockRepository
from kiwoom_monitor.infrastructure.persistence.column_settings_repository import ColumnSettingsRepository
from kiwoom_monitor.infrastructure.persistence.theme_repository import ThemeRepository as DatabaseThemeRepository
from kiwoom_monitor.infrastructure.persistence.google_drive_sync import GoogleDriveSyncService
from kiwoom_monitor.presentation.main_window import MainWindow


def main() -> None:
    paths = AppPaths.for_current_user()
    configure_logging(paths.log_dir)

    database = Database(paths.database_path)
    database.initialize()

    ranking_service = None
    realtime_worker_factory = None
    minute_history_worker_factory = None
    fundamentals_worker_factory = None
    daily_high_worker_factory = None
    nxt_eligibility_worker_factory = None
    try:
        app_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
        local_api = app_root / "data" / "api.env"
        settings = LocalApiConfig(local_api).load()
        if not settings.app_key or not settings.secret_key:
            raise ValueError("API 키가 아직 설정되지 않았습니다.")
        client = KiwoomRestClient(settings)
        ranking_service = RankingService(client, stocks=StockRepository(paths.database_path), query_type=database.settings.get("rank_query_type"))
        realtime_worker_factory = lambda codes: RealtimeTradeWorker(client.get_access_token, settings.environment, codes, client.server_now)
        fundamentals_worker_factory = lambda codes: FundamentalsWorker(StockFundamentalsService(client), codes)
        daily_high_worker_factory = lambda codes: DailyHighWorker(DailyHighService(client), codes)
        nxt_eligibility_worker_factory = lambda codes: NxtEligibilityWorker(NxtEligibilityService(client), codes)
        minute_history_worker_factory = lambda codes: MinuteHistoryWorker(MinuteChartService(client), codes, client.server_now)
    except ValueError as error:
        logging.getLogger(__name__).warning("키움 REST 설정을 불러오지 못했습니다: %s", error)

    app = QApplication(sys.argv)
    app.setApplicationName("키움 실시간 모니터")
    reported_errors: set[str] = set()

    def report_unhandled_error(error_type: type[BaseException], error: BaseException, traceback: object) -> None:
        signature = f"{error_type.__name__}: {error}"
        if signature in reported_errors:
            return
        reported_errors.add(signature)
        logging.getLogger(__name__).error("처리되지 않은 오류", exc_info=(error_type, error, traceback))
        QMessageBox.critical(None, "프로그램 오류", f"처리 중 오류가 발생했습니다.\n{error}\n\n로그 열기에서 자세한 내용을 확인할 수 있습니다.")

    sys.excepthook = report_unhandled_error
    themes = DatabaseThemeRepository(paths.database_path).all_by_name()
    window = MainWindow(
        settings=database.settings,
        ranking_loader=ranking_service,
        realtime_worker_factory=realtime_worker_factory,
        minute_history_worker_factory=minute_history_worker_factory,
        fundamentals_worker_factory=fundamentals_worker_factory,
        daily_high_worker_factory=daily_high_worker_factory,
        nxt_eligibility_worker_factory=nxt_eligibility_worker_factory,
        minute_aggregator=MinuteTradeValueAggregator(),
        themes=themes,
        columns=ColumnSettingsRepository(paths.database_path),
        stock_lookup=StockRepository(paths.database_path),
        theme_store=DatabaseThemeRepository(paths.database_path),
        google_drive_sync=GoogleDriveSyncService(paths.database_path),
    )
    window.show()
    sys.exit(app.exec())
