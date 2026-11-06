"""The same real leader/CLOSE account fixture on the native PostgreSQL writer."""
import unittest


class ParallelStrategyPostgresTests(unittest.IsolatedAsyncioTestCase):
    def factory(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        return LeaderCurrentOrderManagementPostgresTests.factory(self)

    async def test_genuine_final_ready_and_one_binding_restore_two_members_without_orders(self):
        from tests.unit.test_mock_automation_parallel_strategies import verify_parallel_preparation
        await verify_parallel_preparation(self, self.factory())

    async def test_actual_same_symbol_orders_and_staged_reduction_keep_other_plan_and_manual_quantity(self):
        from tests.unit.test_mock_automation_parallel_strategies import verify_parallel_staged_ownership
        await verify_parallel_staged_ownership(self, self.factory())

    async def test_one_account_runner_task_consumes_both_actual_member_fills_and_closes_once(self):
        from tests.unit.test_mock_automation_parallel_strategies import verify_parallel_one_task
        await verify_parallel_one_task(self, self.factory())

    async def test_actual_strategy_orders_share_the_frozen_account_capital_limit(self):
        from tests.unit.test_mock_automation_parallel_strategies import verify_parallel_shared_capital
        await verify_parallel_shared_capital(self, self.factory())

    async def test_recorded_failure_and_new_setup_keep_the_close_plan_and_old_history(self):
        from tests.unit.test_mock_automation_parallel_strategies import verify_parallel_new_setup_reentry
        await verify_parallel_new_setup_reentry(self, self.factory())

    async def test_late_old_fill_after_new_queued_claim_stays_on_old_plan_and_prevents_submission(self):
        from tests.unit.test_mock_automation_parallel_strategies import verify_parallel_late_old_fill_invalidates_reentry
        await verify_parallel_late_old_fill_invalidates_reentry(self, self.factory())

    async def test_partial_unknown_cancel_late_fill_and_native_reopen_require_all_member_resume(self):
        from tests.unit.test_mock_automation_parallel_strategies import verify_parallel_late_fill_restart_resume
        await verify_parallel_late_fill_restart_resume(self, self.factory())

    async def test_actual_two_strategy_peer_orders_amend_and_cancel_after_current_shadow_off(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_peer_current_management
        await verify_group_peer_current_management(self, self.factory())


class AccountRecoveryPostgresTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        self.store = LeaderCurrentOrderManagementPostgresTests.factory(self)()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_verified_peer_broker_order_uses_the_full_native_account_cut(self):
        from tests.unit.test_mock_automation_account_recovery import verify_account_recovery_peer_order
        verify_account_recovery_peer_order(self, self.store)

    def test_native_member_fill_keeps_peer_position_and_original_risk(self):
        from tests.unit.test_mock_automation_account_recovery import AccountRecoveryTests
        AccountRecoveryTests.test_native_fill_keeps_peer_position_without_relabeling_or_zeroing_canonical_risk(self)

    def test_missing_and_stripped_member_observations_cannot_clear(self):
        from tests.unit.test_mock_automation_account_recovery import AccountRecoveryTests
        AccountRecoveryTests.test_missing_incomplete_unmapped_and_stripped_member_observations_cannot_clear(self)

    def test_unknown_reservation_and_old_binding_cannot_clear(self):
        from tests.unit.test_mock_automation_account_recovery import AccountRecoveryTests
        AccountRecoveryTests.test_unknown_extra_reservation_and_current_binding_revision_cannot_clear(self)

    def test_unknown_submission_waits_for_real_late_fill_and_current_cost_evidence(self):
        from tests.unit.test_mock_automation_account_recovery import AccountRecoveryTests
        AccountRecoveryTests.test_submission_unknown_stays_blocked_until_real_late_fill_and_current_cost_evidence(self)
