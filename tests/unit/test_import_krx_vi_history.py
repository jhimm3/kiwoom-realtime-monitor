from __future__ import annotations

import csv
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "import_krx_vi_history.py"
SPEC = importlib.util.spec_from_file_location("import_krx_vi_history", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
vi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vi)


def test_import_deduplicates_overlapping_krx_files_and_keeps_times(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    event = ["1", "2026/09/22", "005930", "삼성전자", "KOSPI", "09:01:00",
             "09:03:05", "70100", "0", "0.00", "65000", "7.85", "동적VI"]
    for path, rows in ((first, [event]), (second, [event, ["2", "2026/09/22",
                        "000660", "SK하이닉스", "KOSPI", "09:04:00", "09:06:00",
                        "210000", "200000", "5.00", "0", "0.00", "정적VI"]])):
        with path.open("w", encoding="cp949", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(vi.HEADERS)
            writer.writerows(rows)
    database = tmp_path / "context.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        vi.initialize(connection)
        a = vi.import_file(connection, first, tmp_path / "raw")
        b = vi.import_file(connection, second, tmp_path / "raw")
        assert (a["inserted"], b["inserted"]) == (1, 1)
        assert connection.execute("SELECT COUNT(*) FROM historical_vi_events").fetchone()[0] == 2
        assert connection.execute(
            "SELECT triggered_at,released_at FROM historical_vi_events WHERE code='005930'"
        ).fetchone() == ("2026-09-22T09:01:00+09:00", "2026-09-22T09:03:05+09:00")
        assert vi.import_file(connection, second, tmp_path / "raw")["status"] == "already_imported"
