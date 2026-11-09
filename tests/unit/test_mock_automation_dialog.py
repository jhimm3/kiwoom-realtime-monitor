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
    "account_ref": "22222222-2222-2222-2222-222222222222", "revision": 4,
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

    def load_mock_automation_specs(self, account_ref, *, include_admission_refs=False):
        return {"account_ref": account_ref, "specs": [
            {"spec": {"spec_id": "blocked", "strategy_ref": "s"},
             "readiness": "BLOCKED", "reasons": ["TBD"]},
            {"spec": {"spec_id": "ready", "strategy_ref": "s"},
             "readiness": "READY", "reasons": []},
        ]}

    def load_mock_automation_account_selections(self, account_ref, *, credential_profile_id):
        return {"account_ref": account_ref, "selections": []}

    def load_mock_automation_status(self, account_ref, *, credential_profile_id):
        return {
            "account_ref": account_ref, "credential_profile_id": credential_profile_id,
            "runtime_mode": None, "control": None, "runner": None, "restore_error": "",
        }


def account_selection_documents():
    from datetime import timezone
    from kiwoom_monitor.domain.execution_activation import MockAutomationAccountLimits, MockAutomationAccountSelection
    from kiwoom_monitor.domain.order_contract import AccountEnvironment, AccountScope
    identities = ["mock_automation_spec_" + value * 64 for value in ("a", "b")]
    limits = {"maximum_concurrent_strategies": 2, "maximum_concurrent_positions": 3,
              "maximum_capital_won": 700000, "maximum_daily_loss_won": 4000, "maximum_data_gap_seconds": 7,
              "maximum_submission_unknown_count": 0, "maximum_reconnect_count": 2, "maximum_balance_mismatch_count": 0}
    selection = MockAutomationAccountSelection(
        account_scope=AccountScope("kiwoom", AccountEnvironment.MOCK, PROFILE["account_ref"]),
        credential_profile_id=PROFILE["profile_id"], binding_revision=2,
        binding_verified_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        frozen_at=datetime(2026, 9, 21, 1, tzinfo=timezone.utc),
        member_spec_ids=tuple(identities), execution_order_spec_ids=tuple(reversed(identities)),
        limits=MockAutomationAccountLimits(**limits),
    )
    specs = [{"spec": {"spec_id": identity, "strategy_ref": name, "maximum_capital_won": capital},
              "readiness": "READY"} for identity, name, capital in zip(identities, ("돌파", "종가"), (150000, 550000))]
    return selection.to_dict(), specs


