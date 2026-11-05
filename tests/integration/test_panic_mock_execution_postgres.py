"""EX-PANIC final candidates and Shadow lineage on the actual native PostgreSQL store."""
import os
import unittest


class PanicFinalCandidatePostgresTests(unittest.TestCase):
    def test_panic_final_candidate_matches_both_native_variants_without_order_authority(self):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        from tests.unit.test_panic_mock_execution import PanicFinalPublicationTests
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url: raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        for variant in ('P0', 'P1'):
            with self.subTest(variant=variant), isolated_observation_schema(url) as scoped:
                case = PanicFinalPublicationTests(); self.addCleanup(case.doCleanups)
                case.verify(variant, native_factory=lambda path, now: PostgresQueryStore(scoped, now_provider=now))


class PanicNativePlanPostgresTests(unittest.TestCase):
    def verify(self, callback, **kwargs):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url: raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        with isolated_observation_schema(url) as scoped:
            case = unittest.TestCase()
            try:
                callback(case, native_factory=lambda path, now: PostgresQueryStore(scoped, now_provider=now), **kwargs)
            finally:
                case.doCleanups()

    def test_both_variants_use_current_news_prices_and_actual_detailed_owned_quantity(self):
        from tests.unit.test_panic_mock_execution import verify_panic_native_cycle
        for variant in ('P0', 'P1'):
            with self.subTest(variant=variant): self.verify(verify_panic_native_cycle, variant=variant)

    def test_native_claim_rechecks_current_settings_original_setup_and_actual_owned_exit(self):
        from tests.unit.test_panic_mock_execution import verify_panic_native_claim_fences
        self.verify(verify_panic_native_claim_fences)

    def test_explicit_stopped_policy_and_closed_session_next_day_reopen(self):
        from tests.unit.test_panic_mock_execution import verify_panic_stopped_management
        for stopped in ('continue_original_plan/v1', 'reconcile_only/v1'):
            with self.subTest(stopped=stopped): self.verify(verify_panic_stopped_management, stopped=stopped)


class PanicCurrentOrderPostgresTests(unittest.IsolatedAsyncioTestCase):
    def factory(self):
        from tests.integration.test_observation_bootstrap_postgres import isolated_observation_schema, PostgresQueryStore
        url = os.environ.get('KIWOOM_DIAGNOSTIC_TEST_DATABASE_URL', '')
        if not url: raise unittest.SkipTest('dedicated diagnostic PostgreSQL URL is required')
        scoped = self.enterContext(isolated_observation_schema(url))
        return lambda path, now: PostgresQueryStore(scoped, now_provider=now)

    async def test_both_variants_reassess_then_amend_and_cancel_failed_remainder_with_late_fill(self):
        from tests.unit.test_panic_mock_execution import verify_panic_current_buy
        for variant in ('P0', 'P1'):
            with self.subTest(variant=variant):
                await verify_panic_current_buy(self, self.factory(), variant=variant, lost_ack=variant == 'P1')

    async def test_native_transaction_rechecks_current_context_before_amendment(self):
        from tests.unit.test_panic_mock_execution import verify_panic_current_native_recheck
        await verify_panic_current_native_recheck(self, self.factory())

    async def test_current_sell_amends_original_owned_remainder_after_stop_and_preserves_journal(self):
        from tests.unit.test_panic_mock_execution import verify_panic_current_sell
        await verify_panic_current_sell(self, self.factory())

    async def test_p0_first_failure_survives_rebound_unknown_cancel_late_fill_and_reopen(self):
        from tests.unit.test_panic_mock_execution import verify_panic_runner
        await verify_panic_runner(self, self.factory())

    async def test_p1_first_rebound_survives_pullback_closed_session_and_next_day_partial_exit(self):
        from tests.unit.test_panic_mock_execution import verify_panic_runner
        await verify_panic_runner(self, self.factory(), variant='P1', kind='rebound')

    async def test_zero_fill_terminal_cancellation_late_fill_restores_original_plan(self):
        from tests.unit.test_panic_mock_execution import verify_panic_runner
        await verify_panic_runner(self, self.factory(), zero_fill=True)
