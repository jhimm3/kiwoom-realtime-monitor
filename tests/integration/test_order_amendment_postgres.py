"""Amendment physical facts and claims use the real isolated PostgreSQL writer."""
import os
import unittest
import asyncio


class OrderAmendmentPostgresTests(unittest.TestCase):
    def factory(self):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        url = os.environ.get("KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL", "")
        if not url:
            raise unittest.SkipTest("dedicated diagnostic PostgreSQL URL is required")
        scoped = self.enterContext(isolated_observation_schema(url))
        return lambda: PostgresQueryStore(scoped)

    def test_original_and_amended_unique_fills_keep_physical_identity_after_reopen(self):
        from kiwoom_monitor.domain.order_contract import OrderSide
        from tests.unit.test_order_amendment import verify_physical_fills
        for side in (OrderSide.BUY, OrderSide.SELL):
            verify_physical_fills(self, self.factory(), sides=(side,))

    def test_lost_ack_keeps_command_and_original_late_fills_without_retransmission(self):
        from tests.unit.test_order_amendment import verify_unknown_and_durable_claim
        verify_unknown_and_durable_claim(self, self.factory())

    def test_pre_network_crash_and_stale_writer_preserve_one_durable_command(self):
        from tests.unit.test_order_amendment import verify_claim_crash_and_cas
        verify_claim_crash_and_cas(self, self.factory())

    def test_partial_amendment_cancels_each_leg_without_releasing_other_exposure(self):
        from tests.unit.test_order_amendment import verify_partial_transfer_and_cancel
        verify_partial_transfer_and_cancel(self, self.factory())

    def test_rejected_unknown_and_invalid_amendments_never_fall_back_to_new_order(self):
        from tests.unit.test_order_amendment import verify_rejected_and_invalid_response
        verify_rejected_and_invalid_response(self, self.factory())

    def test_expired_native_owner_cannot_claim_or_transmit_an_amendment(self):
        from tests.unit.test_order_amendment import verify_expired_owner
        verify_expired_owner(self, self.factory())

    def test_existing_planned_entry_approval_cannot_authorize_a_new_amendment(self):
        from tests.unit.test_order_amendment import verify_planned_admission_is_not_reused
        verify_planned_admission_is_not_reused(self, self.factory())

    def test_unknown_ack_recovers_from_parent_details_and_retained_fill_without_transmission(self):
        from tests.unit.test_order_amendment import verify_recovered_amendment
        verify_recovered_amendment(self, self.factory())

    def test_restart_after_binding_replays_durable_observation_exactly_once(self):
        from tests.unit.test_order_amendment import verify_recovered_amendment
        verify_recovered_amendment(self, self.factory(), crash=True)

    def test_account_monitor_retains_early_child_00_and_recovers_using_existing_reads(self):
        from tests.unit.test_order_amendment import verify_amendment_monitor
        asyncio.run(verify_amendment_monitor(self, self.factory()))

    def test_conflicting_excess_fill_stays_durable_and_unresolved_without_weakening_quantity_cap(self):
        from tests.unit.test_order_amendment import verify_recovered_amendment
        verify_recovered_amendment(self, self.factory(), overfill=True)
