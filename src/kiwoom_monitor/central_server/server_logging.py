from __future__ import annotations

import logging.config
from pathlib import Path
from typing import Any


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
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
            },
        },
        "handlers": {
            "daily_file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "filename": str(log_path),
                "when": "midnight",
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
