"""Native member FILL attribution and shared Risk on isolated PostgreSQL."""
import unittest


class MockMemberRiskPostgresTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        factory=LeaderCurrentOrderManagementPostgresTests.factory(self)
        self.store=factory()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_actual_member_fills_pending_and_plan_quantity_share_one_risk_identity(self):
        from tests.unit.test_mock_automation_member_risk import verify_member_risk_and_positions
        verify_member_risk_and_positions(self,self.store)

    def test_missing_member_metadata_preserves_physical_account_facts_as_incomplete(self):
        from tests.unit.test_mock_automation_member_risk import verify_missing_member_facts
        verify_missing_member_facts(self,self.store)

    def test_native_update_preserves_the_member_when_an_older_client_omits_it(self):
        from tests.unit.test_mock_automation_member_risk import MockMemberRiskTests
        MockMemberRiskTests.test_native_events_retain_ownership_when_an_older_writer_omits_the_optional_field(self)

    def test_submission_rechecks_the_stricter_shared_freshness_after_queue(self):
        from tests.unit.test_mock_automation_member_risk import MockMemberRiskTests
        MockMemberRiskTests.test_shared_freshness_is_checked_again_at_submission_after_a_durable_queue(self)
