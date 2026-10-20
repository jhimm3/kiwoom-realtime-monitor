from __future__ import annotations

import unittest
import asyncio
import json
import secrets
import uuid
import threading
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from kiwoom_monitor.application.mock_automation_admission import MockAutomationDesiredState
from kiwoom_monitor.central_server.mock_automation_supervisor import (
    MockAutomationSupervisor,
    MockAutomationSupervisorError,
)
from kiwoom_monitor.central_server.mock_runtime import MockAccountRuntimeMode


ACCOUNT_REF = "af64a3fa-197f-49df-8e8b-65b71bee02d9"
PROFILE_ID = "mock-profile"
SPEC_ID = "mock_automation_spec_1"
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


@contextmanager
def automation_http_contract_app(app_factory, database_path, configured):
    from kiwoom_monitor.central_server.config import CentralServerSettings
    settings = CentralServerSettings(f"sqlite:///{database_path}", "private-token",
        kiwoom_environment="mock", autonomous_top20_enabled=False,
        market_event_collection_enabled=False, news_history_jobs_enabled=False,
        news_naver_market_enabled=False,
        credential_directory=str(database_path.parent / "vault") if configured else "",
        account_identity_registry_enabled=configured, account_identity_hmac_key=secrets.token_hex(32))
    if not configured:
        yield app_factory(settings), None
        return
    supervisor = MagicMock(spec=MockAutomationSupervisor)
    supervisor.status.return_value = {"account_ref": ACCOUNT_REF, "state": "STOPPED", "control_revision": 7}
    for method in ("activate", "stop", "resume"):
        getattr(supervisor, method).return_value = {"operation": method, "account_ref": ACCOUNT_REF, "orders_enabled": False}
    with patch("kiwoom_monitor.central_server.mock_automation_supervisor.MockAutomationSupervisor", return_value=supervisor) as constructor:
        yield app_factory(settings), supervisor
    if constructor.call_count != 1 or supervisor.start.await_count != 1 or supervisor.close.await_count != 1:
        raise AssertionError("app-owned supervisor construction/start/close behavior changed")


def capture_automation_http_contract(client, supervisor):
    from kiwoom_monitor.central_server.credential_runtime import CredentialOperationError
    headers = {"Authorization": "Bearer private-token"}
    prefix = "configured" if supervisor is not None else "unavailable"
    cases = []
    methods = ("status", "activate", "stop", "resume")

    def calls(name):
        if supervisor is None:
            return []
        mock = getattr(supervisor, name)
        return mock.call_args_list if name == "status" else mock.await_args_list

    def request(name, method, path, *, authenticated=True, **kwargs):
        counts = {key: len(calls(key)) for key in methods}
        response = client.request(method, path, headers=headers if authenticated else {}, **kwargs)
        invoked = [{"method": key, "args": list(call.args), "kwargs": call.kwargs}
                   for key in methods for call in calls(key)[counts[key]:]]
        cases.append({"name": prefix + ":" + name, "status": response.status_code,
                      "headers": dict(response.headers), "body": response.json(), "supervisor_calls": invoked})

    status = f"/api/v1/mock-automation/accounts/{ACCOUNT_REF}"
    params = {"credential_profile_id": PROFILE_ID}
    start = {"account_ref": ACCOUNT_REF, "credential_profile_id": PROFILE_ID, "spec_id": SPEC_ID,
             "expected_settings_revision": 3, "credential_revision": 5}
    stop = {"account_ref": ACCOUNT_REF, "credential_profile_id": PROFILE_ID, "spec_id": SPEC_ID,
            "expected_control_revision": 7, "reason": "사용자 중지"}
    resume = {**stop, "expected_settings_revision": 3, "credential_revision": 5}
    request("status-unauthorized", "GET", status, authenticated=False, params=params)
    request("status-missing-profile", "GET", status)
    request("status-invalid-profile", "GET", status, params={"credential_profile_id": "bad/profile"})
    request("status", "GET", status, params=params)
    for operation, body in (("start", start), ("stop", stop), ("resume", resume)):
        path = "/api/v1/mock-automation/" + operation
        request(operation + "-unauthorized", "POST", path, authenticated=False, json=body)
        request(operation + "-empty", "POST", path, json={})
        request(operation + "-extra-field", "POST", path, json={**body, "unexpected": True})
        revision = "expected_settings_revision" if operation == "start" else "expected_control_revision"
        request(operation + "-boolean-revision", "POST", path, json={**body, revision: True})
        request(operation + "-string-revision", "POST", path, json={**body, revision: "7"})
        request(operation + "-invalid-account", "POST", path, json={**body, "account_ref": "short"})
        if operation != "start":
            request(operation + "-empty-reason", "POST", path, json={**body, "reason": ""})
        request(operation, "POST", path, json=body)
        if supervisor is None:
            continue
        backend = getattr(supervisor, "activate" if operation == "start" else operation)
        for label, error in (
            ("supervisor-conflict", MockAutomationSupervisorError("STALE_CONTROL_REVISION", 409)),
            ("supervisor-blocked", MockAutomationSupervisorError("ADMISSION_REQUIRED", 412)),
            ("credential-forbidden", CredentialOperationError("PROFILE_DISABLED", 403)),
            ("credential-not-found", CredentialOperationError("PROFILE_NOT_FOUND", 404)),
            ("invalid-operation", ValueError("INVALID_SPEC")),
            ("runtime-conflict", RuntimeError("RUN_ALREADY_ACTIVE")),
        ):
            backend.side_effect = error
            request(operation + "-" + label, "POST", path, json=body)
        backend.side_effect = None
    lifecycle = None if supervisor is None else {"start": supervisor.start.await_count}
    return {"cases": cases, "lifecycle": lifecycle}


