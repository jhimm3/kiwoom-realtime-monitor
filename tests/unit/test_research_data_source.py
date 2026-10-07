from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_observations import ranking_observation
from kiwoom_monitor.infrastructure.research_data_source import (
    CentralResearchDataSource,
    load_frozen_research_export,
)


UTC = timezone.utc
KST = timezone(timedelta(hours=9))


class _StoreClient:
    def __init__(self, store: SQLiteQueryStore) -> None:
        self.store = store

    def load_research_observations_page(
        self, start, end, kinds, *, subject="", watermark="", cursor=0, limit=1000,
    ):
        if not watermark:
            watermark = self.store.create_observation_export(start, end, kinds, subject)["fixed_watermark"]
        return self.store.load_observation_export_page(watermark, cursor, limit)


class ResearchDataSourceTests(unittest.TestCase):
    def test_file_export_is_verified_before_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {"ordinal": 1, "revision_id": "revision-1", "kind": "top20_membership"},
                {"ordinal": 2, "revision_id": "revision-2", "kind": "minute_bar"},
            ]
            encoded = "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
            ).encode()
            (root / "observations.jsonl").write_bytes(encoded)
            (root / "manifest.json").write_text(json.dumps({
                "revision_count": 2,
                "revision_ids_hash": hashlib.sha256(b"revision-1\nrevision-2").hexdigest(),
                "observations_file": "observations.jsonl",
                "observations_file_hash": hashlib.sha256(encoded).hexdigest(),
            }), encoding="utf-8")

            dataset = load_frozen_research_export(root)
            self.assertEqual(2, len(dataset.observations))
            (root / "observations.jsonl").write_bytes(encoded + b"{}\n")
            with self.assertRaisesRegex(ValueError, "file hash"):
                load_frozen_research_export(root)

    def test_optional_theme_history_sidecar_is_hash_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observation = b'{"ordinal":1,"revision_id":"revision-1"}\n'
            theme = b'{"snapshot_id":"theme-1","available_at":1.0,"document":{}}\n'
            (root / "observations.jsonl").write_bytes(observation)
            (root / "theme_snapshots.jsonl").write_bytes(theme)
            (root / "manifest.json").write_text(json.dumps({
                "revision_count": 1,
                "revision_ids_hash": hashlib.sha256(b"revision-1").hexdigest(),
                "observations_file": "observations.jsonl",
                "observations_file_hash": hashlib.sha256(observation).hexdigest(),
                "theme_snapshots_file": "theme_snapshots.jsonl",
                "theme_snapshots_file_hash": hashlib.sha256(theme).hexdigest(),
                "theme_snapshot_count": 1,
            }), encoding="utf-8")

            dataset = load_frozen_research_export(root)
            self.assertEqual("theme-1", dataset.theme_snapshots[0]["snapshot_id"])
            (root / "theme_snapshots.jsonl").write_bytes(theme + b"{}\n")
            with self.assertRaisesRegex(ValueError, "theme snapshots file hash"):
                load_frozen_research_export(root)

    def test_more_than_5000_same_time_revisions_have_no_page_gap_or_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            timestamp = "2026-09-12T00:00:00+00:00"
            rows = []
            for index in range(5001):
                revision_id = f"revision-{index:05d}"
                payload = json.dumps({"items": [{"stk_cd": f"{index:06d}"}]}, separators=(",", ":"))
                rows.append((
                    revision_id, "2026-09-12T09:00:00", 1, "fixture", "", "", "ranking",
                    "5", "COMBINED", timestamp, timestamp, timestamp, None,
                    hashlib.sha256(payload.encode()).hexdigest(), "count", "actual", "complete",
                    "query", "ranking_top20", "[]", "source_timezone_confirmed", "{}", payload,
                ))
            with store._lock, store._connection() as connection:
                connection.executemany(
                    "INSERT INTO central_observation_revisions("
                    "revision_id,observation_key,schema_version,source_id,source_session_id,"
                    "source_sequence,kind,subject,venue,effective_at,received_at,available_at,"
                    "revision_of,payload_hash,unit,value_kind,completeness,origin,candidate_universe,"
                    "quality_flags_json,clock_quality,source_ref_json,payload_json) "
                    "VALUES(" + ",".join(("?",) * 23) + ")",
                    rows,
                )
            dataset = CentralResearchDataSource(_StoreClient(store)).load(
                datetime(2026, 9, 12, tzinfo=UTC), datetime(2026, 9, 13, tzinfo=UTC),
                ("ranking",), page_size=997,
            )
            store.close()

        self.assertEqual(5001, len(dataset.observations))
        self.assertEqual(list(range(1, 5002)), [row["ordinal"] for row in dataset.observations])
        self.assertEqual(5001, len({row["revision_id"] for row in dataset.observations}))

    def test_fixed_export_does_not_admit_revision_committed_after_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            start = datetime(2026, 9, 12, tzinfo=UTC)
            end = datetime(2026, 9, 13, tzinfo=UTC)
            key = "2026-09-12T09:00:00"
            first = {"query_type": "5", "items": [{"stk_cd": "005930"}]}
            store.save_dataset_snapshot(
                "ranking", "5", key, first,
                observation=ranking_observation("5", key, first, datetime(2026, 9, 12, 9, tzinfo=KST), source="fixture"),
            )
            manifest = store.create_observation_export(start, end, ("ranking",), "5")
            second_key = "2026-09-12T09:00:30"
            second = {"query_type": "5", "items": [{"stk_cd": "000660"}]}
            store.save_dataset_snapshot(
                "ranking", "5", second_key, second,
                observation=ranking_observation("5", second_key, second, datetime(2026, 9, 12, 9, 0, 30, tzinfo=KST), source="fixture"),
            )
            page = store.load_observation_export_page(manifest["fixed_watermark"], 0, 1000)
            store.close()

        self.assertEqual(1, manifest["revision_count"])
        self.assertEqual([key], [row["observation_key"] for row in page["observations"]])

    def test_rejects_naive_or_multi_session_export_range(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            store.create_observation_export(
                datetime(2026, 9, 12), datetime(2026, 9, 12, 1), ("ranking",),
            )
        with self.assertRaisesRegex(ValueError, "one session"):
            store.create_observation_export(
                datetime(2026, 9, 12, tzinfo=UTC), datetime(2026, 9, 14, tzinfo=UTC),
                ("ranking",),
            )
        store.close()

    def test_explicit_export_session_profile_is_frozen_without_changing_revision_hash(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        start = datetime(2026, 9, 14, tzinfo=UTC)
        end = datetime(2026, 9, 15, tzinfo=UTC)
        legacy = CentralResearchDataSource(_StoreClient(store)).load(
            start, end, ("ranking",),
        )
        profiled = CentralResearchDataSource(_StoreClient(store)).load(
            start, end, ("ranking",), session_profile="krx-after/v1",
        )
        self.assertNotIn("research_session_profile", legacy.manifest)
        self.assertEqual(
            "krx-after/v1", profiled.manifest["research_session_profile"]["profile"],
        )
        self.assertEqual(
            legacy.manifest["revision_ids_hash"], profiled.manifest["revision_ids_hash"],
        )


if __name__ == "__main__":
    unittest.main()
