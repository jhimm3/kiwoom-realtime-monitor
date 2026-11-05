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
