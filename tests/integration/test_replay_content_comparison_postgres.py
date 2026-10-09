"""Post-drain comparison of native rows on a disposable diagnostic DB only."""
import os
import unittest
from urllib.parse import urlsplit

from kiwoom_monitor.central_server.database import PostgresQueryStore
from kiwoom_monitor.central_server.diagnostic_replay_comparison import collect_final_content_comparison
from tests.integration.test_storage_boundary_postgres import _seeded_finalization_fixture


class ReplayContentComparisonPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        if urlsplit(url).path != '/kiwoom_monitor_diagnostic_test':
            raise RuntimeError('content comparison test requires diagnostic database')
        cls.store = PostgresQueryStore(url)
        cls.store.initialize()

    def collect(self, cursor):
        return collect_final_content_comparison(cursor, max_bytes=64 * 1024**2, max_rows=250_000)

    def test_native_revision_uuid_and_receive_time_change_preserves_content_chain(self):
        with _seeded_finalization_fixture(self.store) as (values, closures):
            self.store.finalize_minute_bars(closures)
            subject = values[0]['code'] + ':KRX'
            connection = self.store._connect()
            try:
                with connection.cursor() as cursor:
                    before = self.collect(cursor)
                    cursor.execute(
                        "UPDATE central_observation_revisions SET "
                        "revision_id=md5('comparison-proof-' || revision_id), "
                        "revision_of=CASE WHEN revision_of IS NULL THEN NULL "
                        "ELSE md5('comparison-proof-' || revision_of) END, "
                        "received_at=received_at+INTERVAL '1 day' WHERE kind='minute_bar' AND subject=%s",
                        (subject,),
                    )
                    self.assertGreater(cursor.rowcount, 0)
                    after = self.collect(cursor)
                    a, b = (r['tables']['central_observation_revisions'] for r in (before, after))
                    self.assertTrue(b['content_projection_valid'])
                    self.assertEqual((a['rows'], a['sha256']), (b['rows'], b['sha256']))
                    self.assertNotEqual(a['generated_time_fields'], b['generated_time_fields'])
                    self.assertFalse(after['functional_equivalence_verified'])
            finally:
                connection.rollback()
                connection.close()

    def test_native_available_time_and_invalid_lineage_remain_visible(self):
        with _seeded_finalization_fixture(self.store) as (values, closures):
            self.store.finalize_minute_bars(closures)
            subject = values[0]['code'] + ':KRX'
            connection = self.store._connect()
            try:
                with connection.cursor() as cursor:
                    before = self.collect(cursor)['tables']['central_observation_revisions']
                    cursor.execute(
                        "UPDATE central_observation_revisions SET available_at=available_at+INTERVAL '1 day' "
                        "WHERE kind='minute_bar' AND subject=%s", (subject,),
                    )
                    self.assertGreater(cursor.rowcount, 0)
                    after = self.collect(cursor)['tables']['central_observation_revisions']
                    self.assertNotEqual(before['sha256'], after['sha256'])
                    cursor.execute(
                        "UPDATE central_observation_revisions SET revision_of='missing-comparison-parent' "
                        "WHERE kind='minute_bar' AND subject=%s", (subject,),
                    )
                    invalid = self.collect(cursor)['tables']['central_observation_revisions']
                    self.assertFalse(invalid['content_projection_valid'])
                    self.assertGreater(invalid['lineage_errors']['revision_parent_missing'], 0)
            finally:
                connection.rollback()
                connection.close()


if __name__ == '__main__':
    unittest.main()
