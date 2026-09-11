from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kiwoom_monitor.central_server.persistent_outbox import JsonRecordOutbox


class JsonRecordOutboxTests(unittest.TestCase):
    def test_values_survive_new_instance_and_remove_is_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.json"
            JsonRecordOutbox(path).put("10:01", {"value": 3})
            self.assertEqual({"10:01": {"value": 3}}, JsonRecordOutbox(path).load())
            JsonRecordOutbox(path).remove("10:01")
            self.assertEqual({}, JsonRecordOutbox(path).load())
