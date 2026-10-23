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
        self._bound_bundle = bundle
        self.now_provider = now_provider
        self.leader_context_source = leader_context_source
        self.started = False
        self.closed = False
        self.status = {
            "account_ref": bundle.account_ref,
            "spec_id": spec.spec_id,
            "state": "WARMUP",
        }

    @property
    def account_ref(self):
        return self._bound_bundle.account_ref

    @property
    def credential_profile_id(self):
        return self._bound_bundle.credential_profile_id

    def is_bound_to(self, bundle):
        return self._bound_bundle is bundle

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
        self.assertTrue(current.is_bound_to(self.owner.current))
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
            # Account cancellation barriers must not race the unrelated live
            # news collector and its shared-store processing workers.
            news_naver_market_enabled=False, news_history_jobs_enabled=False,
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

    async def test_cancelled_activation_joins_native_recovery_before_shutdown_retires_owner(self):
        await self.supervisor.bundle_changed(self.profile, None)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        native_controls = []

        def recovery(repository, runtime, *args, **kwargs):
            # Only eligibility is controlled. Both reads use the actual app's
            # native store and admitted control; no order/provider call occurs.
            native_controls.append(repository.load_mock_automation_control(self.account_ref))
            entered.set()
            try:
                if not release.wait(5):
                    raise AssertionError("cancelled activation native barrier was not released")
                native_controls.append(repository.load_mock_automation_control(self.account_ref))
            finally:
                finished.set()

        with patch("kiwoom_monitor.central_server.mock_automation_supervisor.admit_mock_automation"), \
             patch("kiwoom_monitor.central_server.mock_automation_supervisor.record_mock_automation_recovery_from_risk", side_effect=recovery), \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            activation = asyncio.create_task(self.supervisor.activate(credential_profile_id=self.profile,
                account_ref=self.account_ref, spec_id=self.spec.spec_id,
                expected_settings_revision=self.settings["revision"], credential_revision=1))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                activation.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                # Repeated request cancellation must still leave the one native
                # call protected by the existing account serialization lock.
                activation.cancel()
                await asyncio.sleep(0)
                activation.cancel()
                await asyncio.sleep(0)
                observed = {
                    "activation_pending": not activation.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "runtime_retired": self.bundle.runtime._retired,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                outcome = await asyncio.gather(activation, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "activation_pending": True, "account_lock_held": True,
            "shutdown_pending": True, "owner_closing": False,
            "runtime_retired": False, "store_close_calls": 0,
            "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual([self.control, self.control], native_controls)
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.assertEqual({}, self.supervisor._runners)
        self.assertEqual({}, self.supervisor._runner_close_tasks)
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_cancelled_stop_joins_native_receipt_before_shutdown_retires_owner(self):
        # Isolate the accepted STOP operation from the already-tested Runner
        # drain. The real automatic bundle, runtime, control and app remain.
        await self.supervisor.bundle_changed(self.profile, None)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        repository = self.supervisor._repository
        save_receipt = repository.save_mock_automation_stop_revision
        before_writer, after_writer = [], []

        def blocked_receipt(value):
            before_writer.append((repository.load_mock_automation_control(self.account_ref),
                                  self.bundle.runtime._new_orders_enabled))
            entered.set()
            try:
                if not release.wait(5):
                    raise AssertionError("cancelled STOP native receipt barrier was not released")
                result = save_receipt(value)
                after_writer.append((repository.load_mock_automation_control(self.account_ref),
                                     repository.load_mock_automation_stop_revisions(self.account_ref)))
                return result
            finally:
                finished.set()

        with patch.object(repository, "save_mock_automation_stop_revision", side_effect=blocked_receipt) as save, \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/stop", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
                "reason": "operator stop during request cancellation",
            }))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "runtime_retired": self.bundle.runtime._retired,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if entered.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "request_pending": True, "account_lock_held": True,
            "shutdown_pending": True, "owner_closing": False,
            "runtime_retired": False, "store_close_calls": 0,
            "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(1, save.call_count)
        self.assertEqual(1, len(before_writer))
        self.assertEqual(1, len(after_writer))
        before_control, before_enabled = before_writer[0]
        final_control, receipts = after_writer[0]
        self.assertFalse(before_enabled)
        self.assertEqual(MockAutomationDesiredState.STOPPED, before_control.desired_state)
        self.assertEqual(self.control.control_revision + 1, before_control.control_revision)
        self.assertEqual(before_control, final_control)
        self.assertEqual(1, len(receipts))
        self.assertEqual("operator stop during request cancellation", receipts[0].reason)
        self.assertFalse(receipts[0].new_orders_enabled)
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_cancelled_resume_control_read_retains_owner_until_original_query_finishes(self):
        await self.supervisor.bundle_changed(self.profile, None)
        response = await self.http.post("/api/v1/mock-automation/stop", json={
            "credential_profile_id": self.profile, "account_ref": self.account_ref,
            "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
            "reason": "prepare stopped control for cancelled resume read",
        })
        self.assertEqual(200, response.status_code)
        repository = self.supervisor._repository
        control_before = repository.load_mock_automation_control(self.account_ref)
        receipts_before = repository.load_mock_automation_stop_revisions(self.account_ref)
        self.assertEqual(MockAutomationDesiredState.STOPPED, control_before.desired_state)
        self.assertEqual(1, len(receipts_before))
        load_control = repository.load_mock_automation_control
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        native_results = []

        def blocked_control(account_ref):
            # The actual thread call is owned while paused before the original
            # native query; no DB connection is claimed to be blocked here.
            entered.set()
            try:
                if not release.wait(5):
                    raise AssertionError("cancelled resume control-read barrier was not released")
                control = load_control(account_ref)
                native_results.append((control,
                    repository.load_mock_automation_stop_revisions(account_ref),
                    self.bundle.runtime._new_orders_enabled))
                return control
            finally:
                finished.set()

        with patch.object(repository, "load_mock_automation_control", side_effect=blocked_control) as load, \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/resume", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": control_before.control_revision,
                "expected_settings_revision": self.settings["revision"], "credential_revision": 1,
                "reason": "cancel before resume control validation",
            }))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "runtime_retired": self.bundle.runtime._retired,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if entered.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "request_pending": True, "account_lock_held": True,
            "shutdown_pending": True, "owner_closing": False,
            "runtime_retired": False, "store_close_calls": 0,
            "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(1, load.call_count)
        self.assertEqual([(control_before, receipts_before, False)], native_results)
        self.assertEqual({}, self.supervisor._runners)
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_cancelled_resume_spec_read_retains_owner_until_original_query_finishes(self):
        await self.supervisor.bundle_changed(self.profile, None)
        response = await self.http.post("/api/v1/mock-automation/stop", json={
            "credential_profile_id": self.profile, "account_ref": self.account_ref,
            "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
            "reason": "prepare stopped control for cancelled resume spec read",
        })
        self.assertEqual(200, response.status_code)
        repository = self.supervisor._repository
        control_before = repository.load_mock_automation_control(self.account_ref)
        spec_before = repository.load_mock_automation_spec(self.account_ref, self.spec.spec_id)
        receipts_before = repository.load_mock_automation_stop_revisions(self.account_ref)
        self.assertEqual(MockAutomationDesiredState.STOPPED, control_before.desired_state)
        self.assertEqual(1, len(receipts_before))
        load_spec = repository.load_mock_automation_spec
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        native_results = []

        def blocked_spec(account_ref, spec_id):
            # Control/revision validation has already passed. This thread call
            # pauses before the original query, without holding a DB connection.
            entered.set()
            try:
                if not release.wait(5):
                    raise AssertionError("cancelled resume spec-read barrier was not released")
                spec = load_spec(account_ref, spec_id)
                native_results.append((spec, repository.load_mock_automation_control(account_ref),
                    repository.load_mock_automation_stop_revisions(account_ref),
                    self.bundle.runtime._new_orders_enabled))
                return spec
            finally:
                finished.set()

        with patch.object(repository, "load_mock_automation_spec", side_effect=blocked_spec) as load, \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/resume", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": control_before.control_revision,
                "expected_settings_revision": self.settings["revision"], "credential_revision": 1,
                "reason": "cancel during resume spec read",
            }))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "runtime_retired": self.bundle.runtime._retired,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if entered.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "request_pending": True, "account_lock_held": True,
            "shutdown_pending": True, "owner_closing": False,
            "runtime_retired": False, "store_close_calls": 0,
            "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(1, load.call_count)
        self.assertEqual([(spec_before, control_before, receipts_before, False)], native_results)
        self.assertEqual({}, self.supervisor._runners)
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_cancelled_resume_admission_read_retains_owner_until_original_query_finishes(self):
        await self.supervisor.bundle_changed(self.profile, None)
        response = await self.http.post("/api/v1/mock-automation/stop", json={
            "credential_profile_id": self.profile, "account_ref": self.account_ref,
            "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
            "reason": "prepare stopped control for cancelled resume admission read",
        })
        self.assertEqual(200, response.status_code)
        repository = self.supervisor._repository
        control_before = repository.load_mock_automation_control(self.account_ref)
        spec_before = repository.load_mock_automation_spec(self.account_ref, self.spec.spec_id)
        admission_before = repository.load_mock_automation_admission_for_spec(self.account_ref, self.spec.spec_id)
        receipts_before = repository.load_mock_automation_stop_revisions(self.account_ref)
        self.assertEqual(MockAutomationDesiredState.STOPPED, control_before.desired_state)
        self.assertEqual(1, len(receipts_before))
        load_admission = repository.load_mock_automation_admission_for_spec
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        native_results = []

        def blocked_admission(account_ref, spec_id):
            # Existing control/spec drains have completed. This thread call
            # waits before the original admission query, not inside a DB connection.
            entered.set()
            try:
                if not release.wait(5):
                    raise AssertionError("cancelled resume admission-read barrier was not released")
                admission = load_admission(account_ref, spec_id)
                native_results.append((admission,
                    repository.load_mock_automation_spec(account_ref, spec_id),
                    repository.load_mock_automation_control(account_ref),
                    repository.load_mock_automation_stop_revisions(account_ref),
                    self.bundle.runtime._new_orders_enabled))
                return admission
            finally:
                finished.set()

        with patch.object(repository, "load_mock_automation_admission_for_spec", side_effect=blocked_admission) as load, \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/resume", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": control_before.control_revision,
                "expected_settings_revision": self.settings["revision"], "credential_revision": 1,
                "reason": "cancel during resume admission read",
            }))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "runtime_retired": self.bundle.runtime._retired,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if entered.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "request_pending": True, "account_lock_held": True,
            "shutdown_pending": True, "owner_closing": False,
            "runtime_retired": False, "store_close_calls": 0,
            "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(1, load.call_count)
        self.assertEqual([(admission_before, spec_before, control_before, receipts_before, False)], native_results)
        self.assertEqual({}, self.supervisor._runners)
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_cancelled_resume_risk_read_retains_owner_until_original_query_finishes(self):
        response = await self.http.post("/api/v1/mock-automation/stop", json={
            "credential_profile_id": self.profile, "account_ref": self.account_ref,
            "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
            "reason": "prepare stopped control for cancelled resume risk read",
        })
        self.assertEqual(200, response.status_code)
        repository = self.supervisor._repository
        control_before = repository.load_mock_automation_control(self.account_ref)
        spec_before = repository.load_mock_automation_spec(self.account_ref, self.spec.spec_id)
        admission_before = repository.load_mock_automation_admission_for_spec(self.account_ref, self.spec.spec_id)
        receipts_before = repository.load_mock_automation_stop_revisions(self.account_ref)
        self.assertEqual(MockAutomationDesiredState.STOPPED, control_before.desired_state)
        self.assertEqual(1, len(receipts_before))
        # Existing HTTP settings replacement drains AUTO and constructs MANUAL.
        # Re-enable transport through that same caller before the real resume.
        response = await self.put_settings()
        self.assertEqual(200, response.status_code)
        response = await self.put_settings(orders=True, revision=response.json()["applied_revision"])
        self.assertEqual(200, response.status_code)
        settings_revision = response.json()["applied_revision"]
        manual = self.owner.bundle(self.profile)
        self.assertEqual(MockAccountRuntimeMode.MANUAL, manual.runtime_mode)
        self.assertIsNone(manual.automation_context)
        self.assertTrue(self.bundle.runtime._retired)
        load_risk = repository.load_latest_mock_automation_risk
        refresh_recovery = type(manual.monitor).refresh_recovery
        refreshed = []
        target_bundles = []
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        native_results = []

        async def observed_refresh(monitor):
            recovery = await refresh_recovery(monitor)
            refreshed.append((monitor, recovery, monitor.latest_automation_risk_cut))
            return recovery

        def blocked_risk(account_ref):
            # This supervisor repository is distinct from the monitor's reader.
            # The original target query waits after the real broker recovery.
            target = self.owner.bundle(self.profile)
            target_bundles.append(target)
            entered.set()
            try:
                if not release.wait(5):
                    raise AssertionError("cancelled resume risk-read barrier was not released")
                risk = load_risk(account_ref)
                native_results.append((risk,
                    repository.load_mock_automation_spec(account_ref, self.spec.spec_id),
                    repository.load_mock_automation_admission_for_spec(account_ref, self.spec.spec_id),
                    repository.load_mock_automation_control(account_ref),
                    repository.load_mock_automation_stop_revisions(account_ref),
                    target.runtime._new_orders_enabled))
                return risk
            finally:
                finished.set()

        with patch.object(type(manual.monitor), "refresh_recovery", new=observed_refresh), \
             patch.object(repository, "load_latest_mock_automation_risk", side_effect=blocked_risk) as load, \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/resume", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": control_before.control_revision,
                "expected_settings_revision": settings_revision, "credential_revision": 1,
                "reason": "cancel during resume risk read",
            }))
            shutdown = None
            current = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                current = target_bundles[0]
                self.assertIs(current, self.owner.bundle(self.profile))
                self.assertIsNot(current, manual)
                self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, current.runtime_mode)
                self.assertEqual(control_before.control_revision, current.automation_context.control_revision)
                self.assertIsNot(repository, current.monitor._risk_repository)
                self.assertIs(refreshed[-1][0], current.monitor)
                self.assertIsNotNone(refreshed[-1][2])
                target_risk = refreshed[-1][2]["risk"]
                self.assertEqual(current.run_id, target_risk.execution_run_id)
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "runtime_retired": current.runtime._retired,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if entered.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "request_pending": True, "account_lock_held": True,
            "shutdown_pending": True, "owner_closing": False,
            "runtime_retired": False, "store_close_calls": 0,
            "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(1, load.call_count)
        self.assertEqual([(target_risk, spec_before, admission_before, control_before, receipts_before, False)], native_results)
        self.assertEqual({}, self.supervisor._runners)
        self.assertTrue(current.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def _assert_cancelled_resume_native_return_retains_owner(self, *, final_cas):
        from kiwoom_monitor.application.mock_automation_admission import (
            MockAutomationControl, build_mock_automation_lease_receipt,
        )
        from kiwoom_monitor.application.mock_automation_recovery import (
            MockAutomationRecoveryDecision, mock_account_recovery_fingerprint,
        )
        from kiwoom_monitor.application.mock_automation_risk import MockAutomationRiskSnapshot
        from kiwoom_monitor.central_server import mock_automation_supervisor as supervisor_module

        repository = self.supervisor._repository
        # Complete only these tests' previously approved fixture lineage. The
        # original builder/store validate the immutable receipt; this does not
        # exercise or replace admission issuance.
        receipt = build_mock_automation_lease_receipt(
            self.admission, admitted_at=self.admission.requested_at,
        )
        self.assertTrue(repository.save_mock_automation_lease_receipt(receipt))
        self.assertEqual((receipt,), repository.load_mock_automation_lease_receipts(self.account_ref))
        self.assertEqual(receipt, repository.load_mock_automation_lease_for_admission(
            self.account_ref, self.admission.admission_id,
        ))
        response = await self.http.post("/api/v1/mock-automation/stop", json={
            "credential_profile_id": self.profile, "account_ref": self.account_ref,
            "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
            "reason": "prepare stopped control for cancelled resume native return",
        })
        self.assertEqual(200, response.status_code)
        control_before = repository.load_mock_automation_control(self.account_ref)
        receipts_before = repository.load_mock_automation_stop_revisions(self.account_ref)
        self.assertEqual(MockAutomationDesiredState.STOPPED, control_before.desired_state)
        self.assertEqual(1, len(receipts_before))
        response = await self.put_settings()
        self.assertEqual(200, response.status_code)
        response = await self.put_settings(orders=True, revision=response.json()["applied_revision"])
        self.assertEqual(200, response.status_code)
        settings_revision = response.json()["applied_revision"]
        manual = self.owner.bundle(self.profile)
        self.assertEqual(MockAccountRuntimeMode.MANUAL, manual.runtime_mode)
        self.assertIsNone(manual.automation_context)
        refresh_recovery = type(manual.monitor).refresh_recovery
        refreshed = []
        target_bundles, native_results, native_errors = [], [], []
        dispatched, accepted, release, finished = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event()
        )
        self.addCleanup(release.set)
        target_name = "resume_mock_automation" if final_cas else "record_mock_automation_recovery_from_risk"
        original = getattr(supervisor_module, target_name)

        async def observed_refresh(monitor):
            recovery = await refresh_recovery(monitor)
            refreshed.append((monitor, recovery, monitor.latest_automation_risk_cut))
            return recovery

        def held_native_return(*args, **kwargs):
            target_bundles.append(self.owner.bundle(self.profile))
            dispatched.set()
            try:
                result = original(*args, **kwargs)
                native_results.append((result,
                    repository.load_latest_mock_automation_recovery(self.account_ref, self.admission.admission_id),
                    repository.load_mock_automation_control(self.account_ref),
                    repository.load_mock_automation_spec(self.account_ref, self.spec.spec_id),
                    repository.load_mock_automation_admission_for_spec(self.account_ref, self.spec.spec_id),
                    repository.load_mock_automation_stop_revisions(self.account_ref),
                    repository.load_mock_automation_lease_for_admission(self.account_ref, self.admission.admission_id)))
                # The original native body has completed, including its actual
                # immutable write or CAS. Only the worker's return is held; no
                # database connection/transaction or runtime guard is blocked.
                accepted.set()
                if not release.wait(5):
                    raise AssertionError("cancelled resume native-return barrier was not released")
                return result
            except BaseException as error:
                native_errors.append(repr(error))
                raise
            finally:
                finished.set()

        with patch.object(type(manual.monitor), "refresh_recovery", new=observed_refresh), \
             patch.object(supervisor_module, target_name, side_effect=held_native_return) as target, \
             patch.object(repository, "save_mock_automation_recovery_decision", wraps=repository.save_mock_automation_recovery_decision) as recovery_write, \
             patch.object(repository, "save_mock_automation_control", wraps=repository.save_mock_automation_control) as control_cas, \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/resume", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": control_before.control_revision,
                "expected_settings_revision": settings_revision, "credential_revision": 1,
                "reason": "cancel during resume native return",
            }))
            shutdown = None
            try:
                if final_cas:
                    self.assertTrue(await asyncio.to_thread(dispatched.wait, 3),
                        f"target dispatched={dispatched.is_set()}, errors={native_errors}")
                self.assertTrue(await asyncio.to_thread(accepted.wait, 3),
                    f"target dispatched={dispatched.is_set()}, accepted={accepted.is_set()}, errors={native_errors}")
                current = target_bundles[0]
                self.assertIs(current, self.owner.bundle(self.profile))
                self.assertIsNot(current, manual)
                self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, current.runtime_mode)
                self.assertEqual(control_before.control_revision, current.automation_context.control_revision)
                self.assertIs(refreshed[-1][0], current.monitor)
                risk = refreshed[-1][2]["risk"]
                self.assertIsInstance(risk, MockAutomationRiskSnapshot)
                self.assertEqual(risk, repository.load_latest_mock_automation_risk(self.account_ref))
                self.assertEqual(current.run_id, risk.execution_run_id)
                result, decision, control, spec, admission, stop_receipts, stored_receipt = native_results[0]
                self.assertIsInstance(decision, MockAutomationRecoveryDecision)
                self.assertEqual(receipt.receipt_id, decision.lease_receipt_id)
                self.assertEqual(self.admission.admission_id, decision.admission_id)
                self.assertEqual(self.spec.spec_id, decision.spec_id)
                self.assertEqual(risk.snapshot_id, decision.risk_snapshot_id)
                self.assertEqual(risk.reconciliation_revision, decision.risk_reconciliation_revision)
                self.assertEqual(mock_account_recovery_fingerprint(refreshed[-1][1]), decision.recovery_fingerprint)
                self.assertEqual((decision,), repository.load_mock_automation_recovery_decisions(self.account_ref))
                self.assertEqual((self.spec, self.admission, receipts_before, receipt),
                                 (spec, admission, stop_receipts, stored_receipt))
                if final_cas:
                    self.assertIsInstance(result, MockAutomationControl)
                    self.assertEqual(result, control)
                    self.assertEqual(MockAutomationDesiredState.RUNNING, control.desired_state)
                    self.assertEqual(control_before.control_revision + 1, control.control_revision)
                    self.assertEqual("cancel during resume native return", control.reason)
                else:
                    self.assertEqual(result, decision)
                    self.assertEqual(control_before, control)
                self.assertEqual(1, target.call_count)
                self.assertEqual(1, recovery_write.call_count)
                self.assertEqual(int(final_cas), control_cas.call_count)
                self.assertFalse(current.runtime._new_orders_enabled)
                self.assertEqual({}, self.supervisor._runners)
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "runtime_retired": current.runtime._retired,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if dispatched.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "request_pending": True, "account_lock_held": True,
            "shutdown_pending": True, "owner_closing": False,
            "runtime_retired": False, "store_close_calls": 0,
            "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual([], native_errors)
        self.assertEqual(1, len(native_results))
        self.assertEqual({}, self.supervisor._runners)
        self.assertTrue(current.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_cancelled_resume_recovery_record_retains_owner_until_original_native_finishes(self):
        await self._assert_cancelled_resume_native_return_retains_owner(final_cas=False)

    async def test_cancelled_resume_control_cas_retains_owner_until_original_native_finishes(self):
        await self._assert_cancelled_resume_native_return_retains_owner(final_cas=True)

    async def test_cancelled_mode_switch_settings_read_retains_owner_until_original_query_finishes(self):
        response = await self.put_settings()
        self.assertEqual(200, response.status_code)
        response = await self.put_settings(orders=True, revision=response.json()["applied_revision"])
        self.assertEqual(200, response.status_code)
        settings_revision = response.json()["applied_revision"]
        manual = self.owner.bundle(self.profile)
        self.assertEqual(MockAccountRuntimeMode.MANUAL, manual.runtime_mode)
        self.assertNotIn(self.account_ref, self.supervisor._runners)
        repository = self.supervisor._repository
        control_before = repository.load_mock_automation_control(self.account_ref)
        settings_before = self.store.load_account_settings(self.scope)
        load_settings = self.store.load_account_settings
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        native_results, native_errors = [], []

        def held_settings_return(scope):
            try:
                result = load_settings(scope)
                native_results.append(result)
                # The original native query has completed. Hold its return to
                # the existing mode-switch await; no open SQL connection is held.
                entered.set()
                if not release.wait(5):
                    raise AssertionError("cancelled mode-switch settings-return barrier was not released")
                native_results.append(repository.load_mock_automation_control(self.account_ref))
                return result
            except BaseException as error:
                native_errors.append(repr(error))
                raise
            finally:
                finished.set()

        with patch.object(self.store, "load_account_settings", side_effect=held_settings_return) as load, \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/start", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_settings_revision": settings_revision,
                "credential_revision": 1,
            }))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3), str(native_errors))
                self.assertEqual([settings_before], native_results)
                self.assertIs(manual, self.owner.bundle(self.profile))
                self.assertNotIn(self.profile, self.owner._pending_profiles)
                self.assertFalse(self.owner._account_locks[self.account_ref].locked())
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "runtime_retired": manual.runtime._retired,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if entered.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "request_pending": True, "account_lock_held": True,
            "shutdown_pending": True, "owner_closing": False,
            "runtime_retired": False, "store_close_calls": 0,
            "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(1, load.call_count)
        self.assertEqual([settings_before, control_before], native_results)
        self.assertEqual([], native_errors)
        self.assertEqual(MockAutomationDesiredState.RUNNING, control_before.desired_state)
        self.assertEqual({}, self.supervisor._runners)
        self.assertTrue(manual.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def _assert_cancelled_inner_mode_switch_read_retains_owner(self, *, after_new_start):
        if after_new_start:
            response = await self.http.post("/api/v1/mock-automation/stop", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
                "reason": "prepare stopped control for inner mode-switch read",
            })
            self.assertEqual(200, response.status_code)
        response = await self.put_settings()
        self.assertEqual(200, response.status_code)
        response = await self.put_settings(orders=True, revision=response.json()["applied_revision"])
        self.assertEqual(200, response.status_code)
        settings_revision = response.json()["applied_revision"]
        manual = self.owner.bundle(self.profile)
        self.assertEqual(MockAccountRuntimeMode.MANUAL, manual.runtime_mode)
        self.assertIs(manual, self.owner._contexts[self.profile])
        self.assertNotIn(self.account_ref, self.supervisor._runners)
        repository = self.supervisor._repository
        control_before = repository.load_mock_automation_control(self.account_ref)
        receipts_before = repository.load_mock_automation_stop_revisions(self.account_ref)
        settings_before = self.store.load_account_settings(self.scope)
        self.assertEqual(MockAutomationDesiredState.STOPPED if after_new_start
                         else MockAutomationDesiredState.RUNNING, control_before.desired_state)
        load_settings = self.store.load_account_settings
        close_bundle = type(manual).close
        target_read = 3 if after_new_start else 2
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        reads, native_results, native_errors, closed = [], [], [], []

        def held_inner_settings_return(scope):
            result = load_settings(scope)
            reads.append(result)
            if len(reads) != target_read:
                return result
            try:
                # The selected original query has completed. Hold its return,
                # without claiming an open SQL connection or backend wait.
                entered.set()
                if not release.wait(5):
                    raise AssertionError("inner mode-switch settings-return barrier was not released")
                native_results.append((repository.load_mock_automation_control(self.account_ref),
                    repository.load_mock_automation_stop_revisions(self.account_ref)))
                return result
            except BaseException as error:
                native_errors.append(repr(error))
                raise
            finally:
                finished.set()

        async def observed_close(bundle, *, close_broker=True):
            closed.append((bundle, finished.is_set()))
            await close_bundle(bundle, close_broker=close_broker)

        with patch.object(self.store, "load_account_settings", side_effect=held_inner_settings_return) as load, \
             patch.object(type(manual), "close", new=observed_close), \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            payload = {
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_settings_revision": settings_revision,
                "credential_revision": 1,
            }
            if after_new_start:
                payload.update(expected_control_revision=control_before.control_revision,
                               reason="cancel during started context settings read")
            request = asyncio.create_task(self.http.post(
                "/api/v1/mock-automation/resume" if after_new_start else "/api/v1/mock-automation/start",
                json=payload,
            ))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3), str(native_errors))
                self.assertEqual([settings_before] * target_read, reads)
                context = self.owner._contexts[self.profile]
                self.assertIsNone(self.owner.bundle(self.profile))
                self.assertTrue(manual.runtime._retired)
                self.assertTrue(self.owner._account_locks[self.account_ref].locked())
                self.assertIn(self.profile, self.owner._pending_profiles)
                if after_new_start:
                    self.assertIsNot(manual, context)
                    self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, context.runtime_mode)
                    self.assertEqual(manual.account_ref, context.account_ref)
                    self.assertEqual(self.admission.execution_run_id, context.run_id)
                    self.assertEqual(control_before.control_revision, context.automation_context.control_revision)
                    self.assertTrue(context._start_task.done())
                    self.assertTrue(context.monitor.is_started)
                    self.assertTrue(context.runtime._active)
                    self.assertFalse(context.runtime._retired)
                    self.assertFalse(context.runtime._new_orders_enabled)
                else:
                    self.assertIs(manual, context)
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "supervisor_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "owner_lock_held": self.owner._account_locks[self.account_ref].locked(),
                    "profile_pending": self.profile in self.owner._pending_profiles,
                    "shutdown_pending": not shutdown.done(),
                    "owner_closing": self.owner._closing,
                    "context_retained": self.owner._contexts[self.profile] is context,
                    "context_retired": context.runtime._retired,
                    "bundle_unpublished": self.owner.bundle(self.profile) is None,
                    "store_close_calls": close_store.call_count,
                    "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                if not entered.is_set() and not request.done():
                    request.cancel()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if entered.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))

        self.assertEqual({
            "request_pending": True, "supervisor_lock_held": True,
            "owner_lock_held": True, "profile_pending": True,
            "shutdown_pending": True, "owner_closing": False,
            "context_retained": True, "context_retired": not after_new_start,
            "bundle_unpublished": True, "store_close_calls": 0, "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(target_read, load.call_count)
        self.assertEqual([settings_before] * target_read, reads)
        self.assertEqual([(control_before, receipts_before)], native_results)
        self.assertEqual([], native_errors)
        self.assertIs(context, self.owner._contexts[self.profile])
        self.assertTrue(context.runtime._retired)
        self.assertFalse(self.owner._account_locks[self.account_ref].locked())
        self.assertNotIn(self.profile, self.owner._pending_profiles)
        if after_new_start:
            self.assertEqual([(context, True)], [row for row in closed if row[0] is context])
            self.assertFalse(context.monitor.is_started)
        else:
            self.assertTrue(all(bundle is manual for bundle, _ in closed))
        self.assertEqual({}, self.supervisor._runners)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_cancelled_mode_switch_post_close_read_retains_locks_until_native_finishes(self):
        await self._assert_cancelled_inner_mode_switch_read_retains_owner(after_new_start=False)

    async def test_cancelled_mode_switch_started_context_read_retains_owner_until_native_finishes(self):
        await self._assert_cancelled_inner_mode_switch_read_retains_owner(after_new_start=True)

    async def test_stop_status_keeps_fresh_query_failure_and_projects_actual_replacement_on_loop(self):
        repository = self.supervisor._repository
        original = repository.load_mock_automation_control
        failure_reason = "STOP committed before final status query failure"
        failed_queries = []

        def fail_final_query(account_ref):
            control = original(account_ref)
            if control.reason == failure_reason:
                failed_queries.append(control)
                raise RuntimeError("controlled final status read failure")
            return control

        with patch.object(repository, "load_mock_automation_control", side_effect=fail_final_query):
            response = await self.http.post("/api/v1/mock-automation/stop", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
                "reason": failure_reason,
            })
        self.assertEqual(409, response.status_code)
        self.assertEqual("controlled final status read failure", response.json()["detail"])
        stopped = original(self.account_ref)
        self.assertEqual([stopped], failed_queries)
        self.assertEqual(MockAutomationDesiredState.STOPPED, stopped.desired_state)
        self.assertEqual(self.control.control_revision + 1, stopped.control_revision)
        self.assertEqual(1, len(repository.load_mock_automation_stop_revisions(self.account_ref)))
        self.assertFalse(self.bundle.runtime._new_orders_enabled)

        loop_thread = threading.get_ident()
        entered, replaced, release = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        reason = "fresh STOP status after actual owner replacement"
        final_queries, projections, heartbeat = [], [], []
        stop_heartbeat = asyncio.Event()
        project = self.supervisor._status_from_control

        async def beat():
            while not stop_heartbeat.is_set():
                heartbeat.append(None)
                await asyncio.sleep(0.005)

        def release_independently():
            entered.wait(3)
            replaced.wait(3)
            release.set()

        def hold_final_query(account_ref):
            control = original(account_ref)
            if control.reason != reason:
                return control
            final_queries.append((control, threading.get_ident(), len(heartbeat)))
            entered.set()
            if not release.wait(5):
                raise AssertionError("replacement status query was not independently released")
            return control

        def observed_projection(account_ref, *, credential_profile_id, control):
            projections.append((threading.get_ident(), self.owner.bundle(self.profile),
                                self.supervisor._runners.get(self.account_ref)))
            return project(account_ref, credential_profile_id=credential_profile_id, control=control)

        releaser = threading.Thread(target=release_independently, daemon=True)
        heartbeat_task = asyncio.create_task(beat())
        releaser.start()
        with patch.object(repository, "load_mock_automation_control", side_effect=hold_final_query), \
             patch.object(self.supervisor, "_status_from_control", side_effect=observed_projection):
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/stop", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": stopped.control_revision,
                "reason": reason,
            }))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                self.assertNotEqual(loop_thread, final_queries[0][1])
                self.assertTrue(self.supervisor._locks[self.account_ref].locked())
                settings_response = await self.put_settings()
                self.assertEqual(200, settings_response.status_code)
                current = self.owner.bundle(self.profile)
                self.assertIsNot(self.bundle, current)
                self.assertTrue(self.runner._closing)
                self.assertIsNone(self.runner._task)
                self.assertNotIn(self.account_ref, self.supervisor._runners)
                self.assertTrue(self.bundle.runtime._retired)
                self.assertGreater(len(heartbeat), final_queries[0][2])
                replaced.set()
                response = await request
            finally:
                replaced.set()
                release.set()
                await asyncio.gather(request, return_exceptions=True)
                stop_heartbeat.set()
                await heartbeat_task
                await asyncio.to_thread(releaser.join, 3)
        self.assertFalse(releaser.is_alive())
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, len(final_queries))
        latest = original(self.account_ref)
        self.assertEqual(stopped.control_revision + 1, latest.control_revision)
        self.assertEqual(final_queries[0][0].to_dict(), response.json()["control"])
        self.assertEqual(latest.to_dict(), response.json()["control"])
        self.assertEqual("MANUAL", response.json()["runtime_mode"])
        self.assertIsNone(response.json()["runner"])
        self.assertEqual([(loop_thread, current, None)], projections)
        self.assertEqual(2, len(repository.load_mock_automation_stop_revisions(self.account_ref)))
        self.assertFalse(self.bundle.runtime._new_orders_enabled)
        self.assertIsNone(current.gateway)
        status = await self.http.get(f"/api/v1/mock-automation/accounts/{self.account_ref}",
                                     params={"credential_profile_id": self.profile})
        self.assertEqual(200, status.status_code)
        self.assertEqual(response.json(), status.json())
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_cancelled_stop_final_status_query_retains_native_owner_until_shutdown(self):
        repository = self.supervisor._repository
        original = repository.load_mock_automation_control
        reason = "cancel while final STOP status native query returns"
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        final_queries, receipts = [], []

        def held_final_query(account_ref):
            control = original(account_ref)
            if control.reason != reason:
                return control
            final_queries.append(control)
            entered.set()
            try:
                if not release.wait(5):
                    raise AssertionError("cancelled final STOP status query was not released")
                receipts.extend(repository.load_mock_automation_stop_revisions(self.account_ref))
                return control
            finally:
                finished.set()

        with patch.object(repository, "load_mock_automation_control", side_effect=held_final_query), \
             patch.object(self.supervisor, "_status_from_control", wraps=self.supervisor._status_from_control) as project, \
             patch.object(self.store, "close", wraps=self.store.close) as close_store:
            request = asyncio.create_task(self.http.post("/api/v1/mock-automation/stop", json={
                "credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_control_revision": self.control.control_revision,
                "reason": reason,
            }))
            shutdown = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                control = final_queries[0]
                self.assertEqual(MockAutomationDesiredState.STOPPED, control.desired_state)
                self.assertEqual(self.control.control_revision + 1, control.control_revision)
                self.assertEqual(32400, int(control.changed_at.utcoffset().total_seconds()))
                self.assertFalse(self.bundle.runtime._new_orders_enabled)
                request.cancel()
                await asyncio.sleep(0)
                shutdown = asyncio.create_task(self.close_app())
                try:
                    await asyncio.wait_for(asyncio.shield(shutdown), timeout=0.2)
                except TimeoutError:
                    pass
                request.cancel()
                await asyncio.sleep(0)
                request.cancel()
                await asyncio.sleep(0)
                observed = {
                    "request_pending": not request.done(),
                    "account_lock_held": self.supervisor._locks[self.account_ref].locked(),
                    "shutdown_pending": not shutdown.done(), "owner_closing": self.owner._closing,
                    "runtime_retired": self.bundle.runtime._retired,
                    "store_close_calls": close_store.call_count, "native_pending": not finished.is_set(),
                }
            finally:
                release.set()
                if not entered.is_set() and not request.done():
                    request.cancel()
                outcome = await asyncio.gather(request, return_exceptions=True)
                if shutdown is not None:
                    await shutdown
                if entered.is_set():
                    self.assertTrue(await asyncio.to_thread(finished.wait, 3))
        self.assertEqual({
            "request_pending": True, "account_lock_held": True, "shutdown_pending": True,
            "owner_closing": False, "runtime_retired": False, "store_close_calls": 0, "native_pending": True,
        }, observed)
        self.assertIsInstance(outcome[0], asyncio.CancelledError)
        self.assertEqual(1, len(final_queries))
        self.assertEqual(1, len(receipts))
        self.assertEqual(reason, receipts[0].reason)
        self.assertFalse(receipts[0].new_orders_enabled)
        project.assert_not_called()
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual("completed", self.app.state.shutdown_status["state"])
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

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
