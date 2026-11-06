"""The same explicit additional-lot/partial-exit contracts on native PostgreSQL."""
import unittest


class LeaderStagedPostgresTests(unittest.IsolatedAsyncioTestCase):
    def factory(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        return LeaderCurrentOrderManagementPostgresTests.factory(self)

    async def test_native_add_reduction_and_full_stop_share_one_plan_and_actual_fills(self):
        from tests.unit.test_leader_staged_execution import verify_staged_round_trip
        await verify_staged_round_trip(self, self.factory())

    async def test_current_entry_off_before_claim_rolls_back_add_without_consuming_its_step(self):
        from tests.unit.test_leader_staged_execution import verify_current_entry_off
        await verify_current_entry_off(self, self.factory())

    async def test_native_cumulative_entry_budget_counts_original_fills_and_rolls_back_new_action(self):
        from tests.unit.test_leader_staged_execution import verify_cumulative_entry_budget
        await verify_cumulative_entry_budget(self, self.factory())

    async def test_additional_remainder_amends_then_cancels_when_its_own_current_breakout_fails(self):
        from tests.unit.test_leader_staged_execution import verify_additional_current_management
        await verify_additional_current_management(self, self.factory())

    async def test_restart_with_old_checkpoint_recovers_native_action_without_a_second_buy(self):
        from tests.unit.test_leader_staged_execution import verify_staged_restart
        await verify_staged_restart(self, self.factory())

    async def test_additional_queued_commit_ack_loss_restores_once_without_network_retry(self):
        from tests.unit.test_leader_staged_execution import verify_staged_claim_ack_loss
        await verify_staged_claim_ack_loss(self, self.factory())

    async def test_additional_partial_cancel_unknown_late_fill_protects_actual_eight_under_first_stop_seven(self):
        from tests.unit.test_leader_staged_execution import verify_staged_cancel_unknown_late_fill
        await verify_staged_cancel_unknown_late_fill(self, self.factory())

    async def test_partial_and_full_protection_leave_independent_manual_four_untouched(self):
        from tests.unit.test_leader_staged_execution import verify_staged_manual_quantity_isolation
        await verify_staged_manual_quantity_isolation(self, self.factory())
