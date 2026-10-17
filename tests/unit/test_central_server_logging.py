from __future__ import annotations

import io
import logging
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.server_logging import configure_server_logging
from kiwoom_monitor.infrastructure.logging_config import KSTFormatter as DesktopKSTFormatter


class CentralServerLoggingTests(unittest.TestCase):
    def test_desktop_log_formatter_explicitly_uses_kst(self):
        record = logging.LogRecord("clock", logging.INFO, __file__, 0, "ready", (), None)
        record.created = datetime.fromisoformat("2026-10-14T16:30:00+00:00").timestamp()
        record.msecs = 0
        formatter = DesktopKSTFormatter("%(asctime)s KST %(message)s")
        self.assertEqual(formatter.format(record), "2026-10-15 01:30:00,000 KST ready")

    @contextmanager
    def _log_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            try:
                yield directory
            finally:
                self._close_handlers()

    @staticmethod
    def _write_at(timestamp: float, message: str) -> None:
        logger = logging.getLogger("uvicorn.access")
        record = logging.LogRecord(logger.name, logging.INFO, __file__, 0, message, (), None)
        record.created = timestamp
        record.msecs = (timestamp * 1000) % 1000
        with patch("logging.handlers.time.time", return_value=timestamp):
            logger.handle(record)
        for handler in logging.getLogger("uvicorn.access").handlers:
            handler.flush()

    def test_midnight_rollover_uses_completed_kst_date(self) -> None:
        boundary = datetime.fromisoformat("2026-10-12T00:00:00+09:00").timestamp()
        with self._log_directory() as directory:
            log_path = configure_server_logging(Path(directory))
            handler = logging.getLogger("uvicorn.access").handlers[0]
            handler.rolloverAt = boundary
            self._write_at(boundary - 1, "before midnight")
            self._write_at(boundary, "after midnight")
            self.assertEqual(boundary + 86400, handler.rolloverAt)
            archive = log_path.with_name("server.log.2026-10-11")
            self.assertIn("2026-10-11 23:59:59,000 KST", archive.read_text(encoding="utf-8"))
            self.assertNotIn("after midnight", archive.read_text(encoding="utf-8"))
            self.assertIn("2026-10-12 00:00:00,000 KST", log_path.read_text(encoding="utf-8"))
            self._close_handlers()

    def test_legacy_archive_collision_preserves_both_files_and_still_rotates(self) -> None:
        boundary = datetime.fromisoformat("2026-10-12T00:00:00+09:00").timestamp()
        with self._log_directory() as directory:
            root = Path(directory)
            legacy = root / "server.log.2026-10-10"
            existing = root / "server.log.2026-10-11"
            legacy.write_text("legacy UTC archive", encoding="utf-8")
            existing.write_text("existing KST archive", encoding="utf-8")
            log_path = configure_server_logging(root)
            handler = logging.getLogger("uvicorn.access").handlers[0]
            handler.rolloverAt = boundary
            self._write_at(boundary - 1, "before midnight")
            self._write_at(boundary, "after midnight")
            self.assertEqual(boundary + 86400, handler.rolloverAt)
            self.assertEqual("legacy UTC archive", legacy.read_text(encoding="utf-8"))
            self.assertEqual("existing KST archive", existing.read_text(encoding="utf-8"))
            self.assertIn("before midnight", (root / "server.log.2026-10-11.1").read_text(encoding="utf-8"))
            self.assertNotIn("before midnight", log_path.read_text(encoding="utf-8"))
            self._close_handlers()

    def test_retention_counts_dates_and_includes_collision_parts(self) -> None:
        with self._log_directory() as directory:
            root = Path(directory)
            names = ("server.log.2026-10-09", "server.log.2026-10-09.1",
                     "server.log.2026-10-10", "server.log.2026-10-11",
                     "server.log.2026-10-11.1", "server.log.notes")
            for name in names:
                (root / name).write_text(name, encoding="utf-8")
            configure_server_logging(root, retention_days=2)
            handler = logging.getLogger("uvicorn.access").handlers[0]
            self.assertEqual({str(root / name) for name in names[:2]}, set(handler.getFilesToDelete()))
            self._close_handlers()

    def test_restart_and_schedule_are_independent_of_host_timezone(self) -> None:
        boundary = datetime.fromisoformat("2026-10-12T00:00:00+09:00").timestamp()
        with self._log_directory() as directory:
            root = Path(directory)
            path = root / "server.log"
            path.write_text("before restart", encoding="utf-8")
            os.utime(path, (boundary - 20, boundary - 20))
            configure_server_logging(root)
            handler = logging.getLogger("uvicorn.access").handlers[0]
            self.assertEqual(boundary, handler.rolloverAt)
            self.assertEqual(boundary, handler.computeRollover(boundary - 1))
            self.assertEqual(boundary + 86400, handler.computeRollover(boundary))
            self._write_at(boundary, "after restarted midnight")
            self.assertIn("before restart", (root / "server.log.2026-10-11").read_text(encoding="utf-8"))
            self._close_handlers()

    def tearDown(self) -> None:
        self._close_handlers()

    @staticmethod
    def _close_handlers() -> None:
        logging.shutdown()
        root = logging.getLogger()
        for handler in tuple(root.handlers):
            root.removeHandler(handler)
            handler.close()
        for name in ("uvicorn.access", "uvicorn.error", "kiwoom_monitor.kiwoom_api"):
            logger = logging.getLogger(name)
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
                handler.close()

    def test_access_details_are_file_only_and_other_info_reaches_console(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, redirect_stderr(stderr):
            log_path = configure_server_logging(Path(directory), retention_days=7)
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d',
                "172.18.0.1:52276", "GET", "/api/v1/research/candidates", "1.1", 200,
            )
            logging.getLogger("kiwoom_monitor.kiwoom_api").info(
                "request completed namespace=market api_id=ka00198 continuation=N duration_ms=123"
            )
            logging.getLogger("kiwoom_monitor.central_server.test").info("server ready")
            for logger_name in ("", "uvicorn.access", "uvicorn.error", "kiwoom_monitor.kiwoom_api"):
                for handler in logging.getLogger(logger_name).handlers:
                    handler.flush()
            detail = log_path.read_text(encoding="utf-8")
            self._close_handlers()

        self.assertIn("/api/v1/research/candidates", detail)
        self.assertIn("api_id=ka00198", detail)
        self.assertIn("server ready", detail)
        self.assertNotIn("/api/v1/research/candidates", stderr.getvalue())
        self.assertNotIn("api_id=ka00198", stderr.getvalue())
        self.assertIn("server ready", stderr.getvalue())

    def test_retention_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            configure_server_logging(Path(directory), retention_days=999)
            access_handlers = logging.getLogger("uvicorn.access").handlers
            self.assertEqual(1, len(access_handlers))
            self.assertEqual(365, access_handlers[0].backupCount)
            self._close_handlers()


if __name__ == "__main__":
    unittest.main()
