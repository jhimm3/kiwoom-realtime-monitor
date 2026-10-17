from __future__ import annotations

import os
from copy import deepcopy
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidateMonitorDialog, CandidatePollWorker


class FakeClient:
    def load_candidate_events(self, **_kwargs):
        return {"high_watermark": 0, "events": [], "quality": {"status": "DISABLED"}}


class MemorySettings:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def value(self, key: str, default=None):
        return self.values.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.values[key] = value

    def clear(self) -> None:
        self.values.clear()


def _event(event_id: str, sequence: int) -> dict:
    return {
        "event_id": event_id, "accepted_sequence": sequence, "symbol": "005930",
        "available_at": "2026-09-12T00:00:00+00:00",
        "expires_at": "2099-09-12T00:01:00+00:00", "status": "ACTIVE",
        "signal_reference_price": 1000, "quantity": 1,
    }


class CandidateAlertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_initial_sync_is_silent_and_duplicate_transition_does_not_alert(self) -> None:
        settings = MemorySettings()
        with patch(
            "kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"
        ), patch(
            "kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings",
            return_value=settings,
        ), patch.object(QApplication, "beep") as beep:
            dialog = CandidateMonitorDialog(FakeClient())
            dialog._alert_enabled.setChecked(True)
            dialog._apply_page({
                "initial_sync": True, "high_watermark": 1,
                "events": [_event("event-1", 1)], "quality": {"status": "READY"},
            })
            dialog._apply_page({
                "initial_sync": False, "high_watermark": 1,
                "events": [_event("event-1", 1)], "quality": {"status": "READY"},
            })
            self.assertEqual(1, dialog._table.rowCount())
            beep.assert_not_called()

            dialog._apply_page({
                "initial_sync": False, "high_watermark": 2,
                "events": [_event("event-2", 2)], "quality": {"status": "READY"},
            })
            beep.assert_called_once()
            self.assertEqual("2", str(dialog._settings.value("last_consumed_sequence")))
            dialog._settings.clear()
            dialog.deleteLater()

    def test_candidate_polling_parks_without_consumers_and_resumes_silently(self) -> None:
        class CountingClient:
            def __init__(self) -> None:
                self.lock = threading.Lock()
                self.calls = 0

            def load_candidate_events(self, **_kwargs):
                with self.lock:
                    self.calls += 1
                    sequence = self.calls
                return {
                    "high_watermark": sequence,
                    "next_cursor": None,
                    "has_more": False,
                    "events": [_event(f"event-{sequence}", sequence)],
                    "quality": {"status": "READY"},
                }

        client = CountingClient()
        pages: list[dict] = []
        worker = CandidatePollWorker(client, poll_seconds=0.05)
        worker.pageReceived.connect(lambda page: pages.append(page) if not page.get("status_only") else None)

        def pump_until(predicate, timeout: float = 2.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.app.processEvents()
                if predicate():
                    return True
                time.sleep(0.005)
            self.app.processEvents()
            return bool(predicate())

        worker.start()
        try:
            time.sleep(0.1)
            self.assertEqual(0, client.calls)
            worker.set_polling_enabled(True)
            self.assertTrue(pump_until(lambda: len(pages) >= 1))
            self.assertTrue(pages[0]["initial_sync"])

            self.assertTrue(pump_until(lambda: len(pages) >= 2))
            worker.set_polling_enabled(False)
            self.assertTrue(pump_until(lambda: len(pages) >= 2))
            paused_calls = client.calls
            time.sleep(0.15)
            self.app.processEvents()
            self.assertEqual(paused_calls, client.calls)

            resumed_page_index = len(pages)
            worker.set_polling_enabled(True)
            self.assertTrue(pump_until(lambda: len(pages) > resumed_page_index))
            self.assertTrue(pages[resumed_page_index]["initial_sync"])
            self.assertTrue(pump_until(lambda: len(pages) > resumed_page_index + 1))
            self.assertFalse(pages[resumed_page_index + 1]["initial_sync"])
        finally:
            worker.requestInterruption()
            worker.wake_for_shutdown()
            worker.wait(2000)
        self.assertFalse(worker.isRunning())

    def test_dialog_polls_only_when_visible_or_alerts_are_enabled(self) -> None:
        settings = MemorySettings()
        with patch(
            "kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"
        ) as start:
            with patch(
                "kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings",
                return_value=settings,
            ):
                dialog = CandidateMonitorDialog(FakeClient())
                self.assertFalse(dialog._worker._polling_enabled)
                start.assert_not_called()

                dialog.show()
                self.app.processEvents()
                self.assertTrue(dialog._worker._polling_enabled)
                start.assert_called_once()

                dialog.close()
                self.app.processEvents()
                self.assertFalse(dialog._worker._polling_enabled)

                dialog._alert_enabled.setChecked(True)
                self.assertTrue(dialog._worker._polling_enabled)
                dialog.hide()
                self.app.processEvents()
                self.assertTrue(dialog._worker._polling_enabled)

                dialog._alert_enabled.setChecked(False)
                self.assertFalse(dialog._worker._polling_enabled)
                dialog.stop()
                dialog.deleteLater()


class RamShadowDialogTests(unittest.TestCase):
    def test_minute_primary_and_five_minute_confirmation_are_visible_without_seconds(self):
        from test_market_research_features import MinuteMarketCandidateTests
        helper=MinuteMarketCandidateTests();price=helper.compute();price['expires_at']='2099-10-13T09:21:30+09:00'
        page=self.page()['live'];ref='minute-primary-fixture'
        page['market_observed_changes']={ref:{'revision_id':ref,'pattern':'UNKNOWN','quality':{'status':'PARTIAL','reasons':()},
            'observed_at':'2026-09-14T09:00:20+09:00','expires_at':'2099-09-14T09:01:50+09:00',
            'lookup_change':{'state':'RETAINED'},'candidates':(),'input_refs':(),
            'price_trade_leadership':price,'second_trade_auxiliary':None}}
        for slot in page['results']:slot['market_observed_changes_ref']=ref
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start'), \
             patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog=CandidateMonitorDialog(FakeClient())
            try:
                dialog._apply_ram_page({'live':page,'runtime':{'state':'RUNNING'}})
                item=dialog._ram_table.item(0,2)
                self.assertIn('1분 가격·대금 후보: 후보 유지',item.text())
                self.assertIn('5분 지속: 확인',item.text())
                self.assertIn('1분 TOP20 관측: 가격 상승 5.0%',item.text())
                self.assertIn('실시간 시장 관찰에도 확정 분봉',item.toolTip())
                self.assertIn('현재가·체결·보호는 0B',item.toolTip())
                price['expires_at']='2026-10-13T09:21:30+09:00'
                dialog._render_ram_results()
                self.assertIn('1분 가격·대금 후보: 미확인',dialog._ram_table.item(0,2).text())
                self.assertIn('5분 지속: 미확인',dialog._ram_table.item(0,2).text())
            finally:dialog.stop();dialog.deleteLater();self.app.processEvents()

    def test_market_observation_is_separate_and_expiry_hides_live_candidates_only(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        page = self.page()["live"]
        ref = "observed-change-fixture"
        observation = {"revision_id": ref, "pattern": "LEADER_REPLACEMENT",
            "as_of": "2026-09-14T09:00:20+09:00", "observed_at": "2026-09-14T09:00:20+09:00",
            "expires_at": "2099-09-14T09:01:50+09:00", "quality": {"status": "COMPLETE", "reasons": ()},
            "candidates": ({"code": "005930", "state": "REAPPEARED"},), "input_refs": ("rank-fixture",)}
        page["market_observed_changes"] = {ref: observation}
        for slot in page["results"]:
            slot["market_observed_changes_ref"] = ref
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"), \
             patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()), \
             patch.object(QApplication, "beep") as beep:
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                dialog._apply_ram_page({"live": page, "runtime": {"state": "RUNNING"}})
                self.assertIn("조회순위 변화: 첫 순위 교체", dialog._ram_table.item(0, 2).text())
                self.assertIn("재등장 1", dialog._ram_table.item(0, 2).toolTip())
                self.assertIn("rank-fixture", dialog._ram_table.item(0, 2).toolTip())
                observation["expires_at"] = "2026-09-14T09:01:50+09:00"
                dialog._render_ram_results()
                self.assertIn("조회순위 변화: 미확인", dialog._ram_table.item(0, 2).text())
                self.assertIn("재등장 0", dialog._ram_table.item(0, 2).toolTip())
                self.assertEqual("유지 제안", dialog._ram_table.item(0, 5).text())
                dialog._ram_recorded = deepcopy(page)
                dialog._ram_view.setCurrentIndex(1)
                self.assertIn("조회순위 변화: 첫 순위 교체", dialog._ram_table.item(0, 2).text())
                beep.assert_not_called()
            finally:
                dialog.stop(); dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()

    def test_price_candidate_and_lookup_expire_independently(self):
        page=self.page()['live']; ref='rank-price-separate'
        observation={'revision_id':ref,'pattern':'UNKNOWN','observed_at':'2026-09-14T09:00:20+09:00',
            'expires_at':'2099-09-14T09:01:50+09:00','quality':{'status':'PARTIAL','reasons':()},'candidates':(),
            'lookup_change':{'state':'CHANGED'},'input_refs':(),
            'price_trade_leadership':{'state':'RETAINED','expires_at':'2099-09-14T09:00:23+09:00',
                'quality':{'status':'COMPLETE','reasons':(),'coverage':{'usable_members':20,'expected_members':20}},'current_candidates':('005930',),'metrics':(),
                'venue':'KRX','universe':'TOP20'}}
        page['market_observed_changes']={ref:observation}
        for slot in page['results']:slot['market_observed_changes_ref']=ref
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start'), \
             patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog=CandidateMonitorDialog(FakeClient())
            try:
                dialog._apply_ram_page({'live':page,'runtime':{'state':'RUNNING'}})
                text=dialog._ram_table.item(0,2).text()
                self.assertIn('조회순위 변화: 첫 순위 교체',text)
                self.assertIn('가격·대금 주도 후보: 후보 유지',text)
                self.assertIn('005930',text)
                self.assertIn('판단시점 시세자료 20/20종목',text)
                observation['price_trade_leadership']['expires_at']='2026-09-14T09:00:23+09:00'
                dialog._render_ram_results();text=dialog._ram_table.item(0,2).text()
                self.assertIn('조회순위 변화: 첫 순위 교체',text)
                self.assertIn('가격·대금 주도 후보: 미확인',text)
                self.assertIn('시세자료 확인 만료',text)
                dialog._ram_recorded=deepcopy(page);dialog._ram_view.setCurrentIndex(1)
                self.assertIn('가격·대금 주도 후보: 후보 유지',dialog._ram_table.item(0,2).text())
            finally:dialog.stop();dialog.deleteLater();self.app.processEvents()

    def test_partial_price_coverage_shows_code_and_readable_reasons_without_candidate(self):
        page=self.page()['live'];ref='partial-price-coverage'
        observation={'revision_id':ref,'pattern':'UNKNOWN','observed_at':'2026-09-14T09:00:20+09:00',
            'expires_at':'2099-09-14T09:01:50+09:00','quality':{'status':'PARTIAL','reasons':()},'candidates':(),
            'lookup_change':{'state':'RETAINED'},'input_refs':(),
            'price_trade_leadership':{'state':'UNKNOWN','expires_at':'2026-09-14T09:00:20+09:00',
                'quality':{'status':'PARTIAL','reasons':('comparison_cohort_price_trade_incomplete',),
                    'coverage':{'usable_members':17,'expected_members':20}},'current_candidates':(),
                'metrics':({'code':'005930','quality':'PARTIAL','previous_observation_count':2,'current_observation_count':1,
                    'reasons':('current_price_sample_insufficient','trade_source_history_insufficient')},),
                'venue':'KRX','universe':'TOP20'}}
        page['market_observed_changes']={ref:observation}
        for slot in page['results']:slot['market_observed_changes_ref']=ref
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start'), \
             patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog=CandidateMonitorDialog(FakeClient())
            try:
                dialog._apply_ram_page({'live':page,'runtime':{'state':'RUNNING'}})
                item=dialog._ram_table.item(0,2)
                self.assertIn('조회순위 변화: 첫 순위 유지',item.text())
                self.assertIn('가격·대금 주도 후보: 미확인',item.text())
                self.assertIn('판단시점 시세자료 17/20종목',item.text())
                self.assertIn('005930: 이전 2건 / 현재 1건',item.toolTip())
                self.assertIn('수신 승인 뒤 비교 이력 부족',item.toolTip())
                self.assertIn('현재 구간 가격 표본 부족',item.toolTip())
                self.assertNotIn('trade_source_history_insufficient',item.toolTip())
            finally:dialog.stop();dialog.deleteLater();self.app.processEvents()

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return bool(predicate())

    def page(self):
        from tests.unit import test_entry_thesis as fixtures
        from kiwoom_monitor.application.market_theme_shadow import evaluate_stock_thesis_shadow
        fixture = fixtures.StockThesisShadowTests(); fixture.setUp()
        decisions = [evaluate_stock_thesis_shadow(fixture.weak_inputs(), fixture.request(policy),
            leadership_parameters=fixtures.PARAMETERS) for policy in
            ("PRICE_ONLY", "IMMEDIATE_EXIT", "REDUCE_ON_RISK", "CONFIRM_THEN_EXIT")]
        context = decisions[0]["context"]
        results = [{"binding": index, "theme_id": "반도체", "leadership_venue": "KRX", "context_ref": context["revision_id"],
                    "decision": {key: value for key, value in decision.items() if key != "context"},
                    "display_state": "CURRENT"} for index, decision in enumerate(decisions)]
        envelope = {"contexts": {context["revision_id"]: context}, "results": results,
                    "selected_at": context["decision_time"], "session_id": "previous-observation-session"}
        return {"live": deepcopy(envelope), "recorded": {"document": deepcopy(envelope)},
                "runtime": {"state": "RUNNING", "recording": {"last_error": ""}}}

    def selection(self, **changes):
        return {"revision": 2, "enabled": False, "theme_ids": ["반도체"], "leadership_venue": "KRX",
                "recording": False, "runtime_supported": True, "mode": "theme_context",
                "apply_status": "ACTIVE", **changes}

    def test_live_and_recorded_independent_policy_rows_expiry_and_no_alert(self):
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()), patch.object(QApplication, "beep") as beep:
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                page = self.page()
                dialog._apply_ram_selection(self.selection(apply_status="RECOVERY_REQUIRED", last_error="apply pending"))
                dialog._apply_ram_page(page)
                self.assertEqual(4, dialog._ram_table.rowCount())
                self.assertEqual(["유지 제안", "청산 제안", "축소 제안", "대기 제안"], [dialog._ram_table.item(row, 5).text() for row in range(4)])
                self.assertTrue(all("만료" in dialog._ram_table.item(row, 7).text() for row in range(4)))
                self.assertIn("복구 필요", dialog._ram_selection_status.text())
                dialog._ram_view.setCurrentIndex(1)
                self.assertIn("과거 관찰 결과", dialog._ram_status.text())
                dialog._apply_ram_page({"live": None, "recorded": None, "runtime": {"state": "DISABLED"}})
                self.assertEqual(4, dialog._ram_table.rowCount())
                dialog._ram_view.setCurrentIndex(0)
                self.assertEqual(0, dialog._ram_table.rowCount())
                beep.assert_not_called()
                self.assertEqual(0, dialog._table.rowCount())
            finally:
                dialog.stop(); dialog.deleteLater()

    def test_visible_live_only_polling_keeps_edits_and_recorded_reads_are_explicit(self):
        page = self.page(); selection = self.selection(); calls = []
        main_thread = threading.get_ident()
        class Client(FakeClient):
            def load_ram_shadow(self, *, include_recorded=False):
                calls.append((include_recorded, threading.get_ident()))
                value = deepcopy(page)
                if not include_recorded:
                    value["recorded"] = None
                return value
        class Settings:
            def load_ram_shadow_selection(self):
                return deepcopy(selection)
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()), \
             patch("kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"), \
             patch.object(CandidateMonitorDialog, "_start_settings_request"):
            dialog = CandidateMonitorDialog(Client(), Settings())
            try:
                dialog._refresh_ram_if_visible()
                self.assertEqual([], calls)
                dialog.show(); self.app.processEvents(); dialog._tabs.setCurrentIndex(1)
                self.assertTrue(self.pump_until(lambda: calls and dialog._settings_worker is None))
                self.assertFalse(dialog._worker._polling_enabled)
                self.assertTrue(all(thread != main_thread for _, thread in calls))
                dialog._ram_themes.setText("편집 중")
                before = len(calls); dialog._refresh_ram_if_visible()
                self.assertTrue(self.pump_until(lambda: len(calls) > before and dialog._settings_worker is None))
                self.assertEqual("편집 중", dialog._ram_themes.text())
                self.assertFalse(any(include for include, _ in calls))
                dialog._ram_view.setCurrentIndex(1)
                self.assertTrue(self.pump_until(lambda: any(include for include, _ in calls) and dialog._settings_worker is None))
                before = len(calls); dialog._refresh_ram_if_visible()
                self.assertEqual(before, len(calls))
                dialog.hide(); self.app.processEvents(); dialog._refresh_ram_if_visible()
                self.assertEqual(before, len(calls))
            finally:
                dialog.stop(); dialog.deleteLater()

    def test_save_requires_loaded_supported_selection_and_preserves_revision(self):
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                with patch.object(dialog, "_start_ram_request") as request:
                    dialog._save_ram_selection(); request.assert_not_called()
                    dialog._apply_ram_selection(self.selection(mode="explicit_bindings", runtime_supported=False))
                    self.assertFalse(dialog._ram_form.isEnabled()); dialog._save_ram_selection(); request.assert_not_called()
                    dialog._apply_ram_selection(self.selection())
                    dialog._ram_enabled.setChecked(True); dialog._ram_themes.setText("반도체, 바이오")
                    dialog._ram_venue.setCurrentText("NXT"); dialog._ram_recording.setChecked(True)
                    dialog._save_ram_selection()
                    self.assertEqual(("save", {"expected_revision": 2, "enabled": True, "theme_ids": ["반도체", "바이오"],
                        "leadership_venue": "NXT", "recording": True}), request.call_args.args)
                    dialog._show_ram_request_failure("save", "HTTP 409")
                    self.assertFalse(dialog._ram_selection_loaded)
                    self.assertFalse(dialog._ram_form.isEnabled())
            finally:
                dialog.stop(); dialog.deleteLater()

    def test_stop_waits_for_pending_http_worker_and_does_not_request_after_stop(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        class Client(FakeClient):
            def load_ram_shadow(self, **_kwargs):
                calls.append(True); entered.set()
                if not release.wait(3):
                    raise AssertionError("HTTP worker not released")
                return {"live": None, "recorded": None, "runtime": {"state": "DISABLED"}}
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(Client())
            try:
                self.assertTrue(dialog._start_ram_request("live"))
                self.assertTrue(entered.wait(1))
                worker = dialog._settings_worker
                def finish():
                    time.sleep(0.05); release.set()
                thread = threading.Thread(target=finish); thread.start()
                start = time.monotonic(); dialog.stop(); elapsed = time.monotonic() - start
                thread.join(); self.app.processEvents()
                self.assertGreaterEqual(elapsed, 0.04)
                self.assertFalse(worker.isRunning())
                self.assertFalse(dialog._ram_timer.isActive())
                self.assertFalse(dialog._start_ram_request("recorded"))
                self.assertEqual(1, len(calls))
            finally:
                release.set(); dialog.stop(); dialog.deleteLater()


class PositionComparisonDialogTests(unittest.TestCase):
    page = RamShadowDialogTests.page
    selection = RamShadowDialogTests.selection

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def comparison(self):
        from tests.unit.test_entry_thesis import PositionActionComparisonTests
        fixture = PositionActionComparisonTests(); fixture.setUp()
        return fixture.document(), fixture.run_comparison()

    def test_action_specific_missing_context_is_displayed_with_its_action(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        fixture=NativePositionSelectionInputsTests();fixture.setUp()
        policy=fixture.capability_policy(('sampled_price',),action_required_data_capabilities=(('REDUCE',('news_context',)),))
        result=fixture.evaluate(policy=policy,thesis=None)
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog=CandidateMonitorDialog(FakeClient())
            try:
                dialog._render_position_comparison({'position_comparison':{'input_mode':'owner_ram_snapshot',
                    'is_live_account':True,'result':result,'valid_until':'2099-01-01T00:00:00+00:00',
                    'account_as_of':result['decision_time']}},recorded=False)
                self.assertIn('REDUCE 필수 자료: 뉴스 맥락, 현재 가격 · 부족: 뉴스 맥락',dialog._ram_position_status.toolTip())
                self.assertNotIn('required_data_capability_unavailable',dialog._ram_position_status.toolTip())
                self.assertIn('실행 승인 아님',dialog._ram_position_status.toolTip())
            finally:dialog.stop();dialog.deleteLater()

    def test_policy_required_and_missing_capabilities_are_displayed_without_unused_thesis(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        fixture=NativePositionSelectionInputsTests();fixture.setUp()
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog=CandidateMonitorDialog(FakeClient())
            try:
                for caps,label,missing in ((('sampled_price',),'현재 가격','없음'),(('news_context',),'뉴스 맥락','뉴스 맥락')):
                    result=fixture.evaluate(policy=fixture.capability_policy(caps),thesis=None)
                    dialog._render_position_comparison({'position_comparison':{'input_mode':'owner_ram_snapshot',
                        'is_live_account':True,'result':result,'valid_until':'2099-01-01T00:00:00+00:00',
                        'account_as_of':result['decision_time']}},recorded=False)
                    self.assertIn('정책별 자료·Risk RAM 근거',dialog._ram_position_status.text())
                    self.assertIn('정책 필수 자료: '+label,dialog._ram_position_status.toolTip())
                    self.assertIn('부족한 정책 자료: '+missing,dialog._ram_position_status.toolTip())
                    self.assertNotIn(fixture.thesis['decision_id'],dialog._ram_position_status.toolTip())
                    self.assertIn('실행 승인 아님',dialog._ram_position_status.toolTip())
            finally:dialog.stop();dialog.deleteLater()

    def test_native_thesis_and_risk_refs_are_displayed_as_observations(self):
        from tests.unit.test_entry_thesis import NativePositionSelectionInputsTests
        fixture = NativePositionSelectionInputsTests(); fixture.setUp()
        result = fixture.evaluate()
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                dialog._render_position_comparison({'position_comparison':{'input_mode':'owner_ram_snapshot',
                    'is_live_account':True,'result':result,'valid_until':'2099-01-01T00:00:00+00:00',
                    'account_as_of':result['decision_time']}},recorded=False)
                self.assertEqual(2,dialog._ram_position_table.rowCount())
                self.assertIn('기존 타점·Risk RAM 근거',dialog._ram_position_status.text())
                self.assertIn(fixture.risk.snapshot_id,dialog._ram_position_status.toolTip())
                self.assertIn(fixture.thesis['decision_id'],dialog._ram_position_status.toolTip())
                self.assertIn('실행 승인 아님',dialog._ram_position_status.toolTip())
            finally: dialog.stop(); dialog.deleteLater()

    def test_order_free_selection_protection_and_expiry_are_displayed(self):
        from dataclasses import replace
        from tests.unit.test_entry_thesis import PositionActionSelectionTests
        fixture = PositionActionSelectionTests(); fixture.setUp()
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                for constraint, expected in (('ALLOW','축소 제안'),('EXIT_REQUIRED','청산 제안')):
                    result = fixture.run_comparison(parameters=replace(fixture.parameters,
                        selection_policy=fixture.policy(risk_constraint=constraint)))
                    value = {'input_mode':'supplied_fixed_snapshot','is_live_account':False,'result':result}
                    dialog._render_position_comparison({'position_comparison':value},recorded=False)
                    self.assertEqual(2,dialog._ram_position_table.rowCount())
                    self.assertIn('주문 없는 행동 권고: '+expected,dialog._ram_position_status.text())
                    self.assertIn('실제 Risk 승인 아님',dialog._ram_position_status.toolTip())
                    if constraint == 'EXIT_REQUIRED':
                        self.assertIn('보호 Risk·Execution 경로 우선',dialog._ram_position_status.text())
                value.update(input_mode='owner_ram_snapshot',is_live_account=True,
                    valid_until=result['decision_time'],account_as_of=result['decision_time'])
                dialog._render_position_comparison({'position_comparison':value},recorded=False)
                self.assertIn('행동 권고 사용 불가',dialog._ram_position_status.text())
                dialog._render_position_comparison({'position_comparison':{'input_mode':'supplied_fixed_snapshot',
                    'is_live_account':False,'result':fixture.run_comparison()}},recorded=False)
                self.assertEqual('',dialog._ram_position_status.toolTip())
            finally: dialog.stop(); dialog.deleteLater()

    def test_automatic_target_and_limits_are_displayed_without_order_authority(self):
        from dataclasses import replace
        from datetime import timedelta
        from tests.unit.test_entry_thesis import PositionActionComparisonTests
        from kiwoom_monitor.application.position_target import PositionTargetSizingPolicy
        fixture=PositionActionComparisonTests(); fixture.setUp()
        policy=PositionTargetSizingPolicy('bounded-notional-sizing/v1',5_000,30_000,30,1_000,
            fixture.now-timedelta(seconds=1),fixture.now+timedelta(seconds=20))
        requests=(fixture.requests[0],replace(fixture.requests[1],target_quantity=None,target_policy=policy))
        result=fixture.run_comparison(requests=requests)
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog=CandidateMonitorDialog(FakeClient())
            try:
                dialog._render_position_comparison({'position_comparison':{'input_mode':'supplied_fixed_snapshot',
                    'is_live_account':False,'result':result}},recorded=False)
                self.assertEqual(2,dialog._ram_position_table.rowCount())
                self.assertIn('자동 목표 수량 정책 적용',dialog._ram_position_status.text())
                item=dialog._ram_position_table.item(1,2)
                self.assertEqual('10 / 5',item.text())
                self.assertIn('보유 한도 30,000원 / 30주',item.toolTip())
                self.assertIn('남길 현금 1,000원',item.toolTip())
                self.assertFalse(result['order_authorized'])
            finally: dialog.stop(); dialog.deleteLater()

    def test_whole_account_confirmation_is_displayed_without_order_authority(self):
        _, result = self.comparison()
        value = {"input_mode": "owner_ram_snapshot", "is_live_account": True, "result": result,
                 "account_orders_complete": True, "account_reconciliation_complete": True,
                 "quality": {"status": "AVAILABLE", "reasons": []},
                 "account_as_of": result["decision_time"], "valid_until": "2099-01-01T00:00:00+00:00"}
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                dialog._render_position_comparison({"position_comparison": value}, recorded=False)
                self.assertIn("계좌 전체 미체결 확인", dialog._ram_position_status.text())
                self.assertNotIn("근거 미확인", dialog._ram_position_status.text())
                self.assertFalse(value["result"]["order_authorized"])
            finally:
                dialog.stop(); dialog.deleteLater()

    def test_fixed_rows_preserve_unknown_target_and_ev_and_clear_stale_view(self):
        from dataclasses import replace
        from tests.unit.test_entry_thesis import PositionActionComparisonTests
        fixture = PositionActionComparisonTests(); fixture.setUp()
        fixture.requests += (replace(fixture.fixture.request, action="ADD", target_quantity=None),)
        result = fixture.run_comparison()
        page = self.page()
        for envelope in (page["live"], page["recorded"]["document"]):
            envelope["position_comparison"] = {"input_mode": "supplied_fixed_snapshot", "is_live_account": False, "result": deepcopy(result)}
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()), patch.object(QApplication, "beep") as beep:
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                dialog._apply_ram_page(page)
                self.assertEqual(4, dialog._ram_table.rowCount())
                self.assertEqual(3, dialog._ram_position_table.rowCount())
                self.assertIn("미확인", dialog._ram_position_table.item(0, 2).text())
                self.assertEqual("미확인", dialog._ram_position_table.item(0, 4).text())
                self.assertIn("수량 차단", dialog._ram_position_table.item(0, 8).text())
                self.assertEqual(["45.00", "45.00", "2", "25.00"], [dialog._ram_position_table.item(2, c).text() for c in (4, 5, 6, 7)])
                self.assertIn("실시간 계좌 자료 아님", dialog._ram_position_status.text())
                self.assertIn("09-14", dialog._ram_position_status.text())
                dialog._ram_view.setCurrentIndex(1)
                self.assertIn("최근 저장", dialog._ram_position_status.text())
                dialog._ram_view.setCurrentIndex(0)
                dialog._apply_ram_page({"live": None, "runtime": {"state": "DISABLED"}})
                self.assertEqual(0, dialog._ram_position_table.rowCount())
                self.assertIn("자료 없음", dialog._ram_position_status.text())
                beep.assert_not_called()
            finally:
                dialog.stop(); dialog.deleteLater()

    def test_input_selection_save_clear_and_invalid_response(self):
        import json
        import tempfile
        from pathlib import Path
        document, result = self.comparison()
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                dialog._apply_ram_selection(self.selection(position_comparison=document))
                with patch.object(dialog, "_start_ram_request") as request:
                    dialog._save_ram_selection()
                    self.assertEqual(document, request.call_args.args[1]["position_comparison"])
                    dialog._ram_position_enabled.setChecked(False); dialog._save_ram_selection()
                    self.assertIsNone(request.call_args.args[1]["position_comparison"])
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "input.json"; path.write_text(json.dumps(document), encoding="utf-8")
                    with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QFileDialog.getOpenFileName", return_value=(str(path), "")):
                        dialog._choose_position_document()
                        self.assertTrue(dialog._ram_position_enabled.isChecked())
                        self.assertEqual(document, dialog._ram_position_document)
                        path.write_bytes(b"x" * (256 * 1024 + 1)); dialog._choose_position_document()
                        self.assertIn("실패", dialog._ram_position_input_status.text())
                        self.assertEqual(document, dialog._ram_position_document)
                result["order_authorized"] = True
                dialog._render_position_comparison({"position_comparison": {"input_mode": "supplied_fixed_snapshot", "is_live_account": False, "result": result}}, recorded=False)
                self.assertEqual(0, dialog._ram_position_table.rowCount())
                self.assertIn("확인할 수 없습니다", dialog._ram_position_status.text())
            finally:
                dialog.stop(); dialog.deleteLater()


    def test_owner_ram_partial_expired_rows_and_live_selection_clear_are_explicit(self):
        from dataclasses import replace
        from tests.unit.test_entry_thesis import PositionActionComparisonTests
        fixture = PositionActionComparisonTests(); fixture.setUp()
        document = fixture.document()
        result = fixture.run_comparison(requests=tuple(replace(r, open_orders_complete=False) for r in fixture.requests))
        value = {"input_mode": "owner_ram_snapshot", "is_live_account": True, "result": result,
                 "quality": {"status": "PARTIAL", "reasons": ["account_wide_intent_coverage_unverified"]},
                 "account_as_of": result["decision_time"], "valid_until": result["decision_time"]}
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(FakeClient())
            try:
                dialog._apply_ram_selection(self.selection(position_comparison=document, position_account_live=True))
                self.assertIn("계좌 수량·기준가격은 기존 RAM", dialog._ram_position_input_status.text())
                with patch.object(dialog, "_start_ram_request") as request:
                    dialog._save_ram_selection()
                    self.assertIs(True, request.call_args.args[1]["position_account_live"])
                    dialog._ram_position_live.setChecked(False); dialog._save_ram_selection()
                    self.assertIs(False, request.call_args.args[1]["position_account_live"])
                dialog._render_position_comparison({"position_comparison": value}, recorded=False)
                self.assertEqual(2, dialog._ram_position_table.rowCount())
                self.assertIn("RAM 관측", dialog._ram_position_status.text())
                self.assertIn("자료 만료", dialog._ram_position_table.item(0, 8).text())
                self.assertEqual("미확인", dialog._ram_position_table.item(0, 3).text())
                self.assertIn("계좌 전체 미체결 근거 미확인", dialog._ram_position_status.text())
                value["result"] = None
                dialog._render_position_comparison({"position_comparison": value}, recorded=False)
                self.assertEqual(0, dialog._ram_position_table.rowCount())
                self.assertIn("기대값 미확인", dialog._ram_position_status.text())
            finally:
                dialog.stop(); dialog.deleteLater()


class ResearchThesisDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    pump_until = RamShadowDialogTests.pump_until
    selection = RamShadowDialogTests.selection
    page = RamShadowDialogTests.page

    def dialog(self, client):
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(FakeClient(), client)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.stop)
        dialog._apply_ram_selection(self.selection())
        return dialog

    def configure(self, dialog, selected, path=""):
        thesis = selected["evidence"]["thesis"]
        dialog._ram_thesis_enabled.setChecked(True)
        dialog._ram_thesis_db.setText(str(path))
        dialog._ram_thesis_run.setText(thesis["run_id"])
        dialog._ram_thesis_id.setText(thesis["thesis_id"])
        dialog._ram_thesis_policy.setCurrentIndex(dialog._ram_thesis_policy.findData(selected["thesis_policy_version"]))

    def client(self):
        helper = self
        class Client:
            def __init__(self):
                self.calls = []
            def load_ram_shadow_selection(self):
                return helper.selection()
            def save_ram_shadow_selection(self, changes):
                self.calls.append((deepcopy(changes), threading.get_ident()))
                return helper.selection(**changes)
        return Client()

    def test_pc_read_only_evidence_and_save_use_worker_without_transmitting_file_path(self):
        import tempfile
        from pathlib import Path
        from tests.unit.test_candidate_monitor import saved_thesis_selection_fixture
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            path = Path(directory) / "research.sqlite3"
            before = path.read_bytes()
            client = self.client(); dialog = self.dialog(client)
            self.configure(dialog, selected, path)
            main_thread = threading.get_ident(); read_threads = []
            original = ResearchRepository.load_entry_thesis_evidence
            def read(repository, *args):
                read_threads.append(threading.get_ident())
                return original(repository, *args)
            with patch.object(ResearchRepository, "load_entry_thesis_evidence", read):
                dialog._save_ram_selection()
                self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
            self.assertEqual(1, len(client.calls))
            body, saved_thread = client.calls[0]
            self.assertEqual([saved_thread], read_threads)
            self.assertNotEqual(main_thread, saved_thread)
            self.assertEqual(selected, body["stock_thesis"])
            self.assertEqual(2, body["expected_revision"])
            import json
            self.assertNotIn(str(path), json.dumps(body))
            self.assertEqual(before, path.read_bytes())

    def test_saved_snapshot_can_change_policy_and_clear_without_reopening_database(self):
        import tempfile
        from tests.unit.test_candidate_monitor import saved_thesis_selection_fixture
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            client = self.client(); dialog = self.dialog(client)
            dialog._apply_ram_selection(self.selection(stock_thesis=selected))
            self.assertTrue(dialog._ram_thesis_enabled.isChecked())
            self.assertEqual("", dialog._ram_thesis_db.text())
            with patch("kiwoom_monitor.infrastructure.persistence.research_repository.ResearchRepository",
                       side_effect=AssertionError("saved choice reopened PC DB")):
                dialog._ram_thesis_policy.setCurrentIndex(dialog._ram_thesis_policy.findData("REDUCE_ON_RISK"))
                dialog._save_ram_selection()
                self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
                self.assertEqual("REDUCE_ON_RISK", client.calls[0][0]["stock_thesis"]["thesis_policy_version"])
                self.assertEqual("PRICE_ONLY", selected["thesis_policy_version"])
                dialog._ram_thesis_enabled.setChecked(False)
                dialog._save_ram_selection()
                self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
                self.assertIn("stock_thesis", client.calls[1][0])
                self.assertIsNone(client.calls[1][0]["stock_thesis"])
                self.assertFalse(dialog._ram_thesis_details.isEnabled())

    def test_invalid_local_database_error_is_displayed_without_save_or_file_mutation(self):
        import tempfile
        from pathlib import Path
        from tests.unit.test_candidate_monitor import saved_thesis_selection_fixture
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            bad = Path(directory) / "invalid.sqlite3"; bad.write_bytes(b"not an SQLite database")
            client = self.client(); dialog = self.dialog(client)
            self.configure(dialog, selected, bad)
            dialog._save_ram_selection()
            self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
            self.assertEqual([], client.calls)
            self.assertIn("실패", dialog._ram_selection_status.text())
            self.assertEqual(b"not an SQLite database", bad.read_bytes())

    def test_stop_joins_local_read_and_blocks_save_before_http_begins(self):
        import tempfile
        from pathlib import Path
        from tests.unit.test_candidate_monitor import saved_thesis_selection_fixture
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        entered, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            client = self.client(); dialog = self.dialog(client)
            self.configure(dialog, selected, Path(directory) / "research.sqlite3")
            original = ResearchRepository.load_entry_thesis_evidence
            def read(repository, *args):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("local thesis read was not released")
                return original(repository, *args)
            with patch.object(ResearchRepository, "load_entry_thesis_evidence", read):
                try:
                    dialog._save_ram_selection()
                    self.assertTrue(entered.wait(2))
                    worker = dialog._settings_worker
                    def finish():
                        time.sleep(0.05); release.set()
                    thread = threading.Thread(target=finish); thread.start()
                    dialog.stop(); thread.join(); self.app.processEvents()
                    self.assertFalse(worker.isRunning())
                    self.assertEqual([], client.calls)
                finally:
                    release.set()

    def test_live_results_preserve_thesis_edits_and_policy_requires_explicit_choice(self):
        import tempfile
        from tests.unit.test_candidate_monitor import saved_thesis_selection_fixture
        with tempfile.TemporaryDirectory() as directory:
            selected = saved_thesis_selection_fixture(directory)
            client = self.client(); dialog = self.dialog(client)
            self.assertFalse(dialog._ram_thesis_enabled.isChecked())
            self.assertEqual(-1, dialog._ram_thesis_policy.currentIndex())
            dialog._apply_ram_selection(self.selection(stock_thesis=selected))
            dialog._ram_thesis_id.setText("editing-id")
            dialog._apply_ram_page(self.page())
            self.assertEqual("editing-id", dialog._ram_thesis_id.text())
            dialog._ram_thesis_id.setText(selected["evidence"]["thesis"]["thesis_id"])
            dialog._ram_thesis_policy.setCurrentIndex(-1)
            dialog._save_ram_selection()
            self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
            self.assertEqual([], client.calls)
            self.assertIn("실패", dialog._ram_selection_status.text())


class StockThesisSourceCheckDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
    pump_until = RamShadowDialogTests.pump_until
    selection = RamShadowDialogTests.selection

    def selected(self):
        return {"evidence": {"thesis": {"run_id": "source-run", "thesis_id": "stored-thesis"}},
                "thesis_policy_version": "PRICE_ONLY"}

    def report(self, **changes):
        return {"version": "stock-thesis-source-check/v2", "selection_revision": 2,
                "thesis_ref": "stored-thesis", "as_of": "2026-09-14T00:00:20+00:00",
                "lookback_minutes": 20, "scope": "bounded_final_completed_minutes",
                "source_durability": "not_verified", "replay_eligible": False, "order_authorized": False,
                "theme": {"status": "MATCHED", "rows_read": 1, "budget_reached": False},
                "minutes": {"expected": 8, "matched": 8, "unmatched_refs": [], "rows_read": 8},
                "unverified_refs": {"leadership": [], "factors": ["opaque-factor"], "hypotheses": []},
                "timing_ms": {"total": 12.0}, **changes}

    def dialog(self, client):
        with patch("kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings", return_value=MemorySettings()), \
             patch("kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start"):
            dialog = CandidateMonitorDialog(client)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.stop)
        return dialog

    def client(self):
        helper = self
        class Client(FakeClient):
            def __init__(self):
                self.calls = []
            def load_ram_shadow(self, **kwargs):
                self.calls.append(("live", kwargs, threading.get_ident()))
                return {"live": None, "recorded": None, "runtime": {"state": "DISABLED"}}
            def check_ram_shadow_minute_sources(self, **kwargs):
                self.calls.append(("check", kwargs, threading.get_ident()))
                return helper.report(lookback_minutes=kwargs["lookback_minutes"])
        return Client()

    def test_only_loaded_saved_supported_thesis_enables_explicit_check(self):
        client = self.client(); dialog = self.dialog(client)
        self.assertFalse(dialog._ram_source_check.isEnabled())
        dialog._apply_ram_selection(self.selection())
        dialog._ram_thesis_enabled.setChecked(True)
        dialog._ram_thesis_id.setText("unsaved")
        self.assertFalse(dialog._ram_source_check.isEnabled())
        self.assertFalse(dialog._start_ram_request("source_check"))
        dialog._apply_ram_selection(self.selection(stock_thesis=self.selected(), selection_supported=False))
        self.assertFalse(dialog._ram_source_check.isEnabled())
        dialog._apply_ram_selection(self.selection(stock_thesis=self.selected()))
        self.assertTrue(dialog._ram_source_check.isEnabled())
        self.assertFalse(dialog._ram_enabled.isChecked())
        legacy = self.dialog(FakeClient())
        legacy._apply_ram_selection(self.selection(stock_thesis=self.selected()))
        self.assertFalse(legacy._ram_source_check.isEnabled())
        self.assertEqual([], client.calls)

    def test_click_uses_worker_saved_revision_and_captured_window_preserving_edits_and_live(self):
        client = self.client(); dialog = self.dialog(client)
        dialog._apply_ram_selection(self.selection(stock_thesis=self.selected()))
        dialog._ram_thesis_id.setText("editing-thesis")
        dialog._ram_source_window.setValue(30)
        before_page = {"live": None, "recorded": None, "runtime": {"state": "DISABLED"}}
        dialog._apply_ram_page(before_page)
        live_status = dialog._ram_status.text()
        main_thread = threading.get_ident()
        with patch.object(QApplication, "beep") as beep, \
             patch("sqlite3.connect", side_effect=AssertionError("UI check opened PC DB")), \
             patch.object(dialog, "_save_ram_selection", side_effect=AssertionError("check saved settings")):
            dialog._ram_source_check.click()
            self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
        self.assertEqual([("check", {"expected_revision": 2, "lookback_minutes": 30})], [(kind, args) for kind, args, _ in client.calls])
        self.assertNotEqual(main_thread, client.calls[0][2])
        self.assertEqual("editing-thesis", dialog._ram_thesis_id.text())
        self.assertIs(before_page, dialog._ram_page)
        self.assertEqual(live_status, dialog._ram_status.text())
        self.assertIn("8/8", dialog._ram_source_status.text())
        self.assertIn("30분", dialog._ram_source_status.text())
        self.assertIn("운영/재생 승인 없음", dialog._ram_source_status.text())
        self.assertTrue(dialog._ram_source_check.isEnabled())
        beep.assert_not_called()

    def test_visible_live_timer_never_repeats_source_check_and_live_keeps_manual_result(self):
        client = self.client(); dialog = self.dialog(client)
        dialog._apply_ram_selection(self.selection(stock_thesis=self.selected()))
        dialog.show(); self.app.processEvents(); dialog._tabs.setCurrentIndex(1)
        self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
        for _ in range(2):
            dialog._refresh_ram_if_visible()
            self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
        self.assertFalse(any(kind == "check" for kind, _, _ in client.calls))
        dialog._ram_source_check.click()
        self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
        result_text = dialog._ram_source_status.text()
        for _ in range(2):
            dialog._refresh_ram_if_visible()
            self.assertTrue(self.pump_until(lambda: dialog._settings_worker is None))
        self.assertEqual(1, sum(kind == "check" for kind, _, _ in client.calls))
        self.assertEqual(result_text, dialog._ram_source_status.text())
        dialog._apply_ram_selection(self.selection(revision=3, stock_thesis=None))
        self.assertNotIn("8/8", dialog._ram_source_status.text())
        self.assertFalse(dialog._ram_source_check.isEnabled())
        dialog.hide()

    def test_stale_scope_malformed_counts_and_authority_claims_are_not_displayed_as_matches(self):
        dialog = self.dialog(self.client())
        dialog._apply_ram_selection(self.selection(stock_thesis=self.selected()))
        for report in (self.report(selection_revision=1), self.report(order_authorized=True),
                       self.report(replay_eligible=True), self.report(scope="approved"),
                       self.report(minutes={"expected": 8, "matched": 9, "unmatched_refs": [], "rows_read": 8}),
                       self.report(timing_ms={"total": float("nan")})):
            dialog._apply_ram_source_check(report, 2, "stored-thesis", 20)
            self.assertIn("확인할 수 없습니다", dialog._ram_source_status.text())
            self.assertNotIn("8/8", dialog._ram_source_status.text())
        missing = self.report(theme={"status": "NOT_FOUND_IN_BOUNDED_READ", "rows_read": 100, "budget_reached": True},
                              minutes={"expected": 8, "matched": 0, "unmatched_refs": [str(n) for n in range(8)], "rows_read": 0})
        dialog._apply_ram_source_check(missing, 2, "stored-thesis", 20)
        self.assertIn("조회 범위에서", dialog._ram_source_status.text())
        self.assertIn("한도 도달", dialog._ram_source_status.text())
        self.assertIn("0/8", dialog._ram_source_status.text())
        empty = self.report(minutes={"expected": 0, "matched": 0, "unmatched_refs": [], "rows_read": 8},
                            unverified_refs={"leadership": ["legacy-ref"], "factors": [], "hypotheses": []})
        dialog._apply_ram_source_check(empty, 2, "stored-thesis", 20)
        self.assertIn("대조 가능한 참조 없음", dialog._ram_source_status.text())
        self.assertNotIn("0/0", dialog._ram_source_status.text())
        dialog._apply_ram_selection(self.selection(revision=3, stock_thesis=self.selected()))
        dialog._apply_ram_source_check(self.report(), 2, "stored-thesis", 20)
        self.assertIn("선택이 바뀌어", dialog._ram_source_status.text())

    def test_failure_is_separate_from_live_and_stop_joins_without_duplicate_or_late_result(self):
        entered, release = threading.Event(), threading.Event()
        helper = self
        class Client(FakeClient):
            def __init__(self):
                self.calls = 0
            def check_ram_shadow_minute_sources(self, **kwargs):
                self.calls += 1; entered.set()
                if not release.wait(3):
                    raise AssertionError("source HTTP not released")
                return helper.report()
        client = Client(); dialog = self.dialog(client)
        dialog._apply_ram_selection(self.selection(stock_thesis=self.selected()))
        dialog._ram_status.setText("live remains")
        dialog._show_ram_request_failure("source_check", "HTTP 409")
        self.assertEqual("live remains", dialog._ram_status.text())
        self.assertIn("원본 대조 실패", dialog._ram_source_status.text())
        try:
            dialog._ram_source_check.click()
            self.assertTrue(entered.wait(1))
            self.assertFalse(dialog._ram_source_check.isEnabled())
            self.assertFalse(dialog._start_ram_request("source_check"))
            worker = dialog._settings_worker
            def finish():
                time.sleep(0.05); release.set()
            thread = threading.Thread(target=finish); thread.start()
            dialog.stop(); thread.join(); self.app.processEvents()
            self.assertFalse(worker.isRunning())
            self.assertFalse(dialog._ram_source_check.isEnabled())
            self.assertNotIn("8/8", dialog._ram_source_status.text())
            self.assertFalse(dialog._start_ram_request("source_check"))
            self.assertEqual(1, client.calls)
        finally:
            release.set()


