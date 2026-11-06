"""Selected original Leader policy uses the same real C/D scenario on PostgreSQL."""
import unittest

from tests.unit import test_selected_leader_management as leader_fixture


class SelectedLeaderManagementPostgresTests(unittest.IsolatedAsyncioTestCase):
    target = leader_fixture.SelectedLeaderManagementTests.target

    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        self.target_store_factory = LeaderCurrentOrderManagementPostgresTests.factory(self)

    async def test_public_acceptance_cancels_original_partial_buy_then_protects_its_two_actual_fills(self):
        await leader_fixture.SelectedLeaderManagementTests.test_public_acceptance_cancels_original_partial_buy_then_protects_its_two_actual_fills(self)
