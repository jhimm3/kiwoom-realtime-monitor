from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server.persistent_outbox import JsonRecordOutbox


class JsonRecordOutboxTests(unittest.TestCase):
    def test_values_survive_new_instance_and_remove_is_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.json"
            JsonRecordOutbox(path).put("10:01", {"value": 3})
            self.assertEqual({"10:01": {"value": 3}}, JsonRecordOutbox(path).load())
            JsonRecordOutbox(path).remove("10:01")
            self.assertEqual({}, JsonRecordOutbox(path).load())

    def test_missing_and_canonical_empty_outboxes_keep_existing_write_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.json"
            outbox = JsonRecordOutbox(path)
            self.assertEqual({}, outbox.load())
            outbox.remove("missing")
            self.assertFalse(path.exists())
            path.write_bytes(b"{}")
            with patch.object(outbox, "_write", wraps=outbox._write) as write:
                outbox.remove("missing")
                self.assertEqual({}, outbox.load())
                write.assert_not_called()
            self.assertEqual(b"{}", path.read_bytes())
            outbox.put("a", {"value": 1})
            outbox.put("b", {"value": 2})
            self.assertEqual({"a": {"value": 1}, "b": {"value": 2}}, outbox.load())
            outbox.remove("a")
            self.assertEqual({"b": {"value": 2}}, JsonRecordOutbox(path).load())
            outbox.remove("b")
            self.assertEqual(b"{}", path.read_bytes())

    def test_invalid_or_unreadable_outbox_is_explicit_and_never_replaced(self) -> None:
        cases = (
            ("empty-file", b"", json.JSONDecodeError, None),
            ("truncated-json", b'{"old":{"value":1}', json.JSONDecodeError, None),
            ("invalid-utf8", b"\xff\xfe\x00", UnicodeDecodeError, None),
            ("list-root", b'[{"old":{"value":1}}]', ValueError, None),
            ("null-root", b"null", ValueError, None),
            ("scalar-root", b'"not an object"', ValueError, None),
            ("read-error", b'{"old":{"value":1}}', OSError, OSError("controlled read I/O failure")),
            ("permission-error", b'{"old":{"value":1}}', PermissionError, PermissionError("controlled read permission failure")),
        )
        for name, original, error_type, injected in cases:
            for operation in ("load", "put", "remove"):
                with self.subTest(case=name, operation=operation), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "outbox.json"
                    path.write_bytes(original)
                    outbox = JsonRecordOutbox(path)
                    caught = None
                    with patch.object(outbox, "_write", wraps=outbox._write) as write:
                        def invoke():
                            if operation == "put":
                                return outbox.put("new", {"value": 2})
                            if operation == "remove":
                                return outbox.remove("old")
                            return outbox.load()
                        try:
                            if injected is None:
                                invoke()
                            else:
                                with patch.object(Path, "read_text", side_effect=injected):
                                    invoke()
                        except Exception as error:
                            caught = error
                        self.assertEqual(original, path.read_bytes())
                        self.assertIsInstance(caught, error_type)
                        write.assert_not_called()
                    self.assertEqual([path], list(Path(directory).iterdir()))

    def test_failed_atomic_replace_preserves_original_and_next_call_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outbox.json"
            outbox = JsonRecordOutbox(path)
            outbox.put("old", {"value": 1})
            original = path.read_bytes()
            native_replace = Path.replace
            def fail_publication(source, target):
                if target == path:
                    raise OSError("controlled atomic replace failure")
                return native_replace(source, target)
            with patch.object(Path, "replace", fail_publication):
                with self.assertRaisesRegex(OSError, "atomic replace failure"):
                    outbox.put("new", {"value": 2})
            self.assertEqual(original, path.read_bytes())
            self.assertEqual({"old": {"value": 1}}, outbox.load())
            outbox.put("new", {"value": 2})
            self.assertEqual({"old": {"value": 1}, "new": {"value": 2}}, JsonRecordOutbox(path).load())
            self.assertEqual([path], list(Path(directory).iterdir()))
