from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from .time_display import KST


class KSTFormatter(logging.Formatter):
    def converter(self, timestamp: float):
        return datetime.fromtimestamp(timestamp, KST).timetuple()


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    daily_log = log_dir / f"monitor-{datetime.now(KST):%Y%m%d}.log"
    handlers = [logging.FileHandler(daily_log, encoding="utf-8"), logging.StreamHandler()]
    formatter = KSTFormatter("%(asctime)s KST %(levelname)s %(name)s: %(message)s")
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s KST %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
