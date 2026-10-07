import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import collect_candidate_exchange_disclosures as collector  # noqa: E402


def test_exchange_filings_are_saved_with_receipt_date_and_raw_snapshot(tmp_path: Path):
    candidates = tmp_path / "candidates.sqlite3"
    context = tmp_path / "context.sqlite3"
    cache = tmp_path / "corp.json"
    config = tmp_path / "config.dat"
    raw = tmp_path / "raw"
    with closing(sqlite3.connect(candidates)) as connection:
        connection.executescript("""
            CREATE TABLE stocks(code TEXT PRIMARY KEY,name TEXT,market_code TEXT);
            CREATE TABLE candidate_days(code TEXT,dt TEXT);
            INSERT INTO stocks VALUES('123456','회사','10');
            INSERT INTO candidate_days VALUES('123456','2025-01-10');
        """)
    cache.write_text(json.dumps({"123456": "01234567"}), encoding="utf-8")
    config.write_text("test", encoding="utf-8")
    assert collector.seed(candidates, context, "2025-01-31")["candidate_stocks"] == 1

    class FakeConfig:
        def __init__(self, _path):
            pass

        def load_official(self):
            return SimpleNamespace(dart_enabled=True, dart_api_key="test-key")

    class FakeClient:
        def __init__(self, _key, _cache):
            pass

        def list_disclosures(self, code, begin, end, *, disclosure_type=""):
            assert (code, disclosure_type) == ("123456", "I")
            return ({"rcept_no": "20250110900001", "rcept_dt": "20250110",
                     "report_nm": "주권매매거래정지", "flr_nm": "거래소"},)

    with patch.object(collector, "LocalNaverNewsConfig", FakeConfig), patch.object(
        collector, "DartDisclosureClient", FakeClient
    ):
        result = collector.collect(candidates, context, config, cache, raw,
                                   jobs=1, delay_seconds=0)
    assert result["job_states"] == {"complete": 1}
    with closing(sqlite3.connect(context)) as connection:
        assert connection.execute(
            "SELECT receipt_date,report_name FROM historical_candidate_exchange_disclosures"
        ).fetchone() == ("2025-01-10", "주권매매거래정지")
    payload = json.loads((raw / "123456.json").read_text(encoding="utf-8"))
    assert payload["disclosure_type"] == "I"
    assert payload["query_end"] == "2025-01-31"
