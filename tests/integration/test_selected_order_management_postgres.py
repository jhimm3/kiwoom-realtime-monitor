"""Current selected management uses the same native C/D helpers on PostgreSQL."""
import unittest

from tests.unit import test_selected_order_management as fixture
from tests.unit.test_selected_order_recovery import SelectedOrderRecoveryTests


class SelectedOrderManagementPostgresTests(unittest.IsolatedAsyncioTestCase):
    source, target = SelectedOrderRecoveryTests.source, SelectedOrderRecoveryTests.target
    _exercise_current_acceptance = fixture.SelectedManagementOwnerTests._exercise_current_acceptance

    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        self.target_store_factory = LeaderCurrentOrderManagementPostgresTests.factory(self)

    async def test_current_target_token_exchange_preserves_one_canonical_owner(self):
        await fixture.SelectedManagementLeaseTests.test_fresh_current_target_hands_one_lease_to_original_run_without_activating_past_approval(self)

    async def test_public_original_close_first_stop_uses_current_target_authority(self):
        await fixture.SelectedManagementOwnerTests.test_public_current_acceptance_manages_only_original_pending_close_and_preserved_first_stop(self)

    async def test_native_current_acceptance_response_loss_has_one_durable_cas(self):
        await fixture.SelectedManagementOwnerTests.test_current_acceptance_commit_ack_loss_reads_first_native_receipt_once(self)
