"""Leader current-order claims against an isolated native PostgreSQL writer."""
import os
import unittest


class LeaderCurrentOrderManagementPostgresTests(unittest.IsolatedAsyncioTestCase):
    def factory(self):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url:
            raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        scope = self.enterContext(isolated_observation_schema(url))
        return lambda: PostgresQueryStore(scope)

    async def test_current_buy_amends_after_original_ttl_and_keeps_physical_fill_scope(self):
        from tests.unit.test_leader_order_management import verify_current_buy
        await verify_current_buy(self, self.factory())

    async def test_unknown_amendment_reconciles_without_resending(self):
        from tests.unit.test_leader_order_management import verify_current_buy
        await verify_current_buy(self, self.factory(), lost_ack=True)

    async def test_native_current_condition_change_rejects_claim_before_transport(self):
        from tests.unit.test_leader_order_management import verify_native_current_recheck
        await verify_native_current_recheck(self, self.factory())

    async def test_current_breakout_failure_cancels_instead_of_amending_or_reentering(self):
        from tests.unit.test_leader_order_management import verify_current_entry_predicates
        await verify_current_entry_predicates(self, self.factory())

    async def test_ordinary_exit_amends_only_actual_plan_remainder_with_shadow_off(self):
        from tests.unit.test_leader_order_management import verify_current_sell
        await verify_current_sell(self, self.factory())

    async def test_confirmed_protection_exit_keeps_first_stop_and_amends_actual_remainder(self):
        from tests.unit.test_leader_order_management import verify_current_sell
        await verify_current_sell(self, self.factory(), protective=True)

    async def test_protection_after_amended_buy_reconciles_all_physical_orders(self):
        from tests.unit.test_leader_order_management import verify_current_sell
        await verify_current_sell(self, self.factory(), protective=True, amended_buy=True)

    async def test_cancel_unknown_and_late_fill_keep_original_plan_and_protect_only_actual_three(self):
        from tests.unit.test_leader_order_management import verify_cancel_unknown_late_fill
        await verify_cancel_unknown_late_fill(self, self.factory())

    async def test_explicit_amendment_limit_does_not_disable_current_failure_cancellation(self):
        from tests.unit.test_leader_order_management import verify_amendment_limit
        await verify_amendment_limit(self, self.factory())