class MockAutomationHTTPContractTests(unittest.TestCase):
    def test_http_contract_and_supervisor_arguments_match_pre_extraction_baseline(self):
        from fastapi.testclient import TestClient
        from kiwoom_monitor.central_server.app import create_app
        baseline = Path(__file__).resolve().parents[2] / "tests/fixtures/api_contract_baselines/mock_automation_http_v1.json"
        expected = json.loads(baseline.read_text(encoding="utf-8"))["results"]
        actual = []
        with tempfile.TemporaryDirectory() as directory:
            for configured in (True, False):
                path = Path(directory) / str(configured) / "automation.sqlite3"
                path.parent.mkdir()
                with automation_http_contract_app(create_app, path, configured) as (app, supervisor):
                    with TestClient(app) as client:
                        actual.append(capture_automation_http_contract(client, supervisor))
        self.assertEqual(len(expected), len(actual))
        for original, observed in zip(expected, actual, strict=True):
            self.assertEqual(original["lifecycle"], observed["lifecycle"])
            self.assertEqual([case["name"] for case in original["cases"]], [case["name"] for case in observed["cases"]])
            for before, after in zip(original["cases"], observed["cases"], strict=True):
                with self.subTest(case=before["name"]):
                    self.assertEqual(before, after)


class _Control:
    def __init__(self, state=MockAutomationDesiredState.RUNNING, revision=1):
        self.active_spec_id = SPEC_ID
        self.desired_state = state
        self.control_revision = revision
        self.execution_run_id = "mock_auto_run_" + "a" * 64

    def to_dict(self):
        return {
            "active_spec_id": self.active_spec_id,
            "desired_state": self.desired_state.value,
            "control_revision": self.control_revision,
            "execution_run_id": self.execution_run_id,
        }


class _Repository:
    def __init__(self):
        self.control = None
        self.spec = SimpleNamespace(
            spec_id=SPEC_ID,
            strategy_ref="strategy-1",
            account_scope=SimpleNamespace(account_ref=ACCOUNT_REF),
            credential_profile_id=PROFILE_ID,
        )
        self.admission = SimpleNamespace(
            admission_id="admission-1",
            execution_run_id="mock_auto_run_" + "a" * 64,
        )
        self.risk = SimpleNamespace(snapshot_id="risk-1")

    def load_mock_automation_spec(self, account_ref, spec_id):
        return self.spec if (account_ref, spec_id) == (ACCOUNT_REF, SPEC_ID) else None

    def load_mock_automation_admission_for_spec(self, account_ref, spec_id):
        return self.admission

    def load_mock_automation_control(self, account_ref):
        return self.control

    def load_latest_mock_automation_risk(self, account_ref):
        return self.risk


class _Runtime:
    def __init__(self):
        self.enabled = False

    def set_new_orders_enabled(self, enabled):
        self.enabled = enabled


class _Bundle:
    def __init__(self):
        self.account_ref = ACCOUNT_REF
        self.credential_profile_id = PROFILE_ID
        self.runtime_mode = MockAccountRuntimeMode.MANUAL
        self.automation_context = None
        self.runtime = _Runtime()
        self.monitor = SimpleNamespace(refresh_recovery=AsyncMock(return_value=object()))


