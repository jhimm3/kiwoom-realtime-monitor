"""The same original-approval views and actual handoff use the native PG writer."""
import os
import unittest


class RetainedProjectionPostgresTests(unittest.IsolatedAsyncioTestCase):
    def factory(self):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        scoped = self.enterContext(isolated_observation_schema(url))
        return lambda: PostgresQueryStore(scoped)

    async def test_original_native_plan_and_late_fill_keep_read_only_member_proof(self):
        from tests.unit.test_mock_automation_retained_projection import StandaloneRetainedProjectionTests
        self._retained_factory = self.factory()
        await StandaloneRetainedProjectionTests.test_real_old_plan_partial_and_late_fill_use_proved_member_view_without_rewriting_sources(self)

    async def test_actual_standalone_handoff_keeps_new_entry_and_old_protection_separate(self):
        from tests.unit.test_mock_automation_retained_projection import StandaloneRetainedProjectionTests
        self._retained_factory = self.factory()
        await StandaloneRetainedProjectionTests.test_actual_standalone_handoff_preserves_old_partial_owner_and_new_entry_scope(self)
