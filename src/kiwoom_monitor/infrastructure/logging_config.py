from __future__ import annotations

import logging
import os
import re
import stat
import sys
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path

from .time_display import KST


_SECRET_FIELD = (
    r"(?:(?:kiwoom|openai|gemini|claude|naver|dart)[_-]|central[_-]server[_-])?"
    r"(?:access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|app[_-]?key|"
    r"secret[_-]?key|client[_-]?secret|app[_-]?secret|master[_-]?key|"
    r"authorization|proxy[_-]?authorization|password|passwd|pwd|token|secret)"
)
_LOG_SECRETS = (
    # HTTP and PostgreSQL URLs may contain userinfo independently of query keys.
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s\"']*@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[^\s,;\"'<>]+"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)([?&;]" + _SECRET_FIELD + r"=)[^&#\s\"'<>]*"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\b" + _SECRET_FIELD + r"[\"']?\s*[:=]\s*)"
                r"(?:\"(?:\\.|[^\"\\\r\n])*\"|'(?:\\.|[^'\\\r\n])*'|[^\s,;}\]&)]+)"),
     r"\1[REDACTED]"),
)


def safe_log_text(value: str) -> str:
    """Protect labelled secrets in rendered logs, with fixed non-recursive passes.

    This is an output boundary, not a credential store or a general PII detector.
    Newlines are escaped so message/exception text cannot forge another record.
    """
    for pattern, replacement in _LOG_SECRETS:
        value = pattern.sub(replacement, value)
    return value.replace("\r", r"\r").replace("\n", r"\n")


def format_safe_record(formatter: logging.Formatter, record: logging.LogRecord) -> str:
    # Formatter.format normally caches message/asctime/exception text on the record.
    # Keep those writes and sanitization away from other handlers and business code.
    return safe_log_text(logging.Formatter.format(formatter, copy(record)))


def record_sink_failure(handler: logging.Handler, *, stage: str = "emit") -> None:
    """Retain only the latest safe failure kind/stage, without logging recursively."""
    error = sys.exc_info()[1]
    kind = ("io_error" if isinstance(error, OSError) else
            "format_error" if isinstance(error, (TypeError, ValueError)) else "sink_error")
    handler.sink_failure = (stage, kind)


class SafeFileHandler(logging.FileHandler):
    sink_failure: tuple[str, str] | None = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except RecursionError:
            raise  # Preserve logging.StreamHandler's recursion failure behavior.
        except Exception:
            # FileHandler opens a delayed/reopened file outside StreamHandler.emit.
            self.handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:
        record_sink_failure(self)


class SafeStreamHandler(logging.StreamHandler):
    sink_failure: tuple[str, str] | None = None

    def handleError(self, record: logging.LogRecord) -> None:
        record_sink_failure(self)


class KSTCalendarFileHandler(SafeFileHandler):
    """Own new PC log files; keep the current KST date and its prior 13 dates.

    Only log placement/retention uses the approved PC LogRecord epoch clock.
    Business clocks and the record itself are unchanged. Distinct PID/part files
    avoid renaming another process's active file, and old namespaces are ignored.
    """

    _owner_header = b"# kiwoom-monitor basic-log calendar-retention-v1"
    _owned_name = re.compile(
        r"monitor-retained-v1-(\d{8})-[1-9]\d*(?:\.[1-9]\d*)?\.log\Z"
    )

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir.resolve()
        self._pid = os.getpid()
        self._day: date | None = None
        self._retention_day: date | None = None
        # Delayed: no file is created before the first record supplies its epoch.
        super().__init__(self._log_dir / "monitor-retained-v1.log", mode="x",
                         encoding="utf-8", delay=True)

    def _open(self):
        assert self._day is not None
        stem = f"monitor-retained-v1-{self._day:%Y%m%d}-{self._pid}"
        part = 0
        while True:
            suffix = f".{part}" if part else ""
            self.baseFilename = str(self._log_dir / f"{stem}{suffix}.log")
            try:
                stream = super()._open()
                break
            except FileExistsError:
                part += 1  # Exclusive creation also handles concurrent collisions.
        try:
            stream.write(self._owner_header.decode("ascii") + "\n")
            stream.flush()
        except BaseException:
            stream.close()
            raise
        return stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = datetime.fromtimestamp(record.created, KST).date()
            if day != self._day:
                self.close()
                self._day = day
            super().emit(record)
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)
            return
        if self.stream is not None and (self._retention_day is None or day > self._retention_day):
            # A failed cleanup is reported once, then retried next day/restart.
            # Clock reversal cannot cause repeated directory scans or purges.
            self._retention_day = day
            try:
                self._remove_expired(day)
            except RecursionError:
                raise
            except Exception:
                record_sink_failure(self, stage="retention")

    def _remove_expired(self, day: date) -> None:
        cutoff = day - timedelta(days=13)
        for path in self._log_dir.iterdir():
            match = self._owned_name.fullmatch(path.name)
            if not match or str(path) == self.baseFilename:
                continue
            raw_day = match[1]
            try:
                file_day = date.fromisoformat(f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}")
            except ValueError:
                continue
            if file_day >= cutoff:
                continue  # Includes future files and the exact retained boundary.
            try:
                metadata = path.lstat()
                if (not stat.S_ISREG(metadata.st_mode)
                        or getattr(metadata, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                    continue
                if path.resolve() != path:
                    continue
                with path.open("rb") as source:
                    header = source.readline(len(self._owner_header) + 3)
                if header not in (self._owner_header + b"\n", self._owner_header + b"\r\n"):
                    continue  # A matching filename alone never establishes ownership.
                path.unlink()
            except FileNotFoundError:
                continue  # Another owned cleanup may already have removed this file.
            except OSError:
                # Windows preserves files still open by another process. Keep
                # busy/unreadable files and expose safe evidence, without relogging.
                record_sink_failure(self, stage="retention")


class KSTFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return format_safe_record(self, record)

    def converter(self, timestamp: float):
        return datetime.fromtimestamp(timestamp, KST).timetuple()


def logging_sink_failure_snapshot() -> dict[str, object]:
    """Project safe recorded failures; this does not probe current sink health.

    Only root and the existing server logger names participate. Known-name
    registry reads avoid creating loggers in query-only processes. A handler's
    latest recorded failure stays visible after later successful writes.
    """
    root = logging.getLogger()
    owners = [root]
    for name in ("uvicorn.access", "uvicorn.error", "kiwoom_monitor.kiwoom_api"):
        owner = root.manager.loggerDict.get(name)
        if isinstance(owner, logging.Logger):
            owners.append(owner)
    failures = []
    seen = set()
    for owner in owners:
        for handler in owner.handlers:
            identity = id(handler)
            if identity in seen:
                continue
            seen.add(identity)
            if isinstance(handler, logging.FileHandler):
                output = "file"
            elif isinstance(handler, logging.StreamHandler):
                output = "console"
            else:
                continue
            failure = vars(handler).get("sink_failure")
            if (type(failure) is tuple and len(failure) == 2
                    and type(failure[0]) is str and type(failure[1]) is str
                    and failure[0] in {"emit", "retention"}
                    and failure[1] in {"io_error", "format_error", "sink_error"}):
                failures.append({"output": output, "stage": failure[0], "kind": failure[1]})
    return {
        "state": "recorded_failure" if failures else "unknown",
        "coverage": "reported_only",
        "failure_semantics": "latest_recorded_since_handler_configuration",
        "failures": failures,
    }


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers = [KSTCalendarFileHandler(log_dir), SafeStreamHandler()]
    formatter = KSTFormatter("%(asctime)s KST %(levelname)s %(name)s: %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s KST %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
