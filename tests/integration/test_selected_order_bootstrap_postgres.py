"""The same closed native installation and immutable input receipt on PostgreSQL."""
import unittest
from unittest.mock import patch

from kiwoom_monitor.domain.selected_order_evidence import COLLECTION, OWNER
from tests.unit import test_selected_order_bootstrap as bootstrap_fixture


class SelectedOrderBootstrapPostgresTests(unittest.IsolatedAsyncioTestCase):
    source, target = bootstrap_fixture.SelectedOrderBootstrapTests.source, bootstrap_fixture.SelectedOrderBootstrapTests.target
    prepare, assert_empty_execution = bootstrap_fixture.SelectedOrderBootstrapTests.prepare, bootstrap_fixture.SelectedOrderBootstrapTests.assert_empty_execution
    current_read_runtime = bootstrap_fixture.SelectedOrderBootstrapTests.current_read_runtime
    install_from_current_owner = bootstrap_fixture.SelectedOrderBootstrapTests.install_from_current_owner
    native_member_source = bootstrap_fixture.SelectedOrderBootstrapTests.native_member_source

    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        self.target_store_factory = LeaderCurrentOrderManagementPostgresTests.factory(self)

    async def test_closed_installation_preserves_original_facts_and_separate_target_cursor(self):
        await bootstrap_fixture.SelectedOrderBootstrapTests.test_full_policy_export_and_native_install_preserve_ids_with_independent_target_cursor(self)

    async def test_non_anchor_native_member_approval_ref_survives_install_and_account_event_read(self):
        await bootstrap_fixture.SelectedOrderBootstrapTests.test_actual_non_anchor_close_export_retains_its_own_policy_and_admission(self)

    async def test_commit_ack_loss_preserves_first_installation_and_same_packet_replay(self):
        await bootstrap_fixture.SelectedOrderBootstrapTests.test_lost_commit_ack_replays_first_receipt_without_reinstalling_or_renewing_it(self)

    async def test_epoch_race_rolls_back_native_order_events_control_and_installation(self):
        await bootstrap_fixture.SelectedOrderBootstrapTests.test_target_epoch_change_before_commit_rolls_back_every_native_installation_write(self)

    async def test_active_foreign_lease_cannot_install_via_source_uuid(self):
        await bootstrap_fixture.SelectedOrderBootstrapTests.test_an_already_active_canonical_target_cannot_install_under_an_origin_alias(self)

    async def test_offline_input_receipt_replay_is_immutable_and_does_not_renew_its_clock(self):
        source, target, _ = await self.prepare()
        original = target.store.load_document(COLLECTION, OWNER, source.packet['operation_id'])['document']
        with patch.object(target.store, '_now_provider', lambda: self.fail('immutable receipt timestamp renewed')):
            replay = target.store.accept_selected_order_evidence(source.packet, expected_sha256=source.digest,
                account_ref=source.packet['account_ref'], run_id=source.packet['run_id'])
        self.assertEqual({'receipt': original['receipt'], 'replayed': True}, replay)

    async def test_explicit_owned_target_read_runtime_uses_one_canonical_lease(self):
        await bootstrap_fixture.SelectedOrderBootstrapTests.test_explicit_server_owned_read_runtime_keeps_its_single_canonical_lease_and_no_new_authority(self)

    async def test_foreign_token_account_expiry_lease_and_read_snapshot_are_rejected(self):
        await bootstrap_fixture.SelectedOrderBootstrapTests.test_server_runtime_context_rejects_foreign_token_account_expired_lease_and_snapshot(self)
