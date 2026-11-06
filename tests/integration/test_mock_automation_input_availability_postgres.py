"""The same current-input and original-protection cases on native PostgreSQL."""
import unittest


class CurrentInputAvailabilityPostgresTests(unittest.TestCase):
    def verify(self, callback):
        from tests.integration.test_panic_mock_execution_postgres import PanicNativePlanPostgresTests
        PanicNativePlanPostgresTests.verify(self, callback)

    def test_late_current_news_after_shadow_before_native_claim_cannot_authorize_buy(self):
        from tests.unit.test_mock_automation_input_availability import verify_late_news_native_entry_fence
        self.verify(verify_late_news_native_entry_fence)

    def test_partial_trigger_correction_after_shadow_before_native_claim_cannot_authorize_buy(self):
        from tests.unit.test_mock_automation_input_availability import verify_partial_correction_native_entry_fence
        self.verify(verify_partial_correction_native_entry_fence)

    def test_future_available_correction_is_retained_without_becoming_current_knowledge(self):
        from tests.unit.test_mock_automation_input_availability import verify_future_correction_keeps_current_knowledge
        self.verify(verify_future_correction_keeps_current_knowledge)


class HeldInputAvailabilityPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_news_and_partial_correction_keep_first_protection_original_plan_and_screen_journal(self):
        from tests.integration.test_panic_mock_execution_postgres import PanicCurrentOrderPostgresTests
        from tests.unit.test_mock_automation_input_availability import verify_held_protection_after_late_inputs
        await verify_held_protection_after_late_inputs(self, PanicCurrentOrderPostgresTests.factory(self))
