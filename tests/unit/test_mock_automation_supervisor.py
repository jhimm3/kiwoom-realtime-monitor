from __future__ import annotations

from tests.unit.test_mock_automation_runner import _prepared_runner

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
                    with TestClient(app, headers={"X-Request-ID": "mock-automation-http-contract-v1"}) as client:
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

    def refresh_automation_control(self, control):
        if hasattr(self.automation_context, "__dataclass_fields__"):
            self.automation_context = replace(self.automation_context, control_revision=control.control_revision)
        else:
            self.automation_context = SimpleNamespace(**{
                **vars(self.automation_context), "control_revision": control.control_revision,
            })


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


class _Episode:
    def __init__(self, store, bundle, spec, *, price_source=None, now_provider=None, leader_context_source=None):
        self._bound_bundle = bundle
        self.now_provider = now_provider
        self.leader_context_source = leader_context_source
        self.prepared = False
        self.status = {"account_ref": bundle.account_ref, "spec_id": spec.spec_id, "state": "WARMUP"}

    @property
    def account_ref(self):
        return self._bound_bundle.account_ref

    @property
    def credential_profile_id(self):
        return self._bound_bundle.credential_profile_id

    def is_bound_to(self, bundle):
        return self._bound_bundle is bundle

    def prepare_restore(self):
        self.prepared = True


