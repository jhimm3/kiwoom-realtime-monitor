from __future__ import annotations

import hashlib
import json
import sqlite3
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

    def test_mixed_offset_export_uses_exact_instants_and_preserves_raw_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "central.sqlite3")
            store.initialize()
            try:
                # Same-instant IDs oppose sequence order; the later Z row has
                # the smallest sequence, so time must precede both tie breakers.
                cases = (
                    ("end-kst", "2026-09-12T09:00:00.000003+09:00", 50),
                    ("a-start-utc", "2026-09-12T00:00:00.000001+00:00", 60),
                    ("outside-nine-hours", "2026-09-12T00:00:00.000002+09:00", 30),
                    ("inside-z", "2026-09-12T00:00:00.000002Z", 10),
                    ("before-kst", "2026-09-12T09:00:00.000000+09:00", 20),
                    ("z-start-kst", "2026-09-12T09:00:00.000001+09:00", 40),
                    ("end-utc", "2026-09-12T00:00:00.000003+00:00", 70),
                )
                rows, original = [], {}
                for index, (revision_id, timestamp, sequence) in enumerate(cases):
                    payload = json.dumps({"items": [{"stk_cd": f"{index:06d}"}],
                                          "original_revision": revision_id}, separators=(", ", ": "))
                    payload_hash = hashlib.sha256(payload.encode()).hexdigest()
                    rows.append((
                        sequence, revision_id, "fixture-" + revision_id, 1, "fixture", "", "",
                        "ranking", "5", "COMBINED", timestamp, timestamp, timestamp, None,
                        payload_hash, "count", "actual", "complete", "query", "ranking_top20",
                        "[]", "source_timezone_confirmed", "{}", payload,
                    ))
                    original[revision_id] = dict(
                        accepted_sequence=sequence, effective_at=timestamp, received_at=timestamp,
                        available_at=timestamp, payload_hash=payload_hash, payload=json.loads(payload),
                    )
                with store._lock, store._connection() as connection:
                    connection.executemany(
                        "INSERT INTO central_observation_revisions("
                        "accepted_sequence,revision_id,observation_key,schema_version,source_id,source_session_id,"
                        "source_sequence,kind,subject,venue,effective_at,received_at,available_at,"
                        "revision_of,payload_hash,unit,value_kind,completeness,origin,candidate_universe,"
                        "quality_flags_json,clock_quality,source_ref_json,payload_json) "
                        "VALUES(" + ",".join(("?",) * 24) + ")", rows,
                    )
                    before = connection.execute(
                        "SELECT * FROM central_observation_revisions ORDER BY accepted_sequence"
                    ).fetchall()
                start = datetime(2026, 9, 12, 0, 0, 0, 1, tzinfo=UTC)
                end = datetime(2026, 9, 12, 0, 0, 0, 3, tzinfo=UTC)
                expected_ids = ["z-start-kst", "a-start-utc", "inside-z"]
                expected_hash = hashlib.sha256(b"z-start-kst\na-start-utc\ninside-z").hexdigest()
                datasets = []
                for range_start, range_end in ((start, end), (start.astimezone(KST), end.astimezone(KST))):
                    with self.subTest(range_offset=range_start.utcoffset()):
                        dataset = CentralResearchDataSource(_StoreClient(store)).load(
                            range_start, range_end, ("ranking",), subject="5",
                        )
                        datasets.append(dataset)
                        self.assertEqual(expected_ids, [row["revision_id"] for row in dataset.observations])
                        self.assertEqual(3, dataset.manifest["revision_count"])
                        self.assertEqual(expected_hash, dataset.manifest["revision_ids_hash"])
                        self.assertEqual({"start": start.isoformat(), "end": end.isoformat()},
                                         dataset.manifest["captured_range"])
                        self.assertEqual("2026-09-12T09:00:00.000001+09:00", dataset.manifest["recorded_from"])
                        for observation in dataset.observations:
                            self.assertEqual(original[observation["revision_id"]],
                                {key: observation[key] for key in original[observation["revision_id"]]})
                        page = store.load_observation_export_page(dataset.manifest["fixed_watermark"], 0, 1000)
                        self.assertEqual(dataset.manifest, page["manifest"])
                        self.assertEqual(dataset.manifest["fixed_watermark"], page["watermark"])
                        self.assertEqual(list(dataset.observations), page["observations"])
                self.assertEqual(datasets[0].observations, datasets[1].observations)
                self.assertEqual(datasets[0].manifest["revision_ids_hash"], datasets[1].manifest["revision_ids_hash"])
                with store._lock, store._connection() as connection:
                    self.assertEqual(before, connection.execute(
                        "SELECT * FROM central_observation_revisions ORDER BY accepted_sequence"
                    ).fetchall())
                for label, invalid_time in (
                    ("naive", "2026-09-12T09:00:00.000001"),
                    ("invalid-time", "2026-09-12Tinvalid"),
                ):
                    with self.subTest(invalid_available_at=label):
                        with store._lock, store._connection() as connection:
                            connection.execute(
                                "UPDATE central_observation_revisions SET available_at=? WHERE revision_id=?",
                                (invalid_time, "z-start-kst"),
                            )
                            altered = connection.execute(
                                "SELECT * FROM central_observation_revisions ORDER BY accepted_sequence"
                            ).fetchall()
                            export_counts = connection.execute(
                                "SELECT (SELECT COUNT(*) FROM central_research_exports),"
                                "(SELECT COUNT(*) FROM central_research_export_members)"
                            ).fetchone()
                        try:
                            with self.assertRaises(sqlite3.OperationalError):
                                store.create_observation_export(start, end, ("ranking",), "5")
                            with store._lock, store._connection() as connection:
                                self.assertEqual(export_counts, connection.execute(
                                    "SELECT (SELECT COUNT(*) FROM central_research_exports),"
                                    "(SELECT COUNT(*) FROM central_research_export_members)"
                                ).fetchone())
                                self.assertEqual(altered, connection.execute(
                                    "SELECT * FROM central_observation_revisions ORDER BY accepted_sequence"
                                ).fetchall())
                        finally:
                            with store._lock, store._connection() as connection:
                                connection.execute(
                                    "UPDATE central_observation_revisions SET available_at=? WHERE revision_id=?",
                                    (original["z-start-kst"]["available_at"], "z-start-kst"),
                                )
                with store._lock, store._connection() as connection:
                    self.assertEqual(before, connection.execute(
                        "SELECT * FROM central_observation_revisions ORDER BY accepted_sequence"
                    ).fetchall())
            finally:
                store.close()

    def test_explicit_export_session_profile_is_frozen_without_changing_revision_hash(self) -> None:
        store = SQLiteQueryStore(Path(":memory:"))
        store.initialize()
        self.addCleanup(store.close)
        start = datetime(2026, 9, 14, tzinfo=UTC)
        end = datetime(2026, 9, 15, tzinfo=UTC)
        payload = {"query_type": "5", "items": [{"stk_cd": "005930", "rank": "1"}]}
        key = "2026-09-14T09:00:00"
        store.save_dataset_snapshot("ranking", "5", key, payload,
            observation=ranking_observation("5", key, payload, datetime(2026, 9, 14, 9, tzinfo=KST), source="fixture"))
        legacy = CentralResearchDataSource(_StoreClient(store)).load(
            start, end, ("ranking",),
        )
        profiled = CentralResearchDataSource(_StoreClient(store)).load(
            start, end, ("ranking",), session_profile="krx-after/v1",
        )
        self.assertEqual(1, len(legacy.observations))
        self.assertEqual(legacy.observations, profiled.observations)
        self.assertEqual(payload, legacy.observations[0]["payload"])
        self.assertNotIn("research_session_profile", legacy.manifest)
        self.assertEqual(
            "krx-after/v1", profiled.manifest["research_session_profile"]["profile"],
        )
        self.assertEqual(
            legacy.manifest["revision_ids_hash"], profiled.manifest["revision_ids_hash"],
        )


if __name__ == "__main__":
    unittest.main()
