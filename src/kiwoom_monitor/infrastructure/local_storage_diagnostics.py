"""Read-only disk usage inventory for the PC application's data directory."""

from __future__ import annotations

import os
from pathlib import Path


_CATEGORY_LABELS = {
    "market": "주식·설정 DB",
    "news": "뉴스 DB",
    "journal": "매매일지 DB",
    "research": "전략 연구·시뮬레이션",
    "logs": "로그",
    "other": "기타",
}


def _category(relative: Path) -> str:
    parts = tuple(part.lower() for part in relative.parts)
    name = relative.name.lower()
    first = parts[0] if parts else ""
    if name.startswith("news.sqlite3"):
        return "news"
    if name.startswith("journal.sqlite3"):
        return "journal"
    if name.startswith("monitor.sqlite3"):
        return "market"
    if first == "research" or "research" in name or "simulation" in name or "replay" in name:
        return "research"
    if first == "logs" or name.endswith(".log"):
        return "logs"
    return "other"


def inspect_local_storage(data_dir: Path, *, max_entries: int = 100_000) -> dict[str, object]:
    """Measure files without opening live SQLite databases or following redirects."""
    root = Path(data_dir)
    totals = {key: {"category": key, "label": label, "bytes": 0, "files": 0}
              for key, label in _CATEGORY_LABELS.items()}
    complete = True
    visited = 0
    pending = [root]
    try:
        while pending:
            current = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited > max_entries:
                        complete = False
                        pending.clear()
                        break
                    if entry.is_symlink():
                        complete = False
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        complete = False
                        continue
                    path = Path(entry.path)
                    bucket = totals[_category(path.relative_to(root))]
                    bucket["bytes"] = int(bucket["bytes"]) + entry.stat(follow_symlinks=False).st_size
                    bucket["files"] = int(bucket["files"]) + 1
    except OSError:
        complete = False

    categories = [value for value in totals.values() if value["files"]]
    return {
        "complete": complete,
        "total_bytes": sum(int(value["bytes"]) for value in categories),
        "categories": categories,
        "retention": [
            "분봉: 30일 초과분을 앱 시작 뒤 정리",
            "일봉: 종목별 최근 250개를 유지",
            "뉴스·매매일지·완료된 연구 자료: 자동 삭제 없음",
        ],
    }
