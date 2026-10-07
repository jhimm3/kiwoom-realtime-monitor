from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.central_server.central_schema import central_schema_migrations
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_observations import ranking_observation
from kiwoom_monitor.central_server.schema_migrations import CentralSchemaMigrationRunner


KST = timezone(timedelta(hours=9))


def _observation(subject: str, key: str, payload: dict, available_at: datetime):
    return ranking_observation(
        subject, key, payload, available_at, source="kiwoom-ka00198",
    )


class ResearchObservationHistoryTests(unittest.TestCase):
    def test_a_b_a_corrections_are_append_only_and_latest_snapshot_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            key = "2026-09-12T09:00:00"
            first = {"query_type": "5", "items": [{"stk_cd": "005930", "bigd_rank": "1"}]}
            second = {"query_type": "5", "items": [{"stk_cd": "000660", "bigd_rank": "1"}]}
            for offset, payload in enumerate((first, second, first)):
                available_at = datetime(2026, 9, 12, 9, 0, offset, tzinfo=KST)
                store.save_dataset_snapshot(
                    "ranking", "5", key, payload,
                    observation=_observation("5", key, payload, available_at),
                )

            revisions = store.load_observation_revisions("ranking", "5")
            latest = store.load_dataset_snapshots("ranking", "5")
            store.close()

        self.assertEqual(3, len(revisions))
        self.assertEqual(first, latest[0]["payload"])
        self.assertEqual(first, revisions[0]["payload"])
        self.assertEqual(second, revisions[1]["payload"])
        self.assertEqual(first, revisions[2]["payload"])
        self.assertEqual(revisions[1]["revision_id"], revisions[0]["revision_of"])
        self.assertEqual(revisions[2]["revision_id"], revisions[1]["revision_of"])
        self.assertEqual(revisions[0]["payload_hash"], revisions[2]["payload_hash"])
        self.assertEqual("2026-09-12T00:00:00+00:00", revisions[2]["effective_at"])

    def test_identical_cached_response_is_idempotent_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            key = "2026-09-12T09:00:00"
            payload = {"query_type": "5", "items": [{"stk_cd": "005930"}]}
            store = SQLiteQueryStore(path)
            store.initialize()
            store.save_dataset_snapshot(
                "ranking", "5", key, payload,
                observation=_observation("5", key, payload, datetime(2026, 9, 12, 9, 0, tzinfo=KST)),
            )
            store.close()

            restarted = SQLiteQueryStore(path)
            restarted.initialize()
            restarted.save_dataset_snapshot(
                "ranking", "5", key, payload,
                observation=_observation("5", key, payload, datetime(2026, 9, 12, 9, 5, tzinfo=KST)),
            )
            revisions = restarted.load_observation_revisions("ranking", "5")
            restarted.close()

        self.assertEqual(1, len(revisions))

    def test_revision_failure_rolls_back_latest_payload_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            key = "2026-09-12T09:00:00"
            first = {"query_type": "5", "items": [{"stk_cd": "005930"}]}
            second = {"query_type": "5", "items": [{"stk_cd": "000660"}]}
            store.save_dataset_snapshot(
                "ranking", "5", key, first,
                observation=_observation("5", key, first, datetime(2026, 9, 12, 9, 0, tzinfo=KST)),
            )
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TRIGGER reject_observation_revision BEFORE INSERT ON "
                    "central_observation_revisions BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
                )
                connection.commit()

            with self.assertRaises(sqlite3.IntegrityError):
                store.save_dataset_snapshot(
                    "ranking", "5", key, second,
                    observation=_observation("5", key, second, datetime(2026, 9, 12, 9, 1, tzinfo=KST)),
                )
            revisions = store.load_observation_revisions("ranking", "5")
            latest = store.load_dataset_snapshots("ranking", "5")
            store.close()

        self.assertEqual(1, len(revisions))
        self.assertEqual(first, latest[0]["payload"])

    def test_top20_membership_links_to_its_ranking_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            key = "2026-09-12T09:00:00"
            payload = {"observed_at": key, "codes": ["005930"], "items": []}
            store.save_dataset_snapshot(
                "top20_membership", "2026-09-12", key, payload,
                observation=ranking_observation(
                    "2026-09-12", key, payload, datetime(2026, 9, 12, 9, 0, tzinfo=KST),
                    source="nas-autonomous-ka00198",
                ),
            )
            revisions = store.load_observation_revisions("top20_membership", "2026-09-12")
            store.close()

        self.assertEqual(1, len(revisions))
        self.assertEqual(
            {"kind": "ranking", "subject": "5", "observation_key": key},
            revisions[0]["source_ref"],
        )

    def test_v11_snapshot_is_preserved_without_inventing_legacy_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            with closing(sqlite3.connect(path)) as connection:
                CentralSchemaMigrationRunner(connection.cursor(), "sqlite").apply(
                    central_schema_migrations()[:11]
                )
                connection.execute(
                    "INSERT INTO central_dataset_snapshots(kind,subject,snapshot_key,saved_at,payload_json) "
                    "VALUES('ranking','5','legacy',1.0,'{\"items\":[1]}')"
                )
                connection.commit()

            store = SQLiteQueryStore(path)
            store.initialize()
            snapshots = store.load_dataset_snapshots("ranking", "5")
            revisions = store.load_observation_revisions("ranking", "5")
            store.close()

        self.assertEqual([1], snapshots[0]["payload"]["items"])
        self.assertEqual([], revisions)

    def test_feature_flag_stops_history_without_stopping_latest_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(
                Path(directory) / "monitor.sqlite3", observation_history_enabled=False,
            )
            store.initialize()
            key = "2026-09-12T09:00:00"
            payload = {"query_type": "5", "items": [{"stk_cd": "005930"}]}
            store.save_dataset_snapshot(
                "ranking", "5", key, payload,
                observation=_observation("5", key, payload, datetime(2026, 9, 12, 9, 0, tzinfo=KST)),
            )
            snapshots = store.load_dataset_snapshots("ranking", "5")
            revisions = store.load_observation_revisions("ranking", "5")
            store.close()

        self.assertEqual(payload, snapshots[0]["payload"])
        self.assertEqual([], revisions)


if __name__ == "__main__":
    unittest.main()
