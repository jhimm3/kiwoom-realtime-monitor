from pathlib import Path
import tempfile
import unittest

from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.database_observation_readers import ObservationDeliveryState
from kiwoom_monitor.central_server.market_observations import ranking_observation
from datetime import datetime, timezone


class ObservationDeliveryTests(unittest.TestCase):
    def test_fixed_cohort_progresses_while_new_writer_is_pending(self):
        state = ObservationDeliveryState()
        epoch = ('boot', 1, 2, 3)
        old, new = (10, '1/1'), (20, '2/1')
        safe, _ = state.advance(0, epoch, 10, frozenset({old}))
        self.assertEqual(0, safe)
        safe, counts = state.advance(state.snapshot_version(), epoch, 20, frozenset({old, new}))
        self.assertEqual(0, safe)
        self.assertEqual(frozenset({old}), state.pending.owners)
        safe, counts = state.advance(state.snapshot_version(), epoch, 20, frozenset({new}))
        self.assertEqual(10, safe)
        self.assertEqual(frozenset({new}), state.pending.owners)
        self.assertEqual(1, counts['captured_owners'])
        safe, _ = state.advance(state.snapshot_version(), epoch, 20, frozenset())
        self.assertEqual(20, safe)

    def test_stale_concurrent_probe_cannot_publish_or_erase_frontier(self):
        state = ObservationDeliveryState()
        epoch = ('boot', 1, 2, 3)
        state.advance(0, epoch, 12, frozenset())
        safe, diagnostic = state.advance(0, ('older-boot', 1, 2, 3), 2, frozenset())
        self.assertIsNone(safe)
        self.assertEqual('concurrent_frontier_refresh', diagnostic['reason'])
        self.assertEqual(epoch, state.epoch)
        self.assertEqual(12, state.safe)

    def test_epoch_change_discards_old_pending_and_safe_bounds(self):
        state = ObservationDeliveryState()
        state.advance(0, ('boot1', 1, 2, 3), 100, frozenset())
        safe, _ = state.advance(state.snapshot_version(), ('boot2', 1, 2, 4), 2,
                                frozenset({(12, '3/2')}))
        self.assertEqual(0, safe)
        self.assertEqual(2, state.pending.high)
        self.assertEqual(2, state.high_seen)

    def test_sequence_regression_in_same_epoch_is_explicit(self):
        state = ObservationDeliveryState()
        epoch = ('boot', 1, 2, 3)
        state.advance(0, epoch, 12, frozenset())
        with self.assertRaisesRegex(RuntimeError, 'sequence_regressed'):
            state.advance(state.snapshot_version(), epoch, 2, frozenset())
        self.assertEqual(12, state.safe)

    def test_same_backend_new_transaction_does_not_hold_old_fence(self):
        state = ObservationDeliveryState()
        epoch = ('boot', 1, 2, 3)
        state.advance(0, epoch, 12, frozenset({(10, '1/1')}))
        safe, _ = state.advance(state.snapshot_version(), epoch, 13, frozenset({(10, '1/2')}))
        self.assertEqual(12, safe)
        self.assertEqual(13, state.pending.high)

    def test_sqlite_page_and_common_bootstrap_preserve_actual_row_cursor(self):
        with tempfile.TemporaryDirectory() as root:
            store = SQLiteQueryStore(Path(root) / 'observations.sqlite3')
            store.initialize()
            self.assertTrue(store.load_observation_bootstrap(('ranking', 'top20_membership')).ready)
            now = datetime(2026, 10, 9, tzinfo=timezone.utc)
            for index, kind in enumerate(('ranking', 'top20_membership', 'ranking')):
                key = f'2026-10-09T09:00:0{index}+09:00'
                payload = {'rows': [{'code': str(index), 'rank': 1}], 'codes': [str(index)]}
                store.save_dataset_snapshot(kind, 'delivery-test', key, payload,
                    observation=ranking_observation('delivery-test', key, payload, now, source='delivery-test'))
            page = store.load_observation_revision_page(0, ('ranking', 'top20_membership'), 1)
            self.assertTrue(page.ready)
            self.assertFalse(page.exhausted)
            self.assertEqual(1, len(page.rows))
            after = page.rows[-1]['accepted_sequence']
            tail = store.load_observation_revision_page(after, ('ranking', 'top20_membership'), 10)
            self.assertTrue(tail.exhausted)
            self.assertEqual(2, len(tail.rows))
            seed = store.load_observation_bootstrap(('ranking', 'top20_membership'), 1)
            self.assertEqual(['top20_membership', 'ranking'], [row['kind'] for row in seed.rows])
            self.assertEqual(tail.rows[-1]['accepted_sequence'], seed.safe_through)
            limited = store.load_observation_revision_page(0, ('ranking', 'top20_membership'),
                through_sequence=after)
            self.assertEqual(page.rows, limited.rows)
            self.assertEqual(list(page.rows + tail.rows),
                             store.load_observation_revisions_after(0, ('ranking', 'top20_membership')))
            store.close()
