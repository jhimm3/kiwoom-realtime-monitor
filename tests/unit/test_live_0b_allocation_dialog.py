import os
import unittest
from copy import deepcopy
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from kiwoom_monitor.presentation.candidate_monitor_dialog import CandidateMonitorDialog
from tests.unit.test_candidate_alerts import MemorySettings,FakeClient


class LiveAllocationDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def dialog(self):
        with patch('kiwoom_monitor.presentation.candidate_monitor_dialog.QSettings',return_value=MemorySettings()), \
             patch('kiwoom_monitor.presentation.candidate_monitor_dialog.CandidatePollWorker.start'):
            dialog=CandidateMonitorDialog(FakeClient())
        self.addCleanup(dialog.deleteLater);self.addCleanup(dialog.stop)
        return dialog

    def receipt(self):
        return {'version':'dynamic-position-allocation-ram/v1','input_resolution':'0B','order_authorized':False,
            'decision_time':'2026-10-16T09:20:00+09:00','valid_until':'2099-10-16T09:20:03+09:00',
            'quality':{'status':'BLOCKED','reasons':['allocation_ev_unavailable']},
            'facts':[{'symbol':'005930','reference_price_won':1000,'recent_turnover_won':0,
                'observed_close_return_rms_bps':None,'turnover_window_seconds':20,'input_refs':['ram-0B:source:1:2']}]}

    def test_document_baseline_price_facts_are_visible_without_an_enter_candidate_or_alarm(self):
        dialog=self.dialog()
        from tests.unit.test_leader_breakout import DocumentLeaderBreakoutTests,AT
        from kiwoom_monitor.application.leader_breakout import document_breakout_reference,document_breakout_observation
        p=DocumentLeaderBreakoutTests().draft();rows=DocumentLeaderBreakoutTests().native_rows()
        ref=document_breakout_reference(p,rows,symbol='005930',decision_time=AT,mode='0B')
        observed=document_breakout_observation(p,ref,price=1010,observed_at=AT,available_at=AT,
            input_ref='actual-test-ref',source='fixture',mode='0B')
        with patch('PySide6.QtWidgets.QApplication.beep') as beep:
            dialog._apply_page({'quality':{'status':'BLOCKED','document_baseline_observations':[observed]},
                'events':[],'initial_sync':True})
            self.assertEqual(1,dialog._baseline_table.rowCount())
            self.assertEqual('1000',dialog._baseline_table.item(0,2).text())
            self.assertIn('거래 미승인',dialog._baseline_table.item(0,4).text())
            self.assertEqual(0,dialog._table.rowCount())
            beep.assert_not_called()
        self.assertTrue(dialog._quality.wordWrap())
        self.assertNotIn('unconfigured:',dialog._quality.text())
        dialog.resize(1200,950);dialog.show();self.app.processEvents()
        self.assertLessEqual(dialog.width(),1300)
        import json
        draft=json.loads(dialog._leader_policy_editor.toPlainText())
        self.assertIsNone(draft['reference_level']['window_seconds'])

    def test_received_0b_and_unconfigured_facts_show_without_default_order_policy(self):
        dialog=self.dialog();value=self.receipt();original=deepcopy(value)
        dialog._render_position_allocation({'position_allocation':value},recorded=False)
        self.assertIn('개별 0B 체결',dialog._ram_allocation_status.text())
        self.assertIn('계산 보류',dialog._ram_allocation_status.text())
        self.assertIn('주문 승인 없음',dialog._ram_allocation_status.text())
        self.assertEqual('0',dialog._ram_allocation_table.item(0,2).text())
        self.assertEqual('미확인',dialog._ram_allocation_table.item(0,3).text())
        self.assertEqual(original,value)
        self.assertIn('분 조회',dialog._ram_source_window.suffix())

    def test_historical_second_evidence_is_not_relabelled_as_live_0b(self):
        dialog=self.dialog();value=self.receipt();value.pop('input_resolution')
        dialog._render_position_allocation({'position_allocation':value},recorded=False)
        self.assertIn('구형 집계 입력',dialog._ram_allocation_status.text())
        self.assertEqual(0,dialog._ram_allocation_table.rowCount())
        dialog._render_position_allocation({'position_allocation':value},recorded=True)
        self.assertIn('과거 초봉 집계',dialog._ram_allocation_status.text())
        self.assertIn('최근 저장',dialog._ram_allocation_status.text())

    def test_minute_comparison_is_visible_without_claiming_live_rule_or_order_approval(self):
        from PySide6.QtWidgets import QLabel
        dialog=self.dialog();value=self.receipt()
        value['r4_action_comparison']={'goals':[{'symbol':'005930','model_support':{'conditional_shadow':{
            'status':'UNAVAILABLE','result':None,'reasons':['current_closed_minute_quote_cut_mismatch']}}}]}
        original=deepcopy(value)
        dialog._render_position_allocation({'position_allocation':value},recorded=False)
        tip=dialog._ram_allocation_status.toolTip()
        self.assertIn('기존 분봉의 조건부 모의 비교',tip)
        self.assertIn('실시간 적용 방법은 미정',tip)
        self.assertIn('분봉 모의 비교 조건:',tip)
        self.assertIn('주문 승인 없음',tip)
        notes='\n'.join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn('필요한 기존 분봉 계산도 함께 유지',notes)
        self.assertIn('일봉·5분봉·1분봉',notes)
        self.assertEqual(original,value)
        dialog._render_position_allocation({},recorded=False)
        self.assertEqual('',dialog._ram_allocation_status.toolTip())

    def test_rms_observation_basis_and_sample_count_are_visible(self):
        dialog=self.dialog();value=self.receipt()
        value['facts'][0].update(observed_close_return_rms_bps=100,volatility_sample_count=3,
            volatility_basis='last_received_price_per_observed_second/v1')
        dialog._render_position_allocation({'position_allocation':value},recorded=False)
        self.assertEqual('100',dialog._ram_allocation_table.item(0,3).text())
        self.assertIn('관측초 가격 RMS',dialog._ram_allocation_table.horizontalHeaderItem(3).text())
        tooltip=dialog._ram_allocation_table.item(0,3).toolTip()
        self.assertIn('마지막 수신가격',tooltip)
        self.assertIn('표본 3개',tooltip)
        self.assertIn('무체결 초',tooltip)

    def test_h2_price_basis_is_visible_only_when_receipt_explicitly_records_it(self):
        from tests.unit.test_candidate_alerts import RamShadowDialogTests
        dialog=self.dialog();page=RamShadowDialogTests().page()
        context=next(iter(page['live']['contexts'].values()))
        context['scope']['input_resolution']='0B'
        context['leadership']['facts']['price_return_basis']='first_observed_second_last_received_price/v1'
        original=deepcopy(page)
        dialog._apply_ram_page(page)
        self.assertIn('첫 관측 초의 마지막 수신가격',dialog._ram_table.item(0,3).toolTip())
        self.assertIn('모든 수신 체결 반영',dialog._ram_table.item(0,3).toolTip())
        self.assertEqual(original,page)
        context['leadership']['facts'].pop('price_return_basis')
        dialog._apply_ram_page(page)
        self.assertNotIn('첫 관측 초의 마지막 수신가격',dialog._ram_table.item(0,3).toolTip())

    def test_expired_or_order_authorized_results_cannot_look_current(self):
        dialog=self.dialog();value=self.receipt();value['valid_until']='2000-01-01T00:00:00+09:00'
        dialog._render_position_allocation({'position_allocation':value},recorded=False)
        self.assertIn('자료 만료',dialog._ram_allocation_status.text())
        value['order_authorized']=True
        dialog._render_position_allocation({'position_allocation':value},recorded=False)
        self.assertIn('확인 실패',dialog._ram_allocation_status.text())
        self.assertEqual(0,dialog._ram_allocation_table.rowCount())
