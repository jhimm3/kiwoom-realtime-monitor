"""Account composition publication and immutable receipt on native PostgreSQL."""
import unittest


class MockAccountSelectionPostgresTests(unittest.TestCase):
    def store(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        store=LeaderCurrentOrderManagementPostgresTests.factory(self)()
        store.initialize()
        self.addCleanup(store.close)
        return store

    def test_two_ready_members_share_scope_without_creating_execution_authority(self):
        from tests.unit.test_mock_automation_account_selection import verify_account_selection_round_trip
        verify_account_selection_round_trip(self,self.store())

    def test_immutable_commit_response_loss_retries_once_and_rechecks_selected_source(self):
        from tests.unit.test_mock_automation_account_selection import verify_account_selection_ack_loss
        verify_account_selection_ack_loss(self,self.store())

    def test_invalid_member_leaves_no_partial_account_composition(self):
        from tests.unit.test_mock_automation_account_selection import verify_account_selection_invalid_member
        verify_account_selection_invalid_member(self,self.store())
