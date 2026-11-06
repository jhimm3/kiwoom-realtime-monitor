"""The actual account-resume CAS and member recovery proofs on PostgreSQL."""
import unittest


class TypedParallelResumePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_leader_protection_resume_map_survives_native_reopen_and_preserves_close_four(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        from tests.unit.test_mock_automation_account_resume import verify_typed_parallel_protection_resume
        await verify_typed_parallel_protection_resume(self, LeaderCurrentOrderManagementPostgresTests.factory(self))

    async def test_actual_leader_protection_preserves_pending_close_then_resumes_only_its_own_review(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        from tests.unit.test_mock_automation_account_resume import verify_typed_parallel_protection_resume
        await verify_typed_parallel_protection_resume(self, LeaderCurrentOrderManagementPostgresTests.factory(self),
                                                      pending_peer=True)


class AccountResumePostgresTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        self.store = LeaderCurrentOrderManagementPostgresTests.factory(self)()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_explicit_resume_commits_once_after_every_member_recovery_and_keeps_runtime_gate_closed(self):
        from tests.unit.test_mock_automation_account_resume import verify_account_resume_all_members
        verify_account_resume_all_members(self, self.store)

    def test_verified_peer_accepted_order_survives_explicit_group_resume_without_resubmission(self):
        from tests.unit.test_mock_automation_account_resume import verify_account_resume_all_members
        verify_account_resume_all_members(self, self.store, peer_order=True)

    def test_partial_member_recovery_cannot_resume_through_app_or_direct_native_cas(self):
        from tests.unit.test_mock_automation_account_resume import AccountResumeTests
        AccountResumeTests.test_partial_member_recovery_cannot_resume_through_app_or_direct_native_cas(self)

    def test_mixed_risk_cuts_cannot_resume_and_complete_same_cut_can(self):
        from tests.unit.test_mock_automation_account_resume import AccountResumeTests
        AccountResumeTests.test_mixed_risk_cuts_cannot_resume_and_complete_same_cut_can(self)

    def test_new_risk_after_app_validation_is_rejected_in_native_transaction(self):
        from tests.unit.test_mock_automation_account_resume import AccountResumeTests
        AccountResumeTests.test_new_risk_after_app_validation_is_rejected_in_native_transaction(self)

    def test_generic_resolved_notice_cannot_clear_a_members_actual_working_order(self):
        from tests.unit.test_mock_automation_account_resume import AccountResumeTests
        AccountResumeTests.test_generic_resolved_notice_cannot_clear_a_members_actual_working_order(self)

    def test_direct_group_resume_requires_current_owner_and_compatible_current_binding(self):
        from tests.unit.test_mock_automation_account_resume import AccountResumeTests
        AccountResumeTests.test_direct_group_resume_requires_current_owner_and_compatible_current_binding(self)

    def test_group_review_receipts_are_durable_typed_and_never_change_standalone_asdict_fields(self):
        from tests.unit.test_mock_automation_account_resume import AccountResumeTests
        AccountResumeTests.test_group_review_receipts_are_durable_typed_and_never_change_standalone_asdict_fields(self)
