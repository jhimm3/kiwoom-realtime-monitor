"""The native CLOSE deadline contract on an isolated PostgreSQL schema."""
import os
import unittest


class CloseDeadlinePostgresTests(unittest.TestCase):
    def _verify(self, partial, *, gap=False):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        from tests.unit.test_close_mock_execution import verify_close_deadline

        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        scoped = self.enterContext(isolated_observation_schema(url))
        verify_close_deadline(self, lambda: PostgresQueryStore(scoped), partial=partial, gap=gap)

    def test_zero_fill_deadline_cancel_keeps_late_fill_and_restores_management(self):
        self._verify(False)

    def test_partial_deadline_cancel_with_unknown_response_preserves_late_fill_after_restart(self):
        self._verify(True)

    def test_completed_adverse_gap_uses_native_revisions_and_current_quote_after_restart(self):
        self._verify(True, gap=True)
