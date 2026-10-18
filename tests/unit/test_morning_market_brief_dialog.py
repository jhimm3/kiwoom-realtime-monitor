import os
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtWidgets import QApplication
from qt_settings_test_support import dispose_dialogs,wait_until
from kiwoom_monitor.application.morning_market_brief import DEFAULT_SETTINGS,DEFAULT_PROMPT,validate_output
from kiwoom_monitor.presentation.morning_market_brief_dialog import MorningMarketBriefDialog
from tests.unit.test_morning_market_brief import result,NOW


class Client:
    def __init__(self): self.settings=deepcopy(DEFAULT_SETTINGS); self.saved=[]; self.runs=0
    def morning_brief_settings(self): return deepcopy(self.settings)
    def chatgpt_models(self): return (("Sol","gpt-6.1-sol"),("Other","unknown-model"))
    def morning_brief(self): return {"run":None,"context":{"available":False},"history":[]}
    def save_morning_brief_settings(self,values):
        self.saved.append(values)
        self.settings.update({k:v for k,v in values.items() if k!='expected_revision'})
        self.settings['revision']+=1
        return deepcopy(self.settings)
    def run_morning_brief(self): self.runs+=1; return {"status":"running"}


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.client=Client(); self.dialog=MorningMarketBriefDialog(self.client)
        self.dialog.show(); wait_until(lambda:self.dialog._worker is None)
    def tearDown(self): dispose_dialogs()

    def test_prompt_model_effort_schedule_save_and_reopen(self):
        self.assertEqual(self.dialog._time.time().toString('HH:mm'),'07:55')
        self.assertEqual(self.dialog._model.currentData(),'gpt-6.1-sol')
        self.assertEqual(self.dialog._effort.currentData(),'high')
        self.dialog._prompt.setPlainText(DEFAULT_PROMPT+'\n사용자 추가 조건')
        self.dialog.save(); wait_until(lambda:self.dialog._worker is None)
        self.assertEqual(self.client.saved[0]['expected_revision'],0)
        self.assertIn('사용자 추가 조건',self.client.settings['prompt'])
        self.assertNotIn('api_key',self.client.saved[0])
        self.dialog.load(); wait_until(lambda:self.dialog._worker is None)
        self.assertIn('사용자 추가 조건',self.dialog._prompt.toPlainText())
        self.assertEqual(self.dialog._revision,1)

    def test_unknown_model_exposes_only_service_default_effort(self):
        self.dialog._model.setCurrentIndex(self.dialog._model.findData('unknown-model'))
        self.assertEqual(self.dialog._effort.count(),1)
        self.assertEqual(self.dialog._effort.currentData(),'')

    def test_report_cutoff_converts_utc_to_kst_without_mutating_record(self):
        cutoff = '2026-10-14T22:55:00+00:00'
        run = {'status':'running','cutoff_at':cutoff,'provider':'chatgpt_plan',
               'model':'gpt-6.1-sol','reasoning_effort':'high'}
        self.dialog._show_report({'run':run,'context':{}})
        self.assertIn('2026-10-15 07:55:00 KST', self.dialog._report.toPlainText())
        self.assertNotIn('+00:00', self.dialog._report.toPlainText())
        self.assertEqual(run['cutoff_at'], cutoff)

    def test_evidence_failure_is_explained_without_raw_model_output(self):
        run = {'status':'failed','cutoff_at':NOW.isoformat(),'provider':'chatgpt_plan',
               'model':'gpt-6.1-sol','reasoning_effort':'high',
               'error':'brief_evidence_timestamp_missing','raw_response':'private raw output'}
        self.dialog._show_report({'run':run,'context':{}})
        text = self.dialog._report.toPlainText()
        self.assertIn('원문 게시 시각과 시간 정밀도가 맞지 않아', text)
        self.assertNotIn('private raw output', text)
        self.assertNotIn('brief_evidence_timestamp_missing', text)

    def test_run_returns_without_waiting_for_llm_completion(self):
        self.dialog.run_now(); wait_until(lambda:self.dialog._worker is None)
        self.assertEqual(self.client.runs,1)
        self.assertIn('시황 생성 중',self.dialog._status.text())

    def test_timer_refresh_keeps_reading_position_and_same_document(self):
        value = result()
        value['evidence_summary'] = '\n'.join(f'문단 {i}: 읽고 있는 시황 내용' for i in range(80))
        report = {'run':{'run_id':'first','status':'completed','result':value,'cutoff_at':NOW.isoformat(),
            'provider':'chatgpt_plan','model':'gpt-6.1-sol','reasoning_effort':'high',
            'expires_at':(NOW+timedelta(hours=4)).timestamp()},'context':{'reason':'ok'}}
        self.dialog._show_report(report)
        self.app.processEvents()
        bar = self.dialog._report.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum()//2)
        position = bar.value()
        with patch.object(self.client, 'morning_brief', return_value=report), \
                patch.object(self.dialog._report, 'setHtml', wraps=self.dialog._report.setHtml) as repaint:
            self.dialog._timer.timeout.emit()
            wait_until(lambda:self.dialog._worker is None)
            self.assertEqual(position, bar.value())
            repaint.assert_not_called()
        changed = deepcopy(report)
        changed['run']['result']['evidence_summary'] += '\n추가된 최신 내용'
        self.dialog._show_report(changed)
        self.app.processEvents()
        self.assertEqual(position, bar.value())
        self.assertIn('추가된 최신 내용', self.dialog._report.toPlainText())
        changed['run']['run_id'] = 'next'
        self.dialog._show_report(changed)
        self.app.processEvents()
        self.assertEqual(0, bar.value())

    def test_empty_report_clears_render_cache_before_same_run_returns(self):
        value = result()
        report = {'run':{'run_id':'first','status':'completed','result':value,'cutoff_at':NOW.isoformat(),
            'provider':'chatgpt_plan','model':'gpt-6.1-sol','reasoning_effort':'high',
            'expires_at':(NOW+timedelta(hours=4)).timestamp()},'context':{'reason':'ok'}}
        self.dialog._show_report(report)
        self.dialog._show_report({'run':None})
        self.dialog._show_report(report)
        self.assertIn('사실 자료 · 모델 분류', self.dialog._report.toPlainText())

    def test_report_renders_facts_sources_and_escapes_untrusted_markup(self):
        value=result(); value['evidence_summary']='<script>bad</script>'
        self.dialog._show_report({'run':{'status':'completed','result':value,'cutoff_at':NOW.isoformat(),
            'provider':'chatgpt_plan','model':'gpt-6.1-sol','reasoning_effort':'high',
            'expires_at':(NOW+timedelta(hours=4)).timestamp()},'context':{'reason':'ok'}})
        text=self.dialog._report.toPlainText()
        self.assertIn('US_SP500',text); self.assertIn('사실 자료 · 모델 분류',text)
        self.assertIn('<script>bad</script>',text)
        self.assertIn('https://example.test/market',self.dialog._report.toHtml())
        self.assertIn('판단엔진 연결 전',text)
        self.assertIn('무효화',text)

    def test_legacy_report_remains_readable_and_is_labelled_excluded(self):
        self.dialog._show_report({'run':{'status':'completed','result':{
            'schema':'morning-market-brief-v2','summary':'보존된 이전 요약','market_data':[],
            'events':[]},'cutoff_at':NOW.isoformat(),'provider':'chatgpt_plan','model':'gpt-6.1-sol',
            'reasoning_effort':'high','expires_at':(NOW+timedelta(hours=4)).timestamp()},
            'context':{'reason':'legacy_schema'}})
        self.assertIn('보존된 이전 요약',self.dialog._report.toPlainText())
        self.assertIn('현재 엔진 입력에서 제외',self.dialog._report.toPlainText())

    def test_unverified_source_is_retained_with_model_attribution_and_notes(self):
        import json
        from tests.unit.test_morning_market_brief import URL
        value = result()
        url = 'https://example.test/unread'
        value['evidence'].append({**value['evidence'][0], 'evidence_id':'unread',
            'url':url, 'verification':'unverified'})
        value = validate_output(json.dumps(value), {'cutoff_at':NOW.isoformat(), 'sources':[{'url':URL}]})
        self.dialog._show_report({'run':{'status':'completed','result':value,'cutoff_at':NOW.isoformat(),
            'provider':'chatgpt_plan','model':'gpt-6.1-sol','reasoning_effort':'high',
            'expires_at':(NOW+timedelta(hours=4)).timestamp()},'context':{'reason':'ok'}})
        text = self.dialog._report.toPlainText()
        self.assertIn('누락·미확인 정보', text)
        self.assertIn(url, text)
        self.assertIn('사실 자료 · 모델 분류', text)
        self.assertIn('모델이 반환한 연구 자료', text)
        self.assertIn('unread', text)

    def test_retained_failed_run_displays_research_values_without_hiding_original_status(self):
        value = result(); value['market_data'][0]['observed_at'] = None
        self.dialog._show_report({'run':{'status':'failed','error':'brief_evidence_source_unverified',
            'result':value,'result_recovered_from_raw_response':True,'cutoff_at':NOW.isoformat(),
            'provider':'chatgpt_plan','model':'gpt-6.1-sol','reasoning_effort':'high',
            'expires_at':(NOW+timedelta(hours=4)).timestamp()},'context':{'reason':'ok'}})
        text = self.dialog._report.toPlainText()
        self.assertIn('failed', text)
        self.assertIn('US_SP500', text)
        self.assertIn('보존된 응답을 연구 자료로 조회', text)
        self.assertIn('100', text)
        self.assertEqual('보존 응답 연구 자료 조회', self.dialog._status.text())

    def test_retained_unverified_uri_is_plain_text_when_not_a_safe_web_link(self):
        value = result(); value['evidence'][0]['url'] = 'javascript:alert(1)'
        self.dialog._show_report({'run':{'status':'completed','result':value,'cutoff_at':NOW.isoformat(),
            'provider':'chatgpt_plan','model':'gpt-6.1-sol','reasoning_effort':'high',
            'expires_at':(NOW+timedelta(hours=4)).timestamp()},'context':{'reason':'ok'}})
        self.assertIn('javascript:alert(1)', self.dialog._report.toPlainText())
        self.assertNotIn('href="javascript:', self.dialog._report.toHtml())

    def test_date_only_final_close_displays_trading_date_without_synthetic_clock(self):
        value=result(); value['market_data'][0].update(observed_at=None,price_status='final_close',
            timestamp_precision='trading_date',availability_basis='official_completed_session')
        self.dialog._show_report({'run':{'status':'completed','result':value,'cutoff_at':NOW.isoformat(),
            'provider':'chatgpt_plan','model':'gpt-6.1-sol','reasoning_effort':'high',
            'expires_at':(NOW+timedelta(hours=4)).timestamp()},'context':{'reason':'ok'}})
        text=self.dialog._report.toPlainText()
        self.assertIn('2026-10-14 · 모델 표기: 확정 종가(거래일 기준)',text)
        self.assertNotIn('2026-10-14T00:00',text)
