from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from kiwoom_monitor.central_server.shadow_checkpoint import restore_checkpoint, split_checkpoint
from scripts.check_source_database import check_database, supported_schema


class ShadowCheckpointTests(unittest.TestCase):
    def test_roundtrip_keeps_frame_order_unknown_fields_and_empty_array(self):
        for bars in ([], [
            {"code": "B", "observation_key": "older", "extra": {"中文": [1, None]}},
            {"code": "A", "observation_key": "newer", "close": 10},
        ]):
            document = {"schema_version": 1, "cursor": 4, "bars": bars,
                        "strategy_state": {"emitted_candidate_keys": ["seen"]}}
            parts = split_checkpoint(document)
            order = json.loads(parts.order)
            entries = [[key, frame] for key, frame in zip(order, bars)]
            self.assertEqual(document, restore_checkpoint(({}, 2, parts.header, parts.order, entries)))
        self.assertEqual({"cursor": 1}, restore_checkpoint(('{"cursor":1}', None, None, None, [])))
        self.assertIsNone(restore_checkpoint(None))

    def test_ambiguous_or_non_candidate_documents_keep_inline_representation(self):
        for document in ({"cursor": 1}, {"schema_version": 2, "bars": []},
                         {"schema_version": 1, "bars": [{"code": "A"}]},
                         {"schema_version": 1, "bars": [
                             {"code": "A", "observation_key": "one"},
                             {"code": "A", "observation_key": "one", "changed": True}]}):
            self.assertIsNone(split_checkpoint(document))

    def test_missing_corrupt_or_future_storage_fails_closed(self):
        frame = {"code": "A", "observation_key": "one"}
        cases = [
            ({}, 3, {}, [], []),
            ({}, 2, {}, [["A", "one"]], []),
            ({}, 2, {}, [["A", "one"]], [[["A", "one"], None]]),
            ({}, 2, {}, [["A", "one"]], [[["A", "one"], {**frame, "code": "B"}]]),
            ({}, 2, {"bars": []}, [], []),
            ({}, 2, {}, [["A", "one"], ["A", "one"]],
             [[["A", "one"], frame], [["A", "one"], frame]]),
        ]
        for row in cases:
            with self.subTest(row=row), self.assertRaises(RuntimeError):
                restore_checkpoint(row)

    def test_target_schema_inspection_does_not_execute_target_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            schema = root / "src/kiwoom_monitor/central_server/central_schema.py"
            schema.parent.mkdir(parents=True)
            schema.write_text("raise RuntimeError('must not execute')\n"
                              "plan=(CentralSchemaMigration(1,'a',(),()),CentralSchemaMigration(2,'b',(),()))\n")
            self.assertEqual(2, supported_schema(root))
            schema.write_text("plan=(CentralSchemaMigration(2,'missing first',(),()),)\n")
            with self.assertRaises(RuntimeError):
                supported_schema(root)

    def test_database_guard_only_offers_its_known_downgrade(self):
        cursor = Mock()
        cursor.fetchone.return_value = (20,)
        self.assertTrue(check_database(cursor, 21)["compatible"])
        cursor.fetchone.return_value = (21,)
        self.assertTrue(check_database(cursor, 20)["offline_checkpoint_downgrade_required"])
        with self.assertRaises(RuntimeError):
            check_database(cursor, 19)
        cursor.fetchone.return_value = (22,)
        with self.assertRaises(RuntimeError):
            check_database(cursor, 20)


if __name__ == "__main__":
    unittest.main()
