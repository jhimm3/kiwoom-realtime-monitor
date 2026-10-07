import sqlite3
from contextlib import closing
from pathlib import Path

from scripts.collect_candidate_event_disclosures import _dart_query_code, candidate_events, seed


def test_candidate_events_keep_adjustments_and_observed_trading_gaps_distinct(tmp_path: Path):
    candidates = tmp_path / "candidates.sqlite3"
    context = tmp_path / "context.sqlite3"
    with closing(sqlite3.connect(candidates)) as connection:
        connection.executescript("""
            CREATE TABLE stocks(code TEXT PRIMARY KEY, reg_day TEXT, market_code TEXT);
            CREATE TABLE candidate_days(code TEXT, dt TEXT);
            CREATE TABLE daily_bars(code TEXT, dt TEXT, volume INTEGER,
                                    PRIMARY KEY(code,dt));
            INSERT INTO stocks VALUES('123456','2025-01-02','10');
            INSERT INTO stocks VALUES('999999','2025-01-02','8');
            INSERT INTO candidate_days VALUES('123456','2025-01-03');
            INSERT INTO candidate_days VALUES('999999','2025-01-03');
            INSERT INTO daily_bars VALUES('123456','2025-01-02',100);
            INSERT INTO daily_bars VALUES('123456','2025-01-03',0);
            INSERT INTO daily_bars VALUES('123456','2025-01-06',0);
            INSERT INTO daily_bars VALUES('123456','2025-01-07',0);
            INSERT INTO daily_bars VALUES('123456','2025-01-08',0);
            INSERT INTO daily_bars VALUES('123456','2025-01-09',0);
            INSERT INTO daily_bars VALUES('123456','2025-01-10',200);
            INSERT INTO daily_bars VALUES('999999','2025-01-02',0);
        """)
    with closing(sqlite3.connect(context)) as connection:
        connection.executescript("""
            CREATE TABLE historical_stock_adjustments(code TEXT, adjustment_date TEXT);
            CREATE TABLE adjustment_disclosure_jobs(code TEXT, adjustment_date TEXT,
                raw_file TEXT, state TEXT);
            CREATE TABLE historical_stock_adjustment_disclosures(
                code TEXT, adjustment_date TEXT, receipt_no TEXT, receipt_date TEXT,
                report_name TEXT, filer_name TEXT, filing_url TEXT, raw_file TEXT,
                observed_at TEXT);
            INSERT INTO historical_stock_adjustments VALUES('123456','2025-01-10');
            INSERT INTO adjustment_disclosure_jobs VALUES('123456','2025-01-10','old.json','complete');
            INSERT INTO historical_stock_adjustment_disclosures VALUES(
                '123456','2025-01-10','20250110000001','2025-01-10',
                '분할','회사','https://example.test/filing','old.json','2025-01-10T01:00:00Z');
        """)
    count, events = candidate_events(candidates, context, "2025-01-15")
    assert count == 1
    assert {(code, day, kind) for code, day, kind, _, _ in events} == {
        ("123456", "2025-01-02", "listing"),
        ("123456", "2025-01-03", "zero_volume_start"),
        ("123456", "2025-01-10", "zero_volume_resume"),
        ("123456", "2025-01-10", "price_adjustment"),
    }
    result = seed(candidates, context, "2025-01-15")
    assert result["job_states"] == {"pending": 3, "reused_existing": 1}
    assert seed(candidates, context, "2025-01-15")["job_states"] == result["job_states"]
    with closing(sqlite3.connect(context)) as connection:
        assert connection.execute(
            "SELECT raw_file FROM historical_candidate_event_jobs WHERE event_kind='price_adjustment'"
        ).fetchone()[0] == "old.json"
        assert connection.execute(
            "SELECT receipt_no FROM historical_candidate_event_disclosures "
            "WHERE event_kind='price_adjustment'"
        ).fetchone()[0] == "20250110000001"


def test_preferred_share_uses_verified_common_issuer_name():
    corp_codes = {"000880": "corp", "123450": "another", "007810": "third"}
    names = {"000880": "한화", "00088K": "한화3우B",
             "123450": "다른회사", "123455": "비슷한회사우",
             "007810": "코리아써키트", "007815": "코리아써우"}
    assert _dart_query_code("00088K", corp_codes, names) == "000880"
    assert _dart_query_code("007815", corp_codes, names) == "007810"
    assert _dart_query_code("123455", corp_codes, names) == ""
