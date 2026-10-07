"""Best-effort append-only timing records shared by the PC news collectors."""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path


class NewsTimingLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._warned = False

    def write(self, event: str, **fields: object) -> None:
        if self.path is None:
            return
        record = {"at_utc": datetime.now(UTC).isoformat(), "event": event, **fields}
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as error:
            if not self._warned:
                self._warned = True
                print(f"news diagnostic log unavailable: {type(error).__name__}: {error}",
                      file=sys.stderr, flush=True)
