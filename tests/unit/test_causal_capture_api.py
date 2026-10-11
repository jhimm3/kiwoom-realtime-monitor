"""Capture candidate API wiring, without lifespan/network or live controls."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool


class CausalCaptureApiTests(unittest.TestCase):
    def test_three_causal_flags_and_deferred_deadline_reach_same_recorder(self):
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp) / 'diagnostic-workloads.json'
            settings = CentralServerSettings(f'sqlite:///{Path(temp)/"store.sqlite3"}', 'fixture-token')
            with patch.dict(os.environ, {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH': str(control)}):
                client = TestClient(create_app(settings))
                self.addCleanup(client.close)
                session = _set_tool(control, True, 4000)['diagnostic_tool']['session_id']
                deadline = time.time()+7200
                with patch('kiwoom_monitor.central_server.diagnostic_trace.status', return_value={'state':'off'}), \
                     patch('kiwoom_monitor.central_server.diagnostic_trace.start', return_value={'state':'running'}) as start:
                    response = client.post('/api/v1/diagnostics/trace', headers={'Authorization':'Bearer fixture-token'},
                        json={'seconds':3900, 'expected_session':session, 'store_inputs':True,
                              'collector_inputs':True, 'top20_inputs':True, 'persist_at':deadline})
                    self.assertEqual(200, response.status_code, response.text)
                    start.assert_called_once_with(seconds=3900, store_inputs=True, collector_inputs=True,
                                                  top20_inputs=True, large_inputs=False,
                                                  account_inputs=False, account_context_store=None,
                                                  persist_at=deadline)
