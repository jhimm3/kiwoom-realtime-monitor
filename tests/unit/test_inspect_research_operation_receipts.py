import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from kiwoom_monitor.presentation.research_dialog import _record_research_operation_owner
from scripts.inspect_research_operation_receipts import inspect_receipts


class ResearchOperationReceiptTests(unittest.TestCase):
    def test_live_owner_and_changed_request_are_distinguished(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stem = 'final_holdout_example'
            files = {key: root / f'{stem}.{suffix}' for key, suffix in (
                ('request', 'json'), ('result', 'result.json'),
                ('cancel', 'cancel'), ('owner', 'owner.json'))}
            files['request'].write_text('{}', encoding='utf-8')
            self.assertTrue(_record_research_operation_owner(
                files, SimpleNamespace(pid=os.getpid()), 'final_holdout'))
            self.assertEqual('running', inspect_receipts(root)[0]['owner_state'])
            files['request'].write_text('{"changed": true}', encoding='utf-8')
            invalid = inspect_receipts(root)[0]
            self.assertEqual('unknown', invalid['owner_state'])
            self.assertIn('hash changed', invalid['error'])

    def test_reused_pid_token_is_not_treated_as_live_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stem = 'development_validation_example'
            files = {key: root / f'{stem}.{suffix}' for key, suffix in (
                ('request', 'json'), ('result', 'result.json'),
                ('cancel', 'cancel'), ('owner', 'owner.json'))}
            files['request'].write_text('{}', encoding='utf-8')
            self.assertTrue(_record_research_operation_owner(
                files, SimpleNamespace(pid=os.getpid()), 'development_validation'))
            receipt = json.loads(files['owner'].read_text(encoding='utf-8'))
            receipt['start_token'] = 'different-process'
            files['owner'].write_text(json.dumps(receipt), encoding='utf-8')
            self.assertEqual('exited', inspect_receipts(root)[0]['owner_state'])


if __name__ == '__main__':
    unittest.main()
