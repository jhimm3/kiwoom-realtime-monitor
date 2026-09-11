"""작은 중앙 수집 결과를 컨테이너 재시작 사이에도 보존하는 파일 outbox."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any


class JsonRecordOutbox:
    """키별 최신 JSON 레코드를 원자적 파일 교체로 보존한다."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()

    def load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            try:
                value = json.loads(self._path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(key): dict(document)
            for key, document in value.items()
            if isinstance(document, dict)
        }

    def put(self, key: str, document: dict[str, Any]) -> None:
        with self._lock:
            values = self.load()
            values[key] = document
            self._write(values)

    def remove(self, key: str) -> None:
        with self._lock:
            values = self.load()
            if key not in values:
                return
            values.pop(key, None)
            self._write(values)

    def _write(self, values: dict[str, dict[str, Any]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp-{id(self)}")
        temporary.write_text(
            json.dumps(values, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self._path)
