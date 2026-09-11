"""앱 시작 감사에 필요한 DB 규모와 import 시간만 읽기 전용으로 측정한다."""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--construct-empty-window", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    print(f"database_bytes={database.stat().st_size}")
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in (
            "minute_bars", "daily_bars", "top20_trade_value_index",
            "stocks", "themes", "theme_profiles",
        ):
            if table in tables:
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"{table}_rows={count}")
        if "top20_trade_value_index" in tables:
            started_at = time.perf_counter()
            candidates = connection.execute(
                "SELECT COUNT(*) FROM top20_trade_value_index "
                "WHERE trade_value_eok>0 AND kospi_trade_value_eok=0 "
                "AND kosdaq_trade_value_eok=0"
            ).fetchone()[0]
            print(f"top20_repair_candidates={candidates}")
            print(f"top20_repair_scan_seconds={time.perf_counter() - started_at:.4f}")
        if "minute_bars" in tables:
            cutoff = (date.today() - timedelta(days=30)).isoformat()
            started_at = time.perf_counter()
            old_minutes = connection.execute(
                "SELECT COUNT(*) FROM minute_bars WHERE trade_date < ?", (cutoff,)
            ).fetchone()[0]
            print(f"minute_rows_before_cutoff={old_minutes}")
            print(f"minute_cutoff_scan_seconds={time.perf_counter() - started_at:.4f}")
        if "daily_bars" in tables:
            started_at = time.perf_counter()
            excess_daily = connection.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) AS sequence "
                "FROM daily_bars) WHERE sequence>250"
            ).fetchone()[0]
            print(f"daily_rows_beyond_250={excess_daily}")
            print(f"daily_retention_scan_seconds={time.perf_counter() - started_at:.4f}")
    finally:
        connection.close()

    started_at = time.perf_counter()
    import kiwoom_monitor.bootstrap  # noqa: F401
    print(f"bootstrap_import_seconds={time.perf_counter() - started_at:.4f}")

    if args.construct_empty_window:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        from kiwoom_monitor.infrastructure.persistence.database import Database
        from kiwoom_monitor.presentation.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            test_database = Database(Path(directory) / "monitor.sqlite3")
            test_database.initialize()
            started_at = time.perf_counter()
            window = MainWindow(test_database.settings)
            print(f"empty_main_window_seconds={time.perf_counter() - started_at:.4f}")
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()


if __name__ == "__main__":
    main()
