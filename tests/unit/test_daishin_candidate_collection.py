from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/run_daishin_candidate_collection.py"
SPEC = importlib.util.spec_from_file_location("daishin_candidate_collection", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class DaishinCandidateCollectionTest(unittest.TestCase):
    def test_reads_connection_failure_from_bridge_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "1m.ndjson"
            output.write_text(json.dumps({
                "record_type": "error",
                "error": collector.CREON_CONNECTION_ERROR,
            }), encoding="utf-8")
            completed = type("Completed", (), {"returncode": 1, "stderr": ""})()
            with patch.object(collector.subprocess, "run", return_value=completed):
                with self.assertRaises(collector.DaishinEnvironmentUnavailable):
                    collector._run_backfill("005930", 1, output)

    def test_environment_failure_returns_claim_without_charging_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE market_backfill_jobs("
                    "code TEXT PRIMARY KEY,state TEXT,attempts INTEGER,raw_directory TEXT,"
                    "last_error TEXT,updated_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO market_backfill_jobs VALUES"
                    "('005930','running',2,'','','2026-09-22T00:00:00+00:00')"
                )
            raw = Path(directory) / "raw"
            collector._defer_for_environment(
                database, "005930", raw, collector.CREON_CONNECTION_ERROR,
            )
            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT state,attempts,raw_directory,last_error "
                    "FROM market_backfill_jobs WHERE code='005930'"
                ).fetchone()
            self.assertEqual("pending", row[0])
            self.assertEqual(1, row[1])
            self.assertEqual(str(raw), row[2])
            self.assertIn("not connected", row[3])


if __name__ == "__main__":
    unittest.main()
