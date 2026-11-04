"""Shared DTO ownership and preservation of an already accepted scientific version."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from kiwoom_monitor.domain import strategy_contract as shared
from kiwoom_monitor.domain import simulation_contract as simulation
from kiwoom_monitor.application import breakout_strategy, leader_breakout
from kiwoom_monitor.application import pullback_reacceleration_strategy, research_execution
from kiwoom_monitor.application import research_families, research_factors
from kiwoom_monitor.application.research_implementation import research_implementation_hash
from kiwoom_monitor.application.mock_automation_candidate import evaluate_candidate_eligibility
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
from tests.unit.test_mock_automation_candidate import _package, _policy, _hash, ACCOUNT_REF, NOW


class StrategyDataContractTests(unittest.TestCase):
    def test_legacy_paths_reference_the_same_contract_class_objects(self):
        for module, names in (
            (breakout_strategy, ('BreakoutStrategyConfig', 'StrategyDecision', 'StrategyRuntimeConfig')),
            (leader_breakout, ('LeaderBreakoutContext', 'LeaderBreakoutPolicy',
                'LeaderBreakoutExecutionContract', 'LeaderBreakoutConfig')),
            (pullback_reacceleration_strategy, ('PullbackReaccelerationConfig',)),
            (research_factors, ('RollingHighBreakoutParameters', 'RankPersistenceParameters',
                'PullbackReaccelerationParameters')),
        ):
            for name in names:
                with self.subTest(module=module.__name__, name=name):
                    self.assertIs(getattr(shared, name), getattr(module, name))
        self.assertIs(simulation.SimulationCostModel, research_execution.SimulationCostModel)
        self.assertIs(simulation.SimulationExecutionConfig, research_execution.SimulationExecutionConfig)

    def test_full_registry_attaches_original_evaluators_to_common_contracts(self):
        expected = {
            'krx_bar_close_breakout/v1': breakout_strategy.evaluate_breakout_bar,
            'krx_pullback_reacceleration/v1': pullback_reacceleration_strategy.evaluate_pullback_reacceleration_bar,
            'leader_breakout/v1': leader_breakout.evaluate_leader_breakout_bar,
        }
        self.assertEqual(set(expected), set(shared.FAMILY_CONTRACTS))
        for key, evaluator in expected.items():
            common = shared.get_research_family(key)
            full = research_families.get_research_family(key)
            self.assertIs(evaluator, full.evaluate_bar)
            self.assertIs(common.config_type, full.config_type)
            self.assertIs(common.contract, full.contract)
            self.assertEqual(common.factor_ids, full.factor_ids)
            self.assertEqual(common.searchable_parameters, full.searchable_parameters)
            self.assertFalse(hasattr(common, 'evaluate_bar'))

    def test_reading_accepted_package_keeps_original_scientific_version(self):
        # This is an immutable historical fixture version, never a current-hash override.
        old_hash = '7eec843d703d0dc56adcbd3930c8f8e6f3c4d573529630f653d45f9fa01c8c96'
        self.assertNotEqual(old_hash, research_implementation_hash('krx-regular/v1'))
        original = _package()
        spec = {**original.candidate_spec, 'implementation_hash': old_hash}
        package = replace(original, candidate_spec=spec, candidate_spec_hash=_hash(spec),
                          scientific_implementation_hash=old_hash)
        policy = _policy(package)
        receipt = evaluate_candidate_eligibility(package, policy, account_ref=ACCOUNT_REF)
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteQueryStore(Path(directory) / 'accepted.sqlite3')
            try:
                store.initialize()
                store.register_account_identity({'broker':'kiwoom','environment':'mock',
                    'identity_fingerprint':'c'*64,'account_ref':ACCOUNT_REF,'created_at':NOW.isoformat()})
                binding = store.append_account_binding({'credential_profile_id':'stored-original',
                    'broker':'kiwoom','environment':'mock','account_ref':ACCOUNT_REF,
                    'verified_at':NOW.isoformat(),'verification_method':'ka00001'})
                repository = ForwardEvaluationRepository(store)
                repository.publish_mock_automation_candidate(package, policy, receipt,
                    credential_profile_id='stored-original', expected_binding_revision=binding['binding_revision'])
                loaded = repository.load_mock_automation_candidate_publications(ACCOUNT_REF)
                self.assertEqual(((package, policy, receipt),), loaded)
                self.assertEqual(package.to_dict(), loaded[0][0].to_dict())
                self.assertEqual(old_hash, loaded[0][0].scientific_implementation_hash)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
