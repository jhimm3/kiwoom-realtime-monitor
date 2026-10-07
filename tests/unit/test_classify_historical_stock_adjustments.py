from __future__ import annotations

import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "classify_historical_stock_adjustments.py"
SPEC = importlib.util.spec_from_file_location("classify_historical_stock_adjustments", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
classifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(classifier)


def test_classifies_unique_event_and_requires_review_for_multiple_types() -> None:
    event_type, status, evidence = classifier.classify((
        {"rcept_no": "1", "rcept_dt": "20210101", "report_nm": "주식분할 결정"},
        {"rcept_no": "2", "rcept_dt": "20210102", "report_nm": "[기재정정]주식분할 결정"},
    ))
    assert (event_type, status) == ("stock_split", "classified")
    assert len(evidence) == 2
    assert classifier.classify((
        {"rcept_no": "1", "report_nm": "감자 결정"},
        {"rcept_no": "2", "report_nm": "유상증자 결정"},
    ))[:2] == ("ambiguous", "manual_review")
    assert classifier.classify(({"report_nm": "사업보고서"},))[:2] == ("unknown", "unmatched")


def test_seeds_dart_jobs_only_from_collected_candidate_adjustments(tmp_path: Path) -> None:
    database = tmp_path / "context.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE historical_stock_adjustments("
                "provider TEXT,code TEXT,adjustment_date TEXT,adjustment_rate REAL,"
                "previous_factor REAL,new_factor REAL,previous_bar_date TEXT,"
                "source_bar_date TEXT,raw_file TEXT,observed_at TEXT)"
            )
            connection.execute(
                "INSERT INTO historical_stock_adjustments VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("daishin_creon", "035720", "2021-04-15", 20.0,
                 500.0, 100.0, "2021-04-14", "2021-04-15", "raw", "now"),
            )
    assert classifier.initialize(database) == 1
    assert classifier.initialize(database) == 0
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT code,adjustment_date,state FROM adjustment_disclosure_jobs"
        ).fetchone() == ("035720", "2021-04-15", "pending")