class _Owner:
    def __init__(self):
        self.current = _Bundle()
        self.switch_calls = []

    def bundle(self, profile_id):
        return self.current if profile_id == PROFILE_ID else None

    async def switch_execution_mode(self, profile_id, mode, **kwargs):
        self.switch_calls.append((profile_id, mode, kwargs))
        self.current.runtime_mode = mode
        self.current.automation_context = kwargs["automation_context"]
        return self.current

    def account_bindings(self):
        return ()

    def active_revision(self, profile_id):
        return 3


class _Runner:
    def __init__(self, store, bundle, spec, *, poll_seconds, price_source=None, now_provider=None, leader_context_source=None):
        self._bundle = bundle
        self.now_provider = now_provider
        self.leader_context_source = leader_context_source
        self.started = False
        self.closed = False
        self.status = {
            "account_ref": bundle.account_ref,
            "spec_id": spec.spec_id,
            "state": "WARMUP",
        }

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True


class MockAutomationSupervisorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repository = _Repository()
        self.owner = _Owner()
        repository_patch = patch(
            "kiwoom_monitor.central_server.mock_automation_supervisor.ForwardEvaluationRepository",
            return_value=self.repository,
        )
        runner_patch = patch(
            "kiwoom_monitor.central_server.mock_automation_supervisor.MockAutomationRunner",
            _Runner,
        )
        self.addCleanup(repository_patch.stop)
        self.addCleanup(runner_patch.stop)
        repository_patch.start()
        runner_patch.start()
        self.supervisor = MockAutomationSupervisor(object(), self.owner)

    async def asyncTearDown(self):
        await self.supervisor.close()

    async def test_activate_switches_one_bundle_and_starts_one_runner_fail_closed(self):
        with patch(
            "kiwoom_monitor.central_server.mock_automation_supervisor.admit_mock_automation",
        ) as admit, patch(
            "kiwoom_monitor.central_server.mock_automation_supervisor.record_mock_automation_recovery_from_risk",
        ) as recover:
            status = await self.supervisor.activate(
                credential_profile_id=PROFILE_ID,
                account_ref=ACCOUNT_REF,
                spec_id=SPEC_ID,
                expected_settings_revision=2,
                credential_revision=3,
            )
        self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, self.owner.current.runtime_mode)
        self.assertEqual(1, len(self.owner.switch_calls))
        self.assertEqual(SPEC_ID, self.owner.current.automation_context.spec_id)
        self.assertFalse(self.owner.current.runtime.enabled)
        self.assertEqual("WARMUP", status["runner"]["state"])
        admit.assert_called_once()
        recover.assert_called_once()

        again = await self.supervisor.activate(
            credential_profile_id=PROFILE_ID,
            account_ref=ACCOUNT_REF,
            spec_id=SPEC_ID,
            expected_settings_revision=2,
            credential_revision=3,
        )
        self.assertEqual(1, len(self.owner.switch_calls))
        self.assertEqual(status["runner"], again["runner"])

    async def test_activation_error_after_switch_keeps_orders_closed(self):
        with patch(
            "kiwoom_monitor.central_server.mock_automation_supervisor.admit_mock_automation",
            side_effect=RuntimeError("admission failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "admission failed"):
                await self.supervisor.activate(
                    credential_profile_id=PROFILE_ID,
                    account_ref=ACCOUNT_REF,
                    spec_id=SPEC_ID,
                    expected_settings_revision=2,
                    credential_revision=3,
                )
        self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, self.owner.current.runtime_mode)
        self.assertFalse(self.owner.current.runtime.enabled)
        self.assertIsNone(self.supervisor.status(
            ACCOUNT_REF, credential_profile_id=PROFILE_ID,
        )["runner"])

    async def test_stop_and_resume_require_current_control_revision(self):
        self.owner.current.runtime_mode = MockAccountRuntimeMode.AUTOMATIC
        self.owner.current.automation_context = SimpleNamespace(spec_id=SPEC_ID)
        self.repository.control = _Control(revision=4)
        with self.assertRaisesRegex(
            MockAutomationSupervisorError, "MOCK_AUTOMATION_CONTROL_REVISION_CONFLICT",
        ):
            await self.supervisor.stop(
                credential_profile_id=PROFILE_ID, account_ref=ACCOUNT_REF,
                spec_id=SPEC_ID, expected_control_revision=3, reason="operator stop",
            )

        with patch(
            "kiwoom_monitor.central_server.mock_automation_supervisor.emergency_stop_mock_automation",
        ) as stop:
            await self.supervisor.stop(
                credential_profile_id=PROFILE_ID, account_ref=ACCOUNT_REF,
                spec_id=SPEC_ID, expected_control_revision=4, reason="operator stop",
            )
        stop.assert_called_once()

        self.repository.control = _Control(MockAutomationDesiredState.STOPPED, 5)
        self.owner.current.runtime_mode = MockAccountRuntimeMode.MANUAL
        self.owner.current.automation_context = None
        with patch(
            "kiwoom_monitor.central_server.mock_automation_supervisor.record_mock_automation_recovery_from_risk",
        ) as recover, patch(
            "kiwoom_monitor.central_server.mock_automation_supervisor.resume_mock_automation",
        ) as resume:
            await self.supervisor.resume(
                credential_profile_id=PROFILE_ID, account_ref=ACCOUNT_REF,
                spec_id=SPEC_ID, expected_control_revision=5, reason="operator resume",
                expected_settings_revision=2, credential_revision=3,
            )
        self.owner.current.monitor.refresh_recovery.assert_awaited_once()
        recover.assert_called_once()
        resume.assert_called_once()
        self.assertIsNotNone(self.supervisor.status(
            ACCOUNT_REF, credential_profile_id=PROFILE_ID,
        )["runner"])

    async def test_stale_cached_runner_is_closed_before_same_spec_is_reactivated(self):
        with patch("kiwoom_monitor.central_server.mock_automation_supervisor.admit_mock_automation"), \
             patch("kiwoom_monitor.central_server.mock_automation_supervisor.record_mock_automation_recovery_from_risk"):
            arguments = dict(credential_profile_id=PROFILE_ID, account_ref=ACCOUNT_REF, spec_id=SPEC_ID,
                             expected_settings_revision=2, credential_revision=3)
            await self.supervisor.activate(**arguments)
            old = self.supervisor._runners[ACCOUNT_REF]
            self.owner.current = _Bundle()
            await self.supervisor.activate(**arguments)
        current = self.supervisor._runners[ACCOUNT_REF]
        self.assertTrue(old.closed)
        self.assertIsNot(old, current)
        self.assertIs(self.owner.current, current._bundle)
        self.assertEqual(2, len(self.owner.switch_calls))
        self.assertEqual({}, self.supervisor._runner_close_tasks)

    async def test_cancelled_actual_drain_owner_is_retained_as_failure_for_activation_and_close(self):
        entered = asyncio.Event()
        async def blocked_close():
            entered.set()
            await asyncio.Event().wait()
        runner = _Runner(object(), self.owner.current, self.repository.spec, poll_seconds=1)
        runner.close = AsyncMock(side_effect=blocked_close)
        self.supervisor._runners[ACCOUNT_REF] = runner
        withdrawal = asyncio.create_task(self.supervisor.bundle_changed(PROFILE_ID, None))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            owned = self.supervisor._runner_close_tasks[runner]
            owned.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await withdrawal
            self.assertTrue(owned.cancelled())
            self.assertIs(owned, self.supervisor._runner_close_tasks[runner])
            with self.assertRaises(asyncio.CancelledError):
                await self.supervisor.activate(credential_profile_id=PROFILE_ID, account_ref=ACCOUNT_REF,
                    spec_id=SPEC_ID, expected_settings_revision=2, credential_revision=3)
            with self.assertRaises(asyncio.CancelledError):
                await self.supervisor.close()
            self.assertEqual(1, runner.close.await_count)
        finally:
            # The cancelled owner is an injected fixture fault, not a product
            # retry policy. Retire its record only to dispose of this test.
            self.supervisor._runner_close_tasks.clear()
            self.supervisor._close_task = None


class MockAutomationOwnerReplacementTests(unittest.IsolatedAsyncioTestCase):
    """Actual app callback, credential owner, bundle, runner and native control."""

    async def asyncSetUp(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from credential_owner_test_support import FakeClient

        class AutomationClient(FakeClient):
            def request_with_continuation(self, api_id, path, body, **kwargs):
                if api_id == "kt00015":
                    return {"trst_ovrl_trde_prps_array": []}, False, ""
                return super().request_with_continuation(api_id, path, body, **kwargs)

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.client_patch = patch("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient", AutomationClient)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.ws_patch = patch("kiwoom_monitor.central_server.mock_account_monitor.MockAccountRealtimeCollector.start", new=AsyncMock())
        self.ws_patch.start()
        self.addCleanup(self.ws_patch.stop)
        self.transport = MagicMock()
        self.transport.submit.side_effect = AssertionError("replacement test must never submit an order")
        self.transport.cancel.side_effect = AssertionError("replacement test must never cancel an order")
        self.transport_patch = patch("kiwoom_monitor.central_server.mock_runtime.make_mock_transport", return_value=self.transport)
        self.transport_patch.start()
        self.addCleanup(self.transport_patch.stop)
        self.app = create_app(CentralServerSettings(
            f"sqlite:///{root / 'app.sqlite'}", "private-token",
            credential_directory=str(root / "vault"),
            account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
        ))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.app_closed = False
        self.addAsyncCleanup(self.close_app)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://nas.test",
                                      headers={"Authorization": "Bearer private-token"})
        self.addAsyncCleanup(self.http.aclose)
        self.owner = self.app.state.mock_credential_owner
        self.supervisor = self.app.state.mock_automation_supervisor
        self.store = self.owner.store
        # Control the existing restore schedule explicitly; no external clock/poll.
        self.supervisor._restore_stop.set()
        await self.supervisor._restore_task
        self.supervisor._restore_task = None
        credentials = self.app.state.credential_runtime
        self.profile = (await credentials.create_profile("kiwoom_mock", str(uuid.uuid4()), "replacement"))["profile_id"]
        await self.rotate("a1", 0)
        initial = self.owner.bundle(self.profile)
        self.account_ref = initial.account_ref
        self.scope = initial.binding.scope.to_dict()
        await self.owner.update_settings(self.scope, {
            "active_profile_id": self.profile, "monitor_enabled": True, "mock_order_enabled": True,
        }, expected_revision=1)
        self.settings = self.store.load_account_settings(self.scope)
        await self.start_admitted_runner()

    async def close_app(self):
        if not self.app_closed:
            await self.lifespan.__aexit__(None, None, None)
            self.app_closed = True

    async def rotate(self, key, revision):
        credentials = self.app.state.credential_runtime
        prepared = await credentials.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), revision,
                                              {"app_key": key, "secret_key": "fake-secret"})
        operation = credentials._operations[prepared["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("READY", operation.state, operation.error_code)
        await credentials.apply(operation.operation_id, revision, operation.candidate.account_ref)
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("ACTIVE", operation.state)

    async def start_admitted_runner(self):
        from tests.unit.test_mock_automation_runner import MockAutomationRunnerTests
        from tests.unit.test_mock_automation_admission import _criteria
        from kiwoom_monitor.domain.execution_activation import ForwardEvaluationSpec
        from datetime import timedelta
        from kiwoom_monitor.application.mock_automation_admission import (
            MOCK_AUTOMATION_CONTROL_VERSION, MockAutomationControl, build_mock_automation_admission,
        )
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        from kiwoom_monitor.central_server.mock_runtime import MockAutomationRuntimeContext, MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository

        fixture = MockAutomationRunnerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        binding = self.owner.bundle(self.profile).binding
        now = self.owner.bundle(self.profile).runtime.server_now()
        repository = ForwardEvaluationRepository(self.store)
        profile = ForwardEvaluationSpec(strategy_ref=fixture.package.strategy_ref,
            family_version="family/v1", factor_versions=("factor/v1",), policy_version="policy/v1",
            data_path="nas", account_ref=self.account_ref, environment="mock", evaluation_start=now,
            evaluation_end=now + timedelta(days=8), frozen_at=now, criteria=_criteria(), session_profile="krx-regular/v1")
        repository.save_profile(profile)
        self.spec = replace(fixture.spec, account_scope=binding.scope, credential_profile_id=self.profile,
                            binding_revision=binding.binding_revision, binding_verified_at=binding.verified_at,
                            frozen_at=now, forward_profile_id=profile.profile_id)
        repository.save_mock_automation_spec(self.spec)
        self.store.upsert_documents(ForwardEvaluationRepository.MOCK_AUTOMATION_CANDIDATE_COLLECTION, [{
            "owner": fixture.package.strategy_ref, "key": fixture.package.package_hash,
            "document": fixture.package.to_dict(),
        }])
        # A previously approved native RUNNING control is the fixture input. This
        # does not claim to test research eligibility/admission issuance.
        self.admission = build_mock_automation_admission(self.spec, requested_at=now)
        repository.save_mock_automation_admission(self.admission)
        self.control = MockAutomationControl(MOCK_AUTOMATION_CONTROL_VERSION, self.account_ref,
            MockAutomationDesiredState.RUNNING, 1, self.spec.spec_id, self.admission.execution_run_id, now, "previously approved fixture")
        self.assertTrue(repository.save_mock_automation_control(self.control, expected_revision=0))
        context = MockAutomationRuntimeContext(MOCK_ACCOUNT_RUNTIME_CONTEXT_VERSION, self.account_ref,
            self.admission.execution_run_id, self.spec.spec_id, self.admission.admission_id, 1, 1)
        self.bundle = await self.owner.switch_execution_mode(self.profile, MockAccountRuntimeMode.AUTOMATIC,
            expected_settings_revision=self.settings["revision"], credential_revision=1, automation_context=context)
        self.runner = MockAutomationRunner(self.store, self.bundle, self.spec, poll_seconds=60,
                                           now_provider=self.bundle.client.server_now)
        await self.runner.start()
        self.supervisor._runners[self.account_ref] = self.runner

    async def put_settings(self, *, monitor=True, orders=False, revision=None):
        return await self.http.put(f"/api/v1/settings/accounts/{self.account_ref}?environment=mock", json={
            "expected_revision": self.settings["revision"] if revision is None else revision,
            "active_profile_id": self.profile, "monitor_enabled": monitor, "mock_order_enabled": orders,
        })

    async def test_actual_app_settings_replacement_drains_runner_and_preserves_native_control(self):
        response = await self.put_settings()
        self.assertEqual(200, response.status_code)
        self.assertTrue(self.runner._closing)
        self.assertIsNone(self.runner._task)
        self.assertNotIn(self.account_ref, self.supervisor._runners)
        current = self.owner.bundle(self.profile)
        self.assertIsNot(self.bundle, current)
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual(MockAccountRuntimeMode.MANUAL, current.runtime_mode)
        self.assertIsNone(current.automation_context)
        self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
        self.assertEqual((current.binding,), self.app.state.verified_account_bindings)
        self.assertIsNone(self.supervisor.status(self.account_ref, credential_profile_id=self.profile)["runner"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()
        with patch.object(self.supervisor, "activate", wraps=self.supervisor.activate) as activate:
            await self.supervisor._restore_once()
        activate.assert_awaited_once_with(credential_profile_id=self.profile, account_ref=self.account_ref,
            spec_id=self.spec.spec_id, expected_settings_revision=response.json()["applied_revision"], credential_revision=1)
        self.assertEqual("MOCK_ORDER_TRANSPORT_DISABLED", self.supervisor._restore_errors[self.account_ref])
        self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))

    async def test_unchanged_settings_keep_actual_runner_and_do_not_close_it(self):
        with patch.object(self.runner, "close", wraps=self.runner.close) as close, \
             patch.object(self.store, "save_account_settings", wraps=self.store.save_account_settings) as save:
            response = await self.put_settings(orders=True)
        self.assertEqual(200, response.status_code)
        self.assertIs(self.bundle, self.owner.bundle(self.profile))
        self.assertIs(self.runner, self.supervisor._runners[self.account_ref])
        self.assertFalse(self.runner._closing)
        close.assert_not_called()
        save.assert_not_called()
        self.assertEqual({}, self.supervisor._runner_close_tasks)
        with patch.object(self.supervisor._repository, "load_mock_automation_control", side_effect=AssertionError("healthy restore queried control")), \
             patch.object(self.store, "load_account_settings", side_effect=AssertionError("healthy restore queried settings")):
            await self.supervisor._restore_once()

    async def test_cancelled_settings_and_shutdown_waiters_join_one_native_checkpoint_before_lease_release(self):
        entered, release, committed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.runner._save_checkpoint
        def checkpoint(*, force=False):
            if force:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("checkpoint barrier was not released")
            original(force=force)
            if force:
                committed.set()
        stop = self.bundle.runtime.stop
        def release_lease():
            self.assertTrue(committed.is_set(), "old native lease released before final checkpoint")
            return stop()
        with patch.object(self.runner, "_save_checkpoint", side_effect=checkpoint), \
             patch.object(self.runner, "close", wraps=self.runner.close) as close, \
             patch.object(self.bundle.runtime, "stop", side_effect=release_lease):
            request = asyncio.create_task(self.put_settings())
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.assertIsNone(self.owner.bundle(self.profile))
                self.assertEqual((), self.app.state.verified_account_bindings)
                self.assertNotIn(self.account_ref, self.supervisor._runners)
                self.assertFalse(self.bundle.runtime._retired)
                self.assertEqual(self.settings, self.store.load_account_settings(self.scope))
                owned = self.supervisor._runner_close_tasks[self.runner]
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await request
                shutdown = asyncio.create_task(self.supervisor.close())
                await asyncio.sleep(0)
                shutdown.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await shutdown
                self.assertFalse(owned.done())
                self.assertFalse(self.supervisor._close_task.done())
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)
                await asyncio.gather(*tuple(self.owner._settings_tasks), return_exceptions=False)
                await self.supervisor.close()
            self.assertTrue(committed.is_set())
            self.assertTrue(self.bundle.runtime._retired)
            self.assertIsNone(self.runner._task)
            self.assertEqual(1, close.await_count)
            self.assertEqual({}, self.supervisor._runner_close_tasks)
        self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))

    async def test_existing_auto_run_credential_validation_rejection_keeps_current_owner_and_runner(self):
        # Existing credential activation requires UUID run IDs, while automatic
        # execution has a content run ID. Do not silently relax that policy here.
        credentials = self.app.state.credential_runtime
        prepared = await credentials.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1,
                                              {"app_key": "a2", "secret_key": "fake-secret"})
        operation = credentials._operations[prepared["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("FAILED", operation.state)
        self.assertEqual("CREDENTIAL_VALIDATION_FAILED", operation.error_code)
        self.assertIs(self.bundle, self.owner.bundle(self.profile))
        self.assertIs(self.runner, self.supervisor._runners[self.account_ref])
        self.assertFalse(self.runner._closing)
        self.assertFalse(self.bundle.runtime._retired)
        self.assertEqual(1, self.owner.active_revision(self.profile))
        self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
        self.assertEqual({}, self.supervisor._runner_close_tasks)
        self.transport.submit.assert_not_called()

    async def test_precommit_settings_failure_restores_connection_after_old_runner_drains(self):
        with patch.object(self.store, "save_account_settings", side_effect=OSError("controlled precommit failure")):
            response = await self.put_settings()
        self.assertEqual(503, response.status_code)
        self.assertEqual("ACCOUNT_SETTINGS_APPLY_FAILED", response.json()["detail"]["code"])
        self.assertEqual(self.settings, self.store.load_account_settings(self.scope))
        current = self.owner.bundle(self.profile)
        self.assertIsNotNone(current)
        self.assertIsNot(self.bundle, current)
        self.assertIs(self.bundle.client, current.client)
        self.assertTrue(current.runtime._active)
        self.assertTrue(self.runner._closing)
        self.assertIsNone(self.runner._task)
        self.assertNotIn(self.account_ref, self.supervisor._runners)
        self.assertEqual((current.binding,), self.app.state.verified_account_bindings)
        self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))

    async def test_failed_owned_drain_blocks_activation_and_shutdown_without_releasing_old_lease(self):
        original = self.runner.close
        async def failed_ack():
            await original()
            raise OSError("controlled runner drain ACK lost")
        try:
            with patch.object(self.runner, "close", side_effect=failed_ack) as close:
                response = await self.put_settings()
                self.assertEqual(503, response.status_code)
                self.assertNotIn(self.account_ref, self.supervisor._runners)
                self.assertTrue(self.supervisor._runner_close_tasks[self.runner].done())
                self.assertFalse(self.bundle.runtime._retired)
                self.assertEqual(self.settings, self.store.load_account_settings(self.scope))
                with self.assertRaisesRegex(OSError, "drain ACK lost"):
                    await self.supervisor.activate(credential_profile_id=self.profile, account_ref=self.account_ref,
                        spec_id=self.spec.spec_id, expected_settings_revision=self.settings["revision"], credential_revision=1)
                with self.assertRaisesRegex(OSError, "drain ACK lost"):
                    await self.supervisor.close()
                self.assertEqual(1, close.await_count)
                self.assertFalse(self.bundle.runtime._retired)
        finally:
            # Only remove the injected completed fault for fixture disposal.
            # Product recovery/retry policy is deliberately not implemented.
            self.supervisor._runner_close_tasks.clear()
            self.supervisor._close_task = None
        self.transport.submit.assert_not_called()

    async def test_inflight_activation_cannot_publish_a_runner_after_owner_replacement(self):
        from kiwoom_monitor.central_server.mock_automation_runner import MockAutomationRunner
        await self.supervisor.bundle_changed(self.profile, None)
        entered, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        original = MockAutomationRunner.start
        started = []
        async def start(runner):
            started.append(runner)
            await original(runner)
            entered.set()
            await release.wait()
        with patch("kiwoom_monitor.central_server.mock_automation_supervisor.admit_mock_automation"), \
             patch("kiwoom_monitor.central_server.mock_automation_supervisor.record_mock_automation_recovery_from_risk"), \
             patch.object(MockAutomationRunner, "start", new=start):
            activation = asyncio.create_task(self.supervisor.activate(credential_profile_id=self.profile,
                account_ref=self.account_ref, spec_id=self.spec.spec_id,
                expected_settings_revision=self.settings["revision"], credential_revision=1))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                response = await self.put_settings()
                self.assertEqual(200, response.status_code)
            finally:
                release.set()
            with self.assertRaisesRegex(MockAutomationSupervisorError, "PROFILE_RUNTIME_NOT_READY"):
                await activation
        self.assertEqual(1, len(started))
        self.assertTrue(started[0]._closing)
        self.assertIsNone(started[0]._task)
        self.assertNotIn(self.account_ref, self.supervisor._runners)
        self.assertEqual(MockAccountRuntimeMode.MANUAL, self.owner.bundle(self.profile).runtime_mode)
        self.transport.submit.assert_not_called()

    async def test_actual_app_shutdown_joins_inflight_activation_before_closing_owner_and_store(self):
        await self.supervisor.bundle_changed(self.profile, None)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def recovery(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("native activation barrier was not released")
            self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
        with patch("kiwoom_monitor.central_server.mock_automation_supervisor.admit_mock_automation"), \
             patch("kiwoom_monitor.central_server.mock_automation_supervisor.record_mock_automation_recovery_from_risk", side_effect=recovery):
            activation = asyncio.create_task(self.supervisor.activate(credential_profile_id=self.profile,
                account_ref=self.account_ref, spec_id=self.spec.spec_id,
                expected_settings_revision=self.settings["revision"], credential_revision=1))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                shutdown = asyncio.create_task(self.close_app())
                for _ in range(50):
                    if self.supervisor._closing:
                        break
                    await asyncio.sleep(0)
                self.assertTrue(self.supervisor._closing)
                self.assertFalse(shutdown.done())
                self.assertFalse(self.owner._closing)
                self.assertIs(self.bundle, self.owner.bundle(self.profile))
                self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
            finally:
                release.set()
            with self.assertRaisesRegex(MockAutomationSupervisorError, "PROFILE_RUNTIME_NOT_READY"):
                await activation
            await shutdown
        self.assertTrue(self.owner._closing)
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.assertEqual({}, self.supervisor._runners)
        self.assertEqual({}, self.supervisor._runner_close_tasks)
        self.transport.submit.assert_not_called()

    async def test_post_switch_admission_failure_records_original_error_and_closes_current_order_gate(self):
        failure = ValueError("controlled existing approval rejection")
        with patch.object(self.runner, "close", wraps=self.runner.close) as close:
            response = await self.put_settings()
            self.assertEqual(200, response.status_code)
            response = await self.put_settings(orders=True, revision=response.json()["applied_revision"])
            self.assertEqual(200, response.status_code)
            before = self.owner.bundle(self.profile)
            with patch("kiwoom_monitor.central_server.mock_automation_supervisor.admit_mock_automation", side_effect=failure):
                await self.supervisor._restore_once()
            current = self.owner.bundle(self.profile)
            self.assertIsNot(before, current)
            self.assertTrue(before.runtime._retired)
            self.assertFalse(current.runtime._retired)
            self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, current.runtime_mode)
            self.assertFalse(current.runtime._new_orders_enabled)
            self.assertEqual(str(failure), self.supervisor._restore_errors[self.account_ref])
            self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
            self.assertNotIn(self.account_ref, self.supervisor._runners)
            await self.supervisor.close()
            self.assertEqual(1, close.await_count)
            self.assertEqual({}, self.supervisor._runner_close_tasks)
        self.transport.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
