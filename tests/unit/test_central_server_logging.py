from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.server_logging import configure_server_logging
from kiwoom_monitor.infrastructure.logging_config import KSTFormatter as DesktopKSTFormatter
from kiwoom_monitor.infrastructure.logging_config import configure_logging


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

    def test_both_sinks_redact_args_and_urls_without_mutating_the_record(self) -> None:
        for configure in (configure_logging, configure_server_logging):
            for message, arguments in (
                ("stage=applied attempt=2 Authorization: %s url=%s payload=%r\r\nFORGED",
                 ("Bearer header-secret", "https://alice:dsn-secret@nas.test/api?token=query-secret&safe=visible",
                  {"api_key": "payload-secret", "client_secret": "client-secret"})),
                ("stage=applied attempt=2 password=%(password)s url=%(url)s",
                 {"password": "mapping-secret", "url": "/api/v1/realtime?token=query-secret&safe=visible"}),
                ("stage=applied attempt=2 dsn=%s headers=%r url=%s config=%s",
                 ("host=db password='libpq private secret' dbname=safe",
                  {"Authorization": "Basic basic-secret", "refresh_token": "refresh-secret"},
                  "/api?access_token=access-secret&safe=visible",
                  "Settings(kiwoom_secret_key='provider-secret')")),
                ("stage=applied attempt=2 payload=%s url=/api?safe=visible",
                 ('{"api_key":"escaped\\\"tail-secret"}',)),
            ):
                with self.subTest(configure=configure.__name__, mapping=isinstance(arguments, dict)):
                    stderr = io.StringIO()
                    with self._log_directory() as directory, redirect_stderr(stderr):
                        configure(Path(directory))
                        record = logging.LogRecord("safe.output", logging.WARNING, __file__, 0,
                                                   message, (arguments,) if isinstance(arguments, dict) else arguments, None)
                        original = record.__dict__.copy()
                        logging.getLogger(record.name).handle(record)
                        for handler in logging.getLogger().handlers:
                            handler.flush()
                        path = next(Path(directory).glob("*.log"))
                        outputs = (path.read_text(encoding="utf-8"), stderr.getvalue())
                    for output_index, output in enumerate(outputs):
                        self.assertIn("stage=applied attempt=2", output)
                        self.assertIn("safe=visible", output)
                        for secret in ("header-secret", "dsn-secret", "query-secret", "payload-secret",
                                       "client-secret", "mapping-secret", "libpq private secret",
                                       "basic-secret", "refresh-secret", "access-secret", "provider-secret",
                                       "tail-secret"):
                            self.assertNotIn(secret, output)
                        if configure is configure_logging and output_index == 0:
                            self.assertEqual([
                                "# kiwoom-monitor basic-log calendar-retention-v1",
                                outputs[1].removesuffix("\n"),
                            ], output.splitlines())
                        else:
                            self.assertEqual(1, len(output.splitlines()))
                    self.assertEqual(original, record.__dict__)

    def test_exception_chain_stack_and_cached_text_are_safe_for_each_sink(self) -> None:
        try:
            try:
                raise RuntimeError("postgresql://alice:cause-secret@db.test/db")
            except RuntimeError as cause:
                raise ValueError("password=origin-secret") from cause
        except ValueError:
            exception = sys.exc_info()
        for configure in (configure_logging, configure_server_logging):
            for cached in (None, "cached stage=failed api_key=cache-secret\r\nFORGED"):
                with self.subTest(configure=configure.__name__, cached=cached is not None):
                    stderr = io.StringIO()
                    with self._log_directory() as directory, redirect_stderr(stderr):
                        configure(Path(directory))
                        record = logging.LogRecord("safe.chain", logging.ERROR, __file__, 0,
                            "stage=native_failed", (), exception,
                            sinfo="stack api_key=stack-secret\r\nFORGED")
                        record.exc_text = cached
                        original = record.__dict__.copy()
                        logging.getLogger(record.name).handle(record)
                        for handler in logging.getLogger().handlers:
                            handler.flush()
                        outputs = (next(Path(directory).glob("*.log")).read_text(encoding="utf-8"),
                                   stderr.getvalue())
                    for output_index, output in enumerate(outputs):
                        self.assertIn("stage=native_failed", output)
                        if cached is None:
                            self.assertIn("RuntimeError", output)
                            self.assertIn("ValueError", output)
                        for secret in ("cause-secret", "origin-secret", "cache-secret", "stack-secret"):
                            self.assertNotIn(secret, output)
                        if configure is configure_logging and output_index == 0:
                            self.assertEqual([
                                "# kiwoom-monitor basic-log calendar-retention-v1",
                                outputs[1].removesuffix("\n"),
                            ], output.splitlines())
                        else:
                            self.assertEqual(1, len(output.splitlines()))
                    self.assertEqual(original, record.__dict__)
                    self.assertIs(exception[1], record.exc_info[1])
                    self.assertIsNotNone(exception[1].__cause__)

    def test_file_write_flush_and_rotation_faults_keep_safe_state_and_console(self) -> None:
        class FaultStream:
            def __init__(self, raw, phase):
                self.raw, self.phase, self.calls = raw, phase, 0
            def write(self, text):
                self.calls += 1
                if self.phase == "write":
                    raise PermissionError("unlabelled-private-fault")
                return self.raw.write(text)
            def flush(self):
                if self.phase == "flush":
                    raise OSError("unlabelled-private-fault")
                return self.raw.flush()

        for phase in ("write", "flush", "rotation"):
            with self.subTest(phase=phase):
                stderr = io.StringIO()
                with self._log_directory() as directory, redirect_stderr(stderr):
                    configure_server_logging(Path(directory))
                    file_handler = logging.getLogger().handlers[0]
                    logging.getLogger("safe.fault").warning("stage=before_fault")
                    if phase == "rotation":
                        with patch.object(file_handler, "shouldRollover", return_value=True), patch.object(
                                file_handler, "doRollover", side_effect=OSError("unlabelled-private-fault")) as fail:
                            logging.getLogger("safe.fault").warning("stage=still_running token=record-secret")
                            self.assertEqual(1, fail.call_count)
                    else:
                        stream = FaultStream(file_handler.stream, phase)
                        with patch.object(file_handler, "stream", stream):
                            logging.getLogger("safe.fault").warning("stage=still_running token=record-secret")
                        self.assertEqual(1, stream.calls)
                    self.assertEqual(("emit", "io_error"), getattr(file_handler, "sink_failure", None))
                    output = stderr.getvalue()
                    self.assertEqual(1, output.count("stage=still_running"))
                    self.assertNotIn("record-secret", output)
                    self.assertNotIn("unlabelled-private-fault", output)
                    self.assertNotIn("Logging error", output)

    def test_console_failure_never_dumps_raw_record_and_file_remains_active(self) -> None:
        class BrokenConsole(io.StringIO):
            def write(self, text):
                raise OSError("unlabelled-console-secret")

        stderr = io.StringIO()
        with self._log_directory() as directory, redirect_stderr(stderr):
            path = configure_server_logging(Path(directory))
            console = logging.getLogger().handlers[1]
            with patch.object(console, "stream", BrokenConsole()):
                for attempt in (1, 2):
                    logging.getLogger("safe.console").warning("stage=written attempt=%s token=raw-secret", attempt)
            for handler in logging.getLogger().handlers:
                handler.flush()
            self.assertEqual(("emit", "io_error"), getattr(console, "sink_failure", None))
            content = path.read_text(encoding="utf-8")
            self.assertEqual(2, content.count("stage=written"))
            self.assertNotIn("raw-secret", content)
            self.assertEqual("", stderr.getvalue())

    def test_delayed_file_open_and_format_failures_do_not_escape_or_dump_raw_args(self) -> None:
        for configure in (configure_logging, configure_server_logging):
            with self.subTest(configure=configure.__name__):
                stderr = io.StringIO()
                with self._log_directory() as directory, redirect_stderr(stderr):
                    configure(Path(directory))
                    handler = logging.getLogger().handlers[0]
                    handler.close()
                    # Reopening after close exercises FileHandler._open outside its
                    # StreamHandler emit guard as well as the rotating handler guard.
                    with patch.object(handler, "_open", side_effect=PermissionError("private-open-secret")):
                        logging.getLogger("safe.open").warning("stage=after_open_fault password=raw-secret")
                    self.assertEqual(("emit", "io_error"), getattr(handler, "sink_failure", None))
                    record = logging.LogRecord("safe.format", logging.ERROR, __file__, 0,
                        "stage=%(missing)s password=raw-secret", ({"other": "private-argument"},), None)
                    original = record.__dict__.copy()
                    logging.getLogger(record.name).handle(record)
                    self.assertEqual(("emit", "sink_error"), getattr(handler, "sink_failure", None))
                    self.assertEqual(original, record.__dict__)
                    output = stderr.getvalue()
                    self.assertIn("stage=after_open_fault", output)
                    for secret in ("private-open-secret", "raw-secret", "private-argument"):
                        self.assertNotIn(secret, output)
                    self.assertNotIn("Logging error", output)

    def test_sink_snapshot_reads_actual_configured_fault_once_and_keeps_sticky_evidence(self):
        from kiwoom_monitor.infrastructure.logging_config import logging_sink_failure_snapshot

        stderr = io.StringIO()
        with self._log_directory() as directory, redirect_stderr(stderr):
            path = configure_server_logging(Path(directory))
            handler = logging.getLogger().handlers[0]
            with patch.object(handler, "_open", side_effect=PermissionError("private-open-secret")):
                logging.getLogger("safe.status").warning("stage=failed token=private-token")
            expected = {
                "state": "recorded_failure", "coverage": "reported_only",
                "failure_semantics": "latest_recorded_since_handler_configuration",
                "failures": [{"output": "file", "stage": "emit", "kind": "io_error"}],
            }
            self.assertEqual(expected, logging_sink_failure_snapshot())
            logging.getLogger("safe.status").warning("stage=written_after_failure")
            for active in logging.getLogger().handlers:
                active.flush()
            with patch("builtins.open", side_effect=AssertionError("status must not probe files")):
                self.assertEqual(expected, logging_sink_failure_snapshot())
            self.assertIn("stage=written_after_failure", path.read_text(encoding="utf-8"))
            self.assertIn("stage=failed", stderr.getvalue())
            for secret in ("private-open-secret", "private-token", str(path)):
                self.assertNotIn(secret, str(expected))
            self.assertNotIn("private-token", stderr.getvalue())

    def test_sink_snapshot_includes_named_only_and_distinct_handlers_without_reconfiguring(self):
        from kiwoom_monitor.infrastructure.logging_config import SafeFileHandler, logging_sink_failure_snapshot

        with self._log_directory() as directory, redirect_stderr(io.StringIO()):
            configure_server_logging(Path(directory))
            root = logging.getLogger()
            file_handler, console = root.handlers
            second = SafeFileHandler(Path(directory) / "other-private-path.log", delay=True)
            file_handler.sink_failure = second.sink_failure = ("emit", "io_error")
            console.sink_failure = ("emit", "format_error")
            access, error = logging.getLogger("uvicorn.access"), logging.getLogger("uvicorn.error")
            registry_before = dict(root.manager.loggerDict)
            try:
                with patch.object(root, "handlers", [console]), patch.object(access, "handlers", [file_handler]), \
                        patch.object(error, "handlers", [file_handler, second, console]):
                    configured = (list(root.handlers), list(access.handlers), list(error.handlers))
                    snapshot = logging_sink_failure_snapshot()
                    self.assertEqual([
                        {"output": "console", "stage": "emit", "kind": "format_error"},
                        {"output": "file", "stage": "emit", "kind": "io_error"},
                        {"output": "file", "stage": "emit", "kind": "io_error"},
                    ], snapshot["failures"])
                    self.assertEqual(configured, (root.handlers, access.handlers, error.handlers))
                    self.assertEqual(registry_before, root.manager.loggerDict)
                    self.assertEqual(("emit", "io_error"), second.sink_failure)
            finally:
                second.close()

    def test_sink_snapshot_omits_malformed_evidence_and_keeps_unconfigured_state_unknown(self):
        from kiwoom_monitor.infrastructure.logging_config import logging_sink_failure_snapshot

        class PrivateValue:
            def __repr__(self):
                raise AssertionError("status must never format raw handler evidence")

        root = logging.getLogger()
        handler = logging.StreamHandler(io.StringIO())
        expected = {
            "state": "unknown", "coverage": "reported_only",
            "failure_semantics": "latest_recorded_since_handler_configuration", "failures": [],
        }
        registry = {name: object() for name in
                    ("uvicorn.access", "uvicorn.error", "kiwoom_monitor.kiwoom_api")}
        try:
            with patch.object(root, "handlers", []), patch.dict(root.manager.loggerDict, registry):
                keys_before = set(root.manager.loggerDict)
                self.assertEqual(expected, logging_sink_failure_snapshot())
                self.assertEqual(keys_before, set(root.manager.loggerDict))
                with patch.object(root, "handlers", [handler]):
                    for evidence in (None, ["emit", "io_error"], ("emit", "io_error", "private"),
                                     ("private-stage", "io_error"), ("emit", "private-kind"),
                                     (PrivateValue(), "io_error"), ("emit", PrivateValue())):
                        handler.sink_failure = evidence
                        self.assertEqual(expected, logging_sink_failure_snapshot())
        finally:
            handler.close()
    @staticmethod
    def _write_pc_at(timestamp: float, message: str) -> tuple[logging.LogRecord, dict]:
        logger = logging.getLogger("safe.pc.calendar")
        record = logging.LogRecord(logger.name, logging.INFO, __file__, 0, message, (), None)
        record.created = timestamp
        record.msecs = (timestamp * 1000) % 1000
        original = record.__dict__.copy()
        logger.handle(record)
        for handler in logging.getLogger().handlers:
            handler.flush()
        return record, original

    def test_pc_calendar_window_expires_sparse_owned_days_and_preserves_foreign_files(self):
        header = b"# kiwoom-monitor basic-log calendar-retention-v1\n"
        timestamp = datetime.fromisoformat("2026-10-27T09:00:00+09:00").timestamp()
        stderr = io.StringIO()
        with self._log_directory() as directory, redirect_stderr(stderr):
            root = Path(directory)
            expired = ("monitor-retained-v1-20261013-101.log",
                       "monitor-retained-v1-20261013-101.1.log",
                       "monitor-retained-v1-20240102-202.log")
            preserved = {
                "monitor-retained-v1-20261014-101.log": header + b"exact boundary",
                "monitor-retained-v1-20271027-101.log": header + b"future",
                "monitor-retained-v1-20261301-101.log": header + b"invalid date",
                "monitor-retained-v1-20240101-101.log": b"foreign unmarked bytes\xff",
                "monitor-retained-v1-20240101-101.0.log": header + b"invalid part",
                "monitor-20240101.log": b"legacy PC bytes",
                "server.log.2024-01-01": b"NAS archive bytes",
                "other.log": header + b"unrelated name",
            }
            for name in expired:
                (root / name).write_bytes(header + name.encode("ascii"))
            for name, content in preserved.items():
                (root / name).write_bytes(content)
            nested = root / "monitor-retained-v1-20240101-303.log"
            nested.mkdir()
            (nested / "keep.txt").write_bytes(header)
            configure_logging(root)
            record, original = self._write_pc_at(timestamp, "stage=new_date")
            self.assertEqual(original, record.__dict__)
            message = "2026-10-27 09:00:00,000 KST INFO safe.pc.calendar: stage=new_date\n"
            self.assertEqual(message, stderr.getvalue())
            self.assertEqual(header.decode("ascii") + message,
                             Path(logging.getLogger().handlers[0].baseFilename).read_text(encoding="utf-8"))
            for name in expired:
                self.assertFalse((root / name).exists(), name)
            for name, content in preserved.items():
                self.assertEqual(content, (root / name).read_bytes(), name)
            self.assertEqual(header, (nested / "keep.txt").read_bytes())

    def test_pc_midnight_scans_once_per_forward_kst_day_without_using_business_clock(self):
        boundary = datetime.fromisoformat("2026-10-28T00:00:00+09:00").timestamp()
        with self._log_directory() as directory, redirect_stderr(io.StringIO()), patch(
                "kiwoom_monitor.infrastructure.logging_config.os.getpid", return_value=7301):
            root = Path(directory)
            configure_logging(root)
            original_iterdir = Path.iterdir
            with patch.object(Path, "iterdir", autospec=True, side_effect=original_iterdir) as scans:
                self._write_pc_at(boundary - 1, "before midnight")
                self._write_pc_at(boundary - 1, "same day")
                self.assertEqual(1, scans.call_count)
                self._write_pc_at(boundary, "after midnight")
                self.assertEqual(2, scans.call_count)
                self._write_pc_at(boundary - 86401, "clock reversed")
                self._write_pc_at(boundary, "returned to current day")
                self.assertEqual(2, scans.call_count)
            before = (root / "monitor-retained-v1-20261027-7301.log").read_text(encoding="utf-8")
            after = (root / "monitor-retained-v1-20261028-7301.log").read_text(encoding="utf-8")
            self.assertIn("2026-10-27 23:59:59,000 KST", before)
            self.assertNotIn("after midnight", before)
            self.assertIn("2026-10-28 00:00:00,000 KST", after)
            self.assertNotIn("before midnight", after)

    def test_pc_collision_and_restart_exclusively_create_parts_without_rewriting_files(self):
        timestamp = datetime.fromisoformat("2026-10-27T09:00:00+09:00").timestamp()
        with self._log_directory() as directory, redirect_stderr(io.StringIO()), patch(
                "kiwoom_monitor.infrastructure.logging_config.os.getpid", return_value=7301):
            root = Path(directory)
            foreign = root / "monitor-retained-v1-20261027-7301.log"
            foreign.write_bytes(b"foreign collision remains unchanged")
            configure_logging(root)
            self._write_pc_at(timestamp, "first process generation")
            first = root / "monitor-retained-v1-20261027-7301.1.log"
            first_bytes = first.read_bytes()
            configure_logging(root)
            self._write_pc_at(timestamp, "restarted process generation")
            second = root / "monitor-retained-v1-20261027-7301.2.log"
            self.assertEqual(b"foreign collision remains unchanged", foreign.read_bytes())
            self.assertEqual(first_bytes, first.read_bytes())
            self.assertIn(b"first process generation", first_bytes)
            self.assertIn(b"restarted process generation", second.read_bytes())
            self.assertNotIn(b"first process generation", second.read_bytes())

    def test_pc_distinct_pid_writers_keep_independent_daily_files(self):
        from kiwoom_monitor.infrastructure.logging_config import KSTCalendarFileHandler

        timestamp = datetime.fromisoformat("2026-10-27T23:59:59+09:00").timestamp()
        with self._log_directory() as directory:
            root = Path(directory)
            with patch("kiwoom_monitor.infrastructure.logging_config.os.getpid", return_value=7301):
                main = KSTCalendarFileHandler(root)
            with patch("kiwoom_monitor.infrastructure.logging_config.os.getpid", return_value=7302):
                news = KSTCalendarFileHandler(root)
            try:
                for handler, message, created in ((main, "main before", timestamp),
                                                   (news, "news before", timestamp),
                                                   (main, "main after", timestamp + 1),
                                                   (news, "news after", timestamp + 1)):
                    record = logging.LogRecord("safe.pc.writer", logging.INFO, __file__, 0, message, (), None)
                    record.created = created
                    handler.handle(record)
                for pid, before, after in ((7301, b"main before", b"main after"),
                                           (7302, b"news before", b"news after")):
                    self.assertIn(before, (root / f"monitor-retained-v1-20261027-{pid}.log").read_bytes())
                    content = (root / f"monitor-retained-v1-20261028-{pid}.log").read_bytes()
                    self.assertIn(after, content)
                    self.assertNotIn(before, content)
            finally:
                main.close()
                news.close()

    def test_pc_retention_failure_keeps_file_and_console_and_safe_sticky_status(self):
        from kiwoom_monitor.infrastructure.logging_config import logging_sink_failure_snapshot

        timestamp = datetime.fromisoformat("2026-10-27T09:00:00+09:00").timestamp()
        stderr = io.StringIO()
        with self._log_directory() as directory, redirect_stderr(stderr):
            root = Path(directory)
            expired = root / "monitor-retained-v1-20261013-101.log"
            original_bytes = b"# kiwoom-monitor basic-log calendar-retention-v1\nold owned bytes"
            expired.write_bytes(original_bytes)
            configure_logging(root)
            original_unlink = Path.unlink
            def fail_busy(path, *args, **kwargs):
                if path == expired:
                    raise PermissionError("private-maintenance-path token=raw-private-fault")
                return original_unlink(path, *args, **kwargs)
            with patch.object(Path, "unlink", autospec=True, side_effect=fail_busy) as cleanup:
                self._write_pc_at(timestamp, "stage=still_written token=raw-record-secret")
                self._write_pc_at(timestamp, "stage=next_record")
                self.assertEqual(1, cleanup.call_count)
            self.assertEqual(original_bytes, expired.read_bytes())
            snapshot = logging_sink_failure_snapshot()
            self.assertEqual({
                "state": "recorded_failure", "coverage": "reported_only",
                "failure_semantics": "latest_recorded_since_handler_configuration",
                "failures": [{"output": "file", "stage": "retention", "kind": "io_error"}],
            }, snapshot)
            current = Path(logging.getLogger().handlers[0].baseFilename).read_text(encoding="utf-8")
            for output in (current, stderr.getvalue()):
                self.assertIn("stage=still_written", output)
                self.assertIn("stage=next_record", output)
                for secret in ("raw-record-secret", "raw-private-fault", "private-maintenance-path"):
                    self.assertNotIn(secret, output)
            self.assertNotIn(str(root), str(snapshot))

    def test_pc_reparse_candidate_is_preserved_and_recursion_error_still_escapes(self):
        from kiwoom_monitor.infrastructure import logging_config

        timestamp = datetime.fromisoformat("2026-10-27T09:00:00+09:00").timestamp()
        with self._log_directory() as directory, redirect_stderr(io.StringIO()):
            root = Path(directory)
            foreign = root / "monitor-retained-v1-20261013-101.log"
            original_bytes = b"# kiwoom-monitor basic-log calendar-retention-v1\nreparse foreign bytes"
            foreign.write_bytes(original_bytes)
            configure_logging(root)
            original_lstat = Path.lstat
            class ReparseMetadata:
                st_mode = original_lstat(foreign).st_mode
                st_file_attributes = 0x400
            def reparse_metadata(path, *args, **kwargs):
                return ReparseMetadata() if path == foreign else original_lstat(path, *args, **kwargs)
            with patch.object(logging_config.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, create=True), \
                    patch.object(Path, "lstat", autospec=True, side_effect=reparse_metadata):
                self._write_pc_at(timestamp, "stage=foreign_preserved")
            self.assertEqual(original_bytes, foreign.read_bytes())
            handler = logging.getLogger().handlers[0]
            handler.close()
            with patch.object(handler, "_open", side_effect=RecursionError("private-recursion")):
                with self.assertRaises(RecursionError):
                    self._write_pc_at(timestamp, "stage=recursion")


if __name__ == "__main__":
    unittest.main()
