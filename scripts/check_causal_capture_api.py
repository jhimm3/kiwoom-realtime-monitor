"""No-network packaging gate; public HTTP wiring is tested separately on PC."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.diagnostic_workloads import _set_tool

with tempfile.TemporaryDirectory() as temp:
    control = Path(temp)/'diagnostic-workloads.json'
    with patch.dict(os.environ, {'KIWOOM_DIAGNOSTIC_WORKLOAD_PATH':str(control)}):
        app = create_app(CentralServerSettings(f'sqlite:///{Path(temp)/"gate.sqlite3"}', 'fixture-token'))
        capability = next(r for r in app.routes if r.path=='/api/v1/diagnostics/capabilities')
        metadata = asyncio.run(capability.endpoint())['trace_input_capture']
        if metadata['schema_version'] != 3 or metadata['options'] != {
                'store_inputs':False, 'collector_inputs':False, 'top20_inputs':False}:
            raise RuntimeError('causal_capture_capabilities_mismatch')
        profiles = metadata.get('store_input_profiles')
        if profiles != {'stock-catalog-documents/v1': {
                'method': 'replace_documents', 'collection': 'stock_catalog',
                'maximum_copy_bytes': 16 * 1024 * 1024}}:
            raise RuntimeError('catalog_profile_capability_mismatch')
        route = next(r for r in app.routes if r.path=='/api/v1/diagnostics/trace' and 'POST' in r.methods)
        model = route.dependant.body_params[0].field_info.annotation
        session = _set_tool(control, True, 4000)['diagnostic_tool']['session_id']
        deadline = time.time()+7200
        body = model(seconds=3900, expected_session=session, store_inputs=True,
                     collector_inputs=True, top20_inputs=True, persist_at=deadline)
        with patch('kiwoom_monitor.central_server.diagnostic_trace.status', return_value={'state':'off'}), \
             patch('kiwoom_monitor.central_server.diagnostic_trace.start', return_value={'state':'running'}) as start:
            asyncio.run(route.endpoint(body))
            start.assert_called_once_with(seconds=3900, store_inputs=True, collector_inputs=True,
                                          top20_inputs=True, persist_at=deadline)
        _set_tool(control, False)
print(json.dumps({'state':'passed','schema_version':3,'catalog_profile':'stock-catalog-documents/v1',
                  'all_flags_forwarded':True,
                  'private_controls_only':True,'network_access':False,
                  'operational_database_access':False,'private_sqlite_fixture':True}))
