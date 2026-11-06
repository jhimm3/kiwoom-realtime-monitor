"""Reuse the actual whole-plan native installation contract on owned PostgreSQL."""
import unittest
from tests.unit import test_selected_plan_bootstrap as fixture


class SelectedPlanBootstrapPostgresTests(unittest.IsolatedAsyncioTestCase):
    target = fixture.SelectedPlanBootstrapTests.target
    prepare = fixture.SelectedPlanBootstrapTests.prepare

    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        self.target_store_factory = LeaderCurrentOrderManagementPostgresTests.factory(self)

    async def test_terminal_enter_add_reduce_full_native_install_and_independent_cursor(self):
        await fixture.SelectedPlanBootstrapTests.test_terminal_entry_add_reduce_install_exact_native_ledger_and_one_share_with_separate_cursors(self)

    async def test_missing_policy_terminal_order_claim_or_first_cue_cannot_install(self):
        await fixture.SelectedPlanBootstrapTests.test_missing_terminal_order_policy_action_claim_or_first_cue_fails_closed(self)

    async def test_credential_epoch_race_rolls_back_all_plan_orders_and_events(self):
        await fixture.SelectedPlanBootstrapTests.test_epoch_race_rolls_back_all_three_original_intents_and_event_coordinates(self)

    async def test_commit_ack_loss_returns_first_receipt_without_reinstalling_whole_plan(self):
        await fixture.SelectedPlanBootstrapTests.test_lost_commit_ack_replays_the_same_whole_plan_receipt_without_partial_reinstallation(self)

    async def test_actual_native_cancel_claim_raw_json_survives_install_and_cannot_be_deleted_or_coerced(self):
        await fixture.SelectedPlanBootstrapTests.test_actual_amended_cancel_claim_is_preserved_and_cannot_be_deleted_or_coerced_in_source_proof(self)

    async def test_fake_rest_current_management_protects_one_remaining_share_after_price_recovery(self):
        await fixture.SelectedPlanBootstrapTests.test_fake_rest_current_management_protects_one_remaining_share_after_price_recovery(self)
