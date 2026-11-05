"""Result-only authority and durable, unverified research-report acceptance."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from contextlib import ExitStack, closing, contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI, Request

from kiwoom_monitor.central_server.config import CentralServerSettings, ProducerGrant
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.research_read_routes import create_research_read_router
from kiwoom_monitor.domain.research_contract import (
    ReportedResearchResult, ResearchResultConflict, REPORTED_RESEARCH_RESULT_COLLECTION, encode_reported_result_owner,
)
from kiwoom_monitor.domain.time_basis import KST
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, CentralContentUnavailableError
from tests.unit.test_query_authority import wire_server, query_app, ADMIN, QUERY


NOW = datetime(2026, 10, 24, 13, 0, tzinfo=KST)
RESULT_TOKEN, OTHER_TOKEN = "result-only-producer-fixture", "result-only-other-fixture"


def result_document(result_id="run-fixture"):
    return {"version": 1, "kind": "research-run-report", "result_id": result_id,
            "produced_at": "2026-10-24T12:30:00+09:00", "payload": {
                "format": "kiwoom-monitor-research-run", "version": 3, "run_id": result_id,
                "input_dataset_id": "frozen-fixture", "input_revision_ids_hash": "original-input-hash",
                "spec": {"code_hash": "reported-not-trusted", "execution": {"unit": "won"}},
                "logical_result_hash": "reported-logical-hash", "performance": {"status": "UNKNOWN"},
                "research_report": {"report_id": "original-report", "run_id": result_id, "status": "PARTIAL",
                    "reasons": ["missing-source"], "data_quality": {"status": "UNKNOWN"},
                    "limitations": ["not verified"], "fold_reports": [{"net_pnl_won": -10, "status": "FAILED"}]}}}


def result_settings(database="sqlite:///:memory:", grants=None):
    return CentralServerSettings(database, ADMIN, query_access_token=QUERY,
        research_result_grants=(ProducerGrant("processor_a", RESULT_TOKEN),
                                ProducerGrant("processor_b", OTHER_TOKEN)) if grants is None else grants,
        news_naver_api_enabled=False, news_naver_market_enabled=False,
        news_query_set_enabled=False, autonomous_top20_enabled=False)


class ReportedResearchResultTests(unittest.TestCase):
    def test_credentials_are_opt_in_unambiguous_and_secret_free(self):
        from kiwoom_monitor.central_server.app import _token_matches
        self.assertTrue(_token_matches("result-한글-fixture", "result-한글-fixture"))
        self.assertFalse(_token_matches("result-다름-fixture", "result-한글-fixture"))
        self.assertEqual((), CentralServerSettings("sqlite:///:memory:", ADMIN).research_result_grants)
        self.assertNotIn(RESULT_TOKEN, repr(result_settings()))
        self.assertNotIn(RESULT_TOKEN, repr(ProducerGrant("processor_a", RESULT_TOKEN)))
        for grants in ((ProducerGrant("a", ADMIN),), (ProducerGrant("a", QUERY),),
                       (ProducerGrant("a", ""),), (ProducerGrant("a", RESULT_TOKEN), ProducerGrant("b", RESULT_TOKEN)),
                       (ProducerGrant("a", RESULT_TOKEN), ProducerGrant("a", OTHER_TOKEN))):
            with self.subTest(grants=repr(grants)), self.assertRaises(ValueError):
                result_settings(grants=grants)
        encoded = json.dumps([{"owner": "processor_a", "token": RESULT_TOKEN}])
        with patch.dict(os.environ, {"MONITOR_SERVER_ACCESS_TOKEN": ADMIN,
                        "MONITOR_SERVER_RESEARCH_RESULT_GRANTS_JSON": encoded}, clear=True):
            self.assertEqual((ProducerGrant("processor_a", RESULT_TOKEN),),
                             CentralServerSettings.from_environment().research_result_grants)
        for extra in ("owner", "accepted_at", "receipt", "scientific_verified", "order_authorized"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                ReportedResearchResult.from_document(dict(result_document(), **{extra: "forged"}))

    def test_native_sqlite_first_receipt_conflict_restart_and_one_command(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "results.sqlite3"
            store = SQLiteQueryStore(path, now_provider=lambda: NOW)
            store.initialize()
            statements = []
            original = store._connection
            from contextlib import contextmanager
            @contextmanager
            def traced():
                with original() as connection:
                    connection.set_trace_callback(statements.append)
                    yield connection
            with patch.object(store, "_connection", traced):
                accepted = store.accept_research_result("processor_a", result_document())
            self.assertEqual(1, sum(s.startswith("INSERT INTO central_documents") for s in statements))
            self.assertEqual(1, sum(s.startswith("SELECT document_json") for s in statements))
            self.assertEqual(1, sum(s == "BEGIN IMMEDIATE" for s in statements))
            self.assertEqual(1, sum(s == "COMMIT" for s in statements))
            self.assertEqual(result_document(), accepted["result"])
            self.assertEqual(NOW.isoformat(), accepted["receipt"]["accepted_at"])
            self.assertFalse(accepted["receipt"]["scientific_verified"])
            self.assertFalse(accepted["receipt"]["order_authorized"])
            self.assertEqual(accepted, store.accept_research_result("processor_a", result_document()))
            for changed_value in (True, 1.0):
                original_value = result_document("type-sensitive")
                original_value["payload"]["number"] = 1
                store.accept_research_result("processor_a", original_value)
                changed = deepcopy(original_value); changed["payload"]["number"] = changed_value
                with self.assertRaises(ResearchResultConflict):
                    store.accept_research_result("processor_a", changed)
            changed = result_document(); changed["payload"]["research_report"]["status"] = "COMPLETE"
            with self.assertRaises(ResearchResultConflict):
                store.accept_research_result("processor_a", changed)
            for method, args in ((store.upsert_documents, (REPORTED_RESEARCH_RESULT_COLLECTION, [])),
                                 (store.replace_documents, (REPORTED_RESEARCH_RESULT_COLLECTION, [])),
                                 (store.insert_immutable_document, (REPORTED_RESEARCH_RESULT_COLLECTION, "processor_a", "run-fixture", {}))):
                with self.assertRaises(ValueError): method(*args)
            store.close()
            restarted = SQLiteQueryStore(path, now_provider=lambda: NOW.replace(hour=14)); restarted.initialize()
            try:
                self.assertEqual(accepted, restarted.accept_research_result("processor_a", result_document()))
                self.assertIsNone(restarted.load_document(REPORTED_RESEARCH_RESULT_COLLECTION, "processor_b", "run-fixture"))
            finally: restarted.close()

    def test_wire_result_token_has_only_own_report_routes_and_old_tokens_keep_authority(self):
        app = query_app(result_settings())
        store = app.state.query_authority_store
        denied = [("GET", "/api/v1/capabilities", None), ("GET", "/api/v1/settings/operations", None),
                  ("GET", "/api/v1/research/observations", None), ("GET", "/api/v1/research/ram-shadow", None),
                  ("POST", "/api/v1/content/theme_profile", {"documents": []}),
                  ("POST", "/api/v1/research/mock-automation-candidates", {}),
                  ("POST", "/api/v1/mock-automation/start", {}), ("GET", "/api/v1/market/rankings", None)]
        with wire_server(app) as (base, _), httpx.Client(base_url=base, timeout=5) as wire, ExitStack() as stack:
            spies = []
            def routes(router):
                for route in router.routes:
                    if hasattr(route, "original_router"):
                        yield from routes(route.original_router)
                    else:
                        yield route
            for route in routes(app):
                if getattr(route, "path", "") in {path for _, path, _ in denied}:
                    spy = Mock(side_effect=AssertionError("denied handler executed"))
                    stack.enter_context(patch.object(route.dependant, "call", spy)); spies.append(spy)
            for method, path, body in denied:
                response = wire.request(method, path, json=body, headers={"Authorization": "Bearer " + RESULT_TOKEN})
                self.assertIn(response.status_code, (401, 403, 404), (path, response.text))
            self.assertGreaterEqual(len(spies), 7)
            self.assertTrue(all(spy.call_count == 0 for spy in spies))
            stack.close()
            for token in (ADMIN, QUERY, "", "wrong"):
                with patch.object(store, "accept_research_result", side_effect=AssertionError("unauthorized native write")) as write:
                    response = wire.post("/api/v1/research/reported-results", json=result_document(),
                                         headers={"Authorization": "Bearer " + token} if token else {})
                    self.assertEqual(401, response.status_code); write.assert_not_called()
            for token in (ADMIN, QUERY):
                self.assertEqual(200, wire.get("/api/v1/capabilities", headers={"Authorization": "Bearer " + token}).status_code)
            producer = CentralContentClient(base, RESULT_TOKEN)
            accepted = producer.accept_research_result(result_document(), expected_owner="processor_a")
            self.assertEqual(accepted, producer.load_research_result("run-fixture", expected_owner="processor_a"))
            other = CentralContentClient(base, OTHER_TOKEN)
            self.assertEqual(404, wire.get("/api/v1/research/reported-results/run-fixture",
                headers={"Authorization": "Bearer " + OTHER_TOKEN, "X-Kiwoom-Expected-Result-Owner": encode_reported_result_owner("processor_b")}).status_code)
            second = other.accept_research_result(result_document(), expected_owner="processor_b")
            self.assertEqual("processor_b", second["receipt"]["owner"])
            self.assertEqual("processor_a", accepted["receipt"]["owner"])
            self.assertEqual(400, wire.post("/api/v1/research/reported-results", json=dict(result_document(), owner="processor_b"),
                headers={"Authorization": "Bearer " + RESULT_TOKEN, "X-Kiwoom-Expected-Result-Owner": encode_reported_result_owner("processor_a")}).status_code)
            bounded = result_document("wire-bounded")
            bounded["payload"]["measurements"] = [0] * 100_000
            source = ReportedResearchResult.from_document(bounded)
            self.assertLess(len(json.dumps(source.document, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False).encode("utf-8")), 256 * 1024)
            self.assertGreater(len(json.dumps(source.document, ensure_ascii=False).encode("utf-8")), 256 * 1024)
            receipt = producer.accept_research_result(source.document, expected_owner="processor_a")
            self.assertEqual(source.content_hash, receipt["receipt"]["content_hash"])
            self.assertEqual(receipt, producer.load_research_result(source.result_id, expected_owner="processor_a"))
        with wire_server(query_app(result_settings(grants=()))) as (base, _):
            self.assertEqual(401, httpx.post(base + "/api/v1/research/reported-results", json=result_document(),
                headers={"Authorization": "Bearer " + RESULT_TOKEN, "X-Kiwoom-Expected-Result-Owner": encode_reported_result_owner("processor_a")}).status_code)

    def test_wire_commit_ack_loss_and_restart_reuse_exact_first_receipt(self):
        with TemporaryDirectory() as directory:
            settings = result_settings("sqlite:///" + str(Path(directory) / "reports.sqlite3"))
            app = query_app(settings)
            with wire_server(app) as (base, _):
                def lose_ack(request, **kwargs):
                    with urlopen(request, **kwargs) as response:
                        response.read()
                    raise URLError("fixture committed ACK lost")
                client = CentralContentClient(base, RESULT_TOKEN, opener=lose_ack)
                with self.assertRaises(CentralContentUnavailableError): client.accept_research_result(result_document(), expected_owner="processor_a")
                reader = CentralContentClient(base, RESULT_TOKEN)
                original = reader.load_research_result("run-fixture", expected_owner="processor_a")
                self.assertEqual(original, reader.accept_research_result(result_document(), expected_owner="processor_a"))
            with wire_server(query_app(settings)) as (base, _):
                client = CentralContentClient(base, RESULT_TOKEN)
                self.assertEqual(original, client.load_research_result("run-fixture", expected_owner="processor_a"))
                self.assertEqual(original, client.accept_research_result(result_document(), expected_owner="processor_a"))
                changed = result_document(); changed["payload"]["performance"]["status"] = "FAILED"
                from kiwoom_monitor.infrastructure.central_content_client import CentralContentHttpError
                with self.assertRaises(CentralContentHttpError) as error: client.accept_research_result(changed, expected_owner="processor_a")
                self.assertEqual(409, error.exception.status_code)

    def test_request_cancellation_joins_native_transaction_before_returning(self):
        async def exercise(native_error, cancelled):
            store = SQLiteQueryStore(Path(":memory:"), now_provider=lambda: NOW); store.initialize()
            entered, release, finished = threading.Event(), threading.Event(), threading.Event()
            original = store.accept_research_result
            def blocked(owner, document):
                entered.set()
                if not release.wait(5): raise AssertionError("native test release was lost")
                try:
                    if native_error is not None:
                        raise native_error
                    return original(owner, document)
                finally: finished.set()
            router = create_research_read_router(store, lambda: None, lambda: None, result_authorize=lambda: "processor_a")
            endpoint = next(route.endpoint for route in router.routes if getattr(route, "path", "") == "/api/v1/research/reported-results")
            async def receive(): return {"type": "http.request", "body": json.dumps(result_document()).encode(), "more_body": False}
            request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
            try:
                with patch.object(store, "accept_research_result", blocked):
                    task = asyncio.create_task(endpoint(request, "processor_a"))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    if cancelled:
                        task.cancel(); await asyncio.sleep(0); task.cancel(); await asyncio.sleep(0)
                    self.assertFalse(task.done()); self.assertFalse(finished.is_set())
                    release.set()
                    if cancelled:
                        with self.assertRaises(asyncio.CancelledError) as raised: await task
                        if native_error is not None:
                            self.assertIs(native_error, raised.exception.__cause__)
                    elif isinstance(native_error, ResearchResultConflict):
                        from fastapi import HTTPException
                        with self.assertRaises(HTTPException) as raised: await task
                        self.assertEqual(409, raised.exception.status_code)
                        self.assertIs(native_error, raised.exception.__cause__)
                    else:
                        with self.assertRaises(type(native_error)) as raised: await task
                        self.assertIs(native_error, raised.exception)
                self.assertTrue(finished.is_set())
                stored = store.load_document(REPORTED_RESEARCH_RESULT_COLLECTION, "processor_a", "run-fixture")
                if native_error is None:
                    self.assertIsNotNone(stored)
                else:
                    self.assertIsNone(stored)
            finally: release.set(); store.close()
        for error, cancelled in ((None, True),
                (ResearchResultConflict("fixture conflict"), True),
                (sqlite3.OperationalError("fixture native DB failure"), True),
                (ResearchResultConflict("fixture conflict"), False),
                (sqlite3.OperationalError("fixture native DB failure"), False)):
            with self.subTest(native_error=type(error).__name__, cancelled=cancelled):
                asyncio.run(exercise(error, cancelled))

    def test_fresh_server_physically_missing_producer_and_optional_packages(self):
        source = Path(__file__).resolve().parents[2] / "src"
        excluded = ("research_run.py", "research_process.py", "research_result_delivery.py", "application/research_implementation.py",
                    "application/research_execution.py", "application/research_evaluation.py", "application/position_target.py",
                    "application/account_risk_research.py", "application/daily_decline.py", "application/micro_execution_evidence.py",
                    "infrastructure/persistence/research_repository.py", "infrastructure/research_data_source.py")
        with TemporaryDirectory() as directory:
            target = Path(directory) / "src"
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for relative in excluded: (target / "kiwoom_monitor" / relative).unlink()
            script = '''
import importlib.abc, json, sys
from pathlib import Path
class AbsentOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6','paddle','paddleocr','google','openai','anthropic'}:
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, AbsentOptional())
from fastapi.testclient import TestClient
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings, ProducerGrant
from kiwoom_monitor.domain.research_contract import REPORTED_RESEARCH_RESULT_COLLECTION, encode_reported_result_owner
root = Path.cwd() / 'src'
import kiwoom_monitor
assert Path(kiwoom_monitor.__file__).resolve().is_relative_to(root)
settings = CentralServerSettings('sqlite:///:memory:', 'admin', research_result_grants=(ProducerGrant('producer', 'result'),), news_naver_api_enabled=False, news_naver_market_enabled=False, news_query_set_enabled=False, autonomous_top20_enabled=False)
legacy = json.loads(sys.argv[1])
evidence = {**legacy, 'result_id': 'run-evidence-fixture', 'payload': {
    'format': 'kiwoom-monitor-evidence-experiment', 'version': 1, 'run_id': 'run-evidence-fixture',
    'spec': {'family': 'account_risk_study/v1', 'mode': 'account_risk_experiment', 'source_kind': 'synthetic',
             'code_hash': 'a'*64, 'dataset_sha256': 'b'*64, 'parameters': {}},
    'logical_result_hash': 'c'*64, 'result': {'version': 'account-risk-result/v1'}, 'order_authorized': False,
}}
headers = {'Authorization':'Bearer result', 'X-Kiwoom-Expected-Result-Owner':encode_reported_result_owner('producer')}
with TestClient(create_app(settings)) as client:
    for document in (legacy, evidence):
        accepted = client.post('/api/v1/research/reported-results', json=document, headers=headers)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()['receipt']['scientific_verified'] is False
        assert accepted.json()['receipt']['order_authorized'] is False
        assert accepted.json()['result'] == document
        assert client.get('/api/v1/research/reported-results/' + document['result_id'], headers=headers).json() == accepted.json()
for module in ('kiwoom_monitor.research_run','kiwoom_monitor.application.research_evaluation',
               'kiwoom_monitor.application.position_target','kiwoom_monitor.application.account_risk_research',
               'kiwoom_monitor.application.daily_decline','kiwoom_monitor.application.micro_execution_evidence',
               'PySide6','paddle','openai'):
    assert module not in sys.modules, module
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
from kiwoom_monitor.application.feedback_strategy_revision import build_feedback_revalidation_spec, dispatch_feedback_revalidation
assert 'kiwoom_monitor.application.research_search' not in sys.modules
assert 'kiwoom_monitor.application.research_queue' not in sys.modules
store = SQLiteQueryStore(Path(':memory:'))
store.initialize()
try:
    assert ForwardEvaluationRepository(store).load_feedback_strategy_versions('fixture') == ()
finally:
    store.close()
native_calls = []
class ForbiddenNative:
    def __getattr__(self, name):
        native_calls.append(name)
        raise AssertionError('missing producer touched native owner: ' + name)
for operation in (lambda: build_feedback_revalidation_spec(None, None),
                  lambda: dispatch_feedback_revalidation(ForbiddenNative(), ForbiddenNative(), None,
                      campaign_id='fixture', template_job_id='fixture', adoption_reason='fixture')):
    try:
        operation()
    except ModuleNotFoundError as error:
        assert error.name == 'kiwoom_monitor.application.research_evaluation', error.name
    else:
        raise AssertionError('absent producer was accepted')
assert native_calls == [], native_calls
print('producer-absent-native-result-pass')
'''
            completed = subprocess.run([sys.executable, "-c", script, json.dumps(result_document())], cwd=directory,
                env=dict(os.environ, PYTHONPATH=str(target), PYTHONDONTWRITEBYTECODE="1"), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, (completed.stdout or '') + (completed.stderr or ''))
            self.assertIn("producer-absent-native-result-pass", completed.stdout)

    def test_strict_selected_candidate_receiver_without_engine_uses_process_wire_and_native_receipt(self):
        from dataclasses import replace
        from kiwoom_monitor.application.mock_automation_candidate import evaluate_candidate_eligibility
        from kiwoom_monitor.infrastructure.central_content_client import CentralContentHttpError
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
        from tests.unit.test_mock_automation_candidate import _package, _policy, _hash, ACCOUNT_REF
        from tests.unit.test_nas_source_runtime import selected_research_artifact_fixture

        # These are actual calculation/workflow implementations, not every module
        # with "research" in its filename. Common frame/session/split validation stays.
        excluded = (
            "research_run.py", "research_process.py", "research_candidate.py", "research_result_delivery.py",
            "application/research_implementation.py", "application/research_execution.py",
            "application/research_evaluation.py", "application/research_families.py",
            "application/research_factors.py", "application/research_search.py", "application/research_queue.py",
            "application/research_hypotheses.py", "application/research_resources.py",
            "application/position_target.py", "application/market_research_features.py",
            "application/market_theme_shadow.py", "application/theme_leadership.py",
            "application/breakout_strategy.py", "application/leader_breakout.py",
            "application/pullback_reacceleration_strategy.py", "infrastructure/research_storage.py",
            "infrastructure/research_data_source.py", "infrastructure/persistence/research_repository.py",
        )
        shared = (
            "application/market_session_schedule.py", "application/research_splits.py",
            "application/research_replay.py", "application/mock_automation_candidate.py",
            "domain/research_contract.py", "domain/strategy_contract.py", "domain/simulation_contract.py",
            "domain/market_data_contract.py", "domain/order_contract.py", "domain/execution_activation.py",
        )
        collections = (
            ForwardEvaluationRepository.MOCK_AUTOMATION_ELIGIBILITY_POLICY_COLLECTION,
            ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION,
            ForwardEvaluationRepository.MOCK_AUTOMATION_ELIGIBILITY_RECEIPT_COLLECTION,
        )
        script = '''
import importlib.abc, json, os, sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
root, mode = Path(sys.argv[1]), sys.argv[2]
sys.dont_write_bytecode = True
sys.path[:0] = [str(root / 'src'), str(root)]
class AbsentOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PySide6','shiboken6','paddle','paddleocr','google','openai','anthropic'}:
            raise ModuleNotFoundError(fullname, name=fullname)
sys.meta_path.insert(0, AbsentOptional())
from tests.unit.test_query_authority import wire_server
from kiwoom_monitor.central_server.app import create_app
from kiwoom_monitor.central_server.config import CentralServerSettings, ProducerGrant
import kiwoom_monitor
package = root / 'src/kiwoom_monitor'
assert Path(kiwoom_monitor.__file__).resolve().is_relative_to(package)
manifest = json.loads((root / 'absence.json').read_text(encoding='utf-8'))
assert all(not (package / name).exists() for name in manifest['excluded'])
assert all((package / name).is_file() for name in manifest['shared'])
assert not (package / 'presentation').exists()
# Only owned fixture settings are read. No inherited API keys/vault paths enter startup.
startup = {'MONITOR_SERVER_ACCESS_TOKEN':'strict-receiver-admin-fixture',
           'KIWOOM_SERVER_DATABASE_URL':'sqlite:///' + str(root / 'receiver.sqlite3')}
if mode == 'selected':
    startup['MONITOR_SERVER_SELECTED_RESEARCH_ARTIFACT_FILE'] = str(root / 'research-producer.json')
with patch.dict(os.environ, startup, clear=True):
    settings = replace(CentralServerSettings.from_environment(),
        autonomous_top20_enabled=False, market_event_collection_enabled=False,
        news_history_jobs_enabled=False, news_query_set_enabled=False,
        news_naver_api_enabled=False, news_naver_stock_enabled=False, news_naver_market_enabled=False,
        research_result_grants=(ProducerGrant('processor_a', 'result-only-producer-fixture'),))
selected = settings.selected_research_artifact()
assert (selected is not None) == (mode == 'selected')
app = create_app(settings)
if selected is not None:
    assert selected.implementation_hash == manifest['implementation_hash']
    # Startup owns one immutable selection; removing the file is effective at restart.
    Path(settings.selected_research_artifact_file).unlink()
with wire_server(app) as (base, loop):
    ready = root / (mode + '-ready.json')
    pending = ready.with_suffix('.tmp')
    pending.write_text(json.dumps({'base':base}), encoding='utf-8')
    pending.replace(ready)
    assert sys.stdin.readline() == 'stop\\n'
assert loop.is_closed()
absent_modules = {'kiwoom_monitor.' + name[:-3].replace('/', '.') for name in manifest['excluded']}
assert not absent_modules.intersection(sys.modules), sorted(absent_modules.intersection(sys.modules))
assert not any(name.split('.')[0] in {'PySide6','shiboken6','paddle','paddleocr','google','openai','anthropic'} for name in sys.modules)
assert not any(name.startswith('kiwoom_monitor.presentation') for name in sys.modules)
(root / (mode + '-proof.json')).write_text(json.dumps({
    'mode':mode, 'origin':kiwoom_monitor.__file__, 'excluded_count':len(absent_modules),
    'selected':selected is not None, 'loop_closed':loop.is_closed(), 'engine_loaded':False,
}), encoding='utf-8')
'''
        with TemporaryDirectory(prefix="strict-receiver-without-engine-") as directory:
            root = Path(directory)
            source = Path(__file__).resolve().parents[2]
            shutil.copytree(source / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for relative in excluded:
                (root / "src/kiwoom_monitor" / relative).unlink()
            shutil.rmtree(root / "src/kiwoom_monitor/presentation")
            for relative in ("tests/__init__.py", "tests/unit/__init__.py", "tests/unit/test_query_authority.py"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, target)
            selected_file, selected = selected_research_artifact_fixture(root)
            (root / "absence.json").write_text(json.dumps({
                "excluded": excluded, "shared": shared, "implementation_hash": selected["implementation_hash"],
            }), encoding="utf-8")
            package = _package()
            spec = {**package.candidate_spec, "implementation_hash": selected["implementation_hash"]}
            package = replace(package, candidate_spec=spec, candidate_spec_hash=_hash(spec),
                              scientific_implementation_hash=selected["implementation_hash"])
            policy = _policy(package)
            receipt = evaluate_candidate_eligibility(package, policy, account_ref=ACCOUNT_REF)
            database = root / "receiver.sqlite3"
            seed = SQLiteQueryStore(database, now_provider=lambda: NOW)
            seed.initialize()
            try:
                # Simulated native credential/binding fixture; no broker account verification occurs.
                seed.register_account_identity({"broker":"kiwoom", "environment":"mock",
                    "identity_fingerprint":"c" * 64, "account_ref":ACCOUNT_REF, "created_at":NOW.isoformat()})
                binding = seed.append_account_binding({"credential_profile_id":"nas-mock-default",
                    "broker":"kiwoom", "environment":"mock", "account_ref":ACCOUNT_REF,
                    "verified_at":NOW.isoformat(), "verification_method":"ka00001"})
            finally:
                seed.close()
            body = {"account_ref":ACCOUNT_REF, "credential_profile_id":"nas-mock-default",
                "expected_binding_revision":binding["binding_revision"], "package":package.to_dict(),
                "eligibility_policy":policy.to_dict(), "eligibility_receipt":receipt.to_dict()}
            headers = {"Authorization":"Bearer strict-receiver-admin-fixture"}
            candidate_path = "/api/v1/research/mock-automation-candidates"

            def native_rows():
                with closing(sqlite3.connect(database)) as connection:
                    return connection.execute(
                        "SELECT collection,owner,document_key,updated_at,document_json FROM central_documents "
                        "WHERE collection IN (?,?,?) ORDER BY collection,owner,document_key", collections,
                    ).fetchall()

            @contextmanager
            def receiver(mode):
                child = subprocess.Popen([sys.executable, "-I", "-c", script, str(root), mode],
                    cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8")
                ready = root / (mode + "-ready.json")
                base = None
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    if not ready.exists():
                        self.fail("isolated receiver did not publish its existing loopback endpoint")
                    base = json.loads(ready.read_text(encoding="utf-8"))["base"]
                    yield base
                finally:
                    try:
                        stdout, stderr = child.communicate("stop\n", timeout=30)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        stdout, stderr = child.communicate(timeout=5)
                        self.fail("isolated receiver did not drain: " + stdout + stderr)
                    self.assertEqual(0, child.returncode, stdout + stderr)
                    proof = json.loads((root / (mode + "-proof.json")).read_text(encoding="utf-8"))
                    self.assertEqual(len(excluded), proof["excluded_count"])
                    self.assertTrue(proof["loop_closed"])
                    self.assertFalse(proof["engine_loaded"])
                    self.assertEqual(mode == "selected", proof["selected"])
                    self.assertTrue(Path(proof["origin"]).resolve().is_relative_to(root / "src"))
                    # Existing wire_server joined its thread and closed the actual
                    # listener before the child wrote its loop-closed proof.
                    # communicate() also waited for that process to exit.

            with receiver("unselected") as base:
                self.assertEqual(409, httpx.post(base + candidate_path, headers=headers, json=body, timeout=5).status_code)
                self.assertEqual([], native_rows())
            with receiver("selected") as base:
                self.assertFalse(selected_file.exists())
                self.assertEqual(401, httpx.post(base + candidate_path, json=body, timeout=5).status_code)
                self.assertEqual(422, httpx.post(base + candidate_path, headers=headers,
                    json={**body, "expected_binding_revision":True}, timeout=5).status_code)
                self.assertEqual([], native_rows())

                def lose_ack(request, **kwargs):
                    with urlopen(request, **kwargs) as response:
                        response.read()
                    raise URLError("fixture committed strict candidate ACK lost")

                lost = CentralContentClient(base, "strict-receiver-admin-fixture", opener=lose_ack, timeout_seconds=5)
                with self.assertRaises(CentralContentUnavailableError):
                    lost.publish_mock_automation_candidate(**body)
                first = native_rows()
                self.assertEqual(set(collections), {row[0] for row in first})
                self.assertEqual(3, len(first))
                client = CentralContentClient(base, "strict-receiver-admin-fixture", timeout_seconds=5)
                listed = client.load_mock_automation_candidates(ACCOUNT_REF, credential_profile_id="nas-mock-default")
                self.assertEqual([{"package":package.to_dict(), "eligibility_policy":policy.to_dict(),
                    "eligibility_receipt":receipt.to_dict()}], listed["candidates"])
                duplicate = client.publish_mock_automation_candidate(**body)
                self.assertEqual(("unchanged", False), (duplicate["status"], duplicate["orders_started"]))
                self.assertEqual(first, native_rows())
            with receiver("revoked") as base:
                client = CentralContentClient(base, "strict-receiver-admin-fixture", timeout_seconds=5)
                self.assertEqual("unchanged", client.publish_mock_automation_candidate(**body)["status"])
                changed_policy = replace(policy, minimum_closed_trades=11)
                changed_receipt = evaluate_candidate_eligibility(package, changed_policy, account_ref=ACCOUNT_REF)
                with self.assertRaises(CentralContentHttpError) as conflict:
                    client.publish_mock_automation_candidate(**{**body, "eligibility_policy":changed_policy.to_dict(),
                        "eligibility_receipt":changed_receipt.to_dict()})
                self.assertEqual(409, conflict.exception.status_code)
                self.assertEqual(listed, client.load_mock_automation_candidates(ACCOUNT_REF,
                    credential_profile_id="nas-mock-default"))
                self.assertEqual(first, native_rows())
                reporter = CentralContentClient(base, RESULT_TOKEN, timeout_seconds=5)
                reported = reporter.accept_research_result(result_document("revoked-report"), expected_owner="processor_a")
                self.assertFalse(reported["receipt"]["scientific_verified"])
                self.assertFalse(reported["receipt"]["order_authorized"])
                self.assertEqual(reported, reporter.load_research_result("revoked-report", expected_owner="processor_a"))
                # Rotation is another native fixture transition, not a cached binding or fake result.
                seed = SQLiteQueryStore(database, now_provider=lambda: NOW)
                try:
                    seed.append_account_binding({**binding, "verified_at":"2026-10-25T13:00:00+09:00"})
                finally:
                    seed.close()
                with self.assertRaises(CentralContentHttpError) as stale:
                    client.publish_mock_automation_candidate(**body)
                self.assertEqual(409, stale.exception.status_code)
                self.assertEqual(first, native_rows())
            with closing(sqlite3.connect(database)) as connection:
                for table in ("central_execution_intents", "central_execution_events", "central_execution_runtime_leases"):
                    self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
            check = SQLiteQueryStore(database, now_provider=lambda: NOW)
            try:
                self.assertIsNone(check.load_mock_automation_control(ACCOUNT_REF))
            finally:
                check.close()
