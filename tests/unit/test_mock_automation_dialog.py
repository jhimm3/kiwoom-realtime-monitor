from __future__ import annotations

import unittest
import json
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.central_server.mock_runtime import MockAccountRuntimeMode

from kiwoom_monitor.presentation.mock_automation_dialog import (
    MockAutomationWorker,
    MockAutomationDialog,
    available_mock_profiles,
    operation_availability,
)
from kiwoom_monitor.presentation.mock_automation_candidate_dialog import (
    MockAutomationCandidateDialog, MockCandidateFileWorker,
)
from kiwoom_monitor import research_candidate as candidate_files
from tests.unit.test_mock_automation_candidate import _package, _policy
from kiwoom_monitor.application.mock_automation_candidate import evaluate_candidate_eligibility


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
        return {"settings": {
            "scope": {"broker": "kiwoom", "environment": "mock", "account_ref": account_ref},
            "revision": 7, "active_profile_id": PROFILE["profile_id"],
            "monitor_enabled": True, "mock_order_enabled": True,
        }}


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
    def test_operational_refresh_and_commands_never_load_publication_shadow_history(self):
        content, credentials = FakeContent(), FakeCredentials()
        content.load_candidate_events = Mock(side_effect=AssertionError("operational Shadow read"))
        credentials.load = Mock(wraps=credentials.load)
        for operation in ("refresh", "refresh", "start", "stop", "resume", "publish_spec"):
            with self.subTest(operation=operation):
                if operation != "refresh":
                    name = "publish_mock_automation_spec" if operation == "publish_spec" else f"{operation}_mock_automation"
                    setattr(content, name, Mock(return_value={"status": "acknowledged"}))
                completed, failed = Mock(), Mock()
                worker = MockAutomationWorker(content, credentials, operation)
                worker.completed.connect(completed); worker.failed.connect(failed)
                worker.run()
                completed.assert_called_once(); failed.assert_not_called()
                context = completed.call_args.args[1]
                self.assertEqual(("mock-one", "ready", 7, 2), (
                    context["selected_profile"]["profile_id"], context["selected_spec_id"],
                    context["account_settings"]["revision"], context["candidate_binding"]["binding_revision"],
                ))
                self.assertNotIn("shadow_events", context)
                self.assertTrue(operation_availability(context)["start"])
        self.assertEqual(6, credentials.load.call_count)
        content.load_candidate_events.assert_not_called()

    def test_worker_distinguishes_command_failure_from_ack_followup_read_failure(self):
        for operation in ("start", "stop", "resume", "publish_spec"):
            for failure in ("command", "context", "status"):
                with self.subTest(operation=operation, failure=failure):
                    content, credentials = FakeContent(), FakeCredentials()
                    command = Mock(return_value={"status": "acknowledged"})
                    setattr(content, "publish_mock_automation_spec" if operation == "publish_spec" else f"{operation}_mock_automation", command)
                    if failure == "command":
                        command.side_effect = RuntimeError("command rejected")
                    elif failure == "context":
                        credentials.load = Mock(side_effect=OSError("followup unavailable"))
                    else:
                        content.load_mock_automation_status = Mock(side_effect=RuntimeError("status unavailable"))
                    completed, failed = Mock(), Mock()
                    worker = MockAutomationWorker(content, credentials, operation, {"account_ref": "same"})
                    worker.completed.connect(completed); worker.failed.connect(failed)
                    worker.run()
                    command.assert_called_once_with(account_ref="same")
                    if failure == "command":
                        failed.assert_called_once_with(operation, "command rejected")
                        completed.assert_not_called()
                    else:
                        completed.assert_called_once(); failed.assert_not_called()
                        context = completed.call_args.args[1]
                        self.assertIn("unavailable", context["status"]["status_query_error"])
                        self.assertEqual(failure == "context", "context_refresh_error" in context)
                        self.assertEqual({"start": False, "stop": False, "resume": False}, operation_availability(context))

    def test_publication_demand_read_rechecks_selection_and_keeps_existing_page_guards(self):
        selected = candidate_documents()
        other = candidate_documents(minimum_closed_trades=1)
        values = {"account_ref": PROFILE["account_ref"], "credential_profile_id": PROFILE["profile_id"],
                  "candidate_publication": deepcopy(selected)}
        content, credentials = FakeContent(), FakeCredentials()
        original_load = content.load_mock_automation_candidates
        content.load_mock_automation_candidates = Mock(side_effect=lambda *args, **kwargs: {
            **original_load(*args, **kwargs), "candidates": [deepcopy(other), deepcopy(selected)],
        })
        content.load_candidate_events = Mock(side_effect=(
            {"events": [{"event_id": "first"}], "has_more": True, "next_cursor": 1},
            {"events": [{"event_id": "second"}], "has_more": False, "next_cursor": None},
        ))
        completed, failed = Mock(), Mock()
        worker = MockAutomationWorker(content, credentials, "prepare_publication", values)
        worker.completed.connect(completed); worker.failed.connect(failed); worker.run()
        failed.assert_not_called(); completed.assert_called_once()
        self.assertEqual(selected, completed.call_args.args[1]["publication_candidate"])
        self.assertEqual(["first", "second"], [row["event_id"] for row in completed.call_args.args[1]["shadow_events"]])
        self.assertEqual([{"after_sequence": 0, "limit": 1000}, {"after_sequence": 1, "limit": 1000}],
                         [call.kwargs for call in content.load_candidate_events.call_args_list])
        for changed in ("candidate", "profile", "account"):
            with self.subTest(changed=changed):
                changed_credentials = FakeCredentials()
                if changed != "candidate":
                    changed_credentials.load = Mock(return_value={"profiles": [{**PROFILE, {
                        "profile": "profile_id", "account": "account_ref"}[changed]: "other"}]})
                content.load_mock_automation_candidates = Mock(side_effect=lambda *args, **kwargs: {
                    **original_load(*args, **kwargs), "candidates": [deepcopy(other)] if changed == "candidate" else [deepcopy(selected)],
                })
                content.load_candidate_events.reset_mock()
                completed, failed = Mock(), Mock()
                worker = MockAutomationWorker(content, changed_credentials, "prepare_publication", values)
                worker.completed.connect(completed); worker.failed.connect(failed); worker.run()
                completed.assert_not_called(); failed.assert_called_once()
                content.load_candidate_events.assert_not_called()
        for page, message, expected_calls in (
            ({"events": {}, "has_more": False}, "형식", 1),
            ({"events": [], "has_more": True, "next_cursor": 0}, "전진", 1),
            ({"events": [], "has_more": True, "next_cursor": "invalid"}, "invalid", 1),
            (None, "100,000", 100),
        ):
            with self.subTest(page=page):
                content.load_candidate_events = Mock(side_effect=lambda *, after_sequence, limit: page if page is not None else {
                    "events": [], "has_more": True, "next_cursor": after_sequence + 1,
                })
                worker = MockAutomationWorker(content, credentials, "refresh")
                with self.assertRaisesRegex((RuntimeError, ValueError), message):
                    worker._load_shadow_events()
                self.assertEqual(expected_calls, content.load_candidate_events.call_count)

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
            "account_settings": FakeCredentials().account_settings(PROFILE["account_ref"])["settings"],
            "selected_spec_id": "ready",
            "specs": [{"spec": {"spec_id": "ready"}, "readiness": "READY"}],
        }
        running = operation_availability({
            **base, "status": {
                "runtime_mode": MockAccountRuntimeMode.AUTOMATIC.value, "runner": {"state": "READY"},
                "control": {"desired_state": "RUNNING", "active_spec_id": "ready"},
            },
        })
        stopped = operation_availability({
            **base, "status": {
                "runtime_mode": MockAccountRuntimeMode.MANUAL.value, "runner": None,
                "control": {"desired_state": "STOPPED", "active_spec_id": "ready"},
            },
        })

        self.assertEqual(
            {"start": False, "stop": True, "resume": False}, running,
        )
        self.assertEqual(
            {"start": False, "stop": False, "resume": True}, stopped,
        )
        managing = operation_availability({
            **base, "status": {
                "runtime_mode": MockAccountRuntimeMode.AUTOMATIC.value, "runner": {"state": "READY"},
                "control": {"desired_state": "RUNNING", "active_spec_id": "ready", "entry_resume_required": True},
            },
        })
        self.assertEqual({"start": False, "stop": True, "resume": True}, managing)

    def test_failed_status_read_does_not_make_ready_spec_startable(self):
        content = FakeContent()
        with patch.object(content, "load_mock_automation_status", side_effect=RuntimeError("HTTP 503")):
            context = MockAutomationWorker(content, FakeCredentials(), "refresh")._load_context("", "")

        self.assertEqual("ready", context["selected_spec_id"])
        self.assertEqual(
            {"start": False, "stop": False, "resume": False}, operation_availability(context),
        )

    def test_restore_error_blocks_new_start_but_keeps_existing_stop(self):
        context = MockAutomationWorker(FakeContent(), FakeCredentials(), "refresh")._load_context("", "")
        context["status"]["restore_error"] = "RECOVERY_REQUIRED"
        self.assertFalse(operation_availability(context)["start"])

        context["status"].update(
            runtime_mode=MockAccountRuntimeMode.AUTOMATIC.value,
            control={"desired_state": "RUNNING", "active_spec_id": "ready"},
        )
        self.assertTrue(operation_availability(context)["stop"])

    def test_disabled_account_permissions_block_start_and_resume_but_keep_stop(self):
        for flag in ("monitor_enabled", "mock_order_enabled"):
            with self.subTest(flag=flag):
                context = MockAutomationWorker(FakeContent(), FakeCredentials(), "refresh")._load_context("", "")
                context["account_settings"][flag] = False
                self.assertFalse(operation_availability(context)["start"])
                context["status"].update(control={"desired_state": "STOPPED", "active_spec_id": "ready"})
                self.assertFalse(operation_availability(context)["resume"])
                context["status"].update(
                    runtime_mode=MockAccountRuntimeMode.AUTOMATIC.value,
                    control={"desired_state": "RUNNING", "active_spec_id": "ready"},
                )
                self.assertTrue(operation_availability(context)["stop"])

    def test_missing_malformed_or_other_account_settings_block_start_and_resume(self):
        settings = FakeCredentials().account_settings(PROFILE["account_ref"])["settings"]
        for value in (
            None, {}, {**settings, "monitor_enabled": 1}, {**settings, "mock_order_enabled": "true"},
            {**settings, "active_profile_id": "other"}, {**settings, "revision": True},
            {**settings, "revision": -1},
            {**settings, "scope": {**settings["scope"], "account_ref": "other"}},
            {**settings, "scope": {**settings["scope"], "environment": "real"}},
        ):
            with self.subTest(settings=value):
                context = MockAutomationWorker(FakeContent(), FakeCredentials(), "refresh")._load_context("", "")
                context["account_settings"] = value
                self.assertFalse(operation_availability(context)["start"])
                context["status"].update(control={"desired_state": "STOPPED", "active_spec_id": "ready"})
                self.assertFalse(operation_availability(context)["resume"])


class MockAutomationDialogControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch("kiwoom_monitor.presentation.mock_automation_dialog.QSettings") as settings:
            settings.return_value.value.return_value = None
            self.dialog = MockAutomationDialog(FakeContent(), FakeCredentials())
        self.context = MockAutomationWorker(FakeContent(), FakeCredentials(), "refresh")._load_context("", "")
        self.dialog._completed("refresh", self.context)

    def tearDown(self):
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_close_candidate_opens_explicit_ready_review_without_starting_orders(self):
        from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION

        candidate = {'package': {'strategy_ref': 'close-test',
                     'candidate_spec': {'version': CLOSE_FINAL_CANDIDATE_VERSION},
                     'source_final': {'finished_at': '2026-09-21T07:00:00+00:00'}},
                     'eligibility_receipt': {'status': 'ELIGIBLE'}}
        self.context.update(candidate_publications=[candidate], specs=[], selected_spec_id='')
        self.dialog._completed('refresh', self.context)
        self.assertIn('종가베팅 · ELIGIBLE', self.dialog._candidate.currentText())
        self.assertEqual('종가베팅 READY 명세 작성', self.dialog._publish.text())
        event = {'event_id': 'stored-close-event', 'available_at': '2026-09-11T15:17:02+09:00'}
        context = {**self.context, 'publication_candidate': candidate, 'shadow_events': [event]}
        with (patch('kiwoom_monitor.presentation.mock_automation_dialog.matching_shadow_events', return_value=(event,)),
              patch('kiwoom_monitor.presentation.mock_automation_dialog.MockAutomationPublicationDialog') as modal):
            modal.return_value.exec.return_value = 0  # Review cancellation never posts or starts.
            self.dialog._publish_prepared_candidate(context)
            modal.assert_called_once_with(candidate, context['candidate_binding'],
                                           context['selected_profile']['profile_id'], (event,), self.dialog)
        self.assertEqual(0, self.dialog._spec.count())
        self.assertFalse(self.dialog._start.isEnabled())

    def test_ipo_candidates_have_distinct_shadow_review_without_borrowing_close_authority(self):
        from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION

        for family, label in (('ipo_close/v1', '신규주 종가베팅'), ('ipo_breakout/v1', '신규주 돌파')):
            with self.subTest(family=family):
                candidate = {'package': {'strategy_ref': 'ipo-test',
                    'candidate_spec': {'version': CLOSE_FINAL_CANDIDATE_VERSION, 'family': family},
                    'source_final': {'finished_at': '2026-09-21T07:00:00+00:00'}},
                    'eligibility_receipt': {'status': 'ELIGIBLE'}}
                self.context.update(candidate_publications=[candidate], specs=[], selected_spec_id='')
                self.dialog._completed('refresh', self.context)
                self.assertIn(label + ' · ELIGIBLE', self.dialog._candidate.currentText())
                self.assertEqual(label + ' READY 명세 작성', self.dialog._publish.text())
                event = {'event_id': 'stored-ipo-event', 'available_at': '2026-09-11T15:17:02+09:00'}
                context = {**self.context, 'publication_candidate': candidate, 'shadow_events': [event]}
                with (patch('kiwoom_monitor.presentation.mock_automation_dialog.matching_shadow_events', return_value=(event,)),
                      patch('kiwoom_monitor.presentation.mock_automation_dialog.MockAutomationPublicationDialog') as modal):
                    self.dialog._publish_prepared_candidate(context)
                    modal.assert_called_once_with(candidate, context['candidate_binding'],
                        context['selected_profile']['profile_id'], (event,), self.dialog)
                self.assertEqual(0, self.dialog._spec.count())
                self.assertFalse(self.dialog._start.isEnabled())

    def test_ipo_ready_review_requires_explicit_prices_and_preserves_its_family(self):
        from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE
        from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationPublicationDialog

        for family in ('ipo_close/v1', 'ipo_breakout/v1'):
            candidate = {'package': {'candidate_spec': {'version': CLOSE_FINAL_CANDIDATE_VERSION, 'family': family}}}
            dialog = MockAutomationPublicationDialog(candidate, {}, 'profile', ({'event_id': 'shadow'},), self.dialog)
            self.addCleanup(dialog.deleteLater)
            with patch('kiwoom_monitor.presentation.mock_automation_dialog.build_mock_automation_publication_documents') as build:
                with self.assertRaisesRegex(ValueError, '명시하세요'):
                    dialog.publication_values()
                self.assertEqual(0, build.call_count)
                dialog._close_inputs['maximum_adverse_slippage_bps'].setText('100')
                dialog._close_inputs['protective_slippage_bps'].setText('200')
                with self.assertRaisesRegex(ValueError, '명시하세요'):
                    dialog.publication_values()
                dialog._close_management.setCurrentIndex(2)
                dialog.publication_values()
                self.assertEqual(candidate, build.call_args.kwargs['candidate_publication'])
                self.assertEqual(CLOSE_NATIVE_PLAN_MODE, build.call_args.kwargs['execution_plan_mode'])
                self.assertEqual('continue_original_plan/v1', build.call_args.kwargs['close_execution_policy'].stopped_management)
                self.assertEqual('reassess_then_amend_price/v1', build.call_args.kwargs['close_execution_policy'].accepted_order_management)
                self.assertIsNone(build.call_args.kwargs['unfilled_execution_policy'])

    def test_close_ready_review_requires_explicit_prices_and_stopped_management(self):
        from kiwoom_monitor.domain.execution_activation import CLOSE_NATIVE_PLAN_MODE
        from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationPublicationDialog

        candidate = {'package': {'candidate_spec': {'version': CLOSE_FINAL_CANDIDATE_VERSION}}}
        dialog = MockAutomationPublicationDialog(candidate, {}, 'profile', ({'event_id': 'shadow'},), self.dialog)
        self.addCleanup(dialog.deleteLater)
        with patch('kiwoom_monitor.presentation.mock_automation_dialog.build_mock_automation_publication_documents') as build:
            with self.assertRaisesRegex(ValueError, '명시하세요'):
                dialog.publication_values()
            self.assertEqual(0, build.call_count)
            dialog._close_inputs['maximum_adverse_slippage_bps'].setText('100')
            dialog._close_inputs['protective_slippage_bps'].setText('200')
            with self.assertRaisesRegex(ValueError, '명시하세요'):
                dialog.publication_values()
            for version in ('v3', 'v4'):
                dialog._close_management.setCurrentIndex(1 if version == 'v3' else 2)
                dialog.publication_values()
                args = build.call_args.kwargs
                self.assertEqual(CLOSE_NATIVE_PLAN_MODE, args['execution_plan_mode'])
                self.assertIsNone(args['unfilled_execution_policy'])
                policy = args['close_execution_policy']
                self.assertEqual((100, 200, 'close-mock-execution-policy/' + version),
                    (policy.maximum_adverse_slippage_bps, policy.protective_slippage_bps, policy.version))
                self.assertEqual(None if version == 'v3' else 'continue_original_plan/v1', policy.stopped_management)
                self.assertEqual('reassess_then_amend_price/v1', policy.accepted_order_management)
            dialog._close_inputs['protective_slippage_bps'].setText('10000')
            with self.assertRaisesRegex(ValueError, 'basis points'):
                dialog.publication_values()

    def test_server_automatic_mode_enables_stop_for_active_spec(self):
        self.context["status"].update(
            runtime_mode=MockAccountRuntimeMode.AUTOMATIC.value,
            control={"desired_state": "RUNNING", "active_spec_id": "ready"},
            runner={"state": "READY"},
        )
        self.dialog._completed("refresh", self.context)
        self.assertTrue(self.dialog._stop.isEnabled())
        self.assertFalse(self.dialog._start.isEnabled())

        for desired, runner, runner_state in (
            ("RUNNING", {"state": "READY"}, "READY"),
            ("STOPPED", None, "없음"),
        ):
            with self.subTest(desired_state=desired):
                self.context["status"].update(
                    control={"desired_state": desired, "active_spec_id": "ready",
                        "entry_resume_required": True},
                    runner=runner,
                )
                self.dialog._completed("refresh", self.context)
                self.assertEqual(
                    f"모드 AUTOMATIC · 제어 {desired} · 신규 진입 재개 대기 · runner {runner_state}",
                    self.dialog._runtime.text(),
                )
                self.assertNotIn("기존 주문 관리 중", self.dialog._runtime.text())
                self.assertEqual(desired, self.dialog._context["status"]["control"]["desired_state"])
                self.assertEqual(runner, self.dialog._context["status"]["runner"])

    def test_ack_followup_failure_keeps_ack_message_and_closes_stale_controls(self):
        for operation, text in (("start", "시작 요청이 반영"), ("stop", "신규 자동 주문을 중지"),
                                ("resume", "재개 요청이 반영"), ("publish_spec", "명세를 게시")):
            for full_context in (True, False):
                with self.subTest(operation=operation, full_context=full_context):
                    self.dialog._completed("refresh", deepcopy(self.context))
                    context = deepcopy(self.context) if full_context else {"context_refresh_error": "followup unavailable"}
                    context["status"] = {"status_query_error": "followup unavailable"}
                    self.dialog._completed(operation, context)
                    self.dialog._set_busy(False)
                    self.assertIn(text, self.dialog._message.text())
                    self.assertIn("확인하지 못했습니다", self.dialog._message.text())
                    self.assertNotIn("요청 실패", self.dialog._message.text())
                    self.assertFalse(any(button.isEnabled() for button in (self.dialog._start, self.dialog._stop, self.dialog._resume)))

    def test_protection_readback_keeps_unknown_reservation_and_confirmed_fills_separate(self):
        self.context["status"].update(
            runtime_mode=MockAccountRuntimeMode.AUTOMATIC.value,
            control={"desired_state": "STOPPED", "active_spec_id": "ready", "entry_resume_required": True},
        )
        for state, plan_qty, reserved, filled, label in (
            ("SUBMISSION_UNKNOWN", 2, 2, 0, "보호 매도 접수 미확인"),
            ("FILLED", 0, 0, 2, "보호 매도 체결 완료"),
        ):
            with self.subTest(state=state):
                self.context["status"]["runner"] = {"state": "STOPPED", "planned_protection": {
                    "state": state, "plan_id": "plan-1", "plan_revision": 4, "intent_id": "sell-1",
                    "plan_quantity": plan_qty, "reserved_quantity": reserved,
                    "detailed_filled_quantity": filled, "unreconciled_sell_quantity": 0,
                    "order_authorized": False,
                }}
                self.dialog._completed("refresh", self.context)
                self.assertIn(f"{label} · 계획 보유 {plan_qty}주 · 주문 예약 {reserved}주 · 상세 체결 {filled}주",
                              self.dialog._runtime.text())
                self.assertIn("신규 진입 재개 대기", self.dialog._runtime.text())
                self.assertEqual("계획 ID plan-1\n계획 판본 4\n보호 주문 의도 ID sell-1", self.dialog._runtime.toolTip())
                self.assertFalse(self.dialog._start.isEnabled())

        self.context["status"]["runner"] = None
        self.dialog._completed("refresh", self.context)
        self.assertNotIn("보호 매도", self.dialog._runtime.text())
        self.assertEqual("", self.dialog._runtime.toolTip())

    def test_leader_publication_requires_explicit_policy_file_for_native_plan_mode(self):
        from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE, MockUnfilledExecutionPolicy
        from kiwoom_monitor.domain.strategy_contract import LEADER_BREAKOUT_FAMILY_ID
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationPublicationDialog

        policy = MockUnfilledExecutionPolicy(
            maximum_reprices=0, maximum_adverse_slippage_bps=0, protective_slippage_bps=0,
            maximum_management_seconds=30, max_snapshot_age_seconds=10, minimum_net_advantage_won=0,
            uncertainty_multiplier=1, minimum_paired_samples=2, reprice_horizon_seconds=10,
        )
        candidate = {"package": {"candidate_spec": {"family": LEADER_BREAKOUT_FAMILY_ID}}}
        dialog = MockAutomationPublicationDialog(candidate, {}, "profile", ({"event_id": "shadow"},), self.dialog)
        self.addCleanup(dialog.deleteLater)
        with tempfile.TemporaryDirectory() as directory, patch(
            "kiwoom_monitor.presentation.mock_automation_dialog.build_mock_automation_publication_documents",
        ) as build:
            dialog.publication_values()
            self.assertIsNone(build.call_args.kwargs["execution_plan_mode"])
            self.assertIsNone(build.call_args.kwargs["unfilled_execution_policy"])
            path = Path(directory) / "explicit-policy.json"
            dialog._plan_policy_path.setText(str(path))
            with self.assertRaisesRegex(ValueError, "계획형 모의 정책"):
                dialog.publication_values()
            self.assertEqual(1, build.call_count)
            path.write_text(json.dumps(policy.to_dict()), encoding="utf-8")
            dialog.publication_values()
            self.assertEqual(LEADER_NATIVE_PLAN_MODE, build.call_args.kwargs["execution_plan_mode"])
            self.assertEqual(policy, build.call_args.kwargs["unfilled_execution_policy"])

    def test_publication_review_waits_for_thread_finish_and_posts_exact_fresh_selection(self):
        import sys
        import threading
        from datetime import datetime, timezone
        from PySide6.QtCore import QTimer
        from qt_settings_test_support import wait_until
        from kiwoom_monitor.presentation import mock_automation_dialog as module

        profile, selected, other, event = publication_thread_inputs()
        content, credentials = self.dialog._content, self.dialog._credentials
        credentials.load = Mock(return_value={"profiles": [profile]})
        original_candidates, original_specs = content.load_mock_automation_candidates, content.load_mock_automation_specs
        reads = []
        def candidates(*args, **kwargs):
            value = original_candidates(*args, **kwargs)
            reads.append(value)
            value["binding"]["binding_revision"] = 2 if len(reads) == 1 else 3
            value["candidates"] = deepcopy([other, selected])
            return value
        content.load_mock_automation_candidates = Mock(side_effect=candidates)
        content.load_candidate_events = Mock(return_value={"events": [event], "has_more": False, "next_cursor": None})
        content.publish_mock_automation_spec = Mock(return_value={"status": "saved", "orders_started": False})
        def specs(account_ref):
            if content.publish_mock_automation_spec.called:
                raise RuntimeError("publication followup unavailable")
            return original_specs(account_ref)
        content.load_mock_automation_specs = Mock(side_effect=specs)
        for operation in ("start", "stop", "resume"):
            setattr(content, f"{operation}_mock_automation", Mock())
        self.dialog._request("refresh")
        wait_until(lambda: self.dialog._worker is None)
        self.dialog._candidate.setCurrentIndex(1)
        self.dialog.show(); self.app.processEvents()
        emitted, release = threading.Event(), threading.Event()
        original_run, original_review = MockAutomationWorker.run, module.MockAutomationPublicationDialog
        workers, reviews, documents, errors, barrier_errors = [], [], [], [], []
        def run(worker):
            workers.append(worker)
            original_run(worker)
            if worker._operation == "prepare_publication":
                emitted.set()
                if not release.wait(5):
                    barrier_errors.append("prepare thread finish was not released")
        def review(*args):
            self.assertIsNone(self.dialog._worker)
            self.assertFalse(workers[0].isRunning())
            self.assertEqual(selected, args[0])
            self.assertEqual(3, args[1]["binding_revision"])
            self.assertEqual((event,), args[3])
            value = original_review(*args)
            reviews.append(value)
            original_values = value.publication_values
            def values():
                result = original_values()
                documents.append(deepcopy(result))
                return result
            value.publication_values = values
            QTimer.singleShot(0, value.accept)
            return value
        try:
            with patch.object(MockAutomationWorker, "run", run), patch.object(module, "MockAutomationPublicationDialog", side_effect=review) as modal, \
                    patch.object(module, "kst_now", return_value=datetime(2026, 10, 20, tzinfo=timezone.utc)), \
                    patch.object(sys, "excepthook", side_effect=lambda _type, error, _trace: errors.append(error)):
                self.dialog._publish.click()
                first = self.dialog._worker
                wait_until(emitted.is_set)
                self.assertTrue(first.isRunning())
                modal.assert_not_called()
                self.dialog._publish_selected_candidate()
                self.dialog._request("refresh")
                self.assertIs(first, self.dialog._worker)
                self.assertEqual(1, content.load_candidate_events.call_count)
                release.set()
                wait_until(lambda: content.publish_mock_automation_spec.call_count == 1 and self.dialog._worker is None)
                modal.assert_called_once()
                self.assertEqual(1, len(documents))
                content.publish_mock_automation_spec.assert_called_once_with(**documents[0])
                self.assertEqual((profile["account_ref"], profile["profile_id"], 3, event["event_id"]), (
                    documents[0]["account_ref"], documents[0]["credential_profile_id"],
                    documents[0]["expected_binding_revision"], documents[0]["shadow_event_id"],
                ))
                self.assertEqual(selected["eligibility_receipt"]["receipt_id"], documents[0]["stage_revisions"][1]["evidence_refs"][0])
                self.assertEqual(1, content.load_candidate_events.call_count)
                self.assertEqual(3, content.load_mock_automation_candidates.call_count)
                self.assertNotIn("shadow_events", self.dialog._context)
                self.assertNotIn("_publication_request", self.dialog._context)
                self.assertNotIn("_publication_request", json.dumps(documents[0]))
                self.assertEqual(selected, self.dialog._candidate.currentData())
                self.assertIn("명세를 게시", self.dialog._message.text())
                self.assertIn("최신 상태를 확인하지 못했습니다", self.dialog._message.text())
                self.assertFalse(any(button.isEnabled() for button in (self.dialog._start, self.dialog._stop, self.dialog._resume)))
                for operation in ("start", "stop", "resume"):
                    getattr(content, f"{operation}_mock_automation").assert_not_called()
                self.assertEqual([], errors)
                self.assertEqual([], barrier_errors)
        finally:
            release.set()
            if self.dialog._worker is not None:
                self.dialog.stop()
            self.app.processEvents()
            for value in reviews:
                value.close(); value.deleteLater()
            self.dialog.hide()

    def test_hidden_closed_or_stopped_publication_preparation_never_opens_review(self):
        import sys
        import threading
        from qt_settings_test_support import wait_until
        from kiwoom_monitor.presentation import mock_automation_dialog as module

        profile, selected, _other, event = publication_thread_inputs()
        for action in ("hide", "close", "close_reopen", "stop", "finished_stop", "restored_context"):
            with self.subTest(action=action):
                content, credentials = FakeContent(), FakeCredentials()
                credentials.load = Mock(return_value={"profiles": [profile]})
                original_candidates = content.load_mock_automation_candidates
                content.load_mock_automation_candidates = Mock(side_effect=lambda *args, **kwargs: {
                    **original_candidates(*args, **kwargs), "candidates": [deepcopy(selected)],
                })
                entered, release = threading.Event(), threading.Event()
                barriers, errors = [], []
                def page(**_kwargs):
                    entered.set()
                    if not release.wait(5):
                        barriers.append("Shadow read was not released")
                    return {"events": [event], "has_more": False, "next_cursor": None}
                content.load_candidate_events = Mock(side_effect=page)
                content.publish_mock_automation_spec = Mock()
                with patch.object(module, "QSettings") as settings:
                    settings.return_value.value.return_value = None
                    dialog = MockAutomationDialog(content, credentials)
                dialog._request("refresh")
                wait_until(lambda: dialog._worker is None)
                dialog.show(); self.app.processEvents()
                try:
                    with patch.object(module, "MockAutomationPublicationDialog") as modal, \
                            patch.object(sys, "excepthook", side_effect=lambda _type, error, _trace: errors.append(error)):
                        dialog._publish.click()
                        wait_until(entered.is_set)
                        worker = dialog._worker
                        if action == "stop":
                            releaser = threading.Timer(0.05, release.set)
                            releaser.start()
                            try:
                                dialog.stop()
                            finally:
                                releaser.join()
                        elif action == "finished_stop":
                            release.set()
                            self.assertTrue(worker.wait(3000))
                            self.assertFalse(worker.isInterruptionRequested())
                            self.assertIs(worker, dialog._worker)
                            dialog.stop()
                        elif action in {"close_reopen", "restored_context"}:
                            previous = dict(dialog._context)
                            dialog.close()
                            if action == "restored_context":
                                dialog._completed("refresh", previous)
                            dialog.show(); self.app.processEvents()
                            release.set()
                        else:
                            getattr(dialog, action)()
                            release.set()
                        wait_until(lambda: dialog._worker is None)
                        modal.assert_not_called()
                        content.publish_mock_automation_spec.assert_not_called()
                        self.assertEqual(1, content.load_candidate_events.call_count)
                        self.assertNotIn("게시했습니다", dialog._message.text())
                        self.assertNotIn("shadow_events", dialog._context)
                        self.assertNotIn("_publication_request", dialog._context)
                        self.assertEqual([], errors)
                        self.assertEqual([], barriers)
                finally:
                    release.set(); dialog.stop(); dialog.hide(); dialog.deleteLater(); self.app.processEvents()

    def test_failed_request_does_not_reenable_stale_start_after_worker_finishes(self):
        self.assertTrue(self.dialog._start.isEnabled())
        self.dialog._set_busy(True)
        self.dialog._failed("refresh", "HTTP 503")
        self.dialog._set_busy(False)
        self.assertFalse(self.dialog._start.isEnabled())
        self.assertFalse(self.dialog._resume.isEnabled())
        self.assertIn("상태를 확인할 수 없습니다", self.dialog._runtime.text())

        fresh = MockAutomationWorker(FakeContent(), FakeCredentials(), "refresh")._load_context("", "")
        self.dialog._completed("refresh", fresh)
        self.assertTrue(self.dialog._start.isEnabled())

    def test_failed_status_read_is_shown_as_unavailable_not_manual(self):
        content = FakeContent()
        with patch.object(content, "load_mock_automation_status", side_effect=RuntimeError("HTTP 503")):
            context = MockAutomationWorker(content, FakeCredentials(), "refresh")._load_context("", "")
        self.dialog._completed("refresh", context)
        self.assertIn("상태 조회 실패", self.dialog._runtime.text())
        self.assertNotIn("수동", self.dialog._runtime.text())
        self.assertFalse(self.dialog._start.isEnabled())
        self.assertIn("상태를 확인하지 못했습니다", self.dialog._message.text())

    def test_disabled_order_setting_is_explained_and_keeps_stop_available(self):
        self.context["account_settings"]["mock_order_enabled"] = False
        self.dialog._completed("refresh", self.context)
        self.assertFalse(self.dialog._start.isEnabled())
        self.assertIn("모의주문 허용이 꺼져 있습니다", self.dialog._readiness.text())
        self.context["status"].update(
            control={"desired_state": "STOPPED", "active_spec_id": "ready"},
        )
        self.dialog._completed("refresh", self.context)
        self.assertFalse(self.dialog._resume.isEnabled())
        self.context["status"].update(
            runtime_mode=MockAccountRuntimeMode.AUTOMATIC.value,
            control={"desired_state": "RUNNING", "active_spec_id": "ready"},
        )
        self.dialog._completed("refresh", self.context)
        self.assertTrue(self.dialog._stop.isEnabled())

    def test_candidate_preparation_opens_with_current_account_and_refreshes_only_after_publication(self):
        with (patch("kiwoom_monitor.presentation.mock_automation_dialog.MockAutomationCandidateDialog") as dialog,
              patch.object(self.dialog, "_request") as request):
            self.dialog._prepare_candidate_files()
            dialog.assert_called_once_with(self.dialog._content, PROFILE, self.context["candidate_binding"], self.dialog)
            dialog.return_value.exec.assert_called_once()
            callback = dialog.return_value.candidate_published.connect.call_args.args[0]
            request.assert_not_called()
            callback()
            request.assert_called_once_with("refresh")


