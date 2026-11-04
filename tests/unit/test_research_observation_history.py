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
            self.assertEqual(3, len(revisions))
            self.assertEqual(first, latest[0]["payload"])
            self.assertEqual(first, revisions[0]["payload"])
            self.assertEqual(second, revisions[1]["payload"])
            self.assertEqual(first, revisions[2]["payload"])
            self.assertEqual(revisions[1]["revision_id"], revisions[0]["revision_of"])
            self.assertEqual(revisions[2]["revision_id"], revisions[1]["revision_of"])
            self.assertEqual(revisions[0]["payload_hash"], revisions[2]["payload_hash"])
            self.assertEqual("2026-09-12T00:00:00+00:00", revisions[2]["effective_at"])

            original_revisions = {row["revision_id"]: row for row in revisions}
            newer_key = "2026-09-12T09:00:30"
            newer = {"query_type": "5", "items": [{"stk_cd": "000270", "bigd_rank": "1"}]}
            store.save_dataset_snapshot(
                "ranking", "5", newer_key, newer,
                observation=_observation("5", newer_key, newer,
                                         datetime(2026, 9, 12, 9, 0, 31, tzinfo=KST)),
            )
            # These later acceptances correct the older slot; they are not a new UI slot.
            for second_offset, payload in ((32, second), (33, first)):
                store.save_dataset_snapshot(
                    "ranking", "5", key, payload,
                    observation=_observation("5", key, payload,
                                             datetime(2026, 9, 12, 9, 0, second_offset, tzinfo=KST)),
                )
            snapshots = store.load_dataset_snapshots("ranking", "5", 2)
            ui_latest = store.load_dataset_snapshots("ranking", "5", 1)
            corrected_revisions = store.load_observation_revisions("ranking", "5")
            store.close()

        self.assertEqual([newer_key, key], [row["snapshot_key"] for row in snapshots])
        self.assertEqual([newer], [row["payload"] for row in ui_latest])
        self.assertEqual(first, snapshots[1]["payload"])
        self.assertEqual(6, len(corrected_revisions))
        self.assertEqual(key, corrected_revisions[0]["observation_key"])
        self.assertEqual(original_revisions, {
            row["revision_id"]: row for row in corrected_revisions
            if row["revision_id"] in original_revisions
        })
        older_slot = [row for row in corrected_revisions if row["observation_key"] == key]
        self.assertEqual([first, second, first, second, first],
                         [row["payload"] for row in older_slot])
        self.assertEqual([row["revision_id"] for row in older_slot[1:]],
                         [row["revision_of"] for row in older_slot[:-1]])
        self.assertEqual([revisions[0]["payload_hash"]] * 3,
                         [older_slot[index]["payload_hash"] for index in (0, 2, 4)])

    def test_cross_source_retry_repoints_head_without_rewriting_first_revision(self) -> None:
        from dataclasses import replace
        from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            now = datetime(2026, 9, 12, 9, 0, tzinfo=KST)
            store = SQLiteQueryStore(path, now_provider=lambda: now)
            store.initialize()
            key = "2026-09-12T09:00:00"
            first, second = {"items": ["A"]}, {"items": ["B"]}
            original = None
            for source, value, offset in (("A", first, 0), ("B", second, 1), ("A", first, 2)):
                now = datetime(2026, 9, 12, 9, 0, offset, tzinfo=KST)
                observation = ranking_observation("5", key, value, now, source=source)
                store.save_dataset_snapshot("ranking", "5", key, value, observation=observation)
                if original is None:
                    original = store.load_observation_revisions("ranking", "5")[0]
            rows = store.load_observation_revisions("ranking", "5")
            self.assertEqual(2, len(rows))
            self.assertEqual(original, rows[1])
            self.assertEqual(first, store.load_dataset_snapshots("ranking", "5")[0]["payload"])
            self.assertEqual(now.timestamp(), store.load_dataset_snapshots("ranking", "5")[0]["saved_at"])
            metadata = store.load_market_data_metadata(MarketDatasetKind.CANDIDATE_SET, "5", key)
            self.assertEqual("A", metadata.source)
            self.assertEqual(now, metadata.available_at)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual((None, original["revision_id"]), connection.execute(
                    "SELECT payload_json,revision_id FROM central_dataset_snapshots").fetchone())
            store.close()

    def test_empty_source_and_inline_replacement_preserve_original_metadata(self) -> None:
        from kiwoom_monitor.domain.market_data_contract import MarketDatasetKind
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            at = datetime(2026, 9, 12, 9, 0, tzinfo=KST)
            key = "2026-09-12T09:00:00"
            value = {"items": [1]}
            store = SQLiteQueryStore(path)
            store.initialize()
            observation = ranking_observation("5", key, value, at, source="")
            store.save_dataset_snapshot("ranking", "5", key, value, observation=observation)
            metadata = store.load_market_data_metadata(MarketDatasetKind.CANDIDATE_SET, "5", key)
            self.assertEqual(observation.metadata, metadata)
            self.assertEqual("unknown", store.load_observation_revisions("ranking", "5")[0]["source_id"])
            store.save_dataset_snapshot("ranking", "5", key, {"items": [2]})
            self.assertEqual({"items": [2]}, store.load_dataset_snapshots("ranking", "5")[0]["payload"])
            self.assertEqual(observation.metadata, store.load_market_data_metadata(MarketDatasetKind.CANDIDATE_SET, "5", key))
            # Prove actual ref -> inline clearing, not merely inline -> inline.
            referenced = ranking_observation("5", key, value, at, source="A")
            store.save_dataset_snapshot("ranking", "5", key, value, observation=referenced)
            with closing(sqlite3.connect(path)) as connection:
                self.assertIsNotNone(connection.execute("SELECT revision_id FROM central_market_data_observation_meta").fetchone()[0])
            store.save_dataset_snapshot("ranking", "5", key, {"items": [2]})
            store.save_market_data_metadata(key, observation)
            self.assertEqual(observation.metadata, store.load_market_data_metadata(MarketDatasetKind.CANDIDATE_SET, "5", key))
            with closing(sqlite3.connect(path)) as connection:
                self.assertIsNone(connection.execute("SELECT revision_id FROM central_dataset_snapshots").fetchone()[0])
                self.assertEqual((None,), connection.execute(
                    "SELECT revision_id FROM central_market_data_observation_meta").fetchone())
            store.close()

    def test_head_failure_rolls_back_new_revision_and_both_current_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            store = SQLiteQueryStore(path)
            store.initialize()
            key = "2026-09-12T09:00:00"
            value = {"items": [1]}
            at = datetime(2026, 9, 12, 9, 0, tzinfo=KST)
            store.save_dataset_snapshot("ranking", "5", key, value, observation=_observation("5", key, value, at))
            before = store.load_observation_revisions("ranking", "5")
            snapshot = store.load_dataset_snapshots("ranking", "5")
            with closing(sqlite3.connect(path)) as connection:
                meta = connection.execute("SELECT * FROM central_market_data_observation_meta").fetchall()
                connection.execute("CREATE TRIGGER reject_head BEFORE UPDATE ON central_dataset_snapshots BEGIN SELECT RAISE(ABORT,'forced head failure'); END")
                connection.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "forced head failure"):
                changed = {"items": [2]}
                store.save_dataset_snapshot("ranking", "5", key, changed, observation=_observation("5", key, changed, at))
            self.assertEqual(before, store.load_observation_revisions("ranking", "5"))
            self.assertEqual(snapshot, store.load_dataset_snapshots("ranking", "5"))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(meta, connection.execute("SELECT * FROM central_market_data_observation_meta").fetchall())
                connection.execute("DROP TRIGGER reject_head")
                connection.execute("CREATE TRIGGER reject_metadata_head BEFORE UPDATE ON central_market_data_observation_meta BEGIN SELECT RAISE(ABORT,'forced metadata head failure'); END")
                connection.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "forced metadata head failure"):
                changed = {"items": [2]}
                store.save_dataset_snapshot("ranking", "5", key, changed, observation=_observation("5", key, changed, at))
            self.assertEqual(before, store.load_observation_revisions("ranking", "5"))
            self.assertEqual(snapshot, store.load_dataset_snapshots("ranking", "5"))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(meta, connection.execute("SELECT * FROM central_market_data_observation_meta").fetchall())
            store.close()

    def test_commit_response_loss_retry_preserves_first_revision_and_current_receipts(self) -> None:
        from contextlib import contextmanager
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            at = datetime(2026, 9, 12, 9, 0, tzinfo=KST)
            store = SQLiteQueryStore(path, now_provider=lambda: at)
            store.initialize()
            key, value = "2026-09-12T09:00:00", {"items": [1]}
            original_connection = store._connection
            @contextmanager
            def lose_response():
                with original_connection() as connection:
                    yield connection
                raise RuntimeError("committed response lost")
            with patch.object(store, "_connection", lose_response):
                with self.assertRaisesRegex(RuntimeError, "committed response lost"):
                    store.save_dataset_snapshot("ranking", "5", key, value, observation=_observation("5", key, value, at))
            first = store.load_observation_revisions("ranking", "5")
            self.assertEqual(1, len(first))
            store.close()
            at = datetime(2026, 9, 12, 9, 1, tzinfo=KST)
            restarted = SQLiteQueryStore(path, now_provider=lambda: at)
            restarted.initialize()
            restarted.save_dataset_snapshot("ranking", "5", key, value, observation=_observation("5", key, value, at))
            self.assertEqual(first, restarted.load_observation_revisions("ranking", "5"))
            self.assertEqual(value, restarted.load_dataset_snapshots("ranking", "5")[0]["payload"])
            self.assertEqual(at.timestamp(), restarted.load_dataset_snapshots("ranking", "5")[0]["saved_at"])
            restarted.close()

    def test_on_off_on_has_one_current_owner_without_invented_off_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.sqlite3"
            key, value = "2026-09-12T09:00:00", {"items": [1]}
            at = datetime(2026, 9, 12, 9, 0, tzinfo=KST)
            on = SQLiteQueryStore(path)
            on.initialize()
            on.save_dataset_snapshot("ranking", "5", key, value, observation=_observation("5", key, value, at))
            first = on.load_observation_revisions("ranking", "5")
            on.close()
            off = SQLiteQueryStore(path, observation_history_enabled=False)
            off.initialize()
            changed = {"items": [2]}
            off.save_dataset_snapshot("ranking", "5", key, changed, observation=_observation("5", key, changed, at))
            self.assertEqual(first, off.load_observation_revisions("ranking", "5"))
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual((None,), connection.execute("SELECT revision_id FROM central_dataset_snapshots").fetchone())
                self.assertEqual((None,), connection.execute("SELECT revision_id FROM central_market_data_observation_meta").fetchone())
            off.close()
            on = SQLiteQueryStore(path)
            on.initialize()
            on.save_dataset_snapshot("ranking", "5", key, value, observation=_observation("5", key, value, at))
            self.assertEqual(first, on.load_observation_revisions("ranking", "5"))
            self.assertEqual(value, on.load_dataset_snapshots("ranking", "5")[0]["payload"])
            on.close()

    def test_latest_slot_uses_actual_instant_and_keeps_equal_instant_raw_tie(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / "monitor.sqlite3")
            store.initialize()
            at = datetime(2026, 9, 12, 12, 0, tzinfo=KST)
            keys = ("2026-09-12T10:00:00+09:00", "2026-09-12T02:00:00+00:00", "2026-09-12T11:00:00+09:00")
            for index, key in enumerate(keys):
                payload = {"items": [index]}
                store.save_dataset_snapshot("ranking", "5", key, payload,
                                            observation=ranking_observation("5", key, payload, at, source="A"))
            rows = store.load_dataset_snapshots("ranking", "5")
            self.assertEqual([keys[2], keys[1], keys[0]], [row["snapshot_key"] for row in rows])
            self.assertEqual(3, len(store.load_observation_revisions("ranking", "5")))
            # Equal instant keeps both raw identities and deterministic original raw-key tie.
            self.assertEqual({"items": [2]}, rows[0]["payload"])
            store.close()

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
                metadata_before = connection.execute(
                    "SELECT * FROM central_market_data_observation_meta"
                ).fetchall()
                self.assertEqual(1, len(metadata_before))
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
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(metadata_before, connection.execute(
                    "SELECT * FROM central_market_data_observation_meta"
                ).fetchall())
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
            self.assertEqual(payload, snapshots[0]["payload"])
            self.assertEqual([], revisions)

            newer_key = "2026-09-12T09:00:30"
            newer = {"query_type": "5", "items": [{"stk_cd": "000270"}]}
            corrected = {"query_type": "5", "items": [{"stk_cd": "000660"}]}
            for slot, value, second_offset in (
                (newer_key, newer, 31), (key, corrected, 32),
            ):
                store.save_dataset_snapshot(
                    "ranking", "5", slot, value,
                    observation=_observation("5", slot, value,
                                             datetime(2026, 9, 12, 9, 0, second_offset, tzinfo=KST)),
                )
            snapshots = store.load_dataset_snapshots("ranking", "5", 2)
            ui_latest = store.load_dataset_snapshots("ranking", "5", 1)
            revisions = store.load_observation_revisions("ranking", "5")
            store.close()

        self.assertEqual([newer_key, key], [row["snapshot_key"] for row in snapshots])
        self.assertEqual([newer], [row["payload"] for row in ui_latest])
        self.assertEqual(corrected, snapshots[1]["payload"])
        self.assertEqual([], revisions)


if __name__ == "__main__":
    unittest.main()
