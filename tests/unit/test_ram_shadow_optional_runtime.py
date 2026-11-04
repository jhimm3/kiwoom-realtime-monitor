"""Physical optional-kernel absence and native process restart; legacy module198 stays intact."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from tests.unit import test_candidate_monitor as legacy

_OPTIONAL_RUNTIME_CHILD = r"""
import asyncio, json, sys
from pathlib import Path
source, runtime, database, mode, payload = map(Path, sys.argv[1:6])
mode = str(mode)
sys.path[:0] = [str(source), str(runtime)]
assert sys.flags.no_site == 1
assert all(not ('site-packages' in p and Path(p).resolve() != runtime.resolve()) for p in sys.path)
assert all(not (p.endswith('src') and Path(p).resolve() != source.resolve()) for p in sys.path)
import importlib.util
absent = ('openai', 'anthropic', 'paddle', 'paddleocr', 'numpy')
for name in absent:
    assert importlib.util.find_spec(name) is None, name
from unittest.mock import AsyncMock, patch
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings
from kiwoom_monitor.central_server.database import SQLiteQueryStore
import kiwoom_monitor.central_server.app as app_module
assert Path(app_module.__file__).resolve().is_relative_to(source.resolve())
from datetime import datetime
fixed = datetime.fromisoformat('2026-09-14T09:00:00+09:00')
data = json.loads(payload.read_text(encoding='utf-8'))
store = SQLiteQueryStore(database, now_provider=lambda: fixed)
store.initialize()
if data.get('seed'):
    if data.get('selection') is not None:
        store.upsert_documents('ram_shadow_selection', [{'owner':'global','key':'current','document':data['selection']}])
    store.upsert_documents('ram_shadow_diagnostics', [{'owner':'market_theme','key':'latest','document':{'contexts':{},'results':[],'marker':'native-kept'}}])
    store.upsert_documents('theme_metadata', [{'owner':'default','key':'full','document':data['theme']}])
    store.save_dataset_snapshot('top20_membership','2026-09-14','2026-09-14T09:00:00',{'items':[{'stk_cd':f'{i:06}','stk_nm':f'fixture-{i}'} for i in range(1,21)]})
before = store.load_document('ram_shadow_selection','global','current')
record_before = store.load_document('ram_shadow_diagnostics','market_theme','latest')
settings = CentralServerSettings(database_url='sqlite:///:memory:',access_token='admin-fixture',query_access_token='query-fixture',
    kiwoom_app_key='offline-unused' if data.get('collector', True) else '',kiwoom_secret_key='offline-unused' if data.get('collector', True) else '',
    autonomous_top20_enabled=False,market_event_collection_enabled=False,news_history_jobs_enabled=False,
    news_query_set_enabled=False,news_naver_api_enabled=False,news_naver_market_enabled=False)
with patch('kiwoom_monitor.central_server.app.create_query_store', return_value=store):
    app = create_app(settings)
