from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    def __init__(self, store, bundle, spec, *, poll_seconds):
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


if __name__ == "__main__":
    unittest.main()
