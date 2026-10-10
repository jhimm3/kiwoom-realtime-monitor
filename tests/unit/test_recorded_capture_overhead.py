"""Controlled account/VI/large benchmark fixtures preserve native results."""
from datetime import datetime, timezone
from pathlib import Path
import unittest

from scripts.compare_recorded_capture_overhead import AccountLargeFixture, fixture
from kiwoom_monitor.central_server.database import SQLiteQueryStore


class RecordedCaptureOverheadTests(unittest.TestCase):
    def test_same_initial_state_and_native_inputs_preserve_contents_and_revisions(self):
        signatures = []
        for _ in range(2):
            store = SQLiteQueryStore(Path(':memory:'))
            store.initialize()
            try:
                value = AccountLargeFixture(store, datetime(2026, 10, 12, tzinfo=timezone.utc))
                value.batch(0)
                value.batch(50)
                self.assertGreater(value.encoded_bytes, 8 * 1024**2)
                self.assertEqual(20, sum(value.counts.values()))
                self.assertEqual(4, value.counts['append_vi_events'])
                self.assertEqual(2, value.counts['save_shadow_monitor_state'])
                self.assertEqual([1, 0, 1, 0], [result for method, result in value.outcomes if method == 'append_vi_events'])
                with store._connection() as connection:
                    self.assertEqual((2, 2, 2), tuple(connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                        for table in ('central_execution_intents', 'central_execution_events', 'central_vi_event_revisions')))
                signatures.append(value.signature())
            finally:
                store.close()
        self.assertEqual(signatures[0], signatures[1])

    def test_extended_profile_uses_same_receiver_messages_as_mixed(self):
        _, messages = fixture(10, 2, True)
        self.assertEqual([5] * 10, [len(message['data']) for message in messages])
        self.assertEqual({'0B', '0w', '0J', '0U'}, {row['type'] for row in messages[0]['data']})


if __name__ == '__main__':
    unittest.main()
