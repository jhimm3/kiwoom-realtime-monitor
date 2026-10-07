from __future__ import annotations

import io
import logging
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from kiwoom_monitor.central_server.server_logging import configure_server_logging


class CentralServerLoggingTests(unittest.TestCase):
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