class _Runner:
    def __init__(self, episode, *, monitor=None, poll_seconds):
        self.episode = episode
        self.started = False
        self.attached = False
        self.closed = False

    @property
    def status(self):
        return self.episode.status

    def attach_inputs(self):
        if not self.episode.prepared:
            raise AssertionError("runner attached before native preparation")
        self.attached = True

    async def start(self):
        if not self.attached:
            raise AssertionError("runner started before input attachment")
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
            "kiwoom_monitor.central_server.mock_automation_runner.MockAutomationRunner",
            _Runner,
        )
        episode_patch = patch(
            "kiwoom_monitor.central_server.mock_automation_episode.MockAutomationEpisode", _Episode,
        )
        self.addCleanup(episode_patch.stop)
        episode_patch.start()
        self.addCleanup(repository_patch.stop)
        self.addCleanup(runner_patch.stop)
        repository_patch.start()
        runner_patch.start()
        self.supervisor = MockAutomationSupervisor(object(), self.owner)

    async def asyncTearDown(self):
        await self.supervisor.close()

    async def _retain_real_closed_runner(self):
        from tests.unit import test_mock_automation_runner as native
        case = native.MockAutomationRunnerTests("test_checkpoint_from_other_spec_is_never_reused")
        case.setUp()
        self.addCleanup(case.doCleanups)
        history = native._prepared_runner(case.store, case.bundle, case.spec, now_provider=lambda: native.NOW)
        self.assertIs(type(history), native.MockAutomationRunner)
        self.assertFalse(hasattr(history, "account_ref"))
        self.assertIsNot(history, history.episode)
        self.assertEqual(ACCOUNT_REF, history.episode.account_ref)
        closed = asyncio.create_task(history.close())
        await closed
        self.assertTrue(closed.done())
        self.supervisor._runner_close_tasks[history] = closed
        return history

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
            resume.return_value = _Control(MockAutomationDesiredState.RUNNING, 6)
            history = await self._retain_real_closed_runner()
            self.assertIn(history, self.supervisor._runner_close_tasks)
            await self.supervisor.resume(
                credential_profile_id=PROFILE_ID, account_ref=ACCOUNT_REF,
                spec_id=SPEC_ID, expected_control_revision=5, reason="operator resume",
                expected_settings_revision=2, credential_revision=3,
            )
            self.assertNotIn(history, self.supervisor._runner_close_tasks)
        self.owner.current.monitor.refresh_recovery.assert_awaited_once()
        recover.assert_called_once()
        resume.assert_called_once()
        self.assertIsNotNone(self.supervisor.status(
            ACCOUNT_REF, credential_profile_id=PROFILE_ID,
        )["runner"])

    async def test_stale_cached_runner_is_closed_before_same_spec_is_reactivated(self):
        history = await self._retain_real_closed_runner()
        self.assertIn(history, self.supervisor._runner_close_tasks)
        with patch("kiwoom_monitor.central_server.mock_automation_supervisor.admit_mock_automation"), \
             patch("kiwoom_monitor.central_server.mock_automation_supervisor.record_mock_automation_recovery_from_risk"):
            arguments = dict(credential_profile_id=PROFILE_ID, account_ref=ACCOUNT_REF, spec_id=SPEC_ID,
                             expected_settings_revision=2, credential_revision=3)
            await self.supervisor.activate(**arguments)
            self.assertNotIn(history, self.supervisor._runner_close_tasks)
            old = self.supervisor._runners[ACCOUNT_REF]
            self.owner.current = _Bundle()
            await self.supervisor.activate(**arguments)
        current = self.supervisor._runners[ACCOUNT_REF]
        self.assertTrue(old.closed)
        self.assertIsNot(old, current)
        self.assertTrue(current.episode.is_bound_to(self.owner.current))
        self.assertEqual(2, len(self.owner.switch_calls))
        self.assertEqual({}, self.supervisor._runner_close_tasks)

    async def test_cancelled_actual_drain_owner_is_retained_as_failure_for_activation_and_close(self):
        entered = asyncio.Event()
        async def blocked_close():
            entered.set()
            await asyncio.Event().wait()
        runner = _Runner(_Episode(object(), self.owner.current, self.repository.spec), poll_seconds=1)
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
        return operation

    async def start_admitted_runner(self, *, unfilled_execution_policy=None):
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
                            frozen_at=now, forward_profile_id=profile.profile_id,
                            unfilled_execution_policy=unfilled_execution_policy)
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
        self.runner = _prepared_runner(self.store, self.bundle, self.spec, poll_seconds=60,
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
        self.assertTrue(self.runner.episode._closing)
        self.assertIsNone(self.runner._task)
        self.assertNotIn(self.account_ref, self.supervisor._runners)
        current = self.owner.bundle(self.profile)
        self.assertIsNot(self.bundle, current)
        self.assertTrue(self.bundle.runtime._retired)
        self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, current.runtime_mode)
        self.assertEqual(self.bundle.automation_context, current.automation_context)
        self.assertFalse(current.runtime._new_orders_enabled)
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
        self.assertFalse(self.runner.episode._closing)
        close.assert_not_called()
        save.assert_not_called()
        self.assertEqual({}, self.supervisor._runner_close_tasks)
        with patch.object(self.supervisor._repository, "load_mock_automation_control", side_effect=AssertionError("healthy restore queried control")), \
             patch.object(self.store, "load_account_settings", side_effect=AssertionError("healthy restore queried settings")):
            await self.supervisor._restore_once()

    async def test_cancelled_settings_and_shutdown_waiters_join_one_native_checkpoint_before_lease_release(self):
        entered, release, committed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = self.runner.episode._save_checkpoint
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
        with patch.object(self.runner.episode, "_save_checkpoint", side_effect=checkpoint), \
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

    async def test_existing_auto_run_key_validation_preserves_current_owner_until_explicit_apply(self):
        # Activation UUID validation stays strict; AUTO order identity travels
        # separately and never replaces the current owner during prepare.
        credentials = self.app.state.credential_runtime
        prepared = await credentials.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1,
                                              {"app_key": "a2", "secret_key": "fake-secret"})
        operation = credentials._operations[prepared["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("READY", operation.state, operation.error_code)
        uuid.UUID(operation.candidate.run_id)
        self.assertEqual(self.bundle.run_id, operation.candidate.execution_run_id)
        self.assertIs(self.bundle, self.owner.bundle(self.profile))
        self.assertIs(self.runner, self.supervisor._runners[self.account_ref])
        self.assertFalse(self.runner.episode._closing)
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
        self.assertTrue(self.runner.episode._closing)
        self.assertIsNone(self.runner._task)
        self.assertNotIn(self.account_ref, self.supervisor._runners)
        self.assertEqual((current.binding,), self.app.state.verified_account_bindings)
        self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))

    async def test_auto_key_rotation_reconciles_partial_fill_and_keeps_unknown_in_original_run(self):
        from datetime import timedelta
        from kiwoom_monitor.domain.order_contract import (
            OrderIntent, OrderSide, OrderState, OrderType, BrokerSubmission, order_event,
        )
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
        repository = ExecutionRepository(self.store)
        now = self.bundle.runtime.server_now()
        for suffix, symbol, broker_id in (("accepted", "005930", "order-a"), ("partial", "000660", "order-p"),
                                          ("unknown", "035420", "")):
            intent = OrderIntent("rotation-" + suffix, self.bundle.run_id, "decision-" + suffix,
                self.account_ref, "mock", symbol, "KRX", OrderSide.BUY, 2, OrderType.LIMIT, 70_000,
                now, now + timedelta(minutes=5), "existing-order/v1")
            record = repository.create(intent)
            state = OrderState.ACCEPTED if broker_id else OrderState.SUBMISSION_UNKNOWN
            repository.apply(record, order_event(intent.intent_id,
                "ORDER_ACCEPTED" if broker_id else "SUBMISSION_STARTED", state, now, now,
                broker_order_id=broker_id), broker_order_id=broker_id)
        client = self.bundle.client
        request = client.request_with_continuation
        def new_key_snapshot(api_id, path, body, **kwargs):
            if client._settings.app_key == "a2":
                if api_id == "ka10075":
                    return {"oso": [
                        {"ord_no": "order-a", "stk_cd": "005930", "ord_qty": "2", "oso_qty": "2",
                         "ord_pric": "70000", "io_tp_nm": "매수"},
                        {"ord_no": "order-p", "stk_cd": "000660", "ord_qty": "2", "oso_qty": "1",
                         "ord_pric": "70000", "io_tp_nm": "매수", "cntr_no": "partial-fill-1",
                         "unit_cntr_qty": "1", "unit_cntr_pric": "70000"},
                    ]}, False, ""
                if api_id == "kt00018":
                    return {"acnt_evlt_remn_indv_tot": [{"stk_cd": "000660", "rmnd_qty": "1"}]}, False, ""
            return request(api_id, path, body, **kwargs)
        with patch.object(client, "request_with_continuation", side_effect=new_key_snapshot):
            await self.rotate("a2", 1)
            current = self.owner.bundle(self.profile)
            self.assertEqual(self.bundle.run_id, current.run_id)
            self.assertEqual(self.bundle.binding, current.binding)
            self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, current.runtime_mode)
            self.assertTrue(self.bundle.runtime._retired)
            self.assertIsNone(self.runner._task)
            self.assertEqual(1, current.gateway.load("rotation-partial").filled_quantity)
            self.assertEqual(1, current.gateway.load("rotation-partial").detailed_filled_quantity)
            unknown = current.gateway.load("rotation-unknown")
            self.assertEqual(OrderState.SUBMISSION_UNKNOWN, unknown.state)
            self.assertEqual("", unknown.broker_order_id)
            with self.assertRaisesRegex(ValueError, "reconciled and accepted"):
                await current.gateway.cancel("rotation-unknown")
            self.transport.cancel.side_effect = None
            self.transport.cancel.return_value = BrokerSubmission("cancel-a", current.runtime.server_now())
            cancelled = await current.gateway.cancel("rotation-accepted")
            self.assertEqual(OrderState.CANCEL_PENDING, cancelled.state)
            self.assertEqual(1, self.transport.cancel.call_count)
            self.assertEqual(OrderState.SUBMISSION_UNKNOWN, current.gateway.load("rotation-unknown").state)
            with self.assertRaisesRegex(RuntimeError, "CREDENTIAL_CHANGE_IN_PROGRESS"):
                await self.bundle.gateway.cancel("rotation-accepted")
        self.assertTrue(self.supervisor._repository.load_mock_automation_control(self.account_ref).entry_resume_required)
        self.transport.submit.assert_not_called()

    async def test_auto_key_rotation_restores_management_and_requires_explicit_entry_resume(self):
        from kiwoom_monitor.application.mock_automation_admission import build_mock_automation_lease_receipt
        from kiwoom_monitor.domain.order_contract import OrderSide
        # This scenario starts from an already admitted run. Preserve the
        # native receipt that actual admission issues; do not mock restoration.
        self.assertTrue(self.supervisor._repository.save_mock_automation_lease_receipt(
            build_mock_automation_lease_receipt(self.admission, admitted_at=self.admission.requested_at)))
        operation = await self.rotate("a2", 1)
        current = self.owner.bundle(self.profile)
        control = self.supervisor._repository.load_mock_automation_control(self.account_ref)
        self.assertEqual(MockAutomationDesiredState.RUNNING, control.desired_state)
        self.assertTrue(control.entry_resume_required)
        self.assertFalse(current.runtime._new_orders_enabled)
        for monitor, orders in ((True, False), (True, True), (False, False), (True, True)):
            response = await self.put_settings(monitor=monitor, orders=orders,
                revision=self.store.load_account_settings(self.scope)["revision"])
            self.assertEqual(200, response.status_code, response.text)
            current = self.owner.bundle(self.profile)
            self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, current.runtime_mode)
            self.assertEqual(self.bundle.run_id, current.run_id)
            self.assertEqual(control.control_revision, current.automation_context.control_revision)
            self.assertFalse(current.runtime._new_orders_enabled)
            self.assertEqual(control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
            if orders:
                with self.assertRaisesRegex(RuntimeError, "MOCK_ACCOUNT_AUTOMATIC_MODE"):
                    await current.gateway.submit_limit(request_id=str(uuid.uuid4()), symbol="005930",
                        side=OrderSide.BUY, quantity=1, limit_price=70_000, expires_seconds=30, scoped=True)
        await self.supervisor._restore_once()
        self.assertIn(self.account_ref, self.supervisor._runners, self.supervisor._restore_errors)
        self.assertTrue(self.supervisor._repository.load_mock_automation_control(self.account_ref).entry_resume_required)
        response = await self.http.post("/api/v1/mock-automation/resume", json={
            "credential_profile_id": self.profile, "account_ref": self.account_ref, "spec_id": self.spec.spec_id,
            "expected_control_revision": control.control_revision,
            "expected_settings_revision": self.store.load_account_settings(self.scope)["revision"],
            "credential_revision": 2, "reason": "operator explicitly resumes entries",
        })
        self.assertEqual(200, response.status_code, response.text)
        resumed = self.supervisor._repository.load_mock_automation_control(self.account_ref)
        self.assertFalse(resumed.entry_resume_required)
        self.assertEqual(control.control_revision + 1, resumed.control_revision)
        await self.app.state.credential_runtime.apply(operation.operation_id, 1, self.account_ref)
        self.assertEqual(resumed, self.supervisor._repository.load_mock_automation_control(self.account_ref))
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_disabled_transport_auto_resume_preserves_pause_and_stopped_control(self):
        await self.rotate("a2", 1)
        stale_settings_revision = self.store.load_account_settings(self.scope)["revision"]
        response = await self.put_settings(orders=False, revision=stale_settings_revision)
        self.assertEqual(200, response.status_code, response.text)
        response = await self.put_settings(orders=True, revision=response.json()["applied_revision"])
        self.assertEqual(200, response.status_code, response.text)
        control = self.supervisor._repository.load_mock_automation_control(self.account_ref)
        for route in ("start", "resume"):
            request = {"credential_profile_id": self.profile, "account_ref": self.account_ref,
                "spec_id": self.spec.spec_id, "expected_settings_revision": stale_settings_revision, "credential_revision": 2}
            if route == "resume":
                request.update(expected_control_revision=control.control_revision, reason="stale settings request")
            response = await self.http.post("/api/v1/mock-automation/"+route, json=request)
            self.assertEqual(409, response.status_code, response.text)
            self.assertEqual("ACCOUNT_SETTINGS_REVISION_CONFLICT", response.json()["detail"])
            self.assertEqual(control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
            self.assertNotIn(self.account_ref, self.supervisor._runners)
        for stopped in (False, True):
            control = self.supervisor._repository.load_mock_automation_control(self.account_ref)
            if stopped:
                response = await self.http.post("/api/v1/mock-automation/stop", json={
                    "credential_profile_id": self.profile, "account_ref": self.account_ref,
                    "spec_id": self.spec.spec_id, "expected_control_revision": control.control_revision,
                    "reason": "operator stop while credential entry pause remains",
                })
                self.assertEqual(200, response.status_code, response.text)
                control = self.supervisor._repository.load_mock_automation_control(self.account_ref)
            self.assertTrue(control.entry_resume_required)
            for monitor in (True, False):
                response = await self.put_settings(monitor=monitor, orders=False,
                    revision=self.store.load_account_settings(self.scope)["revision"])
                self.assertEqual(200, response.status_code, response.text)
                response = await self.http.post("/api/v1/mock-automation/resume", json={
                    "credential_profile_id": self.profile, "account_ref": self.account_ref, "spec_id": self.spec.spec_id,
                    "expected_control_revision": control.control_revision,
                    "expected_settings_revision": self.store.load_account_settings(self.scope)["revision"],
                    "credential_revision": 2, "reason": "transport remains explicitly disabled",
                })
                self.assertEqual(409, response.status_code, response.text)
                self.assertEqual("MOCK_ORDER_TRANSPORT_DISABLED", response.json()["detail"])
                self.assertEqual(control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
                self.assertNotIn(self.account_ref, self.supervisor._runners)
                self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, self.owner.bundle(self.profile).runtime_mode)
                self.assertFalse(self.owner.bundle(self.profile).runtime._new_orders_enabled)
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_auto_disable_restart_new_key_preserves_original_run_without_reviving_disabled_owner(self):
        import httpx
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.domain.order_contract import OrderIntent, OrderSide, OrderState, OrderType, order_event
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
        from datetime import timedelta
        now = self.bundle.runtime.server_now()
        execution = ExecutionRepository(self.store)
        intent = OrderIntent("disabled-unknown", self.bundle.run_id, "decision-disabled", self.account_ref,
            "mock", "005930", "KRX", OrderSide.BUY, 2, OrderType.LIMIT, 70_000,
            now, now + timedelta(minutes=5), "existing-order/v1")
        record = execution.create(intent)
        execution.apply(record, order_event(intent.intent_id, "SUBMISSION_STARTED",
            OrderState.SUBMISSION_UNKNOWN, now, now))
        credentials = self.app.state.credential_runtime
        prepared = await credentials.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1, {}, disabled=True)
        operation = credentials._operations[prepared["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("READY", operation.state, operation.error_code)
        await credentials.apply(operation.operation_id, 1, self.account_ref)
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("ACTIVE", operation.state, operation.error_code)
        disabled = credentials.vault.load("kiwoom_mock", self.profile)
        self.assertTrue(disabled.disabled)
        self.assertEqual(self.bundle.run_id, disabled.payload["activation"].get("execution_run_id"))
        self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
        await self.close_app()
        root = Path(self.temp.name)
        self.app = create_app(CentralServerSettings(
            f"sqlite:///{root / 'app.sqlite'}", "private-token", credential_directory=str(root / "vault"),
            account_identity_registry_enabled=True, account_identity_hmac_key="x" * 32,
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
            news_naver_market_enabled=False, news_history_jobs_enabled=False,
        ))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.app_closed = False
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://nas.test",
            headers={"Authorization": "Bearer private-token"})
        self.addAsyncCleanup(self.http.aclose)
        self.owner = self.app.state.mock_credential_owner
        self.supervisor = self.app.state.mock_automation_supervisor
        self.store = self.owner.store
        self.supervisor._restore_stop.set()
        await self.supervisor._restore_task
        self.supervisor._restore_task = None
        self.assertEqual([], self.owner._boot_tasks)
        self.assertIsNone(self.owner.bundle(self.profile))
        self.assertIsNone(self.store.load_account_settings(self.scope)["active_profile_id"])
        await self.rotate("a2", 2)
        response = await self.put_settings(orders=True, revision=self.store.load_account_settings(self.scope)["revision"])
        self.assertEqual(200, response.status_code, response.text)
        current = self.owner.bundle(self.profile)
        self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, current.runtime_mode)
        self.assertEqual(self.bundle.run_id, current.run_id)
        self.assertEqual(self.bundle.binding, current.binding)
        self.assertEqual(OrderState.SUBMISSION_UNKNOWN, current.runtime.load_intent(intent.intent_id).state)
        self.assertTrue(self.supervisor._repository.load_mock_automation_control(self.account_ref).entry_resume_required)
        self.assertFalse(current.runtime._new_orders_enabled)
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

    async def test_auto_key_precommit_failure_restores_old_key_run_and_entry_authority(self):
        credentials = self.app.state.credential_runtime
        document = await credentials.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1,
            {"app_key": "a2", "secret_key": "fake-secret"})
        operation = credentials._operations[document["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("READY", operation.state, operation.error_code)
        with patch.object(self.store, "load_account_settings", side_effect=OSError("controlled drain precommit failure")), \
             patch.object(credentials.vault, "save", wraps=credentials.vault.save) as save:
            await credentials.apply(operation.operation_id, 1, self.account_ref)
            await asyncio.wait_for(asyncio.shield(operation.task), 5)
        save.assert_not_called()
        self.assertEqual("FAILED", operation.state)
        current = self.owner.bundle(self.profile)
        self.assertEqual(self.bundle.run_id, current.run_id)
        self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, current.runtime_mode)
        self.assertEqual("token:a1", current.client._token)
        self.assertEqual(self.control, self.supervisor._repository.load_mock_automation_control(self.account_ref))
        self.assertEqual(1, self.owner.active_revision(self.profile))
        self.transport.submit.assert_not_called()

    async def test_auto_key_commit_ack_loss_restores_latest_key_without_repausing_control(self):
        credentials = self.app.state.credential_runtime
        document = await credentials.prepare("kiwoom_mock", self.profile, str(uuid.uuid4()), 1,
            {"app_key": "a2", "secret_key": "fake-secret"})
        operation = credentials._operations[document["operation_id"]]
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        finalize = self.store.finalize_credential_activation
        lost = False
        def lose_ack(value):
            nonlocal lost
            result = finalize(value)
            if value["operation_id"] == operation.operation_id and not lost:
                lost = True
                raise OSError("controlled acknowledgement loss after native commit")
            return result
        with patch.object(self.store, "finalize_credential_activation", side_effect=lose_ack):
            await credentials.apply(operation.operation_id, 1, self.account_ref)
            await asyncio.wait_for(asyncio.shield(operation.task), 5)
        self.assertEqual("RECOVERY_REQUIRED", operation.state)
        paused = self.supervisor._repository.load_mock_automation_control(self.account_ref)
        self.assertTrue(paused.entry_resume_required)
        record = credentials.vault.load("kiwoom_mock", self.profile)
        self.assertEqual(2, record.revision)
        await self.owner._boot_record(self.profile, record)
        current = self.owner.bundle(self.profile)
        self.assertIsNotNone(current, self.owner.errors)
        self.assertEqual("token:a2", current.client._token)
        self.assertEqual(self.bundle.run_id, current.run_id)
        self.assertEqual(self.bundle.binding, current.binding)
        self.assertEqual(paused, self.supervisor._repository.load_mock_automation_control(self.account_ref))
        self.transport.submit.assert_not_called()
        self.transport.cancel.assert_not_called()

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
        self.assertTrue(started[0].episode._closing)
        self.assertIsNone(started[0]._task)
        self.assertNotIn(self.account_ref, self.supervisor._runners)
        self.assertEqual(MockAccountRuntimeMode.AUTOMATIC, self.owner.bundle(self.profile).runtime_mode)
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
        # Settings replacement retains AUTO. Select the existing flat MANUAL
        # mode explicitly to exercise the real resume mode-switch boundary.
        response = await self.put_settings()
        self.assertEqual(200, response.status_code)
        response = await self.put_settings(orders=True, revision=response.json()["applied_revision"])
        self.assertEqual(200, response.status_code)
        settings_revision = response.json()["applied_revision"]
        manual = await self.owner.switch_execution_mode(self.profile, MockAccountRuntimeMode.MANUAL,
            expected_settings_revision=settings_revision, credential_revision=1)
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
        manual = await self.owner.switch_execution_mode(self.profile, MockAccountRuntimeMode.MANUAL,
            expected_settings_revision=settings_revision, credential_revision=1)
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
        manual = await self.owner.switch_execution_mode(self.profile, MockAccountRuntimeMode.MANUAL,
            expected_settings_revision=settings_revision, credential_revision=1)
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
        manual = await self.owner.switch_execution_mode(self.profile, MockAccountRuntimeMode.MANUAL,
            expected_settings_revision=settings_revision, credential_revision=1)
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
                self.assertTrue(self.runner.episode._closing)
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
        self.assertEqual("AUTOMATIC", response.json()["runtime_mode"])
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
            before = await self.owner.switch_execution_mode(self.profile, MockAccountRuntimeMode.MANUAL,
                expected_settings_revision=response.json()["applied_revision"], credential_revision=1)
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


class MockCredentialProtectionContinuityTests(unittest.IsolatedAsyncioTestCase):
    close_app = MockAutomationOwnerReplacementTests.close_app
    rotate = MockAutomationOwnerReplacementTests.rotate
    put_settings = MockAutomationOwnerReplacementTests.put_settings

    async def start_admitted_runner(self):
        from tests.unit.test_mock_automation_specification import MockProtectiveCancelRecoveryTests
        await MockAutomationOwnerReplacementTests.start_admitted_runner(self,
            unfilled_execution_policy=MockProtectiveCancelRecoveryTests.unfilled_execution_policy)

    async def asyncSetUp(self):
        from datetime import timedelta
        case = self
        self.clock = datetime(2026, 9, 21, 10, tzinfo=timezone(timedelta(hours=9)))
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return case.clock.astimezone(tz) if tz else case.clock.replace(tzinfo=None)
        for target, value in (("kiwoom_monitor.central_server.app.datetime", FixedDateTime),
                ("kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient.server_now", lambda client: case.clock)):
            clock_patch = patch(target, value)
            clock_patch.start()
            self.addCleanup(clock_patch.stop)
        def supervised_owner(*args, **kwargs):
            kwargs['price_source'] = lambda symbol: {
                "price_won": getattr(self, "rotation_market_price", 990), "available_at": self.clock, "input_ref": "rotation-protection-price",
                "durable": False, "market_status": "TRADABLE"}
            return MockAutomationSupervisor(*args, **kwargs)
        source_patch = patch("kiwoom_monitor.central_server.mock_automation_supervisor.MockAutomationSupervisor",
            side_effect=supervised_owner)
        source_patch.start()
        self.addCleanup(source_patch.stop)
        await MockAutomationOwnerReplacementTests.asyncSetUp(self)

    async def test_actual_key_rotation_preserves_registered_protective_sell_and_native_cancel_retry(self):
        await self._exercise_rotation_protection()

    async def test_actual_key_rotation_preserves_no_minute_incomplete_management_checkpoint(self):
        await self._exercise_rotation_protection(incomplete_before_rotation=True)

    async def test_same_plan_dispatch_ack_loss_and_late_fill_survive_verified_key_rotation(self):
        from datetime import timedelta
        from kiwoom_monitor.application.mock_automation_admission import build_mock_automation_lease_receipt
        from kiwoom_monitor.application.mock_automation_execution import dispatch_mock_automation_decision_from_risk
        from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
        from kiwoom_monitor.central_server.database_execution import PLAN_POSITION_COLLECTION
        from kiwoom_monitor.domain.order_contract import BrokerSubmission, OrderPlanRef, OrderState
        from kiwoom_monitor.domain.strategy_contract import StrategyDecision
        from tests.unit.test_mock_automation_specification import _automation_decision, _hash

        repository = self.supervisor._repository
        self.assertTrue(repository.save_mock_automation_lease_receipt(
            build_mock_automation_lease_receipt(self.admission, admitted_at=self.admission.requested_at),
        ))
        await self.supervisor.bundle_changed(self.profile, None)
        original_runtime = self.bundle.runtime
        source_binding = self.bundle.binding
        plan = OrderPlanRef("rotation-plan", "rotation-plan/v1", 0)
        scope = dict(account_ref=self.account_ref, spec_id=self.spec.spec_id, strategy_ref=self.spec.strategy_ref)

        async def refresh(bundle):
            recovery = await bundle.monitor.refresh_recovery()
            risk = repository.load_latest_mock_automation_risk(self.account_ref)
            restored = record_mock_automation_recovery_from_risk(repository, bundle.runtime, recovery, risk,
                                                                account_ref=self.account_ref, spec_id=self.spec.spec_id)
            self.assertEqual(risk.snapshot_id, restored.risk_snapshot_id)
            return recovery, risk

        recovery, risk = await refresh(self.bundle)
        decision_body = _automation_decision(self.bundle.run_id, "ENTER", self.clock).to_dict()
        decision_body.pop("decision_id")
        decision_body.update(quantity=3, required_capital_won=210_000)
        decision = StrategyDecision(decision_id="decision_" + _hash(decision_body), **decision_body)
        self.transport.submit.side_effect = None
        self.transport.submit.return_value = BrokerSubmission("planned-rotation-broker-order", self.clock)

        def dispatch(runtime, current_recovery, current_risk):
            return dispatch_mock_automation_decision_from_risk(
                repository, runtime, decision, current_recovery, current_risk, plan=plan, **scope,
            )

        save_receipt = repository.save_mock_automation_dispatch_receipt
        committed_receipts = []

        def lose_ack_after_save(receipt):
            save_receipt(receipt)
            committed_receipts.append(receipt)
            raise OSError("controlled dispatch acknowledgment loss after native commit")

        with patch.object(repository, "save_mock_automation_dispatch_receipt", side_effect=lose_ack_after_save):
            with self.assertRaisesRegex(OSError, "after native commit"):
                dispatch(original_runtime, recovery, risk)
        (first_receipt,) = committed_receipts
        intent_id = first_receipt.intent_id
        self.assertEqual(first_receipt, repository.load_mock_automation_dispatch_receipt(self.account_ref, intent_id))
        self.assertEqual(OrderState.ACCEPTED, original_runtime.load_intent(intent_id).state)
        self.transport.submit.assert_called_once()

        self.clock += timedelta(seconds=1)
        first_fill_at = self.clock
        second_fill_at = self.clock + timedelta(seconds=1)
        read = self.bundle.client.request_with_continuation

        def account_read(api_id, path, body, **kwargs):
            late = self.bundle.client._settings.app_key == "a2"
            if api_id == "ka10075":
                return {"oso": [{
                    "ord_no": "planned-rotation-broker-order", "stk_cd": "005930", "ord_qty": "3",
                    "oso_qty": "1" if late else "2", "ord_pric": "70000", "io_tp_nm": "매수", "stex_tp": "1",
                    "cntr_no": "planned-fill-late" if late else "planned-fill-first",
                    "unit_cntr_qty": "1", "unit_cntr_pric": "70000",
                    "tm": (second_fill_at if late else first_fill_at).strftime("%H%M%S"),
                }]}, False, ""
            if api_id == "kt00018":
                return {"acnt_evlt_remn_indv_tot": [{"stk_cd": "005930", "rmnd_qty": "2" if late else "1"}]}, False, ""
            return read(api_id, path, body, **kwargs)

        with patch.object(self.bundle.client, "request_with_continuation", side_effect=account_read):
            await refresh(self.bundle)
            original = original_runtime.load_intent(intent_id)
            self.assertEqual((OrderState.PARTIALLY_FILLED, ("planned-fill-first",)), (original.state, original.fill_ids))
            source_events = tuple(original_runtime.load_intent_events(intent_id))
            self.clock = second_fill_at
            await self.rotate("a2", 1)
            current = self.owner.bundle(self.profile)
            self.assertEqual(source_binding, current.binding)
            self.assertEqual(2, self.owner.active_revision(self.profile))
            self.assertEqual(original_runtime.run_id, current.runtime.run_id)
            self.assertTrue(original_runtime._retired)
            self.assertNotEqual(original_runtime.owner_token, current.runtime.owner_token)
            fresh_recovery, fresh_risk = await refresh(current)
            self.assertEqual(2, fresh_recovery.account.positions["005930"])
            gate, record, receipt = dispatch(current.runtime, fresh_recovery, fresh_risk)

        self.assertEqual(first_receipt, receipt)
        self.assertEqual(gate.intent_id, record.intent.intent_id)
        self.assertEqual(original.intent, record.intent)
        self.assertEqual(OrderState.PARTIALLY_FILLED, record.state)
        self.assertEqual(("planned-fill-first", "planned-fill-late"), record.fill_ids)
        current_events = tuple(current.runtime.load_intent_events(intent_id))
        self.assertEqual(source_events, current_events[:len(source_events)])
        self.assertEqual(["planned-fill-first", "planned-fill-late"],
                         [event["broker_execution_id"] for event in current_events if event["event_type"] == "FILL"])
        head = self.store.load_document(PLAN_POSITION_COLLECTION, self.account_ref, plan.plan_id)["document"]
        page = current.repository.account_events("mock", self.account_ref)
        newest_fill = next(event for event in page.events if event.broker_execution_id == "planned-fill-late")
        self.assertEqual((2, newest_fill.accepted_sequence), (head["quantity"], head["fill_cursor"]))
        self.assertEqual([intent_id], [row.intent.intent_id for row in current.runtime.load_active_intents()])
        with self.store.mock_automation_read_scope() as reader:
            leases = reader.execute("SELECT owner_token FROM central_execution_runtime_leases WHERE owner_key=?",
                                    (f"mock:{self.account_ref}",)).fetchall()
        self.assertEqual([(f"{current.run_id}:{current.runtime.owner_token}",)], leases)
        self.assertTrue(repository.load_mock_automation_control(self.account_ref).entry_resume_required)
        self.assertFalse(current.runtime._new_orders_enabled)
        self.transport.submit.assert_called_once()
        self.transport.cancel.assert_not_called()

    async def _exercise_rotation_protection(self, *, mutation=None, retry_mutation=False, incomplete_before_rotation=False):
        from datetime import timedelta
        from kiwoom_monitor.application.breakout_strategy import StrategyDecision
        from kiwoom_monitor.application.mock_automation_admission import build_mock_automation_lease_receipt
        from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
        from kiwoom_monitor.domain.order_contract import (
            AccountSnapshot, BrokerFill, BrokerOrderSnapshot, BrokerSubmission, OrderIntent, OrderSide, OrderState, OrderType,
        )
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionRejected
        from tests.unit.test_mock_automation_specification import _automation_decision, _hash
        repository = self.supervisor._repository
        self.assertTrue(repository.save_mock_automation_lease_receipt(
            build_mock_automation_lease_receipt(self.admission, admitted_at=self.admission.requested_at)))
        await self.supervisor.bundle_changed(self.profile, None)
        self.runner = _prepared_runner(self.store, self.bundle, self.spec,
            now_provider=lambda: self.clock, price_source=lambda symbol: {
                "price_won": 990, "available_at": self.clock, "input_ref": "rotation-protection-price",
                "durable": False, "market_status": "TRADABLE"})
        self.supervisor._runners[self.account_ref] = self.runner
        self.transport.submit.side_effect = lambda intent: BrokerSubmission("broker-"+intent.intent_id, self.clock)
        buy = OrderIntent("rotation-protection-buy", self.bundle.run_id, "rotation-protection-owned-position",
            self.account_ref, "mock", "005930", "KRX", OrderSide.BUY, 3, OrderType.LIMIT, 1_000,
            self.clock, self.clock+timedelta(seconds=60), "existing-owned-position/v1")
        self.bundle.runtime.submit_automation_intent(buy,
            AccountSnapshot(self.account_ref, 1_000_000, 0, {}, self.clock),
            spec_id=self.spec.spec_id, control_revision=self.control.control_revision)
        open_sell = None
        request = self.bundle.client.request_with_continuation
        def account_state(api_id, path, body, **kwargs):
            if api_id == "kt00018":
                return {"acnt_evlt_remn_indv_tot": [{"stk_cd": "005930", "rmnd_qty": "3"}]}, False, ""
            if api_id == "ka10075":
                return {"oso": [{"ord_no": "broker-"+buy.intent_id, "stk_cd": "005930", "ord_qty": "3",
                    "oso_qty": "0", "ord_pric": "1000", "io_tp_nm": "매수", "cntr_no": "rotation-protection-buy-fill",
                    "unit_cntr_qty": "3", "unit_cntr_pric": "1000"}] if open_sell is None else [{"ord_no": open_sell.broker_order_id,
                    "stk_cd": "005930", "ord_qty": "3", "oso_qty": "3", "ord_pric": "1000", "io_tp_nm": "매도"}]}, False, ""
            return request(api_id, path, body, **kwargs)
        with patch.object(self.bundle.client, "request_with_continuation", side_effect=account_state):
            recovery = await self.bundle.monitor.refresh_recovery()
            self.assertEqual(OrderState.FILLED, self.bundle.runtime.load_intent(buy.intent_id).state)
            risk = repository.load_latest_mock_automation_risk(self.account_ref)
            record_mock_automation_recovery_from_risk(repository, self.bundle.runtime, recovery, risk,
                account_ref=self.account_ref, spec_id=self.spec.spec_id)
            body = _automation_decision(self.bundle.run_id, "EXIT", self.clock).to_dict()
            body.pop("decision_id")
            body.update(quantity=3, signal_reference_price=1_000, required_capital_won=0, reasons=("stop_loss_reached",))
            decision = StrategyDecision(decision_id="decision_"+_hash(body), **body)
            gate, open_sell, _ = self.runner.episode._dispatch_original_and_register(decision, recovery, risk)
            self.assertIsNotNone(open_sell, gate.reasons)
            registration = self.runner.episode._unfilled_registration
            self.assertEqual("PROTECTIVE_EXIT", registration["purpose"])
            await self.bundle.monitor.refresh_recovery()
            if incomplete_before_rotation:
                # Supported legacy checkpoint shape starts real frame recovery;
                # enrollment/approval/order/anchor data are left byte-equivalent.
                checkpoint = self.store.load_document('execution_mock_automation_runner_current', self.account_ref, 'current')['document']
                self.assertEqual(registration, checkpoint['unfilled_registration'])
                self.assertNotIn('unfilled_execution_preflight', checkpoint)
                await self.runner.close()
                legacy = deepcopy(checkpoint)
                legacy.pop('delivery_protocol', None)
                legacy.pop('input_recovery', None)
                self.store.upsert_documents('execution_mock_automation_runner_current', [{
                    'owner': self.account_ref, 'key': 'current', 'document': legacy}])
                self.runner = _prepared_runner(self.store, self.bundle, self.spec,
                    now_provider=lambda: self.clock, price_source=lambda symbol: {
                        'price_won': 990, 'available_at': self.clock, 'input_ref': 'rotation-protection-price',
                        'durable': False, 'market_status': 'TRADABLE'})
                self.supervisor._runners[self.account_ref] = self.runner
                episode = self.runner.episode
                self.assertTrue(episode._input_progress.checkpoint_incomplete(), episode.status)
                self.assertTrue(episode._input_progress.recovering)
                await self.bundle.monitor.refresh_recovery()
                await asyncio.wait_for(self.bundle.monitor._queue.join(), 3)
                await episode._produce_native_unfilled_inputs(self.control)
                self.assertIsNotNone(episode._unfilled_input, episode.status)
                inputs = episode._unfilled_input[1]
                await asyncio.to_thread(episode._preview_unfilled_management_locked,
                    **{key: value for key, value in inputs.items() if key not in {'recovery', 'risk'}})
                plan = await asyncio.to_thread(episode._assess_unfilled_action_locked, inputs)
                self.assertIsNotNone(plan)
                self.assertEqual('CANCEL', plan['result']['action'])
                checkpoint = self.store.load_document('execution_mock_automation_runner_current', self.account_ref, 'current')['document']
                self.assertEqual(registration, checkpoint['unfilled_registration'])
                self.assertNotIn('unfilled_execution_preflight', checkpoint)
                self.assertTrue(episode._input_progress.recovering)
                self.transport.cancel.assert_not_called()
            cancel_calls = []
            def cancel(*args):
                cancel_calls.append(args)
                if len(cancel_calls) == 1:
                    raise SubmissionRejected("confirmed first cancel rejection")
            self.transport.cancel.side_effect = cancel
            await self.rotate("a2", 1)
            current = self.owner.bundle(self.profile)
            native_reached = threading.Event()
            native_errors = []
            if mutation is not None:
                append = self.store.append_execution_event
                injected = False
                def before_native(value, event, **options):
                    nonlocal injected
                    if (event['event_type'] == 'CANCEL_STARTED' and not injected
                            and bool((options.get('ownership') or {}).get('cancel_retry_claim')) == retry_mutation):
                        injected = True
                        try:
                            self._mutate_management_proof(mutation, current, open_sell)
                            return append(value, event, **options)
                        except Exception as error:
                            native_errors.append(str(error))
                            raise
                        finally:
                            native_reached.set()
                    return append(value, event, **options)
                native_patch = patch.object(self.store, 'append_execution_event', side_effect=before_native)
                native_patch.start()
                self.addCleanup(native_patch.stop)
            await self.supervisor._restore_once()
            self.assertIn(self.account_ref, self.supervisor._runners, self.supervisor._restore_errors)
            restored = self.supervisor._runners[self.account_ref]
            self.assertTrue(restored.episode.is_bound_to(current))
            self.assertIsNotNone(restored._task)
            self.assertEqual(restored._native_risk_listener, current.monitor._automation_risk_listener)
            self.assertTrue(current.monitor.automation_execution_inputs_enabled)
            self.assertEqual(registration, restored.episode._unfilled_registration)
            paused = repository.load_mock_automation_control(self.account_ref)
            self.assertTrue(paused.entry_resume_required)
            self.assertEqual(self.control.control_revision+1, paused.control_revision)
            self.assertEqual(self.bundle.run_id, current.run_id)
            if mutation is not None and not retry_mutation:
                self.assertTrue(await asyncio.to_thread(native_reached.wait, 3), restored.episode.status)
                await self.supervisor.close()
                self.assertTrue(native_errors)
                self.assertIn(self._mutation_rejection(mutation), native_errors[0])
                self.assertEqual([], cancel_calls)
                self.assertFalse(any(event['event_type'] == 'CANCEL_STARTED'
                    for event in current.repository.events(open_sell.intent.intent_id)))
                self.assertEqual(2, self.transport.submit.call_count)
                return
            async def accepted_cancel_phase(attempts, response):
                while True:
                    if len(cancel_calls) < attempts:
                        await asyncio.sleep(.01)
                        continue
                    events = await asyncio.to_thread(current.repository.events, open_sell.intent.intent_id)
                    settled = (response != 'CANCEL_REJECTED' or
                        restored.episode.status.get('protective_cancel_recovery', {}).get('state') == 'WATCH'
                        and not restored.episode._checkpoint.pending)
                    if (sum(event['event_type'] == 'CANCEL_STARTED' for event in events) == attempts
                            and any(event['event_type'] == response for event in events) and settled):
                        return events
                    await asyncio.sleep(.01)
            await asyncio.wait_for(accepted_cancel_phase(1, 'CANCEL_REJECTED'), 3)
            self.assertEqual(1, len(cancel_calls), restored.episode.status)
            self.clock += timedelta(seconds=1.1)
            await current.monitor.refresh_recovery()
            await asyncio.wait_for(current.monitor._queue.join(), 3)
            if mutation is not None:
                self.assertTrue(await asyncio.to_thread(native_reached.wait, 3), restored.episode.status)
                await self.supervisor.close()
                self.assertTrue(native_errors)
                self.assertIn(self._mutation_rejection(mutation), native_errors[0])
                self.assertEqual(1, len(cancel_calls))
                self.assertEqual(1, sum(event['event_type'] == 'CANCEL_STARTED'
                    for event in current.repository.events(open_sell.intent.intent_id)))
                self.assertEqual(2, self.transport.submit.call_count)
                return
            try:
                events = await asyncio.wait_for(accepted_cancel_phase(2, 'CANCEL_ACCEPTED'), 3)
            except TimeoutError:
                status = restored.episode.status
                self.fail(repr({key: status.get(key) for key in (
                    'state', 'reason', 'unfilled_native_source', 'unfilled_input_delivery',
                    'protective_cancel_recovery', 'requires_operator_attention')}) +
                    repr(current.repository.events(open_sell.intent.intent_id)))
            self.assertEqual(2, len(cancel_calls), restored.episode.status)
            starts = [event for event in events if event["event_type"] == "CANCEL_STARTED"]
            self.assertEqual(2, len(starts))
            self.assertEqual(2, starts[-1]["mock_cancel_retry_claim"]["attempt"])
            self.assertEqual(OrderState.CANCEL_PENDING, current.runtime.load_intent(open_sell.intent.intent_id).state)
            self.assertEqual(paused, repository.load_mock_automation_control(self.account_ref))
            self.assertEqual(2, self.transport.submit.call_count)
            self.assertEqual(registration, restored.episode._unfilled_registration)
            self.assertEqual(registration['control_revision'], restored.episode._unfilled_attempt_plan['anchor']['scope']['control_revision'])
            self.assertEqual(registration['policy'], restored.episode._unfilled_attempt_plan['anchor']['policy'])
            if incomplete_before_rotation:
                saved = self.store.load_document('execution_mock_automation_runner_current', self.account_ref, 'current')['document']
                self.assertEqual(registration, saved['unfilled_registration'])
                self.assertEqual(registration['control_revision'],
                    saved['unfilled_execution_preflight']['anchor']['scope']['control_revision'])

    @staticmethod
    def _mutation_rejection(mutation):
        return {'approval_absent': 'MOCK_UNFILLED_MANAGEMENT_PROOF_MISSING',
            'checkpoint_absent': 'MOCK_UNFILLED_MANAGEMENT_PROOF_MISSING',
            'registration_absent': 'invalid_unfilled_native_registration',
            'root_account': 'MOCK_UNFILLED_MANAGEMENT_ROOT_CHANGED',
            'scope_rebase': 'unfilled_original_approval_lineage_changed',
            'deadline_extension': 'unfilled_native_registration_frozen_policy_mismatch',
            'stop': 'MOCK_AUTOMATION_CONTROL_CHANGED',
            'risk_revision': 'MOCK_PROTECTIVE_CANCEL_RETRY_NATIVE_CUT_CHANGED'}[mutation]

    def _mutate_management_proof(self, mutation, current, root):
        from copy import deepcopy
        from kiwoom_monitor.application.mock_automation_session_clock import content_id
        from kiwoom_monitor.application.order_lifecycle import seal_unfilled_attempt_plan
        from tests.unit.test_mock_automation_specification import _hash
        repository = self.supervisor._repository
        if mutation in {'approval_absent', 'checkpoint_absent'}:
            collection, key = (('execution_mock_automation_approved_gates', root.intent.intent_id)
                if mutation == 'approval_absent' else ('execution_mock_automation_runner_current', 'current'))
            with self.store._lock, self.store._connection() as connection:
                connection.execute('DELETE FROM central_documents WHERE collection=? AND owner=? AND document_key=?',
                    (collection, self.account_ref, key))
            return
        if mutation == 'root_account':
            with self.store._lock, self.store._connection() as connection:
                row = connection.execute('SELECT document_json FROM central_execution_intents WHERE intent_id=?',
                    (root.intent.intent_id,)).fetchone()
                value = json.loads(row[0]); value['account_ref'] = str(uuid.uuid4())
                connection.execute('UPDATE central_execution_intents SET document_json=? WHERE intent_id=?',
                    (json.dumps(value), root.intent.intent_id))
            return
        if mutation == 'stop':
            control = repository.load_mock_automation_control(self.account_ref)
            self.assertTrue(repository.save_mock_automation_control(replace(control,
                desired_state=MockAutomationDesiredState.STOPPED, control_revision=control.control_revision+1,
                reason='native claim race operator STOP'), expected_revision=control.control_revision))
            return
        if mutation == 'risk_revision':
            from kiwoom_monitor.application.mock_automation_risk import mock_automation_risk_snapshot_from_dict
            value = repository.load_latest_mock_automation_risk(self.account_ref).to_dict()
            value.pop('snapshot_id'); value['reconciliation_revision'] += 1; value['snapshot_id'] = _hash(value)
            repository.save_mock_automation_risk_snapshot(mock_automation_risk_snapshot_from_dict(value))
            return
        value = deepcopy(self.store.load_document('execution_mock_automation_runner_current', self.account_ref, 'current')['document'])
        if mutation == 'registration_absent':
            value['unfilled_registration'] = None
        elif mutation == 'scope_rebase':
            value['unfilled_execution_preflight']['anchor']['scope']['control_revision'] = \
                repository.load_mock_automation_control(self.account_ref).control_revision
            value['unfilled_execution_preflight'] = seal_unfilled_attempt_plan(value['unfilled_execution_preflight'])
        elif mutation == 'deadline_extension':
            from datetime import timedelta
            policy = value['unfilled_execution_preflight']['anchor']['policy']
            policy['protection_deadline'] = (datetime.fromisoformat(policy['protection_deadline']) + timedelta(seconds=1)).isoformat()
            value['unfilled_registration']['policy'] = deepcopy(policy)
            registration = value['unfilled_registration']
            registration['registration_id'] = content_id('unfilled_registration',
                {key: item for key, item in registration.items() if key != 'registration_id'})
            value['unfilled_execution_preflight'] = seal_unfilled_attempt_plan(value['unfilled_execution_preflight'])
        else:
            raise AssertionError(mutation)
        self.store.upsert_documents('execution_mock_automation_runner_current',
            [{'owner': self.account_ref, 'key': 'current', 'document': value}])

    async def test_rotation_native_cancel_rejects_deleted_original_approval(self):
        await self._exercise_rotation_protection(mutation='approval_absent')

    async def test_rotation_native_cancel_rejects_deleted_checkpoint(self):
        await self._exercise_rotation_protection(mutation='checkpoint_absent')

    async def test_rotation_native_cancel_rejects_deleted_registration(self):
        await self._exercise_rotation_protection(mutation='registration_absent')

    async def test_rotation_native_cancel_rejects_foreign_account_root(self):
        await self._exercise_rotation_protection(mutation='root_account')

    async def test_rotation_native_cancel_rejects_resigned_scope_rebase(self):
        await self._exercise_rotation_protection(mutation='scope_rebase')

    async def test_rotation_native_cancel_rejects_resigned_deadline_extension(self):
        await self._exercise_rotation_protection(mutation='deadline_extension')

    async def test_rotation_native_cancel_rejects_stop_race(self):
        await self._exercise_rotation_protection(mutation='stop')

    async def test_rotation_native_retry_rejects_fresh_risk_change(self):
        await self._exercise_rotation_protection(mutation='risk_revision', retry_mutation=True)


from copy import deepcopy
from datetime import timedelta
from kiwoom_monitor.application.breakout_strategy import StrategyDecision
from kiwoom_monitor.application.mock_automation_admission import build_mock_automation_lease_receipt
from kiwoom_monitor.application.mock_automation_recovery import record_mock_automation_recovery_from_risk
from kiwoom_monitor.domain.order_contract import AccountSnapshot, BrokerSubmission, OrderIntent, OrderSide, OrderState, OrderType
from tests.unit import test_mock_automation_specification as specification_cases
from tests.unit import test_order_lifecycle as lifecycle_cases


class MockCredentialPurposeContinuityTests(unittest.IsolatedAsyncioTestCase):
    # Alias methods rather than inherit the other TestCase's protection test.
    asyncSetUp = MockCredentialProtectionContinuityTests.asyncSetUp
    close_app = MockCredentialProtectionContinuityTests.close_app
    rotate = MockCredentialProtectionContinuityTests.rotate
    put_settings = MockCredentialProtectionContinuityTests.put_settings
    _bar = specification_cases.MockAutomationWorkflowTests._bar

    async def start_admitted_runner(self):
        await MockAutomationOwnerReplacementTests.start_admitted_runner(
            self, unfilled_execution_policy=specification_cases.MockUnfilledPairedInputTests.unfilled_execution_policy)

    async def _prepare_original_registration(self):
        repository = self.supervisor._repository
        self.assertTrue(repository.save_mock_automation_lease_receipt(
            build_mock_automation_lease_receipt(self.admission, admitted_at=self.admission.requested_at)))
        await self.supervisor.bundle_changed(self.profile, None)
        self.runner = _prepared_runner(self.store, self.bundle, self.spec,
            now_provider=lambda: self.clock, price_source=lambda symbol: {
                "price_won": 990, "available_at": self.clock, "input_ref": "rotation-purpose-price",
                "durable": False, "market_status": "TRADABLE"})
        self.supervisor._runners[self.account_ref] = self.runner
        self.transport.submit.side_effect = lambda intent: BrokerSubmission("broker-" + intent.intent_id, self.clock)
        self.transport.cancel.side_effect = lambda *_args: None
        self.broker_positions = {}
        self.broker_orders = []
        self.broker_closed_orders = []
        self.broker_costs = []
        return repository

    @contextmanager
    def _broker_replies(self):
        # Patch the fixture client class so actual a2 reassembly uses the same
        # external broker state; never replace Monitor/native Risk/acceptance.
        client_type = type(self.bundle.client)
        original = client_type.request_with_continuation
        case = self

        def request(client, api_id, path, body, **kwargs):
            if api_id == "kt00018":
                return {"acnt_evlt_remn_indv_tot": [
                    {"stk_cd": symbol, "rmnd_qty": str(quantity)}
                    for symbol, quantity in case.broker_positions.items()]}, False, ""
            if api_id == "ka10075":
                return {"oso": deepcopy(case.broker_orders)}, False, ""
            if api_id == "ka10076":
                return {"cntr": deepcopy(case.broker_closed_orders)}, False, ""
            if api_id == "kt00015":
                return {"trst_ovrl_trde_prps_array": deepcopy(case.broker_costs)}, False, ""
            return original(client, api_id, path, body, **kwargs)

        with patch.object(client_type, "request_with_continuation", new=request):
            yield

    def _order_reply(self, record, *, remaining, fill_id=None):
        row = {"ord_no": record.broker_order_id, "stk_cd": "005930",
            "ord_qty": str(record.intent.quantity), "oso_qty": str(remaining),
            "ord_pric": str(record.intent.limit_price),
            "io_tp_nm": "매수" if record.intent.side is OrderSide.BUY else "매도"}
        if fill_id is not None:
            row.update(cntr_no=fill_id, unit_cntr_qty=str(record.intent.quantity - remaining),
                unit_cntr_pric=str(record.intent.limit_price))
        return row

    def _decision(self, action, quantity, price, *, reasons=()):
        body = specification_cases._automation_decision(self.bundle.run_id, action, self.clock).to_dict()
        body.pop("decision_id")
        body.update(quantity=quantity, signal_reference_price=price,
            required_capital_won=quantity * price if action == "ENTER" else 0, reasons=reasons)
        return StrategyDecision(decision_id="decision_" + specification_cases._hash(body), **body)

    async def _wait_until(self, predicate):
        async def wait():
            while True:
                value = predicate()
                if value:
                    return value
                await asyncio.sleep(.01)
        try:
            return await asyncio.wait_for(wait(), 3)
        except TimeoutError as error:
            from dataclasses import asdict
            current = self.owner.bundle(self.profile)
            runner = self.supervisor._runners.get(self.account_ref)
            episode = runner.episode if runner is not None else None
            repository = self.supervisor._repository
            risk = repository.load_latest_mock_automation_risk(self.account_ref)
            control = repository.load_mock_automation_control(self.account_ref)
            cut = current.monitor.latest_automation_execution_cut if current is not None else None
            registration = episode._unfilled_registration if episode is not None else None
            root_id = registration['root_intent']['intent_id'] if registration else None
            native = current.runtime.load_intent(root_id) if current is not None and root_id else None
            state = {'clock': self.clock.isoformat(), 'control': control.to_dict() if control else None,
                'status': episode.status if episode else None,
                'checkpoint_pending': episode._checkpoint.pending if episode else None,
                'source_pending': episode._unfilled_source_pending if episode else None,
                'source_generation': episode._unfilled_source_generation if episode else None,
                'source_facts': episode._unfilled_source_facts if episode else None,
                'input_identity': episode._unfilled_input_identity if episode else None,
                'ordinary_ready': episode._unfilled_ordinary_ready if episode else None,
                'population_available_at': (episode._unfilled_population or {}).get('available_at') if episode else None,
                'native_latest_risk': risk.to_dict() if risk else None,
                'cut_orders': [asdict(row) for row in cut['recovery'].orders] if cut else None,
                'cut_account_as_of': cut['recovery'].account.as_of if cut else None,
                'native_root': asdict(native) if native else None,
                'native_root_events': list(current.repository.events(root_id)) if current and root_id else None,
                'plan_result': (episode._unfilled_attempt_plan or {}).get('result') if episode else None}
            raise TimeoutError('C20 purpose timeout state: ' + json.dumps(state, ensure_ascii=True, default=str)) from error

    async def _rotate_and_restore(self, registration):
        await self.rotate("a2", 1)
        current = self.owner.bundle(self.profile)
        await self.supervisor._restore_once()
        self.assertIn(self.account_ref, self.supervisor._runners, self.supervisor._restore_errors)
        restored = self.supervisor._runners[self.account_ref]
        self.assertTrue(restored.episode.is_bound_to(current))
        self.assertIsNotNone(restored._task)
        self.assertEqual(restored._native_risk_listener, current.monitor._automation_risk_listener)
        self.assertTrue(current.monitor.automation_execution_inputs_enabled)
        self.assertEqual(registration, restored.episode._unfilled_registration)
        paused = self.supervisor._repository.load_mock_automation_control(self.account_ref)
        self.assertTrue(paused.entry_resume_required)
        self.assertEqual(self.control.control_revision + 1, paused.control_revision)
        self.assertEqual(self.bundle.run_id, current.run_id)
        return current, restored, paused

    def _publish_pair(self, current, restored, record, *, price=990):
        # Reuse the existing empirical fixture generator and real RAM admission.
        episode = restored.episode
        registration = episode._unfilled_registration
        policy = episode._validate_unfilled_registration(registration)
        observation = current.monitor.latest_position_observation
        held = observation.account.positions.get("005930", 0)
        rows = lifecycle_cases.UnfilledPairedEvidenceTests.outcomes_for(record, policy, episode._unfilled_cost_model,
            self.clock, record.intent.quantity - record.filled_quantity, held,
            registration["target_quantity"], price)
        return episode.offer_unfilled_reprice_outcomes(
            root_intent_id=registration["root_intent"]["intent_id"], managed_intent_id=record.intent.intent_id,
            population_ref="cohort:unfilled", available_at=self.clock, outcomes=rows)

    async def test_actual_key_rotation_preserves_known_flat_buy_cancel_without_entry_resume(self):
        repository = await self._prepare_original_registration()
        with self._broker_replies():
            recovery = await self.bundle.monitor.refresh_recovery()
            risk = repository.load_latest_mock_automation_risk(self.account_ref)
            record_mock_automation_recovery_from_risk(repository, self.bundle.runtime, recovery, risk,
                account_ref=self.account_ref, spec_id=self.spec.spec_id)
            gate, original, _ = self.runner.episode._dispatch_original_and_register(
                self._decision("ENTER", 3, 980), recovery, risk)
            self.assertIsNotNone(original, gate.reasons)
            registration = deepcopy(self.runner.episode._unfilled_registration)
            self.assertEqual("BUY_OPPORTUNITY", registration["purpose"])
            approved = repository.load_mock_automation_approved_gate(self.account_ref, original.intent.intent_id)
            self.broker_orders = [self._order_reply(original, remaining=3)]
            await self.bundle.monitor.refresh_recovery()
            current, restored, paused = await self._rotate_and_restore(registration)
            await self._wait_until(lambda:
                restored.episode.status.get("unfilled_input_delivery", {}).get("state") == "CANCEL_REQUESTED"
                and not restored.episode._checkpoint.pending)
            events = current.repository.events(original.intent.intent_id)
            self.assertEqual(1, sum(row["event_type"] == "CANCEL_STARTED" for row in events))
            self.assertEqual(1, sum(row["event_type"] == "CANCEL_ACCEPTED" for row in events))
            self.assertEqual(1, self.transport.cancel.call_count)
            self.assertEqual(1, self.transport.submit.call_count)
            self.assertEqual("ABANDON", restored.episode.status["after_cancel"])
            self.assertEqual(OrderState.CANCEL_PENDING, current.runtime.load_intent(original.intent.intent_id).state)
            pending_events = current.repository.account_events('mock', self.account_ref)
            cancel_cursor = next(row.accepted_sequence for row in pending_events.events
                if row.intent_id == original.intent.intent_id and row.event_type == 'CANCEL_ACCEPTED')
            # CANCEL/checkpoint ACK and the common Risk publication have separate
            # owners. Await this exact native cancellation cut, not the desired
            # reconciliation value, so the original safety assertion stays real.
            await self._wait_until(lambda: repository.load_latest_mock_automation_risk(
                self.account_ref).execution_event_high_watermark >= cancel_cursor)
            pending_risk = repository.load_latest_mock_automation_risk(self.account_ref)
            self.assertFalse(pending_risk.reconciliation_complete,
                f'risk_revision={pending_risk.reconciliation_revision} risk_cursor={pending_risk.execution_event_high_watermark} '
                f'account_cursor={pending_events.next_cursor}')
            self.assertEqual("UNAVAILABLE", restored.episode.status["unfilled_native_source"]["paired_ev"])
            plan = restored.episode._unfilled_attempt_plan
            self.assertEqual(registration["control_revision"], plan["anchor"]["scope"]["control_revision"])
            self.assertFalse(plan["children"])
            recovery = await current.monitor.refresh_recovery()
            risk = repository.load_latest_mock_automation_risk(self.account_ref)
            linked = record_mock_automation_recovery_from_risk(repository, current.runtime, recovery, risk,
                account_ref=self.account_ref, spec_id=self.spec.spec_id)
            self.assertEqual("BLOCKED", linked.status.value)
            self.assertEqual((risk.snapshot_id, risk.reconciliation_revision),
                (linked.risk_snapshot_id, linked.risk_reconciliation_revision))
            self.assertFalse(risk.reconciliation_complete)
            denied, record, receipt = restored.episode._dispatch_original_and_register(
                self._decision("ENTER", 1, 990), recovery, risk)
            self.assertIn("CREDENTIAL_CHANGE_REQUIRES_EXPLICIT_ENTRY_RESUME", denied.reasons)
            self.assertIsNone(record)
            self.assertIsNone(receipt)
            self.assertEqual(registration, restored.episode._unfilled_registration)
            self.assertEqual(approved, repository.load_mock_automation_approved_gate(self.account_ref, original.intent.intent_id))
            self.assertEqual(paused, repository.load_mock_automation_control(self.account_ref))
            self.assertEqual(1, self.transport.submit.call_count)

    async def test_actual_key_rotation_preserves_take_profit_cost_pair_and_new_pair_after_cancel(self):
        await self._exercise_rotation_profit()

    async def test_current_native_take_profit_child_preserves_registered_management(self):
        await self._exercise_rotation_profit(rotate_child=False)

    async def test_rotation_native_take_profit_child_rejects_missing_claim(self):
        await self._exercise_rotation_profit(child_mutation="claim_absent")

    async def test_rotation_native_take_profit_child_rejects_missing_fixed_approval(self):
        await self._exercise_rotation_profit(child_mutation="fixed_approval_absent")

    async def _exercise_rotation_profit(self, *, child_mutation=None, rotate_child=True):
        repository = await self._prepare_original_registration()
        with self._broker_replies():
            buy = OrderIntent("rotation-profit-buy", self.bundle.run_id, "rotation-profit-owned-position",
                self.account_ref, "mock", "005930", "KRX", OrderSide.BUY, 3, OrderType.LIMIT, 1_000,
                self.clock, self.clock + timedelta(seconds=60), "existing-owned-position/v1")
            self.bundle.runtime.submit_automation_intent(buy,
                AccountSnapshot(self.account_ref, 1_000_000, 0, {}, self.clock),
                spec_id=self.spec.spec_id, control_revision=self.control.control_revision)
            buy_record = self.bundle.runtime.load_intent(buy.intent_id)
            self.broker_positions = {"005930": 3}
            self.broker_orders = [self._order_reply(buy_record, remaining=0, fill_id="rotation-profit-buy-fill")]
            recovery = await self.bundle.monitor.refresh_recovery()
            self.assertEqual(OrderState.FILLED, self.bundle.runtime.load_intent(buy.intent_id).state)
            risk = repository.load_latest_mock_automation_risk(self.account_ref)
            record_mock_automation_recovery_from_risk(repository, self.bundle.runtime, recovery, risk,
                account_ref=self.account_ref, spec_id=self.spec.spec_id)
            gate, original, _ = self.runner.episode._dispatch_original_and_register(
                self._decision("EXIT", 3, 1_000, reasons=("target_reached",)), recovery, risk)
            self.assertIsNotNone(original, gate.reasons)
            registration = deepcopy(self.runner.episode._unfilled_registration)
            self.assertEqual("TAKE_PROFIT", registration["purpose"])
            approved = repository.load_mock_automation_approved_gate(self.account_ref, original.intent.intent_id)
            self.broker_orders = [self._order_reply(original, remaining=3)]
            await self.bundle.monitor.refresh_recovery()
            current, restored, paused = await self._rotate_and_restore(registration)
            self.assertIsNone(restored.episode._unfilled_population)
            self.assertIsNone(restored.episode._unfilled_judgment)
            await self._wait_until(lambda:
                restored.episode.status.get("unfilled_native_source", {}).get("paired_ev") == "UNAVAILABLE"
                and restored.episode.status.get("unfilled_input_delivery", {}).get("state") == "BLOCKED"
                and not restored.episode._checkpoint.pending)
            self.transport.cancel.assert_not_called()

            # Supply a fresh actual family input through the existing consumer;
            # never copy/reseal an old private judgment across a2 restoration.
            await asyncio.wait_for(current.monitor._queue.join(), 3)
            self.clock += timedelta(seconds=3.1)
            self.observations = []
            self._bar(self.clock - timedelta(seconds=62), 1_050, 1_060)
            await restored.episode._consume(self.observations[-1])
            judgment = restored.episode._unfilled_judgment
            self.assertIsNotNone(judgment)
            self.assertEqual(("EXIT", ("target_reached",)), (judgment["final_action"], tuple(judgment["reasons"])))
            self.assertTrue(self._publish_pair(current, restored, current.runtime.load_intent(original.intent.intent_id)))
            await current.monitor.refresh_recovery()
            await asyncio.wait_for(current.monitor._queue.join(), 3)
            await self._wait_until(lambda:
                "unfilled_cancel_current_cost_evidence_unavailable" in
                restored.episode.status.get("unfilled_input_delivery", {}).get("reason", "")
                and not restored.episode._checkpoint.pending)
            self.transport.cancel.assert_not_called()
            self.assertFalse(repository.load_latest_mock_automation_risk(self.account_ref).cost_complete)
            self.assertEqual(2, self.transport.submit.call_count)

            # Native Risk learns actual settlement rows from the existing reader.
            day = self.clock.strftime("%Y%m%d")
            self.broker_costs = [{"cntr_dt": day, "trde_dt": day, "stk_cd": "005930", "io_tp_nm": "매수",
                "trde_amt": "3000", "exct_amt": "3000", "cmsn": "0", "trde_agri_tax": "0", "tax_sum_cmsn": "0"}]
            await current.monitor.refresh_recovery()
            await asyncio.wait_for(current.monitor._queue.join(), 3)
            await self._wait_until(lambda:
                restored.episode.status.get("unfilled_input_delivery", {}).get("state") == "CANCEL_REQUESTED"
                and not restored.episode._checkpoint.pending)
            self.assertTrue(repository.load_latest_mock_automation_risk(self.account_ref).cost_complete)
            events = current.repository.events(original.intent.intent_id)
            self.assertEqual(1, sum(row["event_type"] == "CANCEL_STARTED" for row in events))
            self.assertEqual(1, sum(row["event_type"] == "CANCEL_ACCEPTED" for row in events))
            self.assertEqual(1, self.transport.cancel.call_count)
            self.assertEqual("REPRICE", restored.episode.status["after_cancel"])
            self.assertEqual("TAKE_PROFIT", restored.episode._unfilled_attempt_plan["result"]["purpose"])
            self.assertEqual(2, self.transport.submit.call_count)

            # ka10076's explicit cancelled zero-fill snapshot is the broker
            # confirmation. oso_qty=0 alone would incorrectly imply a full fill.
            before_confirmation = current.runtime.load_intent(original.intent.intent_id)
            self.assertEqual(self.clock, before_confirmation.last_broker_as_of)
            # The accepted cancel ACK precedes this new broker confirmation.
            # Keep the original deadline and old pair's publication time fixed.
            self.clock += timedelta(milliseconds=1)
            self.broker_orders = []
            self.broker_closed_orders = [{**self._order_reply(original, remaining=0), "ord_stt": "취소", "cntr_qty": "0"}]
            confirmation = await current.monitor.refresh_recovery()
            broker_confirmation = next(row for row in confirmation.orders if row.broker_order_id == original.broker_order_id)
            self.assertEqual((OrderState.CANCELLED, 0), (broker_confirmation.state, broker_confirmation.filled_quantity))
            self.assertGreater(broker_confirmation.as_of, before_confirmation.last_broker_as_of)
            await asyncio.wait_for(current.monitor._queue.join(), 3)
            await self._wait_until(lambda:
                current.runtime.load_intent(original.intent.intent_id).state is OrderState.CANCELLED
                and restored.episode.status.get("unfilled_native_source", {}).get("paired_ev") == "UNAVAILABLE"
                and not restored.episode._checkpoint.pending)
            self.assertFalse(restored.episode._unfilled_attempt_plan["children"])
            self.assertEqual(2, self.transport.submit.call_count)
            self.clock += timedelta(milliseconds=1)
            self.assertTrue(self._publish_pair(current, restored, current.runtime.load_intent(original.intent.intent_id)))
            await current.monitor.refresh_recovery()
            await asyncio.wait_for(current.monitor._queue.join(), 3)
            await self._wait_until(lambda: restored.episode._unfilled_attempt_plan["children"]
                and self.transport.submit.call_count == 3 and not restored.episode._checkpoint.pending)
            plan = restored.episode._unfilled_attempt_plan
            child_id = plan["children"][-1]["intent"]["intent_id"]
            child = current.runtime.load_intent(child_id)
            self.assertEqual((3, 990, OrderSide.SELL, OrderState.ACCEPTED),
                (child.intent.quantity, child.intent.limit_price, child.intent.side, child.state))
            # The native enrollment preserves the original full EXIT target=0;
            # partial REDUCE remains a separate existing explicit-target path.
            self.assertEqual(0, registration["target_quantity"])
            self.assertEqual(("TAKE_PROFIT", "EXIT"),
                (plan["replacement_gate"]["purpose"], plan["replacement_gate"]["action"]))
            self.assertEqual(registration["control_revision"], plan["anchor"]["scope"]["control_revision"])
            self.assertEqual(registration, restored.episode._unfilled_registration)
            self.assertEqual(approved, repository.load_mock_automation_approved_gate(self.account_ref, original.intent.intent_id))
            self.assertEqual(paused, repository.load_mock_automation_control(self.account_ref))
            self.assertEqual(1, self.transport.cancel.call_count)

            # Keep the actual submitted v3 claim/fixed approval and original anchor.
            child_slot, child_anchor = deepcopy(plan["children"][-1]), deepcopy(plan["anchor"])
            child_claim = current.repository.claim_approval(child_id)
            self.assertIsNotNone(child_claim)
            child_fixed = repository.load_mock_automation_approved_gate(self.account_ref, child_id)
            child_submitted = repository.load_mock_automation_decision_gate(self.account_ref, child_claim["gate_id"])
            self.assertIsNotNone(child_fixed)
            self.assertIsNotNone(child_submitted)
            self.assertEqual("mock_automation_replacement_gate/v3", child_fixed.version)
            self.assertEqual(paused.control_revision, child_submitted.control_revision)
            self.assertEqual((child_claim["risk_snapshot_id"], child_claim["risk_reconciliation_revision"]),
                (child_submitted.risk_snapshot_id, child_submitted.risk_reconciliation_revision))
            await asyncio.wait_for(current.monitor._queue.join(), 3)
            native_reached, native_errors = threading.Event(), []
            append, injected = self.store.append_execution_event, False

            def before_child_native(value, event, **options):
                nonlocal injected
                if event["event_type"] == "CANCEL_STARTED" and value["intent_id"] == child_id and not injected:
                    injected = True
                    try:
                        self.assertEqual(managing_control.control_revision, options["ownership"]["control_revision"])
                        if child_mutation == "claim_absent":
                            with self.store._lock, self.store._connection() as connection:
                                row = connection.execute("SELECT document_json FROM central_execution_intents WHERE intent_id=?",
                                    (child_id,)).fetchone()
                                stored = json.loads(row[0])
                                self.assertEqual(child_claim, stored.pop("mock_replacement_claim"))
                                connection.execute("UPDATE central_execution_intents SET document_json=? WHERE intent_id=?",
                                    (json.dumps(stored), child_id))
                        elif child_mutation == "fixed_approval_absent":
                            with self.store._lock, self.store._connection() as connection:
                                connection.execute("DELETE FROM central_documents WHERE collection=? AND owner=? AND document_key=?",
                                    ("execution_mock_automation_approved_gates", self.account_ref, child_id))
                        elif child_mutation is not None:
                            raise AssertionError(child_mutation)
                        return append(value, event, **options)
                    except Exception as error:
                        native_errors.append(str(error))
                        raise
                    finally:
                        native_reached.set()
                return append(value, event, **options)

            # Keep the root CANCELLED row; report this accepted child as open.
            with patch.object(self.store, "append_execution_event", side_effect=before_child_native):
                self.broker_orders = [self._order_reply(child, remaining=3)]
                self.clock += timedelta(milliseconds=1)
                if rotate_child:
                    await self.rotate("a3", 2)
                    managed = self.owner.bundle(self.profile)
                    await self.supervisor._restore_once()
                    self.assertIn(self.account_ref, self.supervisor._runners, self.supervisor._restore_errors)
                    manager = self.supervisor._runners[self.account_ref]
                    self.assertTrue(manager.episode.is_bound_to(managed))
                    self.assertIsNotNone(manager._task)
                    self.assertEqual(manager._native_risk_listener, managed.monitor._automation_risk_listener)
                    self.assertTrue(managed.monitor.automation_execution_inputs_enabled)
                    self.assertTrue(restored.episode._closing)
                    self.assertIsNone(restored._task)
                    self.assertTrue(current.runtime._retired)
                    self.assertIsNone(manager.episode._unfilled_population)
                    self.assertIsNone(manager.episode._unfilled_judgment)
                else:
                    managed, manager = current, restored
                    await managed.monitor.refresh_recovery()
                managing_control = repository.load_mock_automation_control(self.account_ref)
                self.assertTrue(managing_control.entry_resume_required)
                self.assertEqual(paused.control_revision + int(rotate_child), managing_control.control_revision)
                self.assertEqual(self.bundle.run_id, managed.run_id)
                self.assertEqual(registration, manager.episode._unfilled_registration)
                self.assertEqual(child_slot, manager.episode._unfilled_attempt_plan["children"][-1])
                self.assertEqual(child_anchor, manager.episode._unfilled_attempt_plan["anchor"])
                self.assertEqual(child.intent, managed.runtime.load_intent(child_id).intent)
                self.assertEqual(child_claim, managed.repository.claim_approval(child_id))
                self.assertEqual(child_fixed, repository.load_mock_automation_approved_gate(self.account_ref, child_id))
                self.assertEqual(child_submitted, repository.load_mock_automation_decision_gate(self.account_ref, child_claim["gate_id"]))
                await asyncio.wait_for(managed.monitor._queue.join(), 3)
                await self._wait_until(lambda: not manager.episode._checkpoint.pending)
                # Same-price 990 is a legitimate WAIT. Fresh 991 differs from
                # this child while respecting the original 1000/100bps bound.
                self.rotation_market_price = 991
                self.clock += timedelta(milliseconds=1)
                self._bar(self.clock - timedelta(seconds=62), 1_050, 1_060)
                await manager.episode._consume(self.observations[-1])
                judgment = manager.episode._unfilled_judgment
                self.assertEqual(("EXIT", ("target_reached",)),
                    (judgment["final_action"], tuple(judgment["reasons"])))
                self.assertTrue(self._publish_pair(managed, manager, managed.runtime.load_intent(child_id), price=991))
                await managed.monitor.refresh_recovery()
                await asyncio.wait_for(managed.monitor._queue.join(), 3)
                self.assertTrue(repository.load_latest_mock_automation_risk(self.account_ref).cost_complete)
                self.assertEqual("TAKE_PROFIT", manager.episode._unfilled_registration["purpose"])
                if child_mutation is not None:
                    self.assertTrue(await asyncio.to_thread(native_reached.wait, 3), manager.episode.status)
                    await self.supervisor.close()
                    self.assertTrue(native_errors)
                    expected = ("MOCK_UNFILLED_MANAGEMENT_CHILD_APPROVAL_CHANGED" if child_mutation == "claim_absent"
                        else "MOCK_UNFILLED_MANAGEMENT_PROOF_MISSING")
                    self.assertIn(expected, native_errors[0])
                    self.assertFalse(any(row["event_type"] == "CANCEL_STARTED" for row in managed.repository.events(child_id)))
                    self.assertEqual(OrderState.ACCEPTED, managed.runtime.load_intent(child_id).state)
                    self.assertEqual(1, self.transport.cancel.call_count)
                else:
                    await self._wait_until(lambda:
                        manager.episode.status.get("unfilled_input_delivery", {}).get("state") == "CANCEL_REQUESTED"
                        and not manager.episode._checkpoint.pending)
                    events = managed.repository.events(child_id)
                    self.assertEqual(1, sum(row["event_type"] == "CANCEL_STARTED" for row in events))
                    self.assertEqual(1, sum(row["event_type"] == "CANCEL_ACCEPTED" for row in events))
                    self.assertEqual(OrderState.CANCEL_PENDING, managed.runtime.load_intent(child_id).state)
                    self.assertEqual(2, self.transport.cancel.call_count)
                    self.assertEqual(child_claim, managed.repository.claim_approval(child_id))
                    self.assertEqual(child_fixed, repository.load_mock_automation_approved_gate(self.account_ref, child_id))
                self.assertTrue(injected)
                self.assertEqual(3, self.transport.submit.call_count)
                self.assertEqual([child_slot], manager.episode._unfilled_attempt_plan["children"])
                self.assertEqual(child_anchor, manager.episode._unfilled_attempt_plan["anchor"])
                self.assertEqual(registration, manager.episode._unfilled_registration)
                self.assertEqual(approved, repository.load_mock_automation_approved_gate(self.account_ref, original.intent.intent_id))
                self.assertEqual(managing_control, repository.load_mock_automation_control(self.account_ref))



if __name__ == "__main__":
    unittest.main()
