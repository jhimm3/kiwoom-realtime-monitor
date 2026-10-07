from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import collect_historical_market_context as collector  # noqa: E402
import collect_candidate_exchange_effective_dates as effective_collector  # noqa: E402
import import_krx_vi_history as vi  # noqa: E402
import publish_historical_market_context_to_nas as publisher  # noqa: E402


def test_publishes_context_without_copying_large_main_database(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
    data = tmp_path / "data"
    database = data / "historical_market_context.sqlite3"
    collector.initialize(database, reference)
    raw_root = data / "historical_collection" / "context"
    with closing(sqlite3.connect(database)) as connection:
        vi.initialize(connection)
        for kind, code in connection.execute("SELECT kind,code FROM context_jobs"):
            raw = raw_root / kind / code / "test.ndjson"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("{}\n", encoding="utf-8")
            connection.execute(
                "UPDATE context_jobs SET state='complete',raw_file=? WHERE kind=? AND code=?",
                (str(raw), kind, code),
            )
        event_raw = data / "historical_collection" / "context" / "dart_candidate_events" / "123456" / "event.json"
        event_raw.parent.mkdir(parents=True, exist_ok=True)
        event_raw.write_text("{}\n", encoding="utf-8")
        connection.execute(
            "CREATE TABLE historical_candidate_event_jobs(event_kind TEXT,state TEXT,raw_file TEXT)"
        )
        connection.execute(
            "INSERT INTO historical_candidate_event_jobs VALUES('listing','complete',?)",
            (str(event_raw),),
        )
        exchange_raw = data / "historical_collection" / "context" / "dart_exchange" / "123456.json"
        exchange_raw.parent.mkdir(parents=True, exist_ok=True)
        exchange_raw.write_text("{}\n", encoding="utf-8")
        connection.execute(
            "CREATE TABLE historical_candidate_exchange_jobs(state TEXT,raw_file TEXT)"
        )
        connection.execute(
            "INSERT INTO historical_candidate_exchange_jobs VALUES('complete',?)",
            (str(exchange_raw),),
        )
        effective_raw = data / "historical_collection" / "context" / "dart_effective" / "20260908900687.zip"
        effective_raw.parent.mkdir(parents=True, exist_ok=True)
        effective_raw.write_bytes(b"official-document-fixture")
        connection.execute(
            "CREATE TABLE historical_candidate_exchange_disclosures("
            "code TEXT,receipt_no TEXT,receipt_date TEXT,report_name TEXT,filer_name TEXT)"
        )
        connection.execute(
            "INSERT INTO historical_candidate_exchange_disclosures VALUES(?,?,?,?,?)",
            ("008290", "20260908900687", "2026-09-08", "기타시장안내(상장폐지)", "코스닥시장본부"),
        )
        effective_collector.initialize(connection)
        connection.execute(
            "INSERT INTO historical_exchange_document_fetch "
            "(receipt_no,receipt_date,state,raw_file,raw_sha256) VALUES(?,?,?,?,?)",
            ("20260908900687", "2026-09-08", "complete", str(effective_raw), "fixture"),
        )
        connection.commit()
    nas = tmp_path / "nas"
    (nas / "deploy" / "synology" / "server-data").mkdir(parents=True)
    (nas / "AGENTS.md").write_text("test\n", encoding="utf-8")
    original_default = publisher.DEFAULT_NAS_PROJECT
    original_argv = sys.argv
    try:
        publisher.DEFAULT_NAS_PROJECT = nas
        sys.argv = ["publish", "--database", str(database), "--nas-project", str(nas),
                    "--expected-vi-files", "0"]
        assert publisher.main() == 0
    finally:
        publisher.DEFAULT_NAS_PROJECT = original_default
        sys.argv = original_argv
    root = nas / "deploy" / "synology" / "server-data" / "historical-market-context" / "v1"
    latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
    run = root / "runs" / latest["run"]
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["database"]["integrity_check"] == "ok"
    assert len(manifest["raw_artifacts"]) == 11
    assert any("dart_candidate_events/123456/event.json" in item["name"]
               for item in manifest["raw_artifacts"])
    assert any("dart_exchange/123456.json" in item["name"]
               for item in manifest["raw_artifacts"])
    assert any("dart_effective/20260908900687.zip" in item["name"]
               for item in manifest["raw_artifacts"])
    assert manifest["readiness"]["exchange_effective_expected"] == 1
    assert (run / "historical_market_context.sqlite3").exists()


def test_readiness_reports_provider_coverage_gap(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        with connection:
            connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
            connection.execute("INSERT INTO candidate_days VALUES('008290','2026-09-21')")
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    with closing(sqlite3.connect(database)) as connection:
        vi.initialize(connection)
        with connection:
            connection.execute("UPDATE context_jobs SET state='complete' WHERE kind<>'stock_daily'")
            connection.execute("UPDATE context_jobs SET state='unavailable',error='provider rejected code' "
                               "WHERE kind='stock_daily'")
    readiness = publisher.assert_ready(database, 0)
    assert readiness["coverage_complete"] is False
    assert readiness["job_states"]["unavailable"] == 1


def test_effective_date_collection_must_finish_before_publish(tmp_path: Path) -> None:
    reference = tmp_path / "reference.sqlite3"
    with closing(sqlite3.connect(reference)) as connection:
        connection.execute("CREATE TABLE candidate_days(code TEXT,dt TEXT)")
    database = tmp_path / "context.sqlite3"
    collector.initialize(database, reference)
    with closing(sqlite3.connect(database)) as connection:
        vi.initialize(connection)
        connection.execute("UPDATE context_jobs SET state='complete'")
        connection.execute(
            "CREATE TABLE historical_candidate_exchange_disclosures("
            "code TEXT,receipt_no TEXT,receipt_date TEXT,report_name TEXT,filer_name TEXT)"
        )
        connection.execute(
            "INSERT INTO historical_candidate_exchange_disclosures VALUES(?,?,?,?,?)",
            ("008290", "20260908900687", "2026-09-08", "주권매매거래정지", "코스닥시장본부"),
        )
        effective_collector.initialize(connection)
        connection.commit()
    try:
        publisher.assert_ready(database, 0)
    except RuntimeError as error:
        assert "incomplete" in str(error)
    else:
        raise AssertionError("An uncollected effective-date document was published")
