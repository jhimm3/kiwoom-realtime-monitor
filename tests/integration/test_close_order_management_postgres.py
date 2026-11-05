"""Current-condition order management against the actual isolated native writer."""
import os
import unittest


class CloseCurrentOrderManagementPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_ipo_breakout_rechecks_its_original_breakout_condition_in_both_listing_phases(self):
        from tests.unit.test_close_order_management import verify_ipo_current
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                await verify_ipo_current(self, self.factory(), family='ipo_breakout/v1', phase=phase)

    async def test_ipo_close_uses_current_amendment_and_context_cancellation_in_both_listing_phases(self):
        from tests.unit.test_close_order_management import verify_ipo_current
        for phase in ('listing_day', 'after_listing_day'):
            with self.subTest(phase=phase):
                await verify_ipo_current(self, self.factory(), family='ipo_close/v1', phase=phase)

    def factory(self):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        scoped = self.enterContext(isolated_observation_schema(url))
        return lambda: PostgresQueryStore(scoped)

    async def test_current_buy_reassesses_after_original_intent_expiry_and_cancels_on_context_change(self):
        from tests.unit.test_close_order_management import verify_current_buy
        await verify_current_buy(self, self.factory(), cancel=True)

    async def test_unknown_amendment_is_recovered_without_resending(self):
        from tests.unit.test_close_order_management import verify_current_buy
        await verify_current_buy(self, self.factory(), lost_ack=True)

    async def test_current_sell_amends_owned_remainder_after_stop_without_cancel_and_reentry(self):
        from tests.unit.test_close_order_management import verify_current_sell
        await verify_current_sell(self, self.factory())

    async def test_native_transaction_rechecks_current_context_before_any_mutation(self):
        from tests.unit.test_close_order_management import verify_native_current_recheck
        await verify_native_current_recheck(self, self.factory())