session=app.state.ram_shadow_selection_session
session._now=lambda:fixed
if app.state.realtime_collector is not None:app.state.realtime_collector._now_provider=lambda:fixed
out={}
async def run():
    import httpx
    from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
    from kiwoom_monitor.central_server.rest_broker import CentralRestBroker
    with patch.object(CentralRealtimeCollector,'start',AsyncMock()),patch.object(CentralRealtimeCollector,'close',AsyncMock()), \
         patch.object(CentralRestBroker,'start',AsyncMock()),patch.object(CentralRestBroker,'close',AsyncMock()), \
         patch('kiwoom_monitor.central_server.diagnostic_trace.recover_interrupted'),patch('kiwoom_monitor.central_server.diagnostic_trace.stop'):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                headers={'Authorization':'Bearer admin-fixture'}
                response=await client.get('/api/v1/research/ram-shadow/selection',headers=headers)
                assert response.status_code==200,response.text
                state=response.json();out['selection']=state
                rank=await client.get('/api/v1/market/snapshots/top20_membership',headers={'Authorization':'Bearer query-fixture'})
                assert rank.status_code==200,rank.text
                assert len(rank.json()['snapshots'][0]['payload']['items'])==20,rank.text
                recorded=await client.get('/api/v1/research/ram-shadow?include_recorded=true',headers=headers)
                assert recorded.status_code==200,recorded.text
                assert recorded.json()['recorded']['document']==record_before['document']
                assert recorded.json()['order_authorized'] is False
                if mode in ('missing','restart-missing','no-collector'):
                    assert state['apply_status']=='RECOVERY_REQUIRED',state
                    assert state['revision']==data['selection']['revision']
                    assert state['enabled']==data['selection']['enabled']
                    assert state['last_error'] and 'INVALID_SAVED' not in state['last_error'],state
                    assert session.runtime is None
                    changed=await client.put('/api/v1/research/ram-shadow/selection',headers=headers,json={'expected_revision':state['revision'],'enabled':True,'theme_ids':['반도체']})
                    assert changed.status_code==(422 if mode=='no-collector' else 503),changed.text
                    assert changed.json()['detail']==('RAM_SHADOW_SELECTION_NOT_SUPPORTED' if mode=='no-collector' else 'RAM_SHADOW_SELECTION_PENDING'),changed.text
                    out['mutation_status']=changed.status_code
                    if data['selection'].get('stock_thesis') is not None:
                        checked=await client.get('/api/v1/research/ram-shadow/source-check?expected_revision=7',headers=headers)
                        assert checked.status_code==409,checked.text
                        out['source_check_status']=checked.status_code
                elif mode=='invalid':
                    assert state['last_error']=='INVALID_SAVED_RAM_SHADOW_SELECTION',state
                    assert state['apply_status']=='RECOVERY_REQUIRED'
                elif mode=='restore':
                    assert state['apply_status']=='ACTIVE' and state['applied_revision']==state['revision'],state
                    assert session.runtime is not None
                    assert session.runtime._now()==fixed
                    assert app.state.realtime_hub.client_count==1
                    out['runtime_id']=session.runtime._session_id
                else:
                    assert state['apply_status']=='ACTIVE' and not state['enabled'],state
                    for name in ('kiwoom_monitor.application.market_theme_shadow','kiwoom_monitor.application.research_factors','kiwoom_monitor.central_server.market_theme_shadow_monitor'):
                        assert name not in sys.modules,name
        if session.runtime is not None:
            assert session.runtime.status['state']=='STOPPED'
        assert app.state.realtime_hub.client_count==0
        if app.state.realtime_collector is not None:assert app.state.realtime_collector._ram_observation_owner is None