class MockAutomationDialogPolicyTests(unittest.TestCase):
    def test_group_readiness_requires_explicit_order_and_every_current_ready_member(self):
        context = MockAutomationWorker(FakeContent(), FakeCredentials(), "refresh")._load_context("", "")
        selection, specs = account_selection_documents()
        context.update(account_selections=[selection], selected_account_selection_id=selection["selection_id"],
                       specs=specs, selected_spec_id=selection["member_spec_ids"][0])
        self.assertTrue(operation_availability(context)["start"])
        for changed in (
            {key: value for key, value in selection.items() if key != "execution_order_spec_ids"},
            {**selection, "execution_order_spec_ids": [selection["member_spec_ids"][0]] * 2},
            {**selection, "binding_revision": 1},
            {**selection, "credential_profile_id": "other"},
        ):
            with self.subTest(selection=changed):
                context["account_selections"] = [changed]
                self.assertFalse(operation_availability(context)["start"])
        context["account_selections"] = [selection]
        context["specs"][1]["readiness"] = "BLOCKED"
        self.assertFalse(operation_availability(context)["start"])
        context["status"].update(runtime_mode="AUTOMATIC", control={"desired_state": "RUNNING",
            "active_spec_id": selection["member_spec_ids"][0]})
        self.assertTrue(operation_availability(context)["stop"])

    def test_worker_saves_exact_selection_and_restores_authoritative_active_identity(self):
        selection, specs = account_selection_documents()
        content = FakeContent()
        content.publish_mock_automation_account_selection = Mock(return_value={"status": "saved", "orders_started": False})
        content.load_mock_automation_specs = Mock(return_value={"specs": specs})
        content.load_mock_automation_account_selections = Mock(return_value={"selections": [selection]})
        values = {"account_ref": PROFILE["account_ref"], "credential_profile_id": PROFILE["profile_id"],
                  "expected_binding_revision": 2, "selection": selection}
        worker = MockAutomationWorker(content, FakeCredentials(), "publish_account_selection", values)
        completed, failed = Mock(), Mock()
        worker.completed.connect(completed); worker.failed.connect(failed); worker.run()
        failed.assert_not_called(); completed.assert_called_once()
        content.publish_mock_automation_account_selection.assert_called_once_with(**values)
        context = completed.call_args.args[1]
        self.assertEqual(selection["selection_id"], context["selected_account_selection_id"])
        self.assertEqual(selection["member_spec_ids"][0], context["selected_spec_id"])
        self.assertIsNone(context["status"]["control"])
        active = {**selection, "selection_id": "different-active-selection"}
        content.load_mock_automation_status = Mock(return_value={"control": {
            "active_spec_id": selection["member_spec_ids"][0], "account_selection": active},
            "runner": {"account_selection_id": selection["selection_id"]}})
        restored = worker._load_context("", "", selection["selection_id"])
        self.assertEqual(active["selection_id"], restored["selected_account_selection_id"])
        self.assertFalse(operation_availability(restored)["start"])

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

    def test_local_dialog_opens_account_settings_and_refreshes_with_the_same_client(self):
        from kiwoom_monitor.infrastructure.central_credentials_client import CentralCredentialsClient
        from kiwoom_monitor.infrastructure.central_server_config import DataSourceSettings

        credentials = CentralCredentialsClient(DataSourceSettings("local_server", "http://127.0.0.1:8787", "token"))
        dialog = MockAutomationDialog(FakeContent(), credentials)
        self.addCleanup(dialog.deleteLater)
        with (patch("kiwoom_monitor.presentation.nas_credentials_dialog.NasCredentialsDialog") as settings,
              patch.object(dialog, "_request") as request):
            dialog._account_setup.click()
            request.assert_not_called()
            settings.return_value.finished.connect.call_args.args[0](0)
        settings.assert_called_once_with(credentials, dialog)
        settings.return_value.open.assert_called_once_with()
        request.assert_called_once_with("refresh")

    def test_local_strategy_rehearsal_from_settings_to_flat_and_recovery_on_both_venues(self):
        from dataclasses import asdict, replace
        from datetime import time as daytime, timedelta
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QPushButton
        from kiwoom_monitor.application.market_session_schedule import listing_evidence_document
        from kiwoom_monitor.application.section12_minute_research import MinuteExperiment
        from kiwoom_monitor.domain.strategy_contract import LEADER_BREAKOUT_FAMILY_ID
        from tests.unit.test_close_and_ipo import CloseAndIpoTests
        from tests.unit.test_leader_breakout import AT, policy, trade
        from tests.unit.test_panic_rebound import PanicReboundTests

        def frame(at, price, *, bar=None, tick=None, fill=None):
            result = {'at': at.isoformat(), 'quotes': {
                venue: {'price': price + offset, 'available_at': at.isoformat(), 'eligible': True,
                        'phase': 'CONTINUOUS'} for venue, offset in (('KRX', 0), ('NXT', 1))}}
            if bar is not None:
                result['bar'] = asdict(bar)
            if tick is not None:
                result['trade'] = tick
            if fill:
                result['fills'] = fill
            return result

        def bar_frame(bar):
            return frame(datetime.fromisoformat(bar.available_at), bar.close, bar=bar)

        def close_reference(fixture):
            return {'version': 'strategy-reference/v1', 'listings': [listing_evidence_document(fixture.listing)],
                    'calendar': [{**asdict(day), 'trading_date': day.trading_date.isoformat(),
                                  'available_at': day.available_at.isoformat(),
                                  'sessions': [{**asdict(window), 'trading_date': window.trading_date.isoformat(),
                                                'starts_at': window.starts_at.isoformat(),
                                                'ends_at': window.ends_at.isoformat()} for window in day.sessions]}
                                 for day in fixture.calendar]}

        def tail(at, prices, *, first_fill=True):
            return [frame(at + timedelta(seconds=index + 1), price, fill=(
                {'BUY': 2} if index == 0 and first_fill else {'SELL': 2} if index == 3 else None))
                    for index, price in enumerate(prices)]

        empty = {'version': 'strategy-reference/v1', 'calendar': [], 'listings': []}
        leader = [frame(AT + timedelta(seconds=index), price, tick=trade(price, at=AT + timedelta(seconds=index)))
                  for index, price in enumerate((995, 1010))]
        leader += tail(AT + timedelta(seconds=1), (1012, 947, 945, 944))
        leader_params = {'capital_won': 100_000, 'quantity': 4, 'policy': policy().to_dict()}
        cases = [(LEADER_BREAKOUT_FAMILY_ID, '005930', leader_params, empty, leader, 3)]

        close = CloseAndIpoTests()
        close.setUp()
        cut = datetime.fromisoformat(close.signal.available_at)
        for family, rules in (('close_betting/v1', close.close), ('ipo_breakout/v1', close.ipo_breakout),
                              ('ipo_close/v1', close.ipo_close)):
            frames = [bar_frame(bar) for bar in (*close.prior, close.signal)]
            if family == 'close_betting/v1':
                frames.extend([frame(cut + timedelta(seconds=1), 106, fill={'BUY': 2}),
                    frame(cut + timedelta(seconds=11), 106), bar_frame(close.closing), bar_frame(close.opening),
                    frame(datetime.fromisoformat(close.opening.available_at) + timedelta(seconds=1), 102, fill={'SELL': 2})])
            else:
                frames.extend(tail(cut, (106, 91, 90, 90)))
            cases.append((family, close.symbol, MinuteExperiment(family, 100_000, 4, rules).to_dict(),
                          close_reference(close), frames, 4))

        panic = PanicReboundTests()
        panic.setUp()
        cut = datetime.fromisoformat(panic.rows[-1].available_at)
        panic_params = MinuteExperiment('panic_rebound/v1', 100_000, 4, panic.rules, (panic.proof,)).to_dict()
        cases.append(('panic_rebound/v1', '123456', panic_params, empty,
                      [*(bar_frame(bar) for bar in panic.rows), *tail(cut, (911, 890, 889, 889))], 5))

        with (tempfile.TemporaryDirectory() as directory,
              patch('kiwoom_monitor.infrastructure.kiwoom_rest.client.KiwoomRestClient.request_once',
                    side_effect=AssertionError('local screen attempted real broker I/O')) as wire):
            root = Path(directory)
            for family, symbol, parameters, references, frames, restart_at in cases:
                path = root / f'{family.split("/")[0]}.json'
                path.write_text(json.dumps({'version': 'local-strategy-rehearsal/v1', 'family': family, 'symbol': symbol,
                    'parameters': parameters, 'references': references, 'frames': frames, 'signal_valid_seconds': 10,
                    'maximum_quote_age_seconds': 3, 'maximum_adverse_slippage_bps': 300, 'protective_slippage_bps': 300,
                    'sessions': {'KRX': ['KRX_REGULAR'], 'NXT': ['NXT_MAIN_MARKET']}}, ensure_ascii=False), encoding='utf-8')
                for venue in ('KRX', 'NXT'):
                    with self.subTest(family=family, venue=venue):
                        case_root = root / path.stem
                        ini = str(case_root.with_suffix('.ini'))
                        def open_screen():
                            settings = QSettings(ini, QSettings.Format.IniFormat)
                            with patch('kiwoom_monitor.presentation.mock_automation_dialog.QSettings', return_value=settings):
                                screen = MockAutomationDialog(FakeContent(), FakeCredentials(), rehearsal_root=case_root)
                            button = next(w for w in screen.findChildren(QPushButton)
                                          if w.text() == 'KRX·NXT 로컬 주문 리허설')
                            button.click()
                            return screen, screen._rehearsal_dialog

                        screen, dialog = open_screen()
                        try:
                            dialog._source.setText(str(path))
                            dialog._venue.setCurrentText(venue)
                            dialog._start.click()
                            for _ in range(restart_at):
                                dialog._next.click()
                            self.assertIsNotNone(dialog._run, dialog._summary.text())
                            self.assertEqual(2, dialog._run.status()['commands'], dialog._summary.text())
                            self.assertEqual(2, dialog._run.status()['held'])
                        finally:
                            dialog.close()
                            screen.deleteLater()

                        restarted, dialog = open_screen()
                        try:
                            self.assertEqual((str(path), venue), (dialog._source.text(), dialog._venue.currentText()))
                            dialog._start.click()
                            self.assertEqual(restart_at, dialog._run.cursor, dialog._summary.text())
                            self.assertEqual(2, dialog._run.status()['held'])
                            dialog._play.click()
                            deadline = time.monotonic() + 10
                            while dialog._timer.isActive() and time.monotonic() < deadline:
                                self.app.processEvents()
                            self.assertFalse(dialog._timer.isActive())
                            self.assertIsNotNone(dialog._run, dialog._summary.text())
                            final = dialog._run.status()
                            self.assertEqual(('CANCELLED', 'FILLED', 0, 4),
                                             (final['BUY'], final['SELL'], final['held'], final['commands']), dialog._summary.text())
                            self.assertIn('보유 0주', dialog._summary.text())
                            ledger_path, = case_root.glob(f'*-{venue}/fake-broker.json')
                            commands = json.loads(ledger_path.read_text(encoding='utf-8'))['commands']
                            self.assertEqual(['kt10000', 'kt10002', 'kt10003', 'kt10001'], [item['api_id'] for item in commands])
                            self.assertTrue(all(item['body']['dmst_stex_tp'] == venue for item in commands))
                            self.assertEqual(commands[0]['order_id'], commands[1]['body']['orig_ord_no'])
                            self.assertEqual(commands[1]['order_id'], commands[2]['body']['orig_ord_no'])
                            self.assertEqual('2', commands[3]['body']['ord_qty'])
                            first_quote = frames[restart_at - 2]['quotes'][venue]['price']
                            self.assertEqual(str(first_quote), commands[0]['body']['ord_uv'])
                        finally:
                            dialog.close()
                            restarted.deleteLater()
                        self.app.processEvents()
            from kiwoom_monitor.central_server.local_strategy_rehearsal import LocalStrategyRehearsal

            tape = json.loads((root / f'{LEADER_BREAKOUT_FAMILY_ID.split("/")[0]}.json').read_text(encoding='utf-8'))
            tape['frames'][1]['quotes'].pop('NXT')
            missing_quote = root / 'missing-nxt.json'
            missing_quote.write_text(json.dumps(tape), encoding='utf-8')
            run = LocalStrategyRehearsal(missing_quote, root / 'missing-nxt', 'NXT')
            try:
                run.step()
                self.assertEqual(0, run.step()['commands'])
                run.stop_entry()
            finally:
                run.close()
            resumed = LocalStrategyRehearsal(missing_quote, root / 'missing-nxt', 'NXT')
            try:
                self.assertTrue(resumed.status()['entry_stopped'])
                self.assertEqual(2, resumed.status()['cursor'])
            finally:
                resumed.close()
            wire.assert_not_called()

    def test_portfolio_rehearsal_keeps_staged_orders_and_shared_cash_separate_after_restart(self):
        from dataclasses import replace
        from datetime import timedelta
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QPushButton
        from kiwoom_monitor.central_server.local_strategy_rehearsal import LocalStrategyRehearsal
        from kiwoom_monitor.domain.order_contract import OrderSide
        from kiwoom_monitor.domain.strategy_contract import LEADER_BREAKOUT_FAMILY_ID
        from tests.unit.test_leader_breakout import AT, execution_config, policy, trade

        a, b = '005930', '000660'
        first = execution_config().to_dict()
        first['execution_contract'].update(version='leader_breakout_execution/v2', quantity=4, capital_won=10000,
            position_steps=[{'step_id': 'add', 'action': 'ADD', 'trigger': 'observed_cross_above',
                             'reference_price': 1020, 'quantity': 3, 'reason_ref': 'fixture:second-cross'},
                            {'step_id': 'trim', 'action': 'REDUCE', 'trigger': 'price_at_or_above',
                             'reference_price': 1030, 'quantity': 2, 'reason_ref': 'fixture:partial-exit'}])
        other = replace(policy(), contexts=(replace(policy().contexts[0], symbol=b, evidence_ref='other-symbol'),)).to_dict()
        references = {'version': 'strategy-reference/v1', 'calendar': [], 'listings': []}
        def observation(code, at, price, fills):
            return {'trade': trade(price, at=at, code=code), 'fills': fills,
                    'quotes': {venue: {'price': price + offset, 'available_at': at.isoformat(),
                        'eligible': True, 'phase': 'CONTINUOUS'} for venue, offset in (('KRX', 0), ('NXT', 1))}}

        frames = []
        rows = (
            (995, 995, {}, {}), (1010, 1010, {}, {}), (1015, 1010, {'BUY': 4}, {'BUY': 2}),
            (1025, 1010, {}, {}), (1026, 945, {'BUY': 1}, {}), (1040, 945, {'BUY': 2}, {'SELL': 2}),
            (1041, 945, {'SELL': 1}, {}), (1041, 945, {'SELL': 1}, {}), (945, 945, {}, {}),
            (945, 945, {'SELL': 5}, {}),
        )
        for index, (price_a, price_b, fill_a, fill_b) in enumerate(rows):
            at = AT + timedelta(seconds=index)
            frames.append({'at': at.isoformat(), 'observations': {a: observation(a, at, price_a, fill_a),
                                                                b: observation(b, at, price_b, fill_b)}})
        tape = {'version': 'local-strategy-rehearsal/v2', 'capital_won': 20000, 'frames': frames,
                'plans': [{'family': LEADER_BREAKOUT_FAMILY_ID, 'symbol': code, 'references': references,
                           'parameters': {'policy': rules, 'quantity': quantity, 'capital_won': 10000}}
                          for code, rules, quantity in ((a, first, 4), (b, other, 2))],
                'signal_valid_seconds': 10, 'maximum_quote_age_seconds': 3, 'maximum_adverse_slippage_bps': 300,
                'protective_slippage_bps': 300, 'sessions': {'KRX': ['KRX_REGULAR'], 'NXT': ['NXT_MAIN_MARKET']}}
        with (tempfile.TemporaryDirectory() as directory,
              patch('kiwoom_monitor.infrastructure.kiwoom_rest.client.KiwoomRestClient.request_once',
                    side_effect=AssertionError('portfolio contacted a real broker')) as wire):
            root = Path(directory)
            source = root / 'portfolio.json'
            source.write_text(json.dumps(tape), encoding='utf-8')

            def open_screen():
                settings = QSettings(str(root / 'portfolio.ini'), QSettings.Format.IniFormat)
                with patch('kiwoom_monitor.presentation.mock_automation_dialog.QSettings', return_value=settings):
                    screen = MockAutomationDialog(FakeContent(), FakeCredentials(), rehearsal_root=root / 'runs')
                next(button for button in screen.findChildren(QPushButton)
                     if button.text() == 'KRX·NXT 로컬 주문 리허설').click()
                return screen, screen._rehearsal_dialog

            for venue in ('KRX', 'NXT'):
                with self.subTest(venue=venue):
                    screen, dialog = open_screen()
                    try:
                        dialog._source.setText(str(source))
                        dialog._venue.setCurrentText(venue)
                        dialog._start.click()
                        for _ in range(5):
                            dialog._next.click()
                        self.assertIsNotNone(dialog._run, dialog._summary.text())
                        stage = dialog._run.status()
                        self.assertEqual((5, 2, 5), (stage['plans'][a]['held'], stage['plans'][b]['held'], stage['commands']))
                        self.assertEqual('ACCEPTED', stage['plans'][b]['SELL'])
                        self.assertEqual(1, stage['plans'][a]['stages']['stage-0']['filled'])
                        self.assertIn(a, dialog._summary.text())
                        self.assertIn(b, dialog._summary.text())
                    finally:
                        dialog.close()
                        screen.deleteLater()

                    screen, dialog = open_screen()
                    try:
                        self.assertEqual((str(source), venue), (dialog._source.text(), dialog._venue.currentText()))
                        dialog._start.click()
                        self.assertEqual(5, dialog._run.cursor, dialog._summary.text())
                        self.assertEqual(5, dialog._run.status()['commands'])
                        while dialog._run is not None and not dialog._run.done:
                            dialog._next.click()
                        self.assertIsNotNone(dialog._run, dialog._summary.text())
                        final = dialog._run.status()
                        self.assertEqual((0, 0, 8), (final['plans'][a]['held'], final['plans'][b]['held'], final['commands']))
                        ledger, = (root / 'runs').glob(f'*-{venue}/fake-broker.json')
                        broker = json.loads(ledger.read_text(encoding='utf-8'))
                        commands = broker['commands']
                        self.assertEqual([('kt10000', a), ('kt10000', b), ('kt10000', a), ('kt10002', a),
                                          ('kt10001', b), ('kt10001', a), ('kt10002', a), ('kt10001', a)],
                                         [(item['api_id'], item['body']['stk_cd']) for item in commands])
                        self.assertTrue(all(item['body']['dmst_stex_tp'] == venue for item in commands))
                        self.assertEqual(commands[2]['order_id'], commands[3]['body']['orig_ord_no'])
                        self.assertEqual(commands[5]['order_id'], commands[6]['body']['orig_ord_no'])
                        self.assertEqual(('2', '1', '5'), (commands[3]['body']['mdfy_qty'], commands[6]['body']['mdfy_qty'],
                                                           commands[7]['body']['ord_qty']))
                        for code, side, slot, expected in ((a, OrderSide.BUY, 'stage-0', 3),
                                                            (a, OrderSide.SELL, 'stage-1', 2),
                                                            (a, OrderSide.SELL, 'SELL', 5), (b, OrderSide.SELL, 'SELL', 2)):
                            events = dialog._run.orders.read_events(side, symbol=code, slot=slot)
                            fills = [event for event in events if event['event_type'] == 'FILL']
                            self.assertEqual(expected, sum(event['quantity'] for event in fills))
                            self.assertTrue(all(broker['orders'][event['broker_order_id']]['symbol'] == code for event in fills))
                    finally:
                        dialog.close()
                        screen.deleteLater()
                    self.app.processEvents()

            capped = root / 'capital-conflict.json'
            capped.write_text(json.dumps({**tape, 'capital_won': 5000, 'frames': frames[:2]}), encoding='utf-8')
            replay = LocalStrategyRehearsal(capped, root / 'runs', 'NXT')
            try:
                replay.step()
                cut = replay.step()
                self.assertEqual(('ACCEPTED', 'REJECTED', 1, 4044), (cut['plans'][a]['BUY'],
                    cut['plans'][b]['BUY'], cut['commands'], cut['reserved_cash']))
            finally:
                replay.close()
            wire.assert_not_called()

    def test_ipo_gap_reduction_reprices_the_remainder_and_exits_only_remaining_shares(self):
        from dataclasses import asdict
        from datetime import time as daytime, timedelta
        from kiwoom_monitor.application.market_session_schedule import listing_evidence_document
        from kiwoom_monitor.application.section12_minute_research import MinuteExperiment
        from kiwoom_monitor.central_server.local_strategy_rehearsal import LocalStrategyRehearsal
        from tests.unit.test_close_and_ipo import CloseAndIpoTests

        close = CloseAndIpoTests()
        close.setUp()
        family = 'ipo_close/v1'
        params = MinuteExperiment(family, 100_000, 4, close.ipo_close).to_dict()
        references = {'version': 'strategy-reference/v1', 'listings': [listing_evidence_document(close.listing)],
                      'calendar': [{**asdict(day), 'trading_date': day.trading_date.isoformat(),
                          'available_at': day.available_at.isoformat(),
                          'sessions': [{**asdict(window), 'trading_date': window.trading_date.isoformat(),
                              'starts_at': window.starts_at.isoformat(), 'ends_at': window.ends_at.isoformat()}
                              for window in day.sessions]} for day in close.calendar]}

        def frame(*, bar=None, at=None, price=None, fills=None):
            if bar is not None:
                at, price = datetime.fromisoformat(bar.available_at), bar.close
            row = {'quotes': {venue: dict(price=price + offset, available_at=at.isoformat(),
                    eligible=True, phase='CONTINUOUS') for venue, offset in (('KRX', 0), ('NXT', 1))},
                   'fills': fills or {}}
            if bar is not None:
                row['bar'] = asdict(bar)
            return {'at': at.isoformat(), 'observations': {close.symbol: row}}

        kept = close.bar(close.following, daytime(10, 2), price=103, revision='remainder-held')
        exit_bar = close.bar(close.following, daytime(10, 7), price=103, revision='end-of-hold')
        frames = [*(frame(bar=bar) for bar in (*close.prior, close.signal)),
                  frame(at=datetime.fromisoformat(close.signal.available_at) + timedelta(seconds=1), price=104, fills={'BUY': 4}),
                  frame(bar=close.closing), frame(bar=close.opening),
                  frame(at=datetime.fromisoformat(close.opening.available_at) + timedelta(seconds=1), price=103, fills={'SELL': 1}),
                  frame(bar=kept, fills={'SELL': 1}), frame(bar=exit_bar),
                  frame(at=datetime.fromisoformat(exit_bar.available_at) + timedelta(seconds=1), price=103, fills={'SELL': 2})]
        tape = {'version': 'local-strategy-rehearsal/v2', 'capital_won': 100_000, 'frames': frames,
                'plans': [dict(family=family, symbol=close.symbol, parameters=params, references=references)],
                'signal_valid_seconds': 10, 'maximum_quote_age_seconds': 3, 'maximum_adverse_slippage_bps': 300,
                'protective_slippage_bps': 300, 'sessions': {'KRX': ['KRX_REGULAR'], 'NXT': ['NXT_MAIN_MARKET']}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'ipo-gap-reduce.json'
            source.write_text(json.dumps(tape), encoding='utf-8')
            for venue in ('KRX', 'NXT'):
                with self.subTest(venue=venue):
                    replay = LocalStrategyRehearsal(source, root / 'runs', venue)
                    try:
                        while not replay.done:
                            status = replay.step()
                        plan = status['plans'][close.symbol]
                        self.assertEqual((0, 'FILLED', 2), (plan['held'], plan['SELL'], plan['stages']['ipo-reduce']['filled']))
                        commands = replay.broker.state['commands']
                        self.assertEqual(['kt10000', 'kt10001', 'kt10002', 'kt10001'], [item['api_id'] for item in commands])
                        self.assertEqual(('2', '1', '2'), (commands[1]['body']['ord_qty'], commands[2]['body']['mdfy_qty'],
                                                           commands[3]['body']['ord_qty']))
                        self.assertEqual(commands[1]['order_id'], commands[2]['body']['orig_ord_no'])
                    finally:
                        replay.close()

    def test_selected_original_management_uses_current_target_epoch_and_exact_saved_readback(self):
        dialog = self.dialog
        dialog._management_packet = dict(operation_id='original-order', packet_sha256='a' * 64,
            account_ref='original-source', symbol='005930')
        binding = self.context['candidate_binding']
        context = dict(credential_profile_id=PROFILE['profile_id'], credential_revision=PROFILE['revision'],
            canonical_account_ref=PROFILE['account_ref'], binding_revision=binding['binding_revision'],
            spec_id='original-spec', execution_run_id='original-run')
        status = dict(operation_id='original-order', packet_sha256='a' * 64, account_ref='original-source',
            reconciliation_ready=True, management_ready=False,
            installation=dict(target_account_ref=PROFILE['account_ref']),
            management=dict(context=context, status='RECONCILIATION_ONLY', settings_revision=self.context['account_settings']['revision']),
            control=dict(desired_state='STOPPED', control_revision=9))
        dialog._completed('selected_status', {'selected_management': status})
        self.assertTrue(dialog._management_accept.isEnabled())
        with patch.object(dialog, '_start_worker') as start:
            dialog._request_selected_management('selected_accept')
        values = start.call_args.args[1]
        self.assertEqual((PROFILE['account_ref'], PROFILE['revision'], binding['binding_revision'], 9),
            (values['account_ref'], values['credential_revision'], values['binding_revision'], values['expected_control_revision']))
        self.assertNotIn('source_identity_proof', values)
        self.assertNotIn('spec_id', values)
        self.assertEqual(self.context['account_settings']['revision'], values['expected_settings_revision'])
        applied = deepcopy(status)
        applied['management']['status'] = 'MANAGEMENT_READY'
        applied['management_ready'] = True
        applied['control']['desired_state'] = 'RUNNING'
        dialog._completed('selected_accept', {'selected_management': applied})
        self.assertIn('실행 상태 미확인', dialog._management_label.text())
        applied['supervisor'] = dict(runner=dict(spec_id='original-spec', execution_run_id='original-run'))
        dialog._completed('selected_status', {'selected_management': applied})
        self.assertIn('원주문 관리 연결됨', dialog._management_label.text())
        applied['management']['context']['credential_revision'] -= 1
        dialog._completed('selected_status', {'selected_management': applied})
        self.assertFalse(dialog._management_accept.isEnabled())
        self.assertNotIn('원주문 관리 연결됨', dialog._management_label.text())

    def test_account_selection_editor_requires_inputs_and_preserves_explicit_anchor_order_and_limits(self):
        from datetime import timezone
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationAccountSelectionDialog
        selection, specs = account_selection_documents()
        binding = self.context["candidate_binding"]
        now = datetime(2026, 9, 21, 2, tzinfo=timezone.utc)
        editor = MockAutomationAccountSelectionDialog(specs, binding, PROFILE["profile_id"],
                                                      now_provider=lambda: now, parent=self.dialog)
        self.addCleanup(editor.deleteLater)
        self.assertTrue(all(not field.text() for field in editor._limit_inputs.values()))
        self.assertTrue(all(not field.text() and not check.isChecked()
                            for check, field in editor._member_inputs.values()))
        self.assertIsNone(editor._anchor.currentData())
        with self.assertRaisesRegex(ValueError, "선택"):
            editor.publication_values()
        for identity, (check, field) in editor._member_inputs.items():
            check.setChecked(True)
            field.setText("2" if identity == selection["member_spec_ids"][0] else "1")
        editor._anchor.setCurrentIndex(1)
        with self.assertRaisesRegex(ValueError, "공통 한도"):
            editor.publication_values()
        for name, field in editor._limit_inputs.items():
            field.setText(str(selection["limits"][name]))
        values = editor.publication_values()
        document = values["selection"]
        self.assertEqual(selection["member_spec_ids"], document["member_spec_ids"])
        self.assertEqual(list(reversed(selection["member_spec_ids"])), document["execution_order_spec_ids"])
        self.assertEqual(selection["limits"], document["limits"])
        self.assertEqual(now.isoformat(), document["frozen_at"])
        self.assertEqual([150000, 550000], [row["spec"]["maximum_capital_won"] for row in specs])
        self.assertEqual((PROFILE["account_ref"], PROFILE["profile_id"], 2),
                         (values["account_ref"], values["credential_profile_id"], values["expected_binding_revision"]))
        restored = MockAutomationAccountSelectionDialog(specs, binding, PROFILE["profile_id"],
            now_provider=lambda: now, selection=selection, parent=self.dialog)
        self.addCleanup(restored.deleteLater)
        self.assertEqual(["2", "1"], [field.text() for _, field in restored._member_inputs.values()])
        self.assertEqual(selection["limits"], restored.publication_values()["selection"]["limits"])
        passive = {key: value for key, value in selection.items() if key != "execution_order_spec_ids"}
        unranked = MockAutomationAccountSelectionDialog(specs, binding, PROFILE["profile_id"],
            now_provider=lambda: now, selection=passive, parent=self.dialog)
        self.addCleanup(unranked.deleteLater)
        self.assertTrue(all(not field.text() for _, field in unranked._member_inputs.values()))
        with self.assertRaisesRegex(ValueError, "실행 순서"):
            unranked.publication_values()

    def test_account_selection_editor_rejects_duplicate_missing_order_and_unselected_anchor(self):
        from datetime import timezone
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationAccountSelectionDialog
        selection, specs = account_selection_documents()
        editor = MockAutomationAccountSelectionDialog(specs, self.context["candidate_binding"], PROFILE["profile_id"],
            now_provider=lambda: datetime(2026, 9, 21, 2, tzinfo=timezone.utc), selection=selection, parent=self.dialog)
        self.addCleanup(editor.deleteLater)
        first, second = list(editor._member_inputs.values())
        for a, b, error in (("1", "1", "중복"), ("1", "3", "빠짐없이"), ("", "2", "모두")):
            with self.subTest(order=(a, b)):
                first[1].setText(a); second[1].setText(b)
                with self.assertRaisesRegex(ValueError, error):
                    editor.publication_values()
        first[0].setChecked(False); second[1].setText("1")
        with self.assertRaisesRegex(ValueError, "계좌 기준"):
            editor.publication_values()

    def test_saved_group_start_and_controls_use_anchor_separately_from_execution_priority(self):
        selection, specs = account_selection_documents()
        self.context.update(specs=specs, selected_spec_id=selection["member_spec_ids"][0],
            account_selections=[selection], selected_account_selection_id=selection["selection_id"])
        self.dialog._completed("refresh", self.context)
        self.assertIn("저장됨 · 시작 전 구성", self.dialog._selection_details.text())
        self.assertIn("종가 → 돌파", self.dialog._selection_details.text())
        self.assertIn("700,000", self.dialog._selection_details.text())
        self.assertTrue(self.dialog._start.isEnabled())
        self.assertFalse(self.dialog._spec.isEnabled())
        with patch.object(self.dialog, "_start_worker") as start_worker:
            self.dialog._request("start")
            operation, values = start_worker.call_args.args
            self.assertEqual("start", operation)
            self.assertEqual(selection["selection_id"], values["account_selection_id"])
            self.assertEqual(selection["member_spec_ids"][0], values["spec_id"])
            self.assertNotEqual(selection["execution_order_spec_ids"][0], values["spec_id"])
            self.assertEqual((7, 4), (values["expected_settings_revision"], values["credential_revision"]))
            self.context["status"]["control"] = {"active_spec_id": values["spec_id"], "control_revision": 9,
                                                 "desired_state": "STOPPED", "account_selection": selection}
            self.dialog._reason.setText("명시 재개")
            for command in ("stop", "resume"):
                self.dialog._request(command)
                payload = start_worker.call_args.args[1]
                self.assertEqual(selection["member_spec_ids"][0], payload["spec_id"])
                self.assertNotIn("account_selection_id", payload)
                self.assertEqual((9, "명시 재개"), (payload["expected_control_revision"], payload["reason"]))

    def test_group_status_keeps_saved_applied_unknown_and_member_reservations_separate(self):
        selection, specs = account_selection_documents()
        self.context.update(specs=specs, selected_spec_id=selection["member_spec_ids"][0],
            account_selections=[selection], selected_account_selection_id=selection["selection_id"])
        self.context["status"].update(runtime_mode="AUTOMATIC", control={"active_spec_id": selection["member_spec_ids"][0],
            "desired_state": "RUNNING", "account_selection": selection}, runner={
                "version": "mock_automation_account_runner_status/v1", "account_selection_id": selection["selection_id"],
                "execution_order_spec_ids": selection["execution_order_spec_ids"], "members": [
                    {"spec_id": selection["member_spec_ids"][0], "state": "STOPPED", "planned_protection": {
                        "state": "SUBMISSION_UNKNOWN", "plan_quantity": 2, "reserved_quantity": 2,
                        "detailed_filled_quantity": 0}},
                    {"spec_id": selection["member_spec_ids"][1], "state": "READY"}]},
            held_close_material=[{"symbol": "005930", "cut": "2026-11-09T00:01:00+00:00", "status": "OBSERVED",
                "partial": False, "changes_after_entry": [{"title": "[정정] 공급계약 해지",
                    "effect": "INTACT", "available_at": "2026-11-09T09:00:30+09:00"}]}])
        self.dialog._completed("refresh", self.context)
        self.assertIn("현재 제어에 적용됨", self.dialog._selection_details.text())
        self.assertIn("계좌 공통 실행", self.dialog._runtime.text())
        self.assertIn("종가베팅 005930 · 11-09 09:01:00 판단 시점 공시 · 진입 이후 변경 1건",
                      self.dialog._runtime.text())
        self.assertIn("[정정] 공급계약 해지 · 해지 철회 · 11-09 09:00:30 저장", self.dialog._runtime.text())
        self.assertIn("돌파 · 중지 · 보호 매도 접수 미확인 · 계획 보유 2주 · 주문 예약 2주 · 상세 체결 0주",
                      self.dialog._members_status.text())
        self.assertIn("종가 · 준비", self.dialog._members_status.text())
        self.context["status"]["runner"]["account_selection_id"] = "conflicting-runner-selection"
        self.dialog._completed("refresh", self.context)
        self.assertIn("적용 상태 미확인", self.dialog._selection_details.text())
        self.context["selected_account_selection_id"] = "missing-selection-id"
        self.dialog._completed("refresh", self.context)
        self.assertEqual("missing-selection-id", self.dialog._selection.currentData())
        self.assertIn("저장 내용을 확인할 수 없습니다", self.dialog._selection_details.text())
        self.assertFalse(self.dialog._start.isEnabled())

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

    def test_local_ready_dialog_passes_the_explicit_nxt_choice_to_publication(self):
        from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationPublicationDialog

        candidate = {'package': {'candidate_spec': {'version': CLOSE_FINAL_CANDIDATE_VERSION,
                                                    'family': 'ipo_close/v1'}}}
        dialog = MockAutomationPublicationDialog(candidate, {}, 'profile', ({'event_id': 'shadow'},), self.dialog)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual('SOR', dialog._venue.currentData())
        dialog._close_inputs['maximum_adverse_slippage_bps'].setText('100')
        dialog._close_inputs['protective_slippage_bps'].setText('200')
        dialog._close_management.setCurrentIndex(2)
        with patch('kiwoom_monitor.presentation.mock_automation_dialog.build_mock_automation_publication_documents') as build:
            for venue in ('SOR', 'KRX', 'NXT'):
                dialog._venue.setCurrentIndex(dialog._venue.findData(venue))
                dialog.publication_values()
                self.assertEqual(venue, build.call_args.kwargs['supported_venue'])

    def test_panic_ready_requires_explicit_prices_and_original_plan_management_terms(self):
        from kiwoom_monitor.domain.execution_activation import PANIC_NATIVE_PLAN_MODE
        from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationPublicationDialog
        candidate = {'package': {'candidate_spec': {'version': CLOSE_FINAL_CANDIDATE_VERSION, 'family': 'panic_rebound/v1'}}}
        event = {'event_id': 'panic-ui-event'}
        dialog = MockAutomationPublicationDialog(candidate, {}, 'profile', (event,), self.dialog)
        self.addCleanup(dialog.deleteLater)
        with patch('kiwoom_monitor.presentation.mock_automation_dialog.build_mock_automation_publication_documents') as build:
            with self.assertRaisesRegex(ValueError, '낙주.*명시하세요'): dialog.publication_values()
            self.assertEqual(0, build.call_count)
            dialog._close_inputs['maximum_adverse_slippage_bps'].setText('100')
            dialog._close_inputs['protective_slippage_bps'].setText('500')
            with self.assertRaisesRegex(ValueError, '명시하세요'): dialog.publication_values()
            self.assertEqual(0, build.call_count)
            for index, stopped in ((1, 'reconcile_only/v1'), (2, 'continue_original_plan/v1')):
                dialog._close_management.setCurrentIndex(index); dialog.publication_values()
                values = build.call_args.kwargs
                self.assertEqual((candidate, event, PANIC_NATIVE_PLAN_MODE),
                    (values['candidate_publication'], values['shadow_event'], values['execution_plan_mode']))
                policy = values['panic_execution_policy']
                self.assertEqual((100, 500, stopped, 'reassess_then_amend_price/v1'),
                    (policy.maximum_adverse_slippage_bps, policy.protective_slippage_bps,
                     policy.stopped_management, policy.accepted_order_management))
                self.assertIsNone(values['close_execution_policy'])
                self.assertIsNone(values['unfilled_execution_policy'])
            dialog._close_inputs['protective_slippage_bps'].setText('10000')
            with self.assertRaises(ValueError): dialog.publication_values()

    def test_panic_candidate_and_saved_spec_show_their_own_policy_without_starting_orders(self):
        from kiwoom_monitor.domain.execution_activation import PANIC_NATIVE_PLAN_MODE, PanicMockExecutionPolicy
        from kiwoom_monitor.domain.research_contract import CLOSE_FINAL_CANDIDATE_VERSION
        candidate = {'package': {'strategy_ref': 'panic-test', 'candidate_spec': {
            'version': CLOSE_FINAL_CANDIDATE_VERSION, 'family': 'panic_rebound/v1'},
            'source_final': {'finished_at': '2026-11-06T09:00:00+09:00'}}, 'eligibility_receipt': {'status': 'ELIGIBLE'}}
        self.context.update(candidate_publications=[candidate], specs=[], selected_spec_id='')
        self.dialog._completed('refresh', self.context)
        self.assertIn('낙주 · ELIGIBLE', self.dialog._candidate.currentText())
        self.assertEqual('낙주 READY 명세 작성', self.dialog._publish.text())
        self.assertFalse(self.dialog._start.isEnabled())
        for stopped, text in (('reconcile_only/v1', '대사만 유지'), ('continue_original_plan/v1', '원래 계획 취소·청산 유지')):
            spec = {'strategy_ref': 'panic-test', 'spec_id': 'panic-ready', 'execution_plan_mode': PANIC_NATIVE_PLAN_MODE,
                    'panic_execution_policy': PanicMockExecutionPolicy(100, 500, stopped).to_dict()}
            self.context.update(specs=[{'spec': spec, 'readiness': 'READY'}], selected_spec_id='panic-ready')
            self.dialog._completed('refresh', self.context)
            self.assertIn('낙주 모의 · 현재 조건 재판단·정정 우선', self.dialog._spec.currentText())
            self.assertIn(text, self.dialog._spec.currentText())

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
            from dataclasses import replace
            for version in ('mock-unfilled-execution-policy/v1', 'mock-unfilled-execution-policy/v2'):
                selected = replace(policy, version=version)
                path.write_text(json.dumps(selected.to_dict()), encoding="utf-8")
                dialog.publication_values()
                self.assertEqual(LEADER_NATIVE_PLAN_MODE, build.call_args.kwargs["execution_plan_mode"])
                self.assertEqual(selected, build.call_args.kwargs["unfilled_execution_policy"])

    def test_leader_current_policy_label_matches_saved_spec_without_starting_orders(self):
        from kiwoom_monitor.domain.execution_activation import LEADER_NATIVE_PLAN_MODE
        for version, current in (('mock-unfilled-execution-policy/v1', False), ('mock-unfilled-execution-policy/v2', True)):
            spec = {'spec_id': 'leader-explicit', 'strategy_ref': 'leader-test',
                'execution_plan_mode': LEADER_NATIVE_PLAN_MODE, 'unfilled_execution_policy': {'version': version}}
            context = {**self.context, 'specs': [{'spec': spec, 'readiness': 'READY'}], 'selected_spec_id': spec['spec_id']}
            self.dialog._completed('refresh', context)
            self.assertEqual(current, '현재 조건 재판단·정정 우선' in self.dialog._spec.currentText())
            self.assertEqual('leader-explicit', self.dialog._spec.currentData())

    def test_explicit_leader_v3_review_shows_reentry_terms_and_keeps_v2_staged_values(self):
        from kiwoom_monitor.presentation.mock_automation_dialog import MockAutomationPublicationDialog
        from tests.unit.test_leader_position_steps import staged_config
        config = staged_config().to_dict()
        for version in ('leader_breakout_execution/v2', 'leader_breakout_execution/v3'):
            with self.subTest(version=version):
                parameters = deepcopy(config)
                parameters['execution_contract']['version'] = version
                if version.endswith('/v3'):
                    parameters['execution_contract']['reentry_rule'] = 'new_independent_setup_after_confirmed_flat/v1'
                candidate = {'package': {'candidate_spec': {'family': 'leader_breakout/v1', 'parameters': parameters}}}
                original = deepcopy(candidate)
                review = MockAutomationPublicationDialog(candidate, {}, PROFILE['profile_id'],
                    ({'event_id': 'explicit-shadow'},), self.dialog)
                self.addCleanup(review.deleteLater)
                self.assertIsNotNone(review._position_terms)
                self.assertEqual('SOR', review._venue.currentData())
                text = review._position_terms.text()
                self.assertIn('최초 진입 2주', text)
                self.assertIn('추가 매수 1주', text)
                self.assertEqual(version.endswith('/v3'), '새 독립 셋업으로만 재진입' in text)
                self.assertEqual(version.endswith('/v3'), '같은 셋업의 시각·세대만 바꿔' in text)
                self.assertEqual('', review._plan_policy_path.text())
                self.assertEqual(original, candidate)

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
        def specs(account_ref, **kwargs):
            if content.publish_mock_automation_spec.called:
                raise RuntimeError("publication followup unavailable")
            return original_specs(account_ref, **kwargs)
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
