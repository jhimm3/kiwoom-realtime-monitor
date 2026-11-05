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
