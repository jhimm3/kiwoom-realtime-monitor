"""Member Episode checkpoint/basis ownership on the real native PostgreSQL writer."""
import unittest


class MockAccountRunnerPostgresTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        factory = LeaderCurrentOrderManagementPostgresTests.factory(self)
        self.store = factory()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_actual_member_fills_restore_independent_native_checkpoints(self):
        from tests.unit.test_mock_automation_account_runner import verify_native_member_episode_checkpoint
        verify_native_member_episode_checkpoint(self, self.store)

    def test_member_stop_keeps_the_actual_own_basis_and_full_account_risk_identity(self):
        from tests.unit.test_mock_automation_account_runner import NativeAccountEpisodeTests
        NativeAccountEpisodeTests.test_member_stop_uses_its_actual_basis_and_quantity_from_the_same_full_account_risk(self)

    def test_non_anchor_panic_native_claim_uses_only_its_own_saved_trigger(self):
        from tests.unit.test_mock_automation_account_runner import verify_native_panic_member_checkpoint
        from tests.integration.test_panic_mock_execution_postgres import PanicCurrentOrderPostgresTests
        verify_native_panic_member_checkpoint(self, PanicCurrentOrderPostgresTests.factory(self))
