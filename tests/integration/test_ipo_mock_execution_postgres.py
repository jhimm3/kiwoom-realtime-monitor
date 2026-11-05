"""IPO native claims use the same isolated PostgreSQL transaction contracts."""
import os
import unittest


class IpoMockExecutionPostgresTests(unittest.TestCase):
    def factory(self):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        scoped = self.enterContext(isolated_observation_schema(url))
        return lambda: PostgresQueryStore(scoped)

    def test_ipo_close_native_partial_tranche_and_reopened_residual_keep_original_listing_phase(self):
        from tests.unit.test_ipo_mock_execution import verify_ipo_close_native
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                verify_ipo_close_native(self, self.factory(), phase=phase)

    def test_ipo_close_does_not_reduce_away_actual_partial_buy_residual(self):
        from tests.unit.test_ipo_mock_execution import verify_ipo_close_native
        verify_ipo_close_native(self, self.factory(), phase='listing_day', partial_buy=True)

    def test_ipo_breakout_native_strict_structure_exit_keeps_both_listing_phases(self):
        from tests.unit.test_ipo_mock_execution import verify_ipo_breakout_native
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                verify_ipo_breakout_native(self, self.factory(), phase=phase, deadline=False)

    def test_ipo_breakout_native_original_deadline_exits_actual_partial_buy_same_day(self):
        from tests.unit.test_ipo_mock_execution import verify_ipo_breakout_native
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                verify_ipo_breakout_native(self, self.factory(), phase=phase, deadline=True)
