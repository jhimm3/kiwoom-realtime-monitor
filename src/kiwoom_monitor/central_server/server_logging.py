from __future__ import annotations

import logging.config
from logging.handlers import TimedRotatingFileHandler
import re
from datetime import time as datetime_time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class KSTFormatter(logging.Formatter):
    """Render timestamps in Korea Standard Time independently of the host timezone."""

    _timezone = ZoneInfo("Asia/Seoul")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).astimezone(self._timezone)
        rendered = timestamp.strftime(datefmt or "%Y-%m-%d %H:%M:%S")
        return f"{rendered},{int(record.msecs):03d} KST"


class KSTDailyFileHandler(TimedRotatingFileHandler):
    """Name midnight archives by KST date and preserve pre-existing archives."""

    def rotation_filename(self, default_name: str) -> str:
        completed_day = datetime.fromtimestamp(
            self.rolloverAt - 1, timezone.utc,
        ).astimezone(KSTFormatter._timezone).strftime("%Y-%m-%d")
        base = f"{self.baseFilename}.{completed_day}"
        candidate, part = Path(base), 0
        while candidate.exists():
            part += 1
            candidate = Path(f"{base}.{part}")
        return str(candidate)

    def getFilesToDelete(self) -> list[str]:
        if self.backupCount <= 0:
            return []
        base = Path(self.baseFilename)
        pattern = re.compile(re.escape(base.name) + r"\.(\d{4}-\d{2}-\d{2})(?:\.\d+)?\Z")
        archives: dict[str, list[str]] = {}
        for path in base.parent.iterdir():
            match = pattern.fullmatch(path.name)
            if match and path.is_file():
                archives.setdefault(match[1], []).append(str(path))
        expired_days = sorted(archives)[:-self.backupCount]
        return sorted(path for day in expired_days for path in archives[day])


def configure_server_logging(log_dir: Path, *, retention_days: int = 14) -> Path:
    """Keep request details in bounded daily files while preserving useful console status."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "server.log"
    logging.config.dictConfig(_logging_config(log_path, retention_days=retention_days))
    return log_path


def _logging_config(log_path: Path, *, retention_days: int) -> dict[str, Any]:
    retention = max(1, min(int(retention_days), 365))
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "()": "kiwoom_monitor.central_server.server_logging.KSTFormatter",
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
            },
        },
        "handlers": {
            "daily_file": {
                "class": "kiwoom_monitor.central_server.server_logging.KSTDailyFileHandler",
                "filename": str(log_path),
                "when": "midnight",
                # UTC scheduling is host-independent; the handler names archives in KST.
                "atTime": datetime_time(15, 0),
                "utc": True,
                "interval": 1,
                "backupCount": retention,
                "encoding": "utf-8",
                "delay": True,
                "formatter": "standard",
                "level": "INFO",
            },
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "standard",
                "level": "INFO",
            },
        },
        "loggers": {
            # HTTP request lines are high-volume details. Keep them out of Docker stdout.
            "uvicorn.access": {
                "handlers": ["daily_file"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["daily_file", "console"],
                "level": "INFO",
                "propagate": False,
            },
            # Actual NAS -> Kiwoom REST calls, without bodies, keys, or account values.
            "kiwoom_monitor.kiwoom_api": {
                "handlers": ["daily_file"],
                "level": "INFO",
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["daily_file", "console"],
            "level": "INFO",
        },
    }