asyncio.run(run())
check=SQLiteQueryStore(database,now_provider=lambda:fixed);check.initialize()
assert check.load_document('ram_shadow_selection','global','current')==before
assert check.load_document('ram_shadow_diagnostics','market_theme','latest')==record_before
check.close()
out.update(pid=__import__('os').getpid(),source=str(Path(app_module.__file__).resolve()),mode=mode,absent_packages=list(absent),native_unchanged=True)
print('OPTIONAL_RUNTIME_EVIDENCE='+json.dumps(out,ensure_ascii=False))
"""


class OptionalRuntimeSeamTests(unittest.IsolatedAsyncioTestCase):
    def selection(self, *, enabled=False, comparison=None):
        value={'version':'ram-shadow-selection/v1','revision':7,'enabled':enabled,'theme_ids':['반도체'],
               'leadership_venue':'KRX','recording':False}
        if comparison is not None:value['position_comparison']=comparison
        return value

    def physical_fixture(self, root, *, missing):
        import importlib.util, os, shutil, time
        started=time.monotonic();print('OPTIONAL_FIXTURE=source-start',flush=True)
        source=root/'isolated/src'; runtime=root/'isolated/site-packages'
        shutil.copytree(Path(__file__).resolve().parents[2]/'src',source,copy_function=os.link,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        print('OPTIONAL_FIXTURE=source-done '+str(time.monotonic()-started),flush=True)
        removed=[]
        for name in (('application/market_theme_shadow.py','application/research_factors.py') if missing is True else missing or ()):
            target=source/'kiwoom_monitor'/name;removed.append(name);target.unlink()
        runtime.mkdir()
        for name in ('fastapi','starlette','pydantic','pydantic_core','annotated_types','typing_extensions','typing_inspection','annotated_doc',
                     'httpx','httpcore','anyio','idna','certifi','h11','sniffio','aiohttp','aiohappyeyeballs','aiosignal','attr','attrs','multidict','yarl',
                     'frozenlist','propcache','websockets','tzdata','cryptography','_cffi_backend','cffi','pycparser','psutil','packaging',
                     'charset_normalizer','requests','urllib3','Crypto','dateutil','six'):
            print('OPTIONAL_FIXTURE=package '+name,flush=True)
            spec=importlib.util.find_spec(name)
            if spec is None:continue
            path=Path(spec.origin)
            if spec.submodule_search_locations:
                shutil.copytree(path.parent,runtime/name,copy_function=os.link,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            else:os.link(path,runtime/path.name)
        for name in ('domain/strategy_contract.py','domain/simulation_contract.py'):
            self.assertTrue((source/'kiwoom_monitor'/name).is_file())
        self.assertTrue(all(not (source/'kiwoom_monitor'/name).exists() for name in removed))
        print('OPTIONAL_FIXTURE=runtime-done '+str(time.monotonic()-started),flush=True)
        return source,runtime

    def run_child(self, root, source, runtime, mode, selection, *, seed=True, collector=True):
        import os, subprocess, sys
        from kiwoom_monitor.domain.research_contract import THEME_BACKUP_FORMAT,THEME_BACKUP_VERSION
        fixed=datetime(2026,9,14,9,tzinfo=timezone(timedelta(hours=9)))
        theme=legacy.OwnerContextShadowTests().theme(fixed)
        payload=root/f'payload-{mode}.json'
        payload.write_text(json.dumps({'seed':seed,'selection':selection,'collector':collector,'theme':{'format':THEME_BACKUP_FORMAT,'version':THEME_BACKUP_VERSION,**theme['document']}},ensure_ascii=False),encoding='utf-8')
        environment=dict(os.environ,PYTHONPATH=os.pathsep.join((str(source),str(runtime))),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
        result=subprocess.run([sys.executable,'-S','-c',_OPTIONAL_RUNTIME_CHILD,str(source),str(runtime),str(root/'native.sqlite3'),mode,str(payload)],
            cwd=root,env=environment,capture_output=True,text=True,encoding='utf-8',errors='backslashreplace',timeout=35)
        if os.environ.get('EXECUTION_OPTIONAL_NATIVE'):
            import shutil,uuid
            artifact=Path(os.environ['EXECUTION_OPTIONAL_NATIVE'])/(mode+'-'+uuid.uuid4().hex)
            artifact.mkdir(parents=True)
            for file in root.glob('native.sqlite3*'):shutil.copy2(file,artifact/file.name)
            shutil.copy2(payload,artifact/payload.name)
            (artifact/'child.stdout').write_text(result.stdout,encoding='utf-8')
            (artifact/'child.stderr').write_text(result.stderr,encoding='utf-8')
        self.assertEqual(0,result.returncode,result.stdout+'\n'+result.stderr)
        value=json.loads(next(line.split('=',1)[1] for line in result.stdout.splitlines() if line.startswith('OPTIONAL_RUNTIME_EVIDENCE=')))
        print('OPTIONAL_RUNTIME_CHILD='+json.dumps(value,ensure_ascii=False))
        return value

    async def test_default_and_basic_saved_disabled_do_not_import_optional_compute(self):
        for selection in (None,self.selection(),{**self.selection(),'stock_thesis':None}):
            with self.subTest(saved=selection is not None),tempfile.TemporaryDirectory(prefix='optional-basic-') as folder:
                root=Path(folder);source,runtime=self.physical_fixture(root,missing=False)
                self.run_child(root,source,runtime,'basic',selection)

    async def test_installed_disabled_payload_keeps_evaluation_budget_and_error_order(self):
        import httpx
        from kiwoom_monitor.application import position_target
        fixture=legacy.PositionComparisonSelectionTests()
        with tempfile.TemporaryDirectory(prefix='optional-full-') as folder:
            app,store,now,collector=legacy.RamShadowSelectionTests().app_fixture(folder)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                    headers={'Authorization':'Bearer offline-token'}
                    with patch.object(position_target,'evaluate_position_action_comparison_document',wraps=position_target.evaluate_position_action_comparison_document) as evaluate:
                        response=await client.put(fixture.url,headers=headers,json={'expected_revision':0,'enabled':False,'position_comparison':fixture.document()})
                        self.assertEqual(200,response.status_code,response.text)
                        self.assertEqual(1,evaluate.call_count)
                    before=store.load_document('ram_shadow_selection','global','current')
                    with patch.object(position_target,'evaluate_position_action_comparison_document',return_value={'oversize':'x'*(256*1024)}):
                        response=await client.put(fixture.url,headers=headers,json={'expected_revision':1,'recording':True})
                        self.assertEqual(422,response.status_code,response.text)
                    self.assertEqual(before,store.load_document('ram_shadow_selection','global','current'))
                    response=await client.put(fixture.url,headers=headers,json={'expected_revision':0,'theme_ids':['']})
                    self.assertEqual(409,response.status_code,response.text)
            finally:await legacy.RamShadowSelectionTests().cleanup_app(app,store)

    async def test_physical_absence_preserves_native_payload_and_rejects_before_save(self):
        thesis={'evidence':{'thesis':{'factor_refs':[],'hypothesis_refs':[]},'leadership':{},'run_status':'completed','stored_identity':{}},'thesis_policy_version':'unknown-to-kernel'}
        allocation={'schema':'dynamic-position-allocation-ram/v1','policy':{'context_weights':[]},'symbols':['005930'],
                    'expected_execution_run_id':'fixture','expected_binding_revision':'bad-type','data_path':'nas','maximum_daily_loss_won':1,'cost_model':None}
        cases=[('missing',self.selection(enabled=True)),('missing',self.selection(comparison=legacy.PositionComparisonSelectionTests().document())),
               ('invalid',{'enabled':'invalid'}),('missing',{**self.selection(),'stock_thesis':thesis}),
               ('invalid',{**self.selection(),'stock_thesis':[]}),('invalid',{**self.selection(),'position_comparison':[]}),
               ('invalid',{**self.selection(),'position_allocation':allocation}),('no-collector',self.selection(enabled=True))]
        for index,(mode,selection) in enumerate(cases):
            with self.subTest(index=index),tempfile.TemporaryDirectory(prefix='optional-missing-') as folder:
                root=Path(folder);source,runtime=self.physical_fixture(root,missing=True)
                self.run_child(root,source,runtime,mode,selection,collector=index!=2 and mode!='no-collector')
        with tempfile.TemporaryDirectory(prefix='optional-factor-only-') as folder:
            root=Path(folder);source,runtime=self.physical_fixture(root,missing=('application/research_factors.py',))
            self.run_child(root,source,runtime,'missing',self.selection(enabled=True))

    async def test_native_restart_and_fresh_kernel_restore_recover_same_revision(self):
        import os,shutil
        with tempfile.TemporaryDirectory(prefix='optional-restart-') as folder:
            root=Path(folder);source,runtime=self.physical_fixture(root,missing=True);selection=self.selection(enabled=True)
            first=self.run_child(root,source,runtime,'missing',selection)
            second=self.run_child(root,source,runtime,'restart-missing',selection,seed=False)
            for name in ('application/market_theme_shadow.py','application/research_factors.py'):
                os.link(Path(__file__).resolve().parents[2]/'src/kiwoom_monitor'/name,source/'kiwoom_monitor'/name)
            restored=self.run_child(root,source,runtime,'restore',selection,seed=False)
            self.assertEqual(3,len({row['pid'] for row in (first,second,restored)}))
            self.assertTrue(restored['runtime_id'])
            self.assertEqual((7,7),(restored['selection']['revision'],restored['selection']['applied_revision']))
