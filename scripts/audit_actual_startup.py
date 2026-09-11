"""실제 크기의 복제 DB로 앱 시작 단계를 계측한다.

사용자 DB와 설정은 임시 폴더로 복제하며 원본을 수정하지 않는다. 원격 동기화와
Qt 이벤트 루프는 시작하지 않고, 실제 ``bootstrap.main``이 MainWindow를 생성해
``show()``를 호출하는 지점까지의 동기 경로만 측정한다.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
source_text = str(SOURCE_ROOT)
if source_text not in sys.path:
    sys.path.insert(0, source_text)


def _copy_startup_inputs(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in (
        "monitor.sqlite3",
        "news.sqlite3",
        "data_source.json",
        "api.env",
        "naver_news.dat",
        "google_drive_client.json",
        "google_drive_token.dat",
        ".central_content_seeded",
    ):
        candidate = source / name
        if candidate.is_file():
            shutil.copy2(candidate, destination / name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--event-loop-ms", type=int, default=0)
    args = parser.parse_args()
    source_data = args.data_dir.resolve()
    if not (source_data / "monitor.sqlite3").is_file():
        raise SystemExit(f"monitor.sqlite3 not found: {source_data}")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    process_started = time.perf_counter()
    import_started = time.perf_counter()
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication as RealApplication

    import kiwoom_monitor.bootstrap as bootstrap
    from kiwoom_monitor.infrastructure.app_paths import AppPaths

    import_seconds = time.perf_counter() - import_started
    events: list[tuple[str, float]] = []

    def record(name: str, started: float) -> None:
        events.append((name, time.perf_counter() - started))

    def wrap_method(owner: Any, attribute: str, label: str) -> None:
        original = getattr(owner, attribute)

        def timed(*call_args: Any, **call_kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return original(*call_args, **call_kwargs)
            finally:
                record(label, started)

        setattr(owner, attribute, timed)

    with tempfile.TemporaryDirectory(
        prefix="kiwoom-startup-audit-", ignore_cleanup_errors=True,
    ) as directory:
        audit_data = Path(directory) / "data"
        copy_started = time.perf_counter()
        _copy_startup_inputs(source_data, audit_data)
        copy_seconds = time.perf_counter() - copy_started
        audit_paths = AppPaths(audit_data, audit_data / "logs", audit_data / "monitor.sqlite3")
        audit_paths.log_dir.mkdir(parents=True, exist_ok=True)
        read_connection = sqlite3.connect(
            f"file:{audit_paths.database_path.as_posix()}?mode=ro", uri=True,
        )
        try:
            excess_daily_rows = int(read_connection.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY trade_date DESC) AS sequence "
                "FROM daily_bars) WHERE sequence>250"
            ).fetchone()[0])
            metadata_exists = read_connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_data_metadata'"
            ).fetchone() is not None
            orphan_daily_metadata = int(read_connection.execute(
                "SELECT COUNT(*) FROM market_data_metadata AS metadata "
                "WHERE dataset_kind='daily_bar' AND NOT EXISTS ("
                "SELECT 1 FROM daily_bars AS bars WHERE bars.stock_code=metadata.subject "
                "AND bars.trade_date=metadata.observation_key)"
            ).fetchone()[0]) if metadata_exists else 0
        finally:
            read_connection.close()
        AppPaths.for_current_user = classmethod(lambda cls: audit_paths)

        wrap_method(bootstrap, "configure_logging", "configure_logging")
        wrap_method(bootstrap.Database, "initialize", "database_initialize")
        wrap_method(bootstrap.DataSourceConfig, "load", "data_source_load")
        wrap_method(bootstrap, "migrate_legacy_news_database", "news_database_migration")
        original_minute_purge = bootstrap.MinuteBarRepository.purge_before
        wrap_method(bootstrap.MinuteBarRepository, "purge_before", "minute_retention")
        original_daily_retain = bootstrap.DailyBarRepository.retain_latest
        wrap_method(bootstrap.DailyBarRepository, "retain_latest", "daily_retention")
        wrap_method(bootstrap, "create_query_client", "query_client_create")
        wrap_method(bootstrap.DatabaseThemeRepository, "all_by_name", "theme_load")

        original_thread_start = threading.Thread.start

        def safe_thread_start(thread: threading.Thread) -> None:
            if thread.name == "central-content-sync":
                events.append(("central_content_thread_scheduled", 0.0))
                return
            original_thread_start(thread)

        threading.Thread.start = safe_thread_start

        real_main_window = bootstrap.MainWindow
        if args.event_loop_ms > 0:
            for callback_name in (
                "_refresh_rankings",
                "_start_top20_market_repair",
                "_start_google_drive_sync",
                "_check_for_updates",
                "_open_api_settings",
            ):
                def suppress_external_callback(
                    self: Any, *call_args: Any, _name: str = callback_name,
                    **call_kwargs: Any,
                ) -> None:
                    events.append((f"suppressed.{_name}", 0.0))

                setattr(real_main_window, callback_name, suppress_external_callback)

        def timed_main_window(*call_args: Any, **call_kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                window = real_main_window(*call_args, **call_kwargs)
            finally:
                record("main_window_construct", started)
            original_show = window.show

            def timed_show() -> None:
                show_started = time.perf_counter()
                try:
                    original_show()
                finally:
                    record("main_window_show", show_started)
                    events.append(("bootstrap_elapsed_to_show", time.perf_counter() - main_started))

            window.show = timed_show
            return window

        bootstrap.MainWindow = timed_main_window

        class AuditApplication(RealApplication):
            def __init__(self, *call_args: Any, **call_kwargs: Any) -> None:
                started = time.perf_counter()
                super().__init__(*call_args, **call_kwargs)
                record("qapplication_construct", started)

            def exec(self) -> int:
                event_loop_started: float | None = None

                def finish() -> None:
                    if event_loop_started is not None:
                        record("qt_until_finish_timer", event_loop_started)
                    cleanup_started = time.perf_counter()
                    for window in self.topLevelWidgets():
                        writer = getattr(window, "_entry_snapshot_writer", None)
                        if writer is not None and writer.isRunning():
                            writer.requestInterruption()
                            writer.wait(1_000)
                        window.hide()
                        window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    self.quit()
                    record("qt_diagnostic_cleanup", cleanup_started)

                if args.event_loop_ms <= 0:
                    finish()
                    return 0
                event_loop_started = time.perf_counter()
                bootstrap.QTimer.singleShot(args.event_loop_ms, finish)
                result = super().exec()
                record("qt_first_events", event_loop_started)
                return result

        bootstrap.QApplication = AuditApplication
        original_argv = sys.argv
        sys.argv = [original_argv[0]]
        main_started = time.perf_counter()
        try:
            bootstrap.main()
        except SystemExit as exit_signal:
            if exit_signal.code not in (None, 0):
                raise
        finally:
            sys.argv = original_argv
            threading.Thread.start = original_thread_start
            logging.shutdown()
        bootstrap_seconds = time.perf_counter() - main_started
        deferred_minute_started = time.perf_counter()
        original_minute_purge(
            bootstrap.MinuteBarRepository(audit_paths.database_path),
            datetime.now().date() - timedelta(days=30),
        )
        deferred_minute_seconds = time.perf_counter() - deferred_minute_started
        clean_minute_started = time.perf_counter()
        original_minute_purge(
            bootstrap.MinuteBarRepository(audit_paths.database_path),
            datetime.now().date() - timedelta(days=30),
        )
        clean_minute_seconds = time.perf_counter() - clean_minute_started
        deferred_retain_started = time.perf_counter()
        original_daily_retain(bootstrap.DailyBarRepository(audit_paths.database_path), 250)
        deferred_retain_seconds = time.perf_counter() - deferred_retain_started
        clean_retain_started = time.perf_counter()
        original_daily_retain(bootstrap.DailyBarRepository(audit_paths.database_path), 250)
        clean_retain_seconds = time.perf_counter() - clean_retain_started

    print(f"source_database_bytes={(source_data / 'monitor.sqlite3').stat().st_size}")
    print(f"excess_daily_rows={excess_daily_rows}")
    print(f"orphan_daily_metadata_rows={orphan_daily_metadata}")
    print(f"input_copy_seconds={copy_seconds:.4f}")
    print(f"bootstrap_import_seconds={import_seconds:.4f}")
    for name, seconds in events:
        print(f"stage.{name}={seconds:.4f}")
    print(f"bootstrap_to_show_and_cleanup_seconds={bootstrap_seconds:.4f}")
    print(f"deferred_minute_retention_seconds={deferred_minute_seconds:.4f}")
    print(f"already_clean_minute_retention_seconds={clean_minute_seconds:.4f}")
    print(f"deferred_daily_retention_seconds={deferred_retain_seconds:.4f}")
    print(f"already_clean_daily_retention_seconds={clean_retain_seconds:.4f}")
    print(f"whole_probe_seconds={time.perf_counter() - process_started:.4f}")
    print("remote_threads_started=0")
    print(f"qt_event_loop_ms={max(0, args.event_loop_ms)}")


if __name__ == "__main__":
    main()
