from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import hashlib
from copy import deepcopy
from types import SimpleNamespace
from contextlib import closing
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from audit_historical_monthly_gap_raw import audit, audit_development_gap_provenance  # noqa: E402


class MonthlyGapRawTests(unittest.TestCase):
    def test_archived_gap_equals_saved_gap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gaps = root / "gaps.json"
            gaps.write_text(json.dumps({"gaps": [{
                "code": "123456", "outcome_date": "2025-12-02",
                "from_bar": "2025-12-02T09:01:00+09:00",
                "to_bar": "2025-12-02T09:13:00+09:00",
                "gap_seconds": 720,
            }]}), encoding="utf-8")
            raw_dir = root / "historical_collection" / "daishin" / "123456" / "attempt1"
            raw_dir.mkdir(parents=True)
            raw = raw_dir / "1m.ndjson"
            raw.write_text(json.dumps({"record_type": "page", "interval_seconds": 60,
                                       "bars": [
                                           {"bar_time": "2025-12-02T09:01:00+09:00", "volume": 40},
                                           {"bar_time": "2025-12-02T09:13:00+09:00", "volume": 60},
                                       ]}) + "\n", encoding="utf-8")
            market = root / "market.sqlite3"
            with closing(sqlite3.connect(market)) as db:
                db.executescript("""
                    CREATE TABLE market_backfill_jobs(code TEXT,state TEXT,raw_directory TEXT);
                    CREATE TABLE market_bars(provider TEXT,code TEXT,venue TEXT,
                        session_scope TEXT,interval_seconds INTEGER,adjustment_mode TEXT,
                        bar_time TEXT,volume INTEGER);
                    INSERT INTO market_bars VALUES
                        ('daishin_creon','123456','K','regular',60,'raw',
                         '2025-12-02T09:01:00+09:00',40),
                        ('daishin_creon','123456','K','regular',60,'raw',
                         '2025-12-02T09:13:00+09:00',60);
                """)
                db.execute("INSERT INTO market_backfill_jobs VALUES(?,?,?)",
                           ("123456", "complete", str(raw_dir)))
                db.commit()

            report = audit(gaps, market)

            self.assertEqual(1, report["counts"]["stock_days_raw_equals_saved"])
            self.assertEqual(0, report["counts"]["gaps_with_raw_bar_inside"])
            self.assertEqual(0, report["gaps"][0]["raw_bars_strictly_inside_gap"])

            raw.write_text(json.dumps({"record_type": "page", "interval_seconds": 60,
                                       "bars": [
                                           {"bar_time": "2025-12-02T09:01:00+09:00", "volume": 40},
                                           {"bar_time": "2025-12-02T09:07:00+09:00", "volume": 5},
                                           {"bar_time": "2025-12-02T09:13:00+09:00", "volume": 60},
                                       ]}) + "\n", encoding="utf-8")
            omitted = audit(gaps, market)
            self.assertEqual(0, omitted["counts"]["stock_days_raw_equals_saved"])
            self.assertEqual(1, omitted["counts"]["gaps_with_raw_bar_inside"])
            self.assertEqual(1, omitted["stock_days"][0]["raw_bars_missing_in_saved"])


class DevelopmentGapEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / "123456/attempt/1m.ndjson"
        self.raw.parent.mkdir(parents=True)
        self.raw.write_text(json.dumps({"record_type": "page", "interval_seconds": 60, "bars": [
            {"bar_time": "2025-12-02T09:01:00+09:00", "volume": 40},
            {"bar_time": "2025-12-02T09:13:00+09:00", "volume": 60},
            {"bar_time": "2025-12-02T15:30:00+09:00", "volume": 70}]}))
        self.past = {"version": "historical_monthly_selected_gap_raw_provenance/v1", "stock_days": [{
            "code": "123456", "outcome_date": "2025-12-02", "raw_file": str(self.raw),
            "raw_sha256": hashlib.sha256(self.raw.read_bytes()).hexdigest()}], "gaps": [{
                "code": "123456", "outcome_date": "2025-12-02",
                "from_bar": "2025-12-02T00:01:00+00:00", "to_bar": "2025-12-02T00:13:00+00:00", "gap_seconds": 720}]}
        self.archive = self.root / "report.json"
        self.save_archive()
        self.dataset = SimpleNamespace(manifest={"observations_file_hash": "frozen-bytes"}, observations=[
            {"kind": "minute_bar", "venue": "KRX", "payload": {"code": "123456", "volume": volume,
             "bar_start": f"2025-12-02T{start}+09:00", "bar_end": f"2025-12-02T{end}+09:00"}}
            for start, end, volume in (("09:00:00", "09:01:00", 40), ("09:12:00", "09:13:00", 60),
                                       ("15:20:00", "15:30:00", 70))])
        self.review = {"stock_days": [{"code": "123456", "outcome_date": "2025-12-02", "internal_gaps": [{
            "previous_bar_start": "2025-12-02T09:00:00+09:00", "next_bar_start": "2025-12-02T09:12:00+09:00",
            "unobserved_minutes": 11}]}]}

    def save_archive(self):
        self.archive.write_text(json.dumps(self.past))

    def inspect(self, **kwargs):
        return audit_development_gap_provenance(self.dataset, self.review, self.archive, archive_root=self.root, **kwargs)

    def test_exact_raw_volume_end_timestamps_and_auction_do_not_prove_cause(self):
        result = self.inspect()
        self.assertEqual(1, result["counts"]["current_raw_input_match_count"])
        self.assertEqual(1, result["counts"]["archived_gap_match_count"])
        self.assertFalse(result["full_session_completeness_verified"])
        self.assertFalse(result["gaps"][0]["cause_verified"])
        self.assertEqual("2025-12-02T00:01:00+00:00", result["gaps"][0]["source_from_bar_end"])

    def test_missing_raw_keeps_historical_match_separate(self):
        self.raw.unlink()
        result = self.inspect()
        self.assertEqual(1, result["counts"]["archived_gap_match_count"])
        self.assertEqual(0, result["counts"]["current_raw_input_match_count"])
        self.assertEqual(1, result["counts"]["raw_unavailable_stock_days"])

    def test_changed_raw_hash_and_volume_or_omission_are_not_matches(self):
        self.raw.write_text(self.raw.read_text() + "\n")
        self.assertEqual("RAW_HASH_MISMATCH", self.inspect()["stock_days"][0]["status"])
        self.past["stock_days"][0]["raw_sha256"] = hashlib.sha256(self.raw.read_bytes()).hexdigest()
        self.save_archive()
        self.dataset.observations[0]["payload"]["volume"] += 1
        self.assertEqual(1, self.inspect()["stock_days"][0]["volume_mismatches"])
        self.dataset.observations.pop()
        self.assertEqual(1, self.inspect()["stock_days"][0]["raw_bars_absent_from_input"])

    def test_duplicate_input_conflicts_cannot_be_hidden_by_last_row(self):
        changed = deepcopy(self.dataset.observations[0])
        changed["payload"]["volume"] = 1
        self.dataset.observations.insert(0, changed)
        self.assertEqual("CURRENT_RAW_INPUT_MISMATCH", self.inspect()["stock_days"][0]["status"])

    def test_hash_matching_malformed_raw_is_rejected_as_validation_error(self):
        for document in ([], {"record_type": "page", "interval_seconds": 60, "bars": {}},
                         {"record_type": "page", "interval_seconds": 60, "bars": [None]},
                         {"record_type": "page", "interval_seconds": 60,
                          "bars": [{"bar_time": "2025-12-02T09:01:00+09:00"}]}):
            self.raw.write_text(json.dumps(document))
            self.past["stock_days"][0]["raw_sha256"] = hashlib.sha256(self.raw.read_bytes()).hexdigest()
            self.save_archive()
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.inspect()

    def test_untrusted_archive_path_duplicate_and_malformed_rows_rejected(self):
        original = deepcopy(self.past)
        for variant in ("escape", "duplicate", "missing", "bad_gap", "naive"):
            self.past = deepcopy(original)
            if variant == "escape":
                self.past["stock_days"][0]["raw_file"] = str(self.root.parent / "1m.ndjson")
            elif variant == "duplicate":
                self.past["stock_days"] *= 2
            elif variant == "missing":
                del self.past["stock_days"][0]["raw_sha256"]
            elif variant == "bad_gap":
                self.past["gaps"][0]["gap_seconds"] = 60
            else:
                self.past["gaps"][0]["from_bar"] = "2025-12-02T09:01:00"
            self.save_archive()
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                self.inspect()

    def test_cancelled_audit_never_returns_a_partial_approval(self):
        def cancelled():
            raise RuntimeError("cancelled")
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.inspect(checkpoint=cancelled)


if __name__ == "__main__":
    unittest.main()
