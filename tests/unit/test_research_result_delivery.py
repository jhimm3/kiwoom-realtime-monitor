"""Actual completed producer reports, explicit delivery, immutable recovery and target pins."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import unittest
from contextlib import closing, contextmanager, ExitStack
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from kiwoom_monitor import research_process
from kiwoom_monitor import research_result_delivery as delivery
from kiwoom_monitor.central_server.config import ProducerGrant
from kiwoom_monitor.domain.research_contract import encode_reported_result_owner, decode_reported_result_owner
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, CentralContentHttpError, CentralContentUnavailableError
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from research_test_support import research_request_document, write_empty_dataset
from tests.unit.test_query_authority import wire_server, query_app
from tests.unit.test_research_reported_results import result_settings, RESULT_TOKEN, OTHER_TOKEN


class ResearchResultDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))

    def fixture(self, name="run"):
        root = self.root / name
        root.mkdir()
        write_empty_dataset(root)
        document = {**research_request_document(), "mode": "single_run"}
        path = root / "request.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return root, path, research_process.load_research_process_request(path)

    def completed(self, name="run"):
        root, path, request = self.fixture(name)
        result = research_process.execute_process_request(request)
        return root, path, request, result

    def assert_completed(self, request, result):
        record = ResearchRepository(request.database, read_only=True).load_run(result["run_id"])
        self.assertEqual("completed", record["status"])
        return record

    def connection(self, root, url, token=RESULT_TOKEN):
        path = root / "research_result_connection.json"
        path.write_text(json.dumps({"mode": "personal_server", "server_url": url, "access_token": token}), encoding="utf-8")
        return path

    @contextmanager
    def native_counts(self, app):
        store = app.state.query_authority_store
        statements, connections, calls = [], [], []
        connection, request = store._connection, CentralContentClient._request
        @contextmanager
        def traced_connection():
            connections.append(True)
            with connection() as owned:
                owned.set_trace_callback(statements.append)
                yield owned
        def traced_request(client, method, path, *args, **kwargs):
            calls.append(method)
            return request(client, method, path, *args, **kwargs)
        counts = {}
        with patch.object(store, '_connection', traced_connection), patch.object(CentralContentClient, '_request', traced_request):
            yield counts
        counts.update(HTTP=calls, native_connections=len(connections),
                      INSERT=sum(line.startswith('INSERT INTO central_documents') for line in statements),
                      document_SELECT=sum(line.startswith('SELECT ') and 'FROM central_documents ' in line for line in statements),
                      BEGIN=sum(line == 'BEGIN IMMEDIATE' for line in statements),
                      COMMIT=sum(line == 'COMMIT' for line in statements))

    def test_actual_receiver_delivers_full_manifest_and_first_receipt_without_changing_science(self):
        _, _, request = self.fixture()
        app = query_app(result_settings())
        with wire_server(app) as (url, _):
            target = delivery.ResearchResultTarget(url, "processor_a", RESULT_TOKEN)
            with self.native_counts(app) as counts:
                result = research_process.execute_process_request(request, reported_result_target=target)
            self.assertEqual((['POST'], 1, 1, 1, 1, 1), (counts['HTTP'], counts['native_connections'], counts['INSERT'], counts['document_SELECT'], counts['BEGIN'], counts['COMMIT']))
            print(json.dumps({'normal_delivery_counts': counts}))
            self.assertEqual(("ok", "completed", "delivered"), (result["status"], result["calculation_status"], result["delivery"]["status"]))
            row = self.assert_completed(request, result)
            path = Path(result["manifest"])
            raw = path.read_bytes()
            descriptor = json.loads(path.with_name(delivery.TARGET_FILE).read_bytes())
            ack_raw = path.with_name(delivery.ACK_FILE).read_bytes()
            stored = CentralContentClient(url, RESULT_TOKEN).load_research_result(result["run_id"], expected_owner="processor_a")
            self.assertEqual(json.loads(raw), stored["result"]["payload"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), descriptor["source"]["manifest_sha256"])
            self.assertEqual(row["finished_at"], descriptor["source"]["finished_at"])
            self.assertEqual(datetime.fromisoformat(row["finished_at"]), datetime.fromisoformat(stored["result"]["produced_at"]))
            self.assertTrue(stored["result"]["produced_at"].endswith("+09:00"))
            self.assertFalse(stored["receipt"]["scientific_verified"])
            self.assertFalse(stored["receipt"]["order_authorized"])
            self.assertEqual(result["delivery"]["receipt"], stored["receipt"])
            with patch.object(CentralContentClient, "accept_research_result", side_effect=AssertionError("valid ACK must be a local no-op")):
                self.assertEqual(result["delivery"], delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target))
            self.assertEqual((raw, ack_raw, row), (path.read_bytes(), path.with_name(delivery.ACK_FILE).read_bytes(), self.assert_completed(request, result)))
            self.assertNotIn(RESULT_TOKEN.encode(), path.with_name(delivery.TARGET_FILE).read_bytes() + ack_raw)

    def test_commit_ack_loss_explicit_resend_reuses_original_source_time_and_receipt(self):
        root, _, request, result = self.completed()
        local_db = request.database.read_bytes()
        raw = Path(result["manifest"]).read_bytes()
        row = self.assert_completed(request, result)
        with wire_server(query_app(result_settings())) as (url, _):
            target = delivery.ResearchResultTarget(url, "processor_a", RESULT_TOKEN)
            original = CentralContentClient.accept_research_result
            def lost(client, document, **keywords):
                original(client, document, **keywords)
                raise CentralContentUnavailableError("fixture committed ACK lost")
            with patch.object(CentralContentClient, "accept_research_result", autospec=True, side_effect=lost), \
                    patch.object(CentralContentClient, "load_research_result", side_effect=CentralContentUnavailableError('fixture confirmation unavailable')):
                pending = delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target)
            self.assertEqual(("pending", "confirm", "CentralContentUnavailableError"), (pending["status"], pending["stage"], pending["reason"]))
            self.assertEqual('CentralContentUnavailableError', pending['confirmation_reason'])
            self.assertFalse(Path(result["manifest"]).with_name(delivery.ACK_FILE).exists())
            first = CentralContentClient(url, RESULT_TOKEN).load_research_result(result["run_id"], expected_owner="processor_a")
            connection = self.connection(root, url)
            with patch.object(research_process, "execute_research", side_effect=AssertionError("resend recalculated")):
                resumed = delivery.resend_completed_result(request.database, request.runs_dir, result["run_id"], connection)
            self.assertEqual(("delivered", first["receipt"]), (resumed["status"], resumed["receipt"]))
            self.assertEqual((local_db, raw, row), (request.database.read_bytes(), Path(result["manifest"]).read_bytes(), self.assert_completed(request, result)))

    def test_descriptor_confirm_and_ack_failures_keep_calculation_completed_and_do_not_invent_ack(self):
        for failure in ("descriptor", "confirm", "ack"):
            with self.subTest(failure=failure), wire_server(query_app(result_settings())) as (url, _):
                _, _, request, result = self.completed(failure)
                target = delivery.ResearchResultTarget(url, "processor_a", RESULT_TOKEN)
                writer = delivery.write_immutable_bytes
                def write(path, payload):
                    if (failure == "descriptor" and path.name == delivery.TARGET_FILE) or (failure == "ack" and path.name == delivery.ACK_FILE):
                        raise PermissionError("fixture blocked publication")
                    writer(path, payload)
                original = CentralContentClient.accept_research_result
                def lose_response(client, document, **keywords):
                    original(client, document, **keywords)
                    raise CentralContentUnavailableError('fixture committed response lost')
                with ExitStack() as stack:
                    stack.enter_context(patch.object(delivery, 'write_immutable_bytes', side_effect=write))
                    if failure == 'confirm':
                        stack.enter_context(patch.object(CentralContentClient, 'accept_research_result', autospec=True, side_effect=lose_response))
                        stack.enter_context(patch.object(CentralContentClient, 'load_research_result', side_effect=CentralContentUnavailableError('fixture GET loss')))
                    outcome = delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target)
                self.assertEqual("not_prepared" if failure == "descriptor" else "pending", outcome["status"])
                self.assertEqual(failure, outcome["stage"])
                self.assert_completed(request, result)
                self.assertFalse(Path(result["manifest"]).with_name(delivery.ACK_FILE).exists())
                if failure == "descriptor":
                    with self.assertRaises(CentralContentHttpError) as error:
                        CentralContentClient(url, RESULT_TOKEN).load_research_result(result["run_id"], expected_owner="processor_a")
                    self.assertEqual(404, error.exception.status_code)
                else:
                    self.assertEqual("delivered", delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target)["status"])

    def test_target_and_unicode_owner_pin_rejects_credential_switch_before_native_and_allows_rotation(self):
        owner = "연구_PC"
        encoded = encode_reported_result_owner(owner)
        self.assertTrue(encoded.isascii())
        self.assertEqual(owner, decode_reported_result_owner(encoded))
        app = query_app(result_settings(grants=(ProducerGrant(owner, RESULT_TOKEN), ProducerGrant("other", OTHER_TOKEN))))
        root, _, request, result = self.completed()
        with wire_server(app) as (url, _):
            target = delivery.ResearchResultTarget(url, owner, "old-unavailable-result-token")
            self.assertEqual("pending", delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target, cancel_requested=lambda: True)["status"])
            before = Path(result["manifest"]).with_name(delivery.TARGET_FILE).read_bytes()
            with patch.object(app.state.query_authority_store, "accept_research_result", side_effect=AssertionError("changed credential wrote")) as native, \
                    patch.object(CentralContentClient, 'load_research_result', side_effect=AssertionError('owner rejection must not fall back to GET')):
                changed = delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], replace(target, access_token=OTHER_TOKEN))
                self.assertEqual(("pending", "post", 409), (changed["status"], changed["stage"], changed["http_status"]))
                for header in (None, owner, encode_reported_result_owner("other")):
                    headers = {"Authorization": "Bearer " + RESULT_TOKEN}
                    if header is not None:
                        # raw non-ASCII is invalid at the HTTP library boundary; malformed ASCII is rejected server-side.
                        headers["X-Kiwoom-Expected-Result-Owner"] = header if header.isascii() else "unencoded"
                    self.assertEqual(409, httpx.post(url + "/api/v1/research/reported-results", content=b"invalid", headers=headers).status_code)
                native.assert_not_called()
            for changed in (replace(target, server_url=url + "/different"), replace(target, owner="other")):
                with patch.object(CentralContentClient, "accept_research_result", side_effect=AssertionError("changed target sent")):
                    self.assertEqual("descriptor", delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], changed)["stage"])
            self.assertEqual(before, Path(result["manifest"]).with_name(delivery.TARGET_FILE).read_bytes())
            connection = self.connection(root, url)
            self.assertEqual("delivered", delivery.resend_completed_result(request.database, request.runs_dir, result["run_id"], connection)["status"])

    def test_result_post_and_own_get_do_not_follow_redirect_or_forward_bearer(self):
        calls = []
        destination = FastAPI()
        @destination.api_route("/{path:path}", methods=["GET", "POST"])
        async def wrong_server(request: Request, path: str):
            calls.append(request.headers.get("authorization"))
            return {}
        with wire_server(destination) as (other, _):
            redirect = FastAPI()
            @redirect.api_route("/{path:path}", methods=["GET", "POST"])
            async def move(request: Request, path: str):
                return RedirectResponse(other + "/unexpected", status_code=307 if request.method == "POST" else 302)
            with wire_server(redirect) as (url, _):
                _, _, request, result = self.completed()
                target = delivery.ResearchResultTarget(url, "processor_a", RESULT_TOKEN)
                outcome = delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target)
                self.assertEqual(("pending", 307), (outcome["status"], outcome["http_status"]))
                with self.assertRaises(CentralContentHttpError) as error:
                    CentralContentClient(url, RESULT_TOKEN).load_research_result(result["run_id"], expected_owner="processor_a")
                self.assertEqual(302, error.exception.status_code)
        self.assertEqual([], calls)

    def test_cancellation_joins_inflight_request_and_preserves_completed_pending(self):
        app = query_app(result_settings())
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        original = app.state.query_authority_store.accept_research_result
        def held(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("fixture native release did not arrive")
            return original(*args)
        with wire_server(app) as (url, _):
            _, _, request, result = self.completed()
            target = delivery.ResearchResultTarget(url, "processor_a", RESULT_TOKEN)
            with patch.object(app.state.query_authority_store, "accept_research_result", side_effect=held), ThreadPoolExecutor(max_workers=1) as worker:
                future = worker.submit(delivery.deliver_completed_result, request.database, request.runs_dir, result["run_id"], target, cancel_requested=cancelled.is_set)
                try:
                    self.assertTrue(entered.wait(5))
                    cancelled.set()
                    self.assertFalse(future.done())
                    self.assertFalse(Path(result["manifest"]).with_name(delivery.ACK_FILE).exists())
                finally:
                    release.set()
                outcome = future.result(timeout=10)
            self.assertEqual(("pending", "after_post", "cancelled"), (outcome["status"], outcome["stage"], outcome["reason"]))
            self.assert_completed(request, result)
            self.assertEqual("delivered", delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target)["status"])

    def test_raw_timestamp_path_and_payload_guards_leave_original_completed_evidence(self):
        for mutation in ("raw", "finished", "oversize", "nonfinite", "ack"):
            with self.subTest(mutation=mutation):
                _, _, request, result = self.completed(mutation)
                path = Path(result["manifest"])
                target = delivery.ResearchResultTarget("http://127.0.0.1:1", "processor_a", RESULT_TOKEN)
                if mutation not in {"oversize", "nonfinite"}:
                    delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target, cancel_requested=lambda: True)
                if mutation == "finished":
                    with closing(sqlite3.connect(request.database)) as connection, connection:
                        connection.execute("UPDATE research_runs SET finished_at=? WHERE run_id=?", ("2020-01-01T00:00:00+00:00", result["run_id"]))
                elif mutation == "ack":
                    path.with_name(delivery.ACK_FILE).write_text('{"forged":true}', encoding="utf-8")
                else:
                    document = json.loads(path.read_bytes())
                    document["extra"] = "x" * (256 * 1024) if mutation == "oversize" else float("nan") if mutation == "nonfinite" else "changed"
                    path.write_text(json.dumps(document), encoding="utf-8")
                source = path.read_bytes()
                with patch.object(CentralContentClient, "accept_research_result", side_effect=AssertionError("invalid source sent")):
                    outcome = delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target)
                self.assertEqual("pending", outcome["status"])
                self.assertEqual(source, path.read_bytes())
                self.assert_completed(request, result)
        with self.assertRaises(ValueError):
            delivery._paths(self.root, "../other")
        with patch.object(delivery, "load_result_target", side_effect=AssertionError("missing descriptor loaded connection")):
            self.assertEqual("not_prepared", delivery.resend_completed_result(self.root / "absent.sqlite3", self.root / "absent-runs", "missing", self.root / "absent.json")["status"])

    def test_cli_defaults_and_opt_in_failure_exit_keep_local_completion_separate(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                root, path, request = self.fixture(str(explicit))
                args = ["--request", str(path), "--result", str(root / "result.json")]
                if explicit:
                    connection = self.connection(root, "http://127.0.0.1:1")
                    args += ["--result-connection", str(connection), "--result-owner", "processor_a"]
                with patch("sys.stdout", new=StringIO()), patch.object(delivery, "load_result_target", wraps=delivery.load_result_target) as load, \
                        patch.object(CentralContentClient, "accept_research_result", side_effect=CentralContentUnavailableError("fixture unavailable")) as send, \
                        patch.object(CentralContentClient, 'load_research_result', side_effect=CentralContentUnavailableError('fixture confirmation unavailable')) as confirm:
                    code = research_process._run_research_process(args)
                outcome = json.loads((root / "result.json").read_bytes())
                self.assertEqual(4 if explicit else 0, code)
                self.assertEqual("delivery_pending" if explicit else "ok", outcome["status"])
                self.assert_completed(request, outcome)
                self.assertEqual(int(explicit), load.call_count)
                self.assertEqual(int(explicit), send.call_count)
                self.assertEqual(int(explicit), confirm.call_count)
                self.assertEqual(int(explicit), len(list(request.runs_dir.rglob(delivery.TARGET_FILE))))
                self.assertFalse(list(request.runs_dir.rglob(delivery.ACK_FILE)))
                self.assertNotIn(RESULT_TOKEN, json.dumps(outcome))
        _, _, request = self.fixture("unsupported")
        with patch.object(research_process, "load_research_input", side_effect=AssertionError("unsupported delivery executed")) as load:
            with self.assertRaisesRegex(ValueError, "ordinary single_run"):
                research_process.execute_process_request(replace(request, mode="rank_comparison"), reported_result_target=delivery.ResearchResultTarget("http://127.0.0.1:1", "processor_a", RESULT_TOKEN))
            load.assert_not_called()

    def test_ordinary_cli_actual_receiver_preserves_success_and_reports_delivery_failure_exit(self):
        from scripts.run_research import main

        root, _, request = self.fixture("ordinary")
        connection = self.connection(root, "http://127.0.0.1:1")
        strategy = request.strategy.to_dict()
        args = ["--dataset", str(request.dataset), "--database", str(request.database), "--runs-dir", str(request.runs_dir)]
        for name in ("lookback_bars", "buffer_bps", "rank_top_k", "rank_window_seconds", "rank_max_gap_seconds",
                     "rank_min_residency_seconds", "stop_loss_bps", "target_bps", "max_hold_minutes", "quantity",
                     "capital_won", "signal_valid_seconds", "cooldown_seconds"):
            args += ["--" + name.replace("_", "-"), str(strategy[name])]
        args += ["--rank-enabled", "--rank-required", "--initial-cash-won", "1000000",
                 "--commission-bps", "1", "--sell-tax-bps", "18", "--slippage-bps", "5",
                 "--result-connection", str(connection), "--result-owner", "processor_a"]
        output = StringIO()
        with patch("sys.stdout", new=output), patch.object(CentralContentClient, "accept_research_result", side_effect=CentralContentUnavailableError("fixture unavailable")), \
                patch.object(CentralContentClient, 'load_research_result', side_effect=CentralContentUnavailableError('fixture confirmation unavailable')):
            self.assertEqual(4, main(args))
        result = json.loads(output.getvalue())
        self.assertEqual(("delivery_pending", "completed", "confirm"), (result["status"], result["calculation_status"], result["delivery"]["stage"]))
        self.assert_completed(request, result)
        self.assertEqual(result["run_id"], json.loads(Path(result["manifest"]).read_bytes())["run_id"])
        before = (request.database.read_bytes(), Path(result['manifest']).read_bytes())
        for value in ([], None):
            with self.subTest(invalid_connection=value):
                connection.write_text(json.dumps(value), encoding='utf-8')
                with patch('sys.stdout', new=StringIO()), patch('sys.stderr', new=StringIO()), \
                        patch('scripts.run_research.execute_research', side_effect=AssertionError('invalid connection calculated')):
                    with self.assertRaises(SystemExit) as error:
                        main(args)
                self.assertEqual(2, error.exception.code)
                self.assertEqual(before, (request.database.read_bytes(), Path(result['manifest']).read_bytes()))

    def test_same_run_concurrent_delivery_keeps_one_original_ack_and_conflicting_target_never_replaces_it(self):
        _, _, request, result = self.completed("concurrent")
        with wire_server(query_app(result_settings())) as (url, _):
            target = delivery.ResearchResultTarget(url, "processor_a", RESULT_TOKEN)
            barrier = threading.Barrier(2)
            def send(_):
                barrier.wait(timeout=5)
                return delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], target)
            with ThreadPoolExecutor(max_workers=2) as workers:
                outcomes = list(workers.map(send, (1, 2)))
            self.assertEqual(["delivered", "delivered"], [row["status"] for row in outcomes])
            self.assertEqual(outcomes[0]["receipt"], outcomes[1]["receipt"])
            path = Path(result["manifest"])
            files = {name: path.with_name(name).read_bytes() for name in (delivery.TARGET_FILE, delivery.ACK_FILE)}
            self.assertEqual("pending", delivery.deliver_completed_result(request.database, request.runs_dir, result["run_id"], replace(target, owner="processor_b", access_token=OTHER_TOKEN))["status"])
            self.assertEqual(files, {name: path.with_name(name).read_bytes() for name in files})
            self.assertEqual([], list(path.parent.glob("*.tmp")))

    def test_uncertain_lost_or_invalid_post_response_alone_uses_get_and_preserves_first_receipt(self):
        for uncertainty in ('lost', 'invalid'):
            with self.subTest(uncertainty=uncertainty):
                _, _, request, result = self.completed(uncertainty)
                before = (request.database.read_bytes(), Path(result['manifest']).read_bytes())
                app = query_app(result_settings())
                first = []
                original = CentralContentClient.accept_research_result
                def uncertain(client, document, **keywords):
                    accepted = original(client, document, **keywords)
                    first.append(deepcopy(accepted))
                    if uncertainty == 'lost':
                        raise CentralContentUnavailableError('fixture committed response lost')
                    accepted['receipt']['content_hash'] = 'invalid-response-hash'
                    return accepted
                with wire_server(app) as (url, _), self.native_counts(app) as counts, \
                        patch.object(CentralContentClient, 'accept_research_result', autospec=True, side_effect=uncertain):
                    outcome = delivery.deliver_completed_result(request.database, request.runs_dir, result['run_id'], delivery.ResearchResultTarget(url, 'processor_a', RESULT_TOKEN))
                self.assertEqual(('delivered', first[0]['receipt']), (outcome['status'], outcome['receipt']))
                self.assertEqual((['POST', 'GET'], 2, 1, 2, 1, 1), (counts['HTTP'], counts['native_connections'], counts['INSERT'], counts['document_SELECT'], counts['BEGIN'], counts['COMMIT']))
                self.assertEqual(before, (request.database.read_bytes(), Path(result['manifest']).read_bytes()))
                print(json.dumps({'uncertain_delivery_counts': counts, 'case': uncertainty}))

    def test_definite_http_rejection_never_uses_get_to_hide_original_status(self):
        for status in (403, 409, 422):
            with self.subTest(status=status):
                _, _, request, result = self.completed(str(status))
                original = self.assert_completed(request, result)
                with patch.object(CentralContentClient, 'accept_research_result', side_effect=CentralContentHttpError(status, 'fixture rejection')), \
                        patch.object(CentralContentClient, 'load_research_result', side_effect=AssertionError('definite rejection must not read an old row')) as read:
                    outcome = delivery.deliver_completed_result(request.database, request.runs_dir, result['run_id'], delivery.ResearchResultTarget('http://127.0.0.1:1', 'processor_a', RESULT_TOKEN))
                read.assert_not_called()
                self.assertEqual(('pending', 'post', status, 'CentralContentHttpError'), (outcome['status'], outcome['stage'], outcome['http_status'], outcome['reason']))
                self.assertEqual(original, self.assert_completed(request, result))
                self.assertFalse(Path(result['manifest']).with_name(delivery.ACK_FILE).exists())

    def test_nonobject_connection_has_bounded_headless_and_resend_outcomes_without_work(self):
        for value in ([], None):
            with self.subTest(value=value):
                root, path, request = self.fixture('invalid-' + str(value))
                connection = root / 'connection.json'
                connection.write_text(json.dumps(value), encoding='utf-8')
                with patch('sys.stdout', new=StringIO()), patch.object(research_process, 'load_research_input', side_effect=AssertionError('invalid connection loaded calculation input')) as load, \
                        patch.object(CentralContentClient, 'accept_research_result', side_effect=AssertionError('invalid connection sent')) as send:
                    code = research_process._run_research_process(['--request', str(path), '--result', str(root/'result.json'), '--result-connection', str(connection), '--result-owner', 'processor_a'])
                self.assertEqual(1, code)
                failed = json.loads((root/'result.json').read_bytes())
                self.assertEqual('failed', failed['status'])
                self.assertIn('ValueError: explicit result connection is invalid', failed['reason'])
                self.assertFalse(request.database.exists())
                load.assert_not_called(); send.assert_not_called()
        root, _, request, result = self.completed('resend-invalid')
        delivery.deliver_completed_result(request.database, request.runs_dir, result['run_id'], delivery.ResearchResultTarget('http://127.0.0.1:1', 'processor_a', RESULT_TOKEN), cancel_requested=lambda: True)
        connection = root/'connection.json'
        source = (request.database.read_bytes(), Path(result['manifest']).read_bytes())
        for value in ([], None):
            connection.write_text(json.dumps(value), encoding='utf-8')
            with patch.object(CentralContentClient, 'accept_research_result', side_effect=AssertionError('invalid resend sent')) as send:
                outcome = delivery.resend_completed_result(request.database, request.runs_dir, result['run_id'], connection)
            self.assertEqual(('pending', 'connection', 'ValueError'), (outcome['status'], outcome['stage'], outcome['reason']))
            send.assert_not_called()
            self.assertEqual(source, (request.database.read_bytes(), Path(result['manifest']).read_bytes()))
