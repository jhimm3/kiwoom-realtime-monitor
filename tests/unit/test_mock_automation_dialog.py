from __future__ import annotations

import unittest

from kiwoom_monitor.presentation.mock_automation_dialog import (
    MockAutomationWorker,
    available_mock_profiles,
    operation_availability,
)


PROFILE = {
    "provider": "kiwoom_mock", "profile_id": "mock-one", "label": "모의 1",
    "configured": True, "supported": True, "disabled": False,
    "account_ref": "account-one", "revision": 4,
}


class FakeCredentials:
    def load(self):
        return {"profiles": [
            {**PROFILE, "profile_id": "disabled", "disabled": True},
            {**PROFILE, "profile_id": "real", "provider": "kiwoom_real"},
            PROFILE,
        ]}

    def account_settings(self, account_ref):
        return {"settings": {"scope": {"account_ref": account_ref}, "revision": 7}}


class FakeContent:
    def load_mock_automation_candidates(self, account_ref, *, credential_profile_id):
        return {
            "account_ref": account_ref,
            "binding": {
                "credential_profile_id": credential_profile_id,
                "broker": "kiwoom", "environment": "mock", "account_ref": account_ref,
                "binding_revision": 2, "verified_at": "2026-09-21T00:00:00+00:00",
                "verification_method": "ka00001",
            },
            "candidates": [],
        }

    def load_candidate_events(self, *, after_sequence, limit):
        return {"events": [], "has_more": False, "next_cursor": None}

    def load_mock_automation_specs(self, account_ref):
        return {"account_ref": account_ref, "specs": [
            {"spec": {"spec_id": "blocked", "strategy_ref": "s"},
             "readiness": "BLOCKED", "reasons": ["TBD"]},
            {"spec": {"spec_id": "ready", "strategy_ref": "s"},
             "readiness": "READY", "reasons": []},
        ]}

    def load_mock_automation_status(self, account_ref, *, credential_profile_id):
        return {
            "account_ref": account_ref, "credential_profile_id": credential_profile_id,
            "runtime_mode": None, "control": None, "runner": None, "restore_error": "",
        }


class MockAutomationDialogPolicyTests(unittest.TestCase):
    def test_only_configured_active_mock_profiles_are_selectable(self):
        values = available_mock_profiles(FakeCredentials().load())

        self.assertEqual(("mock-one",), tuple(value["profile_id"] for value in values))

    def test_worker_context_selects_latest_ready_spec_and_account_revisions(self):
        worker = MockAutomationWorker(FakeContent(), FakeCredentials(), "refresh")

        context = worker._load_context("", "")

        self.assertEqual("mock-one", context["selected_profile"]["profile_id"])
        self.assertEqual(7, context["account_settings"]["revision"])
        self.assertEqual(2, context["candidate_binding"]["binding_revision"])
        self.assertEqual("ready", context["selected_spec_id"])
        self.assertTrue(operation_availability(context)["start"])

    def test_controls_follow_persisted_state_and_exact_active_spec(self):
        base = {
            "selected_profile": PROFILE,
            "selected_spec_id": "ready",
            "specs": [{"spec": {"spec_id": "ready"}, "readiness": "READY"}],
        }
        running = operation_availability({
            **base, "status": {
                "runtime_mode": "automatic", "runner": {"state": "READY"},
                "control": {"desired_state": "RUNNING", "active_spec_id": "ready"},
            },
        })
        stopped = operation_availability({
            **base, "status": {
                "runtime_mode": "manual", "runner": None,
                "control": {"desired_state": "STOPPED", "active_spec_id": "ready"},
            },
        })

        self.assertEqual(
            {"start": False, "stop": True, "resume": False}, running,
        )
        self.assertEqual(
            {"start": False, "stop": False, "resume": True}, stopped,
        )


if __name__ == "__main__":
    unittest.main()