def candidate_documents(**changes):
    package = _package()
    policy = _policy(package, **changes)
    receipt = evaluate_candidate_eligibility(package, policy, account_ref=PROFILE["account_ref"])
    return {"package": package.to_dict(), "eligibility_policy": policy.to_dict(), "eligibility_receipt": receipt.to_dict()}


def publication_thread_inputs():
    from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
    from kiwoom_monitor.application.research_families import shadow_monitor_id_for_config

    profile = {**PROFILE, "account_ref": "11111111-1111-4111-8111-111111111111"}
    package = _package()
    documents = []
    for changes in ({}, {"minimum_closed_trades": 1}):
        policy = _policy(package, **changes)
        receipt = evaluate_candidate_eligibility(package, policy, account_ref=profile["account_ref"])
        documents.append({"package": package.to_dict(), "eligibility_policy": policy.to_dict(), "eligibility_receipt": receipt.to_dict()})
    family = package.candidate_spec["family"]
    strategy_id, strategy_version = family.rsplit("/", 1)
    event = {"event_id": "selected-shadow-event", "transition": "flat->candidate", "quantity": 1,
             "available_at": "2026-09-21T10:00:00+09:00", "strategy_id": strategy_id, "strategy_version": strategy_version,
             "run_id": shadow_monitor_id_for_config(family, default_shadow_breakout_config(), "krx-regular/v1")}
    return profile, documents[0], documents[1], event


class MockCandidateFileWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.publication = self.root / 'publication.json'
        self.document = candidate_documents()
        self.publication.write_text(json.dumps(self.document), encoding='utf-8')
        self.binding = FakeContent().load_mock_automation_candidates(PROFILE['account_ref'], credential_profile_id=PROFILE['profile_id'])['binding']
        self.content = Mock()
        self.content.load_mock_automation_candidates.return_value = {'binding': self.binding}
        self.freeze_time = datetime.fromisoformat('2026-10-24T08:59:00+09:00')
        self.content.server_now = lambda: self.freeze_time
        self.content.publish_mock_automation_candidate.return_value = {
            'status': 'saved', 'orders_started': False,
            'package_hash': self.document['package']['package_hash'],
            'policy_id': self.document['eligibility_policy']['policy_id'],
            'receipt_id': self.document['eligibility_receipt']['receipt_id'],
            'eligibility_status': 'ELIGIBLE',
        }
        self.patch_code = patch.object(candidate_files, 'research_implementation_hash', return_value='a' * 64)
        self.patch_code.start()
        self.addCleanup(self.patch_code.stop)

    def worker(self, operation='publish', **values):
        return MockCandidateFileWorker(self.content, PROFILE, self.binding, operation,
            {'publication': str(self.publication), 'reviewed_documents': deepcopy(self.document), **values})

    def test_review_validates_artifact_without_network_or_orders(self):
        result = self.worker('review').execute()
        self.assertEqual(self.document, result['documents'])
        self.assertEqual([], self.content.mock_calls)

    def test_publication_uses_exact_review_and_fresh_binding_without_start(self):
        result = self.worker().execute()
        self.assertTrue(result['published'])
        self.content.publish_mock_automation_candidate.assert_called_once_with(
            account_ref=PROFILE['account_ref'], credential_profile_id=PROFILE['profile_id'],
            expected_binding_revision=2, **self.document,
        )
        self.assertEqual(['load_mock_automation_candidates', 'publish_mock_automation_candidate'],
                         [call[0] for call in self.content.mock_calls])

    def test_different_account_rejected_before_network(self):
        wrong = deepcopy(PROFILE)
        wrong['account_ref'] = 'another-account'
        worker = MockCandidateFileWorker(self.content, wrong, self.binding, 'review', {'publication': str(self.publication)})
        with self.assertRaisesRegex(ValueError, '계좌가 다릅니다'):
            worker.execute()
        self.assertEqual([], self.content.mock_calls)

    def test_valid_but_changed_artifact_requires_new_review(self):
        self.publication.write_text(json.dumps(candidate_documents(minimum_closed_trades=1)), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '파일이 변경'):
            self.worker().execute()
        self.assertEqual([], self.content.mock_calls)

    def test_changed_binding_blocks_post(self):
        self.content.load_mock_automation_candidates.return_value = {'binding': {**self.binding, 'binding_revision': 3}}
        with self.assertRaisesRegex(ValueError, '연결이 변경'):
            self.worker().execute()
        self.content.publish_mock_automation_candidate.assert_not_called()

    def test_interruption_after_binding_read_prevents_publication(self):
        worker = self.worker()
        with patch.object(worker, 'isInterruptionRequested', return_value=False) as interrupted:
            def read(*args, **kwargs):
                interrupted.return_value = True
                return {'binding': self.binding}
            self.content.load_mock_automation_candidates.side_effect = read
            with self.assertRaisesRegex(RuntimeError, '작업 종료'):
                worker.execute()
        self.content.publish_mock_automation_candidate.assert_not_called()

    def test_invalid_publication_reply_is_not_automatically_retried(self):
        self.content.publish_mock_automation_candidate.return_value = {'status': 'saved'}
        with self.assertRaisesRegex(RuntimeError, '자동 재전송하지'):
            self.worker().execute()
        self.assertEqual(1, self.content.publish_mock_automation_candidate.call_count)

    def test_forged_receipt_with_recomputed_hash_fails_reassessment(self):
        from kiwoom_monitor.application.mock_automation_candidate import eligibility_receipt_from_dict
        from dataclasses import replace
        original = eligibility_receipt_from_dict(self.document['eligibility_receipt'])
        forged = replace(original, observed_metrics={**original.observed_metrics, 'net_realized_pnl_won': 9_999_999})
        self.document['eligibility_receipt'] = forged.to_dict()
        self.publication.write_text(json.dumps(self.document), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '재계산 결과'):
            self.worker('review').execute()

    def test_blocked_candidate_is_reviewable_but_not_publishable(self):
        self.document = candidate_documents(minimum_closed_trades=None)
        self.publication.write_text(json.dumps(self.document), encoding='utf-8')
        result = self.worker('review').execute()
        self.assertEqual('BLOCKED', result['documents']['eligibility_receipt']['status'])
        with self.assertRaisesRegex(ValueError, '적격 후보만'):
            self.worker().execute()
        self.assertEqual([], self.content.mock_calls)

    def test_dialog_enables_only_reviewed_eligible_publication_and_invalidates_edits(self):
        dialog = MockAutomationCandidateDialog(self.content, PROFILE, self.binding)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog._buttons['publish'].isEnabled())
        dialog._completed('review', {'documents': self.document})
        dialog._set_busy(False)
        self.assertTrue(dialog._buttons['publish'].isEnabled())
        self.assertIn('연구 비용 모델', dialog._summary.toPlainText())
        self.assertIn('완료 거래 최소', dialog._summary.toPlainText())
        dialog._inputs['publication'].setText(str(self.publication))
        self.assertFalse(dialog._buttons['publish'].isEnabled())
        self.assertIn('다시 검토', dialog._status.text())
        dialog._completed('review', {'documents': candidate_documents(minimum_closed_trades=None)})
        dialog._set_busy(False)
        self.assertFalse(dialog._buttons['publish'].isEnabled())
        self.assertIn('차단', dialog._status.text())

    def test_dialog_completion_emits_publication_only_not_automation_start(self):
        dialog = MockAutomationCandidateDialog(self.content, PROFILE, self.binding)
        self.addCleanup(dialog.deleteLater)
        published = []
        dialog.candidate_published.connect(lambda: published.append(True))
        dialog._completed('review', {'documents': self.document})
        self.assertEqual([], published)
        dialog._completed('publish', {'documents': self.document, 'published': True})
        self.assertEqual([True], published)
        self.assertIn('주문은 시작되지', dialog._status.text())
        self.assertIsNone(dialog._reviewed_documents)
        self.assertEqual([], self.content.mock_calls)

    def test_dialog_stop_requests_interruption_and_drains_owned_worker(self):
        from PySide6.QtCore import QThread
        import threading
        entered, stopped = threading.Event(), threading.Event()
        class Worker(QThread):
            def run(self):
                entered.set()
                while not self.isInterruptionRequested():
                    self.msleep(1)
                stopped.set()
        dialog = MockAutomationCandidateDialog(self.content, PROFILE, self.binding)
        self.addCleanup(dialog.deleteLater)
        worker = Worker(dialog)
        dialog._worker = worker
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            dialog.stop()
            self.assertTrue(stopped.is_set())
            self.assertFalse(worker.isRunning())
        finally:
            worker.requestInterruption()
            worker.wait(2000)
            dialog._worker = None

    def test_real_dialog_thread_freezes_missing_criteria_without_loading_dataset(self):
        spec = self.document['package']['candidate_spec']
        request_path = self.root / 'request.json'
        policy_path = self.root / 'policy.json'
        dataset = self.root / 'empty-dataset'
        dataset.mkdir()
        request_path.write_text(json.dumps({
            'mode': 'single_run', 'family': spec['family'], 'strategy': spec['parameters'],
            'dataset': str(dataset), 'database': str(self.root / 'absent.sqlite3'),
            'runs_dir': str(self.root / 'absent-runs'), 'execution': spec['execution_model'],
            'evaluation': self.document['package']['evaluation_evidence']['evaluation_spec'],
            'session_profile': spec['session_profile']['profile'],
        }), encoding='utf-8')
        dialog = MockAutomationCandidateDialog(self.content, PROFILE, self.binding)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.stop)
        for name, value in {'request': str(request_path), 'policy': str(policy_path), 'strategy_ref': 'strategy-1'}.items():
            dialog._inputs[name].setText(value)
        dialog._request('freeze')
        self.assertFalse(dialog._buttons['freeze'].isEnabled())
        deadline = time.monotonic() + 10
        while dialog._worker is not None and time.monotonic() < deadline:
            dialog._worker.wait(10)
            self.app.processEvents()
        self.assertIsNone(dialog._worker)
        self.assertIn('미정 기준', dialog._status.text())
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        self.assertEqual(self.freeze_time.isoformat(), policy['frozen_at'])
        self.assertIsNone(policy['minimum_closed_trades'])
        self.assertEqual([], list(dataset.iterdir()))
        self.assertFalse((self.root / 'absent.sqlite3').exists())
        self.assertEqual([], self.content.mock_calls)

    def test_missing_or_non_mock_binding_cannot_be_published(self):
        for binding in ({}, {**self.binding, 'environment': 'real'}):
            with self.subTest(binding=binding):
                self.content.load_mock_automation_candidates.return_value = {'binding': binding}
                with self.assertRaisesRegex(ValueError, '연결이 변경'):
                    self.worker().execute()
        self.content.publish_mock_automation_candidate.assert_not_called()

    def test_current_code_mismatch_blocks_file_review(self):
        with patch.object(candidate_files, 'research_implementation_hash', return_value='b' * 64):
            with self.assertRaisesRegex(ValueError, '현재 전략 구현'):
                self.worker('review').execute()
        self.assertEqual([], self.content.mock_calls)

    def test_busy_dialog_does_not_close_or_accept_stale_review(self):
        from PySide6.QtGui import QCloseEvent
        dialog = MockAutomationCandidateDialog(self.content, PROFILE, self.binding)
        self.addCleanup(dialog.deleteLater)
        dialog._worker = Mock()
        event = QCloseEvent()
        dialog.closeEvent(event)
        self.assertFalse(event.isAccepted())
        dialog._completed('review', {'documents': self.document})
        dialog._set_busy(True)
        self.assertFalse(dialog._buttons['publish'].isEnabled())
        dialog._failed('fixture failed')
        dialog._worker = None
        dialog._set_busy(False)
        self.assertFalse(dialog._buttons['publish'].isEnabled())

    def test_gui_worker_prepares_native_final_evidence_without_network(self):
        from tests.unit.test_research_final_execution import FinalExecutionTests
        from kiwoom_monitor import research_process as rp
        self.patch_code.stop()
        fixture = FinalExecutionTests('test_real_final_execution_uses_fresh_engine_and_completes_owned_candidate')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        request_path, policy_path, output = fixture.candidate_cli_paths()
        candidate_files.freeze_policy(request_path, policy_path, strategy_ref='strategy-1')
        row = fixture.execute()['candidates'][0]
        content = Mock()
        worker = MockCandidateFileWorker(content, {'account_ref': 'account-ref-1'}, {}, 'prepare', {
            'request': str(request_path), 'policy': str(policy_path), 'publication': str(output),
            'batch_id': fixture.prepared.batch.batch_id, 'run_id': row['run_id'],
        })
        with patch.object(rp, 'load_research_input', side_effect=AssertionError('no OOS reread')):
            result = worker.execute()
        self.assertEqual(row['logical_result_hash'], result['documents']['package']['source_final']['result_hash'])
        self.assertEqual('BLOCKED', result['documents']['eligibility_receipt']['status'])
        self.assertEqual(result['documents'], json.loads(output.read_text(encoding='utf-8')))
        self.assertEqual([], content.mock_calls)

    def test_development_worker_reads_native_baselines_without_http(self):
        from tests.unit.test_historical_baseline_requests import seed_development_baselines
        seed_development_baselines(self.root)
        worker = MockCandidateFileWorker(self.content, PROFILE, self.binding, 'development', {
            'development_root': str(self.root),
        })
        result = worker.execute()['development_review']
        self.assertEqual(4, len(result['runs']))
        self.assertFalse(result['candidate_eligibility_evaluated'])
        self.assertEqual([], self.content.mock_calls)

    def test_development_review_invalidates_publish_and_does_not_fill_criteria(self):
        from tests.unit.test_historical_baseline_requests import seed_development_baselines
        from kiwoom_monitor.research_candidate import inspect_development_baselines
        seed_development_baselines(self.root)
        dialog = MockAutomationCandidateDialog(self.content, PROFILE, self.binding)
        dialog._completed('review', {'documents': self.document})
        dialog._set_busy(False)
        self.assertTrue(dialog._buttons['publish'].isEnabled())
        dialog._inputs['development_root'].setText(str(self.root))
        dialog._completed('development', {'development_review': inspect_development_baselines(self.root)})
        dialog._set_busy(False)
        self.assertFalse(dialog._buttons['publish'].isEnabled())
        self.assertIsNone(dialog._reviewed_documents)
        self.assertTrue(all(edit.text() == '' for edit in dialog._criteria.values()))
        self.assertIn('자동매매 적격 판정은 수행하지 않음', dialog._summary.toPlainText())
        self.assertEqual([], self.content.mock_calls)

    def test_development_folder_is_required_and_qt_worker_completes(self):
        from tests.unit.test_historical_baseline_requests import seed_development_baselines
        dialog = MockAutomationCandidateDialog(self.content, PROFILE, self.binding)
        dialog._request('development')
        self.assertIsNone(dialog._worker)
        seed_development_baselines(self.root)
        dialog._inputs['development_root'].setText(str(self.root))
        dialog._request('development')
        deadline = time.monotonic() + 5
        while dialog._worker is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertIsNone(dialog._worker)
        self.assertIn('개발 원장 확인 완료', dialog._status.text())
        self.assertFalse(dialog._buttons['publish'].isEnabled())
        self.assertEqual([], self.content.mock_calls)

    def test_open_position_diagnostic_is_visible_without_eligibility_or_posting(self):
        from tests.unit.test_historical_baseline_requests import seed_development_baselines
        from tests.unit.test_research_execution import late_exit_trace
        from kiwoom_monitor.research_candidate import inspect_development_baselines, position_observation_summary
        seed_development_baselines(self.root)
        review = inspect_development_baselines(self.root)
        _, events, _, _ = late_exit_trace()
        review['runs'][0]['position_observations'] = position_observation_summary(tuple(event.to_dict() for event in events))
        dialog = MockAutomationCandidateDialog(self.content, PROFILE, self.binding)
        dialog._completed('development', {'development_review': review})
        dialog._set_busy(False)
        text = dialog._summary.toPlainText()
        self.assertIn('미청산 포지션이 있는 실행 1개', text)
        self.assertIn('청산 요청 1건', text)
        self.assertIn('체결 전 세션 변경 1건', text)
        self.assertFalse(dialog._buttons['publish'].isEnabled())
        self.assertTrue(all(edit.text() == '' for edit in dialog._criteria.values()))
        self.assertEqual([], self.content.mock_calls)


class DailyInputCandidateDialogTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_historical_baseline_requests import DailyDevelopmentRequestTests
        DailyDevelopmentRequestTests.setUp(self)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_inputs_worker_uses_frozen_source_without_network_or_candidate_creation(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        prepare_daily_requests(self.selection, self.source, self.output)
        content = Mock()
        content.server_now = lambda: datetime.fromisoformat("2026-10-24T08:59:00+09:00")
        result = MockCandidateFileWorker(content, PROFILE, {}, "inputs", {
            "development_root": str(self.output),
        }).execute()["input_review"]
        self.assertEqual(2, result["summary"]["stock_day_count"])
        self.assertFalse(result["candidate_eligibility_evaluated"])
        self.assertEqual([], content.mock_calls)
        self.assertFalse((self.output / "research.sqlite3").exists())

    def test_optional_reference_is_used_and_changes_invalidate_publish(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        from tests.unit.test_historical_loss_review import cost_reference_fixture
        manifest_path = self.source / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["individual_stocks_only"] = True
        manifest_path.write_text(json.dumps(manifest))
        prepare_daily_requests(self.selection, self.source, self.output)
        reference = self.root / "costs.json"
        plan = json.loads((self.output / "daily-plan.json").read_text())
        reference.write_text(json.dumps(cost_reference_fixture(plan, manifest)))
        content = Mock()
        content.server_now = lambda: datetime.fromisoformat("2026-10-24T08:59:00+09:00")
        result = MockCandidateFileWorker(content, PROFILE, {}, "inputs", {
            "development_root": str(self.output), "cost_reference": str(reference),
        }).execute()["input_review"]
        self.assertEqual(2, result["cost_reference"]["tax_mismatch_day_count"])
        dialog = MockAutomationCandidateDialog(content, PROFILE, {})
        dialog._completed("review", {"documents": candidate_documents()})
        dialog._set_busy(False)
        self.assertTrue(dialog._buttons["publish"].isEnabled())
        dialog._inputs["gap_provenance"].setText("report.json")
        self.assertFalse(dialog._buttons["publish"].isEnabled())
        self.assertIsNone(dialog._reviewed_documents)
        self.assertEqual([], content.mock_calls)

    def test_inputs_qt_worker_requires_folder_and_invalidates_previous_review(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        content = Mock()
        content.server_now = lambda: datetime.fromisoformat("2026-10-24T08:59:00+09:00")
        dialog = MockAutomationCandidateDialog(content, PROFILE, {})
        dialog._request("inputs")
        self.assertIsNone(dialog._worker)
        prepare_daily_requests(self.selection, self.source, self.output)
        dialog._inputs["development_root"].setText(str(self.output))
        dialog._completed("review", {"documents": candidate_documents()})
        dialog._set_busy(False)
        self.assertTrue(dialog._buttons["publish"].isEnabled())
        dialog._request("inputs")
        self.assertIsNone(dialog._reviewed_documents)
        self.assertFalse(dialog._buttons["publish"].isEnabled())
        deadline = time.monotonic() + 10
        while dialog._worker is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertIsNone(dialog._worker)
        self.assertIn("비용·입력 점검 완료", dialog._status.text())
        self.assertIn("미검증", dialog._summary.toPlainText())
        self.assertTrue(all(edit.text() == "" for edit in dialog._criteria.values()))
        self.assertFalse(dialog._buttons["publish"].isEnabled())
        self.assertEqual([], content.mock_calls)

    def test_preparation_qt_worker_keeps_choices_unset_and_publish_disabled(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        prepare_daily_requests(self.selection, self.source, self.output)
        content = Mock()
        content.server_now = lambda: datetime.fromisoformat("2026-10-24T08:59:00+09:00")
        dialog = MockAutomationCandidateDialog(content, PROFILE, {})
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.stop)
        dialog._inputs["development_root"].setText(str(self.output))
        dialog._completed("review", {"documents": candidate_documents()})
        dialog._set_busy(False)
        self.assertTrue(dialog._buttons["publish"].isEnabled())
        dialog._request("preparation")
        self.assertFalse(dialog._buttons["publish"].isEnabled())
        self.assertFalse(dialog._inputs["final_start"].isEnabled())
        deadline = time.monotonic() + 10
        while dialog._worker is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertIsNone(dialog._worker)
        self.assertIn("동결 준비 점검 완료", dialog._status.text())
        self.assertIn("final 시작: 미정", dialog._summary.toPlainText())
        self.assertTrue(all(edit.text() == "" for edit in dialog._criteria.values()))
        self.assertFalse(dialog._buttons["publish"].isEnabled())
        self.assertEqual([], content.mock_calls)
        self.assertFalse((self.output / "research.sqlite3").exists())

    def test_overlap_in_preparation_fails_and_cannot_reuse_previous_publication(self):
        from scripts.prepare_historical_baseline_requests import prepare_daily_requests
        prepare_daily_requests(self.selection, self.source, self.output)
        content = Mock()
        content.server_now = lambda: datetime.fromisoformat("2026-10-24T08:59:00+09:00")
        dialog = MockAutomationCandidateDialog(content, PROFILE, {})
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.stop)
        dialog._inputs["development_root"].setText(str(self.output))
        dialog._inputs["final_start"].setText("2024-09-03T00:00:00+09:00")
        dialog._completed("review", {"documents": candidate_documents()})
        dialog._set_busy(False)
        dialog._request("preparation")
        deadline = time.monotonic() + 10
        while dialog._worker is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        self.assertIsNone(dialog._worker)
        self.assertIn("모든 고정 개발 기간 이후", dialog._status.text())
        self.assertIsNone(dialog._reviewed_documents)
        self.assertFalse(dialog._buttons["publish"].isEnabled())
        self.assertEqual([], content.mock_calls)


if __name__ == "__main__":
    unittest.main()
