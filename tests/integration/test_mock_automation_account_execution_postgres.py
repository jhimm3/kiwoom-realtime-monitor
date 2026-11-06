"""Shared account authority/reservation/plan FILL contracts on native PostgreSQL."""
import unittest


class MockAccountExecutionPostgresTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        factory=LeaderCurrentOrderManagementPostgresTests.factory(self)
        self.store=factory()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_one_native_account_authority_admits_two_exact_frozen_members(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_admission
        verify_group_admission(self,self.store)

    def test_native_member_reservations_share_one_account_without_broker_retry(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_native_reservation
        verify_group_native_reservation(self,self.store)

    def test_actual_unknown_peer_blocks_new_claim_at_stricter_account_limit(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_native_unknown_boundary
        verify_group_native_unknown_boundary(self, self.store)

    def test_actual_unknown_peer_blocks_already_queued_native_submission(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_native_unknown_boundary
        verify_group_native_unknown_boundary(self, self.store, already_queued=True)

    def test_verified_physical_peer_reservation_is_counted_once(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_native_peer_reservation
        verify_group_native_peer_reservation(self, self.store)

    def test_common_capital_failure_rolls_back_the_second_plan_and_intent(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_native_reservation
        verify_group_native_reservation(self,self.store,capital=100_000)

    def test_actual_plan_fill_exit_preserves_the_other_member_and_its_original_cost(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_native_fill_isolation
        verify_group_native_fill_isolation(self,self.store)

    def test_queued_commit_ack_loss_never_retransmits_to_the_broker(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_queued_ack_loss
        verify_group_queued_ack_loss(self,self.store)

    def test_member_stop_fences_new_entry_without_discarding_an_accepted_fill(self):
        from tests.unit.test_mock_automation_account_execution import verify_group_stop
        verify_group_stop(self,self.store)
