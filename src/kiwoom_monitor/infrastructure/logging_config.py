from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    daily_log = log_dir / f"monitor-{datetime.now():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(daily_log, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
