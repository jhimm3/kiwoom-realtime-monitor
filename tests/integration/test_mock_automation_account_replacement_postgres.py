"""The same explicit replacement, late FILL and native reopen scenarios on PostgreSQL."""
import unittest


class AccountReplacementPostgresTests(unittest.TestCase):
    def setUp(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        self.store = LeaderCurrentOrderManagementPostgresTests.factory(self)()
        self.store.initialize()
        self.addCleanup(self.store.close)

    def test_explicit_native_cas_preserves_original_admissions_and_is_idempotent(self):
        from tests.unit.test_mock_automation_account_replacement import verify_native_replacement
        verify_native_replacement(self, self.store)

    def test_replacing_a_stopped_group_preserves_stop_and_never_resumes_entries(self):
        from tests.unit.test_mock_automation_account_replacement import verify_native_replacement
        data, _, _, result = verify_native_replacement(self, self.store, stopped=True)
        self.assertEqual('STOPPED', result.desired_state.value)
        self.assertFalse(data.owner.runtime._new_orders_enabled)

    def test_native_cas_rejects_partial_prospective_recovery_and_pause_state_change(self):
        from tests.unit.test_mock_automation_account_replacement import AccountReplacementTests
        AccountReplacementTests.test_native_cas_rejects_partial_prospective_recovery_and_pause_state_change(self)


class ActualAccountReplacementPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_old_plans_and_late_fill_survive_explicit_replacement_response_loss_and_db_reopen(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        from tests.unit.test_mock_automation_account_replacement import verify_actual_group_replacement
        await verify_actual_group_replacement(self, LeaderCurrentOrderManagementPostgresTests.factory(self),
                                             lose_commit_response=True)

    async def test_failed_new_member_prepare_keeps_original_episode_and_restores_old_recovery(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        from tests.unit.test_mock_automation_account_replacement import verify_actual_group_replacement
        await verify_actual_group_replacement(self, LeaderCurrentOrderManagementPostgresTests.factory(self),
                                             fail_prepare=True)
