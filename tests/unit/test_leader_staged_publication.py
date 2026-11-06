"""Actual staged READY review, policy file, authenticated persistence and readback."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from fastapi.testclient import TestClient


class LeaderStagedPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_exact_staged_review_and_current_policy_reach_ready_api_without_order_authority(self):
        from tests.unit import test_mock_automation_specification as support
        from tests.unit.test_leader_position_steps import staged_config
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationPublicationDialog
        from kiwoom_monitor.application.research_implementation import research_implementation_hash

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with support.mock_publication_contract_app(create_app, root / 'staged-ready.sqlite3') as (app, store, _):
                config = staged_config()
                config = replace(config, contexts=(replace(config.contexts[0],
                    available_at=(support.NOW-timedelta(hours=1)).isoformat(),
                    valid_until=(support.NOW+timedelta(days=1)).isoformat()),))
                inputs = support.mock_specification_inputs(store, leader_config=config,
                    scientific_hash=research_implementation_hash('krx-regular/v1'))
                publication = dict(package=inputs.package.to_dict(), eligibility_policy=inputs.policy.to_dict(),
                                   eligibility_receipt=inputs.receipt.to_dict())
                original = deepcopy(publication)
                dialog = MockAutomationPublicationDialog(publication, inputs.binding, support.PROFILE_ID,
                                                         (inputs.shadow_event,))
                self.addCleanup(dialog.deleteLater)
                terms = dialog._position_terms.text()
                for expected in ('최초 진입 2주', '누적 매수금액 한도 10,000원', '추가 매수 1주 · 1,050원',
                                 '일부 청산 1주 · 1,100원 이상', '정정을 우선', '되살아나지 않습니다'):
                    self.assertIn(expected, terms)
                # A caller mutating its dictionary cannot change terms already under review.
                publication['package']['candidate_spec']['parameters']['execution_contract']['position_steps'][0]['quantity'] = 99
                path = root / 'current-policy.json'
                policy = support.MockUnfilledReplacementDispatchTests.unfilled_execution_policy
                with patch('kiwoom_monitor.presentation.mock_automation_dialog.kst_now',
                           return_value=support.NOW+timedelta(hours=1)):
                    with self.assertRaisesRegex(ValueError, '현재 조건 재판단'):
                        dialog.publication_values()
                    dialog._plan_policy_path.setText(str(path))
                    path.write_text(json.dumps(policy.to_dict()), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, '현재 조건 재판단'):
                        dialog.publication_values()
                    policy = replace(policy, version='mock-unfilled-execution-policy/v2')
                    path.write_text(json.dumps(policy.to_dict()), encoding='utf-8')
                    documents = dialog.publication_values()
                self.assertEqual(LEADER_NATIVE_PLAN_MODE, documents['operating_spec']['execution_plan_mode'])
                self.assertEqual(policy.to_dict(), documents['operating_spec']['unfilled_execution_policy'])
                self.assertEqual(original['package']['package_hash'], documents['operating_spec']['candidate_package_hash'])
                self.assertEqual((), inputs.repository.load_mock_automation_specs(support.ACCOUNT_REF))
                headers = {'Authorization': 'Bearer private-token'}
                with TestClient(app) as client:
                    response = client.post('/api/v1/research/mock-automation-specs', headers=headers, json=documents)
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertEqual(('saved', 'READY', False),
                        (response.json()['status'], response.json()['readiness'], response.json()['orders_started']))
                    retry = client.post('/api/v1/research/mock-automation-specs', headers=headers, json=documents)
                    self.assertEqual(('unchanged', False), (retry.json()['status'], retry.json()['orders_started']))
                    response = client.get('/api/v1/research/mock-automation-specs/'+support.ACCOUNT_REF, headers=headers)
                    self.assertEqual(200, response.status_code, response.text)
                    row, = response.json()['specs']
                    self.assertEqual(('READY', documents['operating_spec']), (row['readiness'], row['spec']))
                saved = inputs.repository.load_mock_automation_candidate_package('strategy-1', original['package']['package_hash'])
                self.assertEqual(original['package'], saved.to_dict())
                self.assertIsNone(store.load_mock_automation_control(support.ACCOUNT_REF))
                from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
                execution = ExecutionRepository(store)
                self.assertEqual(((), True), execution.account_active_intents(support.ACCOUNT_REF))
                self.assertEqual((), execution.account_events('mock', support.ACCOUNT_REF).events)


if __name__ == '__main__':
    unittest.main()
