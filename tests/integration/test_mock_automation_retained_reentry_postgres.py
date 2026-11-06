"""Genuine v3 entry beside a retained standalone native peer on PostgreSQL."""
import unittest


class RetainedReentryPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_genuine_v3_first_entry_treats_only_verified_standalone_admission_as_other_member(self):
        from tests.integration.test_leader_order_management_postgres import LeaderCurrentOrderManagementPostgresTests
        from tests.unit.test_mock_automation_retained_reentry import verify_genuine_v3_entry_with_standalone_peer
        await verify_genuine_v3_entry_with_standalone_peer(self, LeaderCurrentOrderManagementPostgresTests.factory(self))