class NativePositionOutcomeDialogTests(unittest.TestCase):
    pump_until = RamShadowDialogTests.pump_until
    selection = RamShadowDialogTests.selection

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def fixture(self):
        import json
        from dataclasses import replace
        from tests.unit.test_entry_thesis import NativePositionOutcomeReaderTests, PositionActionComparisonTests
        reader = NativePositionOutcomeReaderTests(); reader.setUp(); self.addCleanup(reader.doCleanups)
        selections = [reader.source(action, minute=minute)[0] for minute in (0,20) for action in ('HOLD','EXIT')]
        outcomes, _ = reader.read(selections)
        policy = PositionActionComparisonTests(); policy.setUp()
        document = policy.document(outcomes=())
        document['account']['as_of'] = reader.cut.isoformat()
        by_action = {o.action:o for o in outcomes}
        for request in document['requests']:
            action = 'HOLD' if request['action']=='HOLD' else 'EXIT'
            request.update(action=action,target_quantity=by_action[action].target_quantity,
                policy_version=by_action[action].policy_version,decision_time=reader.cut.isoformat(),
                reference_price_available_at=reader.cut.isoformat())
        document['parameters']['execution_model_ref'] = outcomes[0].execution_model_ref
        windows = reader.path.parent/'windows.json'; windows.write_text(json.dumps(selections),encoding='utf-8')
        helper = self
        class Client:
            def __init__(self):
                self.saved = helper.selection(position_comparison=deepcopy(document)); self.calls=[]
            def load_ram_shadow_selection(self): return deepcopy(self.saved)
            def save_ram_shadow_selection(self,changes):
                self.calls.append((deepcopy(changes),threading.get_ident()))
                self.saved = helper.selection(**changes)
                return deepcopy(self.saved)
        client = Client()
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()):
            dialog = CandidateMonitorDialog(FakeClient(),client)
        self.addCleanup(dialog.deleteLater); self.addCleanup(dialog.stop)
        dialog._apply_ram_selection(client.saved)
        dialog._ram_position_db.setText(str(reader.path))
        dialog._ram_position_windows.setText(str(windows))
        dialog._ram_position_role.setCurrentIndex(dialog._ram_position_role.findData('TRAIN'))
        return dialog,client,reader,document,windows

    def test_worker_reads_once_per_run_and_saves_values_without_paths_or_reread(self):
        import json
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        from kiwoom_monitor.application.position_target import evaluate_position_action_comparison_document
        dialog,client,reader,document,windows = self.fixture()
        original = ResearchRepository.load_position_action_evidence
        threads=[]; sql=[]
        def read(repository,*args,**kwargs):
            threads.append(threading.get_ident())
            connect=repository._connect
            def traced():
                conn=connect(); conn.set_trace_callback(sql.append); return conn
            with patch.object(repository,'_connect',side_effect=traced): return original(repository,*args,**kwargs)
        before=reader.path.read_bytes()
        with patch.object(ResearchRepository,'load_position_action_evidence',read):
            self.assertTrue(dialog._start_ram_request('position_source'))
            self.assertTrue(self.pump_until(lambda:dialog._settings_worker is None,timeout=6))
        self.assertEqual(4,len(threads)); self.assertEqual(1,len(set(threads)))
        self.assertNotEqual(threading.get_ident(),threads[0])
        self.assertEqual(16,sum(s.startswith('SELECT') for s in sql))
        self.assertFalse(any(s.startswith('COMMIT') for s in sql))
        self.assertEqual(before,reader.path.read_bytes())
        prepared=deepcopy(dialog._ram_position_document)
        self.assertEqual({k:v for k,v in document.items() if k!='outcomes'},
                         {k:v for k,v in prepared.items() if k!='outcomes'})
        self.assertEqual(4,len(prepared['outcomes']))
        self.assertIn('4/4창',dialog._ram_position_input_status.text())
        self.assertEqual([],client.calls)
        with patch.object(ResearchRepository,'load_position_action_evidence',side_effect=AssertionError('save/reload read DB')):
            dialog._save_ram_selection(); self.assertTrue(self.pump_until(lambda:dialog._settings_worker is None))
            dialog._start_ram_request('selection'); self.assertTrue(self.pump_until(lambda:dialog._settings_worker is None))
            self.assertEqual(1,len(client.calls))
            body=client.calls[0][0]
            self.assertEqual(prepared,body['position_comparison'])
            self.assertNotIn(str(reader.path),json.dumps(body)); self.assertNotIn(str(windows),json.dumps(body))
            self.assertIn('원장 재조회 없음',dialog._ram_position_input_status.text())
            result=evaluate_position_action_comparison_document(prepared)
            dialog._render_position_comparison({'position_comparison':{'input_mode':'supplied_fixed_snapshot',
                'is_live_account':False,'result':result}},recorded=False)
            self.assertEqual(2,dialog._ram_position_table.rowCount())
            self.assertFalse(result['order_authorized'])
            dialog._clear_position_outcomes()
            self.assertEqual([],dialog._ram_position_document['outcomes'])
            self.assertEqual(document['requests'],dialog._ram_position_document['requests'])

    def test_invalid_mixed_or_oversized_selection_keeps_draft_without_native_read(self):
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        dialog,client,reader,document,windows=self.fixture()
        with patch.object(ResearchRepository,'load_position_action_evidence',side_effect=AssertionError('invalid selection read DB')):
            for raw in ('[]','x'*(256*1024+1)):
                windows.write_text(raw,encoding='utf-8')
                self.assertTrue(dialog._start_ram_request('position_source'))
                self.assertTrue(self.pump_until(lambda:dialog._settings_worker is None))
                self.assertEqual(document,dialog._ram_position_document)
                self.assertIn('실패',dialog._ram_position_input_status.text())
            dialog._ram_position_document={**deepcopy(document),'outcomes':[{'not_native':'preserve'}]}
            previous=deepcopy(dialog._ram_position_document)
            self.assertTrue(dialog._start_ram_request('position_source'))
            self.assertTrue(self.pump_until(lambda:dialog._settings_worker is None))
            self.assertEqual(previous,dialog._ram_position_document)
        self.assertEqual([],client.calls)

    def test_role_change_and_close_during_read_discard_old_result(self):
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        dialog,client,reader,document,windows=self.fixture()
        entered,release=threading.Event(),threading.Event()
        original=ResearchRepository.load_position_action_evidence
        def read(repository,*args,**kwargs):
            entered.set()
            if not release.wait(3): raise RuntimeError('test read wait timeout')
            return original(repository,*args,**kwargs)
        with patch.object(ResearchRepository,'load_position_action_evidence',read):
            try:
                dialog._start_ram_request('position_source'); self.assertTrue(entered.wait(2))
                self.assertFalse(dialog._start_ram_request('position_source'))
                dialog._ram_position_role.setCurrentIndex(dialog._ram_position_role.findData('VALIDATION'))
                release.set(); self.assertTrue(self.pump_until(lambda:dialog._settings_worker is None))
                self.assertEqual(document,dialog._ram_position_document)
                self.assertIn('적용하지 않았습니다',dialog._ram_position_input_status.text())
                entered.clear(); release.clear()
                dialog._ram_position_role.setCurrentIndex(dialog._ram_position_role.findData('TRAIN'))
                dialog._start_ram_request('position_source'); self.assertTrue(entered.wait(2))
                dialog.close(); release.set()
                self.assertTrue(self.pump_until(lambda:dialog._settings_worker is None))
                self.assertEqual(document,dialog._ram_position_document)
            finally: release.set()
        self.assertEqual([],client.calls)

    def test_stop_drains_local_read_and_never_applies_or_saves_late_values(self):
        import time
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        dialog,client,reader,document,windows=self.fixture()
        entered,release=threading.Event(),threading.Event()
        original=ResearchRepository.load_position_action_evidence
        def read(repository,*args,**kwargs):
            entered.set()
            if not release.wait(3): raise RuntimeError('test read wait timeout')
            return original(repository,*args,**kwargs)
        with patch.object(ResearchRepository,'load_position_action_evidence',read):
            try:
                dialog._start_ram_request('position_source'); self.assertTrue(entered.wait(2))
                worker=dialog._settings_worker
                def finish(): time.sleep(.05); release.set()
                thread=threading.Thread(target=finish); thread.start()
                dialog.stop(); thread.join(); self.app.processEvents()
                self.assertFalse(worker.isRunning())
                self.assertEqual(document,dialog._ram_position_document)
                self.assertFalse(dialog._start_ram_request('position_source'))
                self.assertFalse(dialog._ram_position_source_load.isEnabled())
                self.assertEqual([],client.calls)
            finally: release.set()


if __name__ == "__main__":
    unittest.main()
