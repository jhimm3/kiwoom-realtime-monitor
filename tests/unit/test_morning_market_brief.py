import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
import threading
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException
from kiwoom_monitor.application.morning_market_brief import (
    SCHEMA, DEFAULT_PROMPT, DEFAULT_SETTINGS, RUNS_COLLECTION, SETTINGS_COLLECTION,
    validate_output, context_from_run, render_prompt, output_schema,
    RESEARCH_SCHEMA, RESEARCH_PROMPT, render_research_prompt, research_feature_rows,
)
from kiwoom_monitor.central_server.morning_market_brief import MorningMarketBrief, create_morning_market_brief_router
from kiwoom_monitor.infrastructure.chatgpt_plan import GroundedResponse, search_evidence, validate_reasoning

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 15, 7, 55, tzinfo=KST)
URL = "https://example.test/market"


async def request(app, method, path, body=None, *, authorized=True):
    messages = []
    completed = asyncio.Event()
    received = False
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b"", "more_body": False}
        await completed.wait()
        return {"type": "http.disconnect"}
    async def send(message):
        messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False): completed.set()
    headers = [(b"content-type", b"application/json")]
    if authorized: headers.append((b"authorization",b"Bearer test"))
    await asyncio.wait_for(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "scheme": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": headers, "client": ("127.0.0.1",1), "server": ("test",80)}, receive, send), 5)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    return status,json.loads(b"".join(m.get("body",b"") for m in messages if m["type"] == "http.response.body"))


def result(at=NOW):
    units = {"US_SP500": "index", "US_NASDAQ": "index", "US_SOX": "index",
        "KR_KOSPI": "index", "KR_KOSDAQ": "index", "NASDAQ_FUTURES": "index",
        "USDKRW": "KRW_per_USD", "US10Y": "percent", "WTI": "USD_per_barrel"}
    signal = {"data_source": "TURNOVER", "target_ref": "market", "observation": "장중 거래대금 반응 재확인",
        "expected_change": "increase", "evidence_ids": []}
    return {"schema": SCHEMA, "source_type": "GLOBAL_CONTEXT", "source_mode": "ai_web_search",
        "as_of": at.isoformat(), "evidence_summary": "시장 기준과 조건",
        "calendar": {"kr_session_status": "open", "us_session_status": "open",
            "kr_last_trading_date": (at-timedelta(days=1)).date().isoformat(), "kr_next_trading_date": at.date().isoformat(),
            "us_last_trading_date": (at-timedelta(days=1)).date().isoformat(), "evidence_ids": ["e1"]},
        "market_assessment": {"state": "mixed", "basis": "initial_hypothesis", "reason": "상반된 근거", "evidence_ids": ["e1"]},
        "participation": {"state": "unknown", "observation_scope": "not_observed", "reason": "장중 관측 전", "evidence_ids": []},
        "concentration": {"state": "unknown", "observation_scope": "not_observed", "reason": "장중 관측 전", "evidence_ids": []},
        "market_data": [{"id": id_, "value": 100,
            "change_pct": 0.1, "unit": unit, "observed_at": (at-timedelta(hours=3)).isoformat(),
            "trading_date": (at-timedelta(days=1)).date().isoformat(),
            "price_status": "intraday", "timestamp_precision": "datetime", "availability_basis": "source_timestamp",
            "status": "confirmed", "instrument": id_, "market_venue": "합성 테스트 시장",
            "quote_type": "futures" if id_ == "NASDAQ_FUTURES" else "yield" if id_ == "US10Y" else "close",
            "contract_month": "2026-12" if id_ == "NASDAQ_FUTURES" else None,
            "comparison_basis": "합성 테스트 전일 종가", "evidence_ids": ["e1"]} for id_,unit in units.items()],
        "evidence": [{"evidence_id": "e1", "url": URL, "publisher": "합성 자료",
            "source_kind": "official", "verification": "original_read", "publisher_published_at": (at-timedelta(hours=2)).isoformat(),
            "source_modified_at": None, "published_date": at.date().isoformat(), "timestamp_precision": "datetime"}],
        "facts": [{"fact_id": "f1", "statement": "확인 사실", "fact_status": "CONFIRMED",
            "event_occurred_at": (at-timedelta(hours=3)).isoformat(), "observed_at": (at-timedelta(hours=2)).isoformat(), "evidence_ids": ["e1"]}],
        "events": [{"event_key": "evt1", "title": "재료", "fact_ids": ["f1"], "interpretation": "조건부 영향",
            "scope": "broad", "direction": "mixed", "importance": "medium", "importance_reason": "전달 경로 확인 필요",
            "novelty": "new", "novelty_basis": "새 발표", "persistence": {"horizon": "intraday",
                "mechanism": "후속 국내 반응에 의존", "assumptions": ["추가 정정 없음"], "review_due_at": None}, "evidence_ids": ["e1"]}],
        "themes_to_observe": [], "stock_contexts": [],
        "hypotheses": [{"hypothesis_key": "h1", "status": "UNCONFIRMED", "statement": "조건부 시장 반응",
            "scope": "broad", "direction": "mixed", "event_keys": ["evt1"], "theme_keys": [], "stock_keys": [],
            "confirming_signals": [signal], "contradicting_signals": [{**signal, "expected_change": "decrease"}],
            "invalidation_conditions": [{**signal, "data_source": "NEWS_REVISION", "observation": "원 발표 취소 확인", "expected_change": "cancel"}],
            "unknowns": ["실시간 테마 동조 미확인"]}],
        "unknowns": [], "changes_from_previous": []}


class Store:
    def __init__(self):
        self.rows = {}
        self.fail_claim = False
        self.claim_started = threading.Event()
        self.claim_release = None

    def load_documents(self, collection, owner="", limit=1000, *args):
        rows = [deepcopy(row) for (c,o,k),row in self.rows.items() if c == collection and (not owner or o == owner)]
        return list(reversed(rows))[:limit]

    def upsert_documents(self, collection, values):
        if collection == RUNS_COLLECTION and values[0]["document"]["status"] == "running":
            self.claim_started.set()
            if self.claim_release: self.claim_release.wait(5)
            if self.fail_claim: raise OSError("storage failed")
        for row in values:
            self.rows[(collection,row["owner"],row["key"])] = deepcopy(row)


class AI:
    def __init__(self):
        self.calls = []
        self.release = None
        self.fail = False

    async def research_market(self, prompt, provider, model, effort):
        self.calls.append((prompt,provider,model,effort))
        if self.release: await self.release.wait()
        if self.fail: raise ValueError("secret upstream body")
        cutoff=datetime.fromisoformat(json.loads(prompt.rsplit("실행 정보:\n",1)[1])["cutoff_at"])
        return GroundedResponse(json.dumps(result(cutoff)), {"input_tokens": 5, "output_tokens": 6, "total_tokens": 11},
            ({"url": URL, "title": "source"},), 1)


class ContractTests(unittest.TestCase):
    def inputs(self):
        return {"sources": [{"url": URL}], "data_gaps": [], "cutoff_at": NOW.isoformat()}

    def test_no_internal_news_or_quote_input_in_prompt(self):
        prompt = render_prompt(DEFAULT_SETTINGS, {"cutoff_at": NOW.isoformat(), "watchlist": "관심"})
        self.assertIn("직접 최신 원문", prompt)
        self.assertIn("뉴스엔진", DEFAULT_PROMPT)
        self.assertNotIn("30년", DEFAULT_PROMPT)
        self.assertIn("new/developing/recycled", DEFAULT_PROMPT)

    def test_missing_duplicate_nonfinite_or_future_market_value_rejected(self):
        mutations = [lambda v: v["market_data"].pop(),
            lambda v: v["market_data"].__setitem__(0, v["market_data"][1]),
            lambda v: v["market_data"][0].__setitem__("value", float("nan")),
            lambda v: v["market_data"][0].__setitem__("observed_at", (NOW+timedelta(seconds=1)).isoformat()),
            lambda v: v["market_data"][0].__setitem__("observed_at", "2026-10-15T04:00:00")]
        for mutation in mutations:
            value = result(); mutation(value)
            with self.assertRaises(ValueError): validate_output(json.dumps(value), self.inputs())

    def test_unreturned_sources_are_retained_with_verification_notes(self):
        for verification in ('original_read', 'secondary_only', 'unverified'):
            value = result()
            value['evidence'][0].update(url='https://example.test/not-returned', verification=verification)
            raw = json.dumps(value)
            checked = validate_output(raw, self.inputs())
            with self.subTest(verification=verification):
                self.assertEqual(value['evidence'], checked['evidence'])
                self.assertEqual(['e1'], checked['facts'][0]['evidence_ids'])
                self.assertEqual(value['market_data'][0]['value'], checked['market_data'][0]['value'])
                self.assertTrue(any(item['field'] == 'e1' and '대조 미확인' in item['reason'] for item in checked['unknowns']))
                self.assertEqual(value, json.loads(raw))

    def test_missing_evidence_times_are_retained_without_synthesizing_timestamps(self):
        for precision, field in (('datetime', 'publisher_published_at'), ('date_only', 'published_date')):
            value = result(); value['evidence'][0].update(timestamp_precision=precision)
            value['evidence'][0][field] = None
            checked = validate_output(json.dumps(value), self.inputs())
            with self.subTest(precision=precision):
                self.assertEqual(value['evidence'], checked['evidence'])
                self.assertTrue(any(item['field'] == 'e1' for item in checked['unknowns']))

    def test_research_without_confirmed_sources_retains_values_facts_and_interpretation(self):
        value = result()
        value['evidence'][0]['verification'] = 'unverified'
        for section in ('market_assessment', 'calendar', 'participation', 'concentration'):
            value[section]['evidence_ids'] = []
        value['participation']['state'] = 'broad'
        value['facts'][0]['evidence_ids'] = []
        value['market_data'][0].update(status='unavailable', observed_at=None, evidence_ids=[])
        checked = validate_output(json.dumps(value), self.inputs())
        self.assertEqual(value['market_data'][0], checked['market_data'][0])
        self.assertEqual(value['facts'], checked['facts'])
        self.assertEqual('broad', checked['participation']['state'])
        self.assertTrue(any(item['field'] == 'f1' for item in checked['unknowns']))

    def test_verification_notes_cannot_make_retained_research_exceed_a_note_count_gate(self):
        value = result()
        value['unknowns'] = [{'field':f'note{i}', 'reason':'원래 연구 메모', 'effect':'limits_interpretation'}
                             for i in range(60)]
        value['evidence'][0]['url'] = 'https://example.test/not-returned'
        checked = validate_output(json.dumps(value), self.inputs())
        self.assertGreater(len(checked['unknowns']), 60)
        self.assertEqual(value['evidence'], checked['evidence'])

    def test_invalid_references_and_future_comparison_times_still_rejected(self):
        for refs in (['e1', 'e1'], ['never_defined']):
            value = result(); value['facts'][0]['evidence_ids'] = refs
            with self.assertRaises(ValueError): validate_output(json.dumps(value), self.inputs())
        value = result(); value['evidence'][0]['publisher_published_at'] = (NOW+timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError): validate_output(json.dumps(value), self.inputs())

    def test_generation_schema_and_prompt_enforce_same_precision_pairs(self):
        rules = output_schema()['$defs']['_Evidence']['allOf']
        self.assertEqual([(r['if']['properties']['timestamp_precision']['const'],
                           next(iter(r['then']['properties']))) for r in rules],
                         [('datetime', 'publisher_published_at'), ('date_only', 'published_date')])
        prompt = render_prompt(DEFAULT_SETTINGS, {'cutoff_at': NOW.isoformat()})
        self.assertIn('지표 관측 시각의 정밀도가 아니다', prompt)
        self.assertIn('실제 반환한 출처 URL을 그대로', prompt)
        self.assertIn('확인 부족에 따른 자료 제외/null 처리보다 이 보존 규칙을 우선', prompt)

    def test_old_quote_and_missing_metric_are_visible_not_filled(self):
        value = result()
        value["market_data"][0]["observed_at"] = (NOW-timedelta(days=4)).isoformat()
        metric = value["market_data"][1]
        metric.update(status="unavailable", value=None, change_pct=None, observed_at=None, evidence_ids=[])
        checked = validate_output(json.dumps(value), self.inputs())
        self.assertEqual(checked["market_data"][0]["status"], "stale")
        self.assertTrue(any(item["field"] == "US_SP500" for item in checked["unknowns"]))
        self.assertTrue(any(item["field"] == "US_NASDAQ" for item in checked["unknowns"]))
        self.assertIsNone(checked["market_data"][1]["value"])

    def test_search_sources_are_tool_returned_and_only_safe_links(self):
        sources,calls = search_evidence({"output": [{"type": "web_search_call", "status": "completed",
            "action": {"sources": [{"url": URL}, {"url": "javascript:bad"}, {"url": "https://u:p@bad.test"}]}},
            {"type": "message", "content": [{"annotations": [{"type": "url_citation", "url": URL}]}]}]})
        self.assertEqual(calls, 1); self.assertEqual(len(sources), 1)
        actions = []
        self.assertEqual(((), 1), search_evidence({"output": [{"type": "web_search_call", "status": "in_progress"}]}, actions=actions))
        self.assertEqual('in_progress', actions[0]['status'])

    def test_reasoning_capability_is_model_specific(self):
        validate_reasoning("gpt-6.1-sol", "high")
        validate_reasoning("unknown", "")
        for model,effort in (("gpt-6.1-sol", "none"), ("unknown", "high")):
            with self.assertRaises(ValueError): validate_reasoning(model, effort)

    def test_opened_and_searched_page_urls_are_retained_without_rewriting(self):
        encoded = "https://example.test/quote/USDKRW%3ACUR/"
        opened = "https://example.test/quote/USDKRW:CUR/"
        actions = []
        sources, calls = search_evidence({"output": [
            {"type": "web_search_call", "status": "completed", "action": {
                "type": "search", "sources": [{"url": encoded}]}},
            {"type": "web_search_call", "status": "completed", "action": {
                "type": "open_page", "url": opened}},
            {"type": "web_search_call", "status": "completed", "action": {
                "type": "find_in_page", "url": URL}},
        ]}, actions=actions)
        self.assertEqual([encoded, opened, URL], [s['url'] for s in sources])
        self.assertEqual(3, calls)
        self.assertEqual([{'type': 'search', 'urls': [encoded]},
            {'type': 'open_page', 'urls': [opened]}, {'type': 'find_in_page', 'urls': [URL]}], actions)

    def test_action_receipt_excludes_private_fields_and_unobserved_urls(self):
        actions = []
        sources, calls = search_evidence({"output": [
            {"type": "web_search_call", "status": "completed", "action": {
                "type": "search", "query": "private query", "url": URL,
                "sources": [{"url": "javascript:bad"}, {"url": "https://u:p@bad.test"}]}},
            {"type": "message", "content": [{"text": '{"url":"https://invented.test"}'}]},
        ]}, actions=actions)
        self.assertEqual((), sources)
        self.assertEqual(1, calls)
        self.assertEqual([{'type': 'search', 'urls': []}], actions)
        sources, calls = search_evidence({"output": [{"type": "web_search_call", "status": "failed",
            "action": {"type": "open_page", "url": URL}}]}, actions=actions)
        self.assertEqual(((), 1), (sources, calls))
        self.assertEqual({'type':'open_page', 'urls':[], 'status':'failed'}, actions[-1])

    def test_research_stream_retains_open_page_url_without_os_specific_store(self):
        from contextlib import nullcontext
        from io import BytesIO
        from types import SimpleNamespace
        from kiwoom_monitor.infrastructure.chatgpt_plan import ChatGPTPlanClient
        final = {'status': 'completed', 'output': [
            {'type': 'web_search_call', 'status': 'completed',
             'action': {'type': 'open_page', 'url': URL}},
            {'type': 'message', 'status': 'completed', 'role': 'assistant',
             'content': [{'type': 'output_text', 'text': 'research output'}]},
        ], 'usage': {'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5}}
        posts = []
        def opener(request):
            posts.append(request)
            stream = BytesIO(('event: response.completed\ndata: ' +
                json.dumps({'type': 'response.completed', 'response': final}) + '\n\n').encode())
            stream.status = 200
            stream.headers = {'Content-Type': 'text/event-stream'}
            return stream
        auth = SimpleNamespace(session=lambda: nullcontext({'access_token': 'fake-token'}), opener=opener)
        client = ChatGPTPlanClient(auth)
        with patch.object(client, '_models', return_value=(('Test', 'test-model'),)):
            reply = client.research('test-model', 'research prompt')
        self.assertEqual(({'url': URL, 'title': ''},), reply.sources)
        self.assertEqual(({'type': 'open_page', 'urls': [URL]},), reply.web_actions)
        self.assertEqual('research output', reply.text)
        self.assertEqual(1, len(posts))

    def test_research_stream_without_source_records_retains_completed_output_and_usage(self):
        from contextlib import nullcontext
        from io import BytesIO
        from types import SimpleNamespace
        from kiwoom_monitor.infrastructure.chatgpt_plan import ChatGPTPlanClient
        final = {'status':'completed','output':[{'type':'message','status':'completed','role':'assistant',
            'content':[{'type':'output_text','text':'research output'}]}],
            'usage':{'input_tokens':2,'output_tokens':3,'total_tokens':5}}
        posts = []
        def opener(request):
            posts.append(request)
            stream = BytesIO(('event: response.completed\ndata: '+json.dumps({'type':'response.completed','response':final})+'\n\n').encode())
            stream.status = 200; stream.headers = {'Content-Type':'text/event-stream'}
            return stream
        client = ChatGPTPlanClient(SimpleNamespace(session=lambda:nullcontext({'access_token':'fake-token'}),opener=opener))
        with patch.object(client, '_models', return_value=(('Test','test-model'),)):
            reply = client.research('test-model','prompt')
        self.assertEqual('research output',reply.text)
        self.assertEqual(5,reply.usage['total_tokens'])
        self.assertEqual((),reply.sources)
        self.assertEqual(0,reply.search_calls)
        self.assertEqual(1,len(posts))

    def test_missing_observation_time_preserves_value_without_fabricating_time(self):
        value=result(); value['market_data'][0]['observed_at']=None
        checked=validate_output(json.dumps(value),self.inputs())
        metric=checked['market_data'][0]
        self.assertEqual(metric['status'],'confirmed')
        self.assertEqual(metric['value'],100); self.assertIsNone(metric['observed_at'])
        self.assertTrue(any('관측 시각' in item['reason'] for item in checked['unknowns']))

    def test_official_final_close_uses_verified_trading_date_without_invented_time(self):
        value=result(); metric=value['market_data'][0]
        metric.update(observed_at=None,price_status='final_close',timestamp_precision='trading_date',
                      availability_basis='official_completed_session')
        checked=validate_output(json.dumps(value),self.inputs())['market_data'][0]
        self.assertEqual(checked['status'],'confirmed')
        self.assertEqual(checked['value'],100)
        self.assertEqual(checked['trading_date'],'2026-10-14')
        self.assertIsNone(checked['observed_at'])

    def test_date_alone_or_unverified_close_retains_research_value(self):
        for change in ({'trading_date':None}, {'availability_basis':'unverified'}, {'price_status':'intraday'}):
            value=result(); metric=value['market_data'][0]
            metric.update(observed_at=None,price_status='final_close',timestamp_precision='trading_date',
                          availability_basis='official_completed_session')
            metric.update(change)
            checked=validate_output(json.dumps(value),self.inputs())['market_data'][0]
            self.assertEqual(checked['value'],100); self.assertIsNone(checked['observed_at'])
        value=result(); value['market_data'][0].update(observed_at=None,price_status='final_close',
            timestamp_precision='trading_date',availability_basis='official_completed_session')
        value['evidence'][0]['source_kind']='media'
        checked=validate_output(json.dumps(value),self.inputs())['market_data'][0]
        self.assertEqual(checked['value'],100)

    def test_holiday_latest_final_close_is_not_discarded_by_wall_clock_age(self):
        at=datetime(2026,10,19,7,55,tzinfo=KST); value=result(at)
        value['calendar'].update(us_last_trading_date='2026-10-16')
        metric=value['market_data'][0]
        metric.update(price_status='final_close',trading_date='2026-10-16',observed_at=None,
            timestamp_precision='trading_date',availability_basis='official_completed_session')
        inputs=self.inputs(); inputs['cutoff_at']=at.isoformat()
        checked=validate_output(json.dumps(value),inputs)['market_data'][0]
        self.assertEqual(checked['status'],'confirmed'); self.assertIsNone(checked['observed_at'])
        metric.update(trading_date='2026-10-15')
        checked=validate_output(json.dumps(value),inputs)['market_data'][0]
        self.assertEqual(checked['status'],'stale')

    def test_current_futures_value_is_preserved_without_fabricating_observation_time(self):
        value=result(); metric=next(m for m in value['market_data'] if m['id']=='NASDAQ_FUTURES')
        metric.update(observed_at=None,price_status='final_close',timestamp_precision='trading_date',
            availability_basis='official_completed_session')
        checked=validate_output(json.dumps(value),self.inputs())
        metric=next(m for m in checked['market_data'] if m['id']=='NASDAQ_FUTURES')
        self.assertEqual(metric['value'],100); self.assertIsNone(metric['observed_at'])

    def test_future_trading_date_and_imputed_precision_are_rejected(self):
        for change in ({'trading_date':'2026-10-16'}, {'trading_date':'2026-02-30'},
                       {'trading_date':'invalid'}):
            value=result(); value['market_data'][0].update(change)
            with self.assertRaises(ValueError): validate_output(json.dumps(value),self.inputs())

    def test_ap_alias_requires_exact_tool_returned_article_id(self):
        value=result(); identity='a'*32
        original='https://apnews.com/article/'+identity
        value['evidence'][0]['url']='https://apnews.com/article/market-report-'+identity
        inputs=self.inputs(); inputs['sources'].append({'url':original})
        checked=validate_output(json.dumps(value),inputs)
        self.assertEqual(checked['evidence'][0]['url'],original)
        value['evidence'][0]['url']='https://apnews.com/article/market-report-'+'b'*32
        checked=validate_output(json.dumps(value),inputs)
        self.assertTrue(any(item['field']=='e1' for item in checked['unknowns']))

    def test_scores_probabilities_confirmed_states_and_foreign_path_rejected(self):
        mutations = [lambda v: v.update(confidence=90),
            lambda v: v['events'][0].update(importance_score=10),
            lambda v: v['hypotheses'][0].update(probability=0.8),
            lambda v: v['hypotheses'][0].update(status='FLOW_CONFIRMED'),
            lambda v: v.update(source_type='STOCK_NEWS'),
            lambda v: v.update(source_mode='internal_news'),
            lambda v: v['market_assessment'].update(basis='confirmed'),
            lambda v: v.update(changes_from_previous=['없던 이전 판단']),
            lambda v: v['market_data'][0].update(value=True)]
        for mutation in mutations:
            value=result(); mutation(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_output(json.dumps(value), self.inputs())

    def test_source_cutoff_and_references_validated_independently_of_json_shape(self):
        mutations = [lambda v: v.update(as_of=(NOW+timedelta(seconds=1)).isoformat()),
            lambda v: v['evidence'][0].update(source_modified_at=(NOW+timedelta(seconds=1)).isoformat()),
            lambda v: v['facts'][0].update(event_occurred_at=(NOW+timedelta(seconds=1)).isoformat()),
            lambda v: v['facts'][0].update(evidence_ids=['missing']),
            lambda v: v['events'][0].update(fact_ids=['missing']),
            lambda v: v['hypotheses'][0]['confirming_signals'][0].update(target_ref='invented_stock'),
            lambda v: v['facts'][0].update(fact_id='e1')]
        for mutation in mutations:
            value=result(); mutation(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_output(json.dumps(value),self.inputs())
        text=json.dumps(result()).replace('"schema":', '"schema":"duplicate", "schema":', 1)
        with self.assertRaises(ValueError): validate_output(text,self.inputs())

    def test_theme_stock_relations_are_proposals_and_local_ids_are_not_native_ids(self):
        value=result()
        relation={'relation_source':'explicit_article','status':'proposed','reason':'본문 연결 근거',
            'event_keys':['evt1'],'evidence_ids':['e1'],'contradicting_evidence_ids':[]}
        value['themes_to_observe']=[{**relation,'theme_key':'t1','name':'합성 테마','kind':'market_theme'}]
        value['stock_contexts']=[{**relation,'stock_key':'s1','security_name':'합성 회사','market':'KR',
            'code':'123456','security_id':None,'relation_type':'direct','theme_keys':['t1']}]
        hypothesis=value['hypotheses'][0]
        hypothesis.update(scope='stock',theme_keys=['t1'],stock_keys=['s1'])
        hypothesis['confirming_signals'][0].update(target_ref='s1',data_source='TOP20')
        checked=validate_output(json.dumps(value),self.inputs())
        self.assertEqual(checked['stock_contexts'][0]['status'],'proposed')
        self.assertTrue(any(item['effect']=='blocks_target_mapping' for item in checked['unknowns']))
        for change in ({'security_id':'native-id'}, {'status':'reviewed'}, {'market':'US'}):
            bad=deepcopy(value); bad['stock_contexts'][0].update(change)
            with self.assertRaises(ValueError): validate_output(json.dumps(bad),self.inputs())

    def test_review_time_is_future_plan_not_future_fact_and_no_previous_state_is_invented(self):
        value=result(); value['events'][0]['persistence']['review_due_at']=(NOW+timedelta(hours=2)).isoformat()
        checked=validate_output(json.dumps(value),self.inputs())
        self.assertEqual(checked['hypotheses'][0]['status'],'UNCONFIRMED')
        self.assertEqual(checked['changes_from_previous'],[])
        value['evidence'][0].update(publisher_published_at=None,timestamp_precision='date_only')
        checked=validate_output(json.dumps(value),self.inputs())
        self.assertTrue(any(item['field']=='e1' for item in checked['unknowns']))

    def test_legacy_reports_are_kept_but_not_reinterpreted_as_current_contract(self):
        run={'status':'completed','result':{'schema':'morning-market-brief-v2','summary':'old'},'input_quality_ok':True}
        self.assertEqual(context_from_run(run,NOW)['reason'],'legacy_schema')
        self.assertEqual(run['result']['summary'],'old')

    def test_exported_schema_and_runtime_contract_match(self):
        from pathlib import Path
        exported=Path(__file__).resolve().parents[1]/'fixtures/morning-market-brief-output.schema.json'
        documentation=Path(__file__).resolve().parents[2]/'docs/workstreams/morning-market-brief-output.schema.json'
        # NAS ships the fixed test fixture; developer CI also checks the exported document.
        if documentation.is_file():
            self.assertEqual(documentation.read_bytes(),exported.read_bytes())
        self.assertEqual(json.loads(exported.read_text(encoding='utf-8')),output_schema())
        self.assertFalse(output_schema()['additionalProperties'])
        self.assertEqual(validate_output(json.dumps(result()),self.inputs())['source_type'],'GLOBAL_CONTEXT')


def research_result():
    value = result()
    value.update(schema=RESEARCH_SCHEMA, source_mode="provided_snapshot",
        market_regime={"state": "mixed", "reason": "합성 테스트: 상반된 전달 경로", "evidence_ids": ["e1"]})
    value["themes_to_observe"] = [
        {"theme_key": key, "name": name, "kind": "industry", "relation_source": "inferred",
         "status": "proposed", "reason": "합성 테스트 관계", "event_keys": ["evt1"],
         "evidence_ids": ["e1"], "contradicting_evidence_ids": []}
        for key, name in (("exporters", "합성 수출 업종"), ("importers", "합성 수입 업종"))]
    value["events"][0].update(category="fx", variable="USDKRW", change="increase", impacts=[
        {"target_ref": target, "direction": direction, "channel": channel, "horizon": "short",
         "thesis_change": "unknown", "reason": "합성 테스트의 가정이며 실제 기업 노출 아님", "evidence_ids": ["e1"]}
        for target, direction, channel in (("exporters", "positive", "fx_translation"),
                                           ("importers", "negative", "costs"))])
    value["measurements"] = [{"measurement_key": "m1", "indicator": "USDKRW", "target_ref": "market",
        "value": 1400.0, "unit": "KRW_per_USD", "comparison_value": 1390.0,
        "comparison_basis": "합성 테스트 전일값", "period": None,
        "observed_at": NOW.isoformat(), "value_basis": "reported", "fact_id": "f1", "evidence_ids": ["e1"]}]
    value["evidence_summary"] = "합성 테스트 예시. 실제 시세·기업 노출·검증된 투자 규칙이 아니다."
    return value


class ResearchContractTests(unittest.TestCase):
    def inputs(self, **changes):
        return {"sources": [{"url": URL}], "cutoff_at": NOW.isoformat(),
                "source_mode": "provided_snapshot", "search_calls": 0, **changes}

    def checked(self, value=None, **changes):
        return validate_output(json.dumps(value if value is not None else research_result()),
                               self.inputs(**changes), research=True)

    def test_exchange_rate_change_and_target_impact_are_separate_coded_rows(self):
        rows = research_feature_rows(self.checked())
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["change"] for r in rows}, {"increase"})
        self.assertEqual({r["variable"] for r in rows}, {"USDKRW"})
        self.assertEqual([(r["target_ref"], r["direction"], r["channel"]) for r in rows],
            [("exporters", "positive", "fx_translation"), ("importers", "negative", "costs")])
        self.assertEqual(rows[0]["measurements"][0]["value"], 1400.0)
        self.assertEqual(rows[0]["interpretation_status"], "MODEL_HYPOTHESIS")
        self.assertNotIn("order", rows[0])

    def test_changing_prose_never_changes_categorical_feature_values(self):
        original = self.checked()
        edited = deepcopy(original)
        edited["evidence_summary"] = "설명을 바꿔도 분류값은 그대로다"
        edited["events"][0].update(title="상반된 제목", interpretation="상반된 설명")
        for impact in edited["events"][0]["impacts"]:
            impact["reason"] = "부정 긍정이라는 단어를 바꿔도 파서는 문장을 해석하지 않는다"
        self.assertEqual(research_feature_rows(original), research_feature_rows(edited))

    def test_unverified_snapshot_keeps_values_and_refs_without_claiming_a_web_search(self):
        value = research_result()
        value["evidence"][0].update(verification="unverified", publisher_published_at=None,
            published_date=None, timestamp_precision="unknown")
        value["measurements"][0]["observed_at"] = None
        checked = self.checked(value, sources=[])
        rows = research_feature_rows(checked)
        self.assertEqual(rows[0]["measurements"][0]["value"], 1400.0)
        self.assertEqual(rows[0]["evidence_ids"], ["e1"])
        self.assertTrue(any(x["field"] == "m1" for x in checked["unknowns"]))
        self.assertFalse(any(x["field"] == "web_search" for x in checked["unknowns"]))

    def test_event_without_a_resolved_impact_target_is_not_discarded(self):
        value = research_result()
        value["events"][0]["impacts"] = []
        rows = research_feature_rows(self.checked(value))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["category"], rows[0]["change"]), ("fx", "increase"))
        self.assertIsNone(rows[0]["target_ref"])
        self.assertEqual(rows[0]["target_kind"], "unknown")

    def test_retained_research_time_descriptions_do_not_block_other_data(self):
        value = research_result()
        value["evidence"][0]["publisher_published_at"] = "2026-01-02 10:51; 페이지 시간대 미표기"
        value["facts"][0]["observed_at"] = "2026-01-02; 미국 거래일"
        value["measurements"][0]["observed_at"] = "2026-01-02"
        checked = self.checked(value)
        self.assertEqual(checked["evidence"][0]["publisher_published_at"], value["evidence"][0]["publisher_published_at"])
        self.assertEqual(checked["facts"][0]["observed_at"], value["facts"][0]["observed_at"])
        self.assertEqual(research_feature_rows(checked)[0]["measurements"][0]["value"], 1400.0)
        for field in ("e1.publisher_published_at", "f1.observed_at", "m1.observed_at"):
            self.assertTrue(any(x["field"] == field for x in checked["unknowns"]))

    def test_later_version_in_unzoned_time_text_is_preserved_and_flagged(self):
        value = research_result()
        value["evidence"][0]["source_modified_at"] = "2027-07-17 20:25; 본문 시간대 미표기"
        checked = self.checked(value)
        self.assertEqual(checked["evidence"][0]["source_modified_at"], value["evidence"][0]["source_modified_at"])
        self.assertTrue(any(x["field"] == "e1.source_modified_at" and "기준 날짜 이후" in x["reason"]
                            for x in checked["unknowns"]))

    def test_missing_codes_bad_target_nonfinite_future_or_broken_refs_rejected(self):
        mutations = [lambda v: v["events"][0].pop("category"),
            lambda v: v["events"][0].__setitem__("variable", "환율 상승이라는 문장"),
            lambda v: v["events"][0]["impacts"][0].__setitem__("target_ref", "undefined"),
            lambda v: v["measurements"][0].__setitem__("value", float("nan")),
            lambda v: v["measurements"][0].__setitem__("observed_at", (NOW+timedelta(seconds=1)).isoformat()),
            lambda v: v["measurements"][0].__setitem__("fact_id", "undefined")]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = research_result(); mutation(value)
                with self.assertRaises(ValueError): self.checked(value)

    def test_forecast_fundamental_and_long_term_thesis_remain_distinct(self):
        value = research_result()
        value["measurements"][0].update(indicator="FORWARD_PER", value=18.0, unit="multiple",
            value_basis="forecast", period="합성 다음 회계연도", comparison_value=None)
        value["events"][0].update(category="valuation", variable="FORWARD_PER", change="unknown")
        value["events"][0]["impacts"][0].update(horizon="long", thesis_change="unchanged")
        row = research_feature_rows(self.checked(value))[0]
        self.assertEqual((row["category"], row["horizon"], row["thesis_change"]),
                         ("valuation", "long", "unchanged"))
        self.assertEqual(row["measurements"][0]["value_basis"], "forecast")

    def test_prompt_schema_and_example_are_exported_from_the_same_app_contract(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        # Production NAS does not ship authoring documents.
        prompt = root / "docs/workstreams/news-market-research-prompt.txt"
        if prompt.exists():
            self.assertEqual(prompt.read_text(encoding="utf-8").rstrip(), RESEARCH_PROMPT)
            self.assertEqual(json.loads(prompt.with_name("news-market-research-output.schema.json").read_text(encoding="utf-8")),
                             output_schema(research=True))
            example = json.loads((root / "docs/examples/news-market-research-example.json").read_text(encoding="utf-8"))
            self.assertEqual(research_feature_rows(self.checked(example)), research_feature_rows(self.checked()))
        for schema in (output_schema(), output_schema(research=True)):
            for definition in schema["$defs"].values():
                self.assertTrue(set(definition.get("required", [])) <= set(definition.get("properties", {})))
        self.assertIn("title", output_schema()["$defs"]["_Event"]["properties"])
        prompt = render_research_prompt(self.inputs())
        self.assertIn('"const":"news-market-research-v1"', prompt)
        self.assertIn("provided_snapshot", prompt)
        execution = json.loads(prompt.split("실행 정보·입력 자료:\n", 1)[1])
        self.assertEqual(execution["cutoff_at"], NOW.isoformat())

    def test_research_contract_cannot_silently_replace_saved_v4_or_source_mode(self):
        with self.assertRaises(ValueError): validate_output(json.dumps(research_result()), self.inputs())
        with self.assertRaises(ValueError): self.checked(source_mode="ai_web_search")
        with self.assertRaises(ValueError): validate_output(json.dumps(result()), self.inputs(), research=True)
        with self.assertRaises(ValueError): render_research_prompt({"cutoff_at": NOW.isoformat()})


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store, self.ai = Store(), AI()
        self.now = NOW
        self.runtime = MorningMarketBrief(self.store, self.ai, clock=lambda: self.now)

    async def asyncTearDown(self):
        if self.ai.release: self.ai.release.set()
        await self.runtime.close()

    async def run_once(self):
        value = await self.runtime.request()
        if self.runtime._run_task: await self.runtime._run_task
        return value

    async def test_exact_kst_daily_once_and_restart_no_second_search(self):
        self.now -= timedelta(seconds=1)
        self.assertEqual((await self.runtime.request())["status"], "not_due")
        self.now += timedelta(seconds=1)
        self.assertEqual((await self.run_once())["status"], "running")
        self.assertEqual((await self.runtime.request())["status"], "already_attempted")
        restarted = MorningMarketBrief(self.store, self.ai, clock=lambda: self.now)
        self.assertEqual((await restarted.request())["status"], "already_attempted")
        self.assertEqual(len(self.ai.calls), 1)
        latest = await self.runtime.latest()
        self.assertEqual(latest["run"]["status"], "completed")
        self.assertTrue(latest["context"]["available"])
        self.assertEqual(latest['context']['consumer_binding'],'not_connected')
        self.assertEqual(latest['context']['hypothesis_status'],'UNCONFIRMED')
        self.assertFalse(latest['context']['auto_trade_eligible'])

    async def test_utc_clock_normalizes_to_kst_and_weekends_run(self):
        self.now = datetime(2026,10,17,7,55,tzinfo=KST).astimezone(ZoneInfo("UTC"))
        self.assertEqual((await self.run_once())["status"], "running")
        self.assertEqual(len(self.ai.calls), 1)
        self.assertEqual((await self.runtime.latest())["run"]["status"], "completed")

    async def test_no_late_morning_catchup_and_disabled_no_search(self):
        self.now = NOW+timedelta(minutes=15)
        self.assertEqual((await self.runtime.request())["status"], "not_due")
        self.now = NOW; self.runtime.settings["enabled"] = False
        self.assertEqual((await self.runtime.request())["status"], "not_due")
        self.assertEqual(self.ai.calls, [])

    async def test_no_confirmed_metric_or_tool_source_does_not_block_research_context(self):
        original = self.ai.research_market
        async def incomplete(*args):
            reply = await original(*args)
            value = json.loads(reply.text)
            for metric in value['market_data']:
                metric.update(status='unavailable', value=None, change_pct=None, observed_at=None)
            return GroundedResponse(json.dumps(value), reply.usage, (), 0)
        self.ai.research_market = incomplete
        await self.run_once()
        report = await self.runtime.latest()
        self.assertEqual('completed', report['run']['status'])
        self.assertFalse(report['run']['input_quality_ok'])
        self.assertTrue(report['context']['available'])
        self.assertEqual(9, len(report['context']['context']['market_data']))

    async def test_retained_failed_response_is_readable_without_rewriting_history_or_inference(self):
        await self.run_once()
        row = next(row for (collection, _, _), row in self.store.rows.items() if collection == RUNS_COLLECTION)
        run = row['document']
        run.update(status='failed', error='brief_evidence_source_unverified', input_quality_ok=False)
        run.pop('result'); run['available_at'] = None
        value = json.loads(run['raw_response'])
        value['evidence'][0]['url'] = 'https://example.test/not-returned'
        run['raw_response'] = json.dumps(value)
        before = deepcopy(self.store.rows)
        report = await self.runtime.latest()
        self.assertEqual('failed', report['run']['status'])
        self.assertTrue(report['run']['result_recovered_from_raw_response'])
        self.assertEqual(value['evidence'], report['run']['result']['evidence'])
        self.assertTrue(report['context']['available'])
        self.assertEqual(run['finished_at'], report['context']['available_at'])
        self.assertNotIn('result', report['history'][0])
        self.assertEqual(before, self.store.rows)
        self.assertEqual(1, len(self.ai.calls))

    async def test_pending_claim_survives_restart_without_silent_retry(self):
        self.store.upsert_documents(RUNS_COLLECTION, [{"owner": NOW.date().isoformat(), "key": "scheduled",
            "document": {"status": "running", "requested_at": NOW.timestamp()}}])
        self.assertEqual((await self.runtime.request())["status"], "already_attempted")
        self.assertEqual(self.ai.calls, [])

    async def test_failed_request_is_sanitized_and_not_retried(self):
        self.ai.fail = True
        await self.run_once()
        latest = await self.runtime.latest()
        self.assertEqual(latest["run"]["status"], "failed")
        self.assertNotIn("secret", json.dumps(latest))
        self.assertFalse(latest["context"]["available"])
        await self.runtime.request(); self.assertEqual(len(self.ai.calls), 1)

    async def test_missing_source_timestamp_is_saved_as_research_without_retry(self):
        original = self.ai.research_market
        async def malformed(*args):
            reply = await original(*args)
            value = json.loads(reply.text)
            value['evidence'][0]['publisher_published_at'] = None
            return GroundedResponse(json.dumps(value), reply.usage, reply.sources, reply.search_calls)
        self.ai.research_market = malformed
        await self.run_once()
        run = (await self.runtime.latest())['run']
        self.assertEqual(run['status'], 'completed')
        self.assertIsNone(run['result']['evidence'][0]['publisher_published_at'])
        self.assertTrue(any(item['field']=='e1' for item in run['result']['unknowns']))
        await self.runtime.request()
        self.assertEqual(len(self.ai.calls), 1)

    async def test_durable_claim_failure_never_starts_inference(self):
        self.store.fail_claim = True
        with self.assertRaises(OSError): await self.runtime.request()
        self.assertEqual(self.ai.calls, [])

    async def test_tool_action_receipt_survives_output_validation_failure(self):
        original = self.ai.research_market
        async def malformed(*args):
            reply = await original(*args)
            return GroundedResponse('{"invalid":true}', reply.usage, reply.sources,
                reply.search_calls, ({'type': 'open_page', 'urls': [URL]},))
        self.ai.research_market = malformed
        await self.run_once()
        run = (await self.runtime.latest())['run']
        self.assertEqual('failed', run['status'])
        self.assertEqual([{'type': 'open_page', 'urls': [URL]}], run['web_actions'])
        self.assertNotIn('result', run)
        self.assertEqual('already_attempted', (await self.runtime.request())['status'])
        self.assertEqual(1, len(self.ai.calls))

    async def test_unverified_output_is_stored_with_original_raw_response_and_no_retry(self):
        original = self.ai.research_market
        responses = []
        async def partial(*args):
            reply = await original(*args)
            value = json.loads(reply.text)
            value['evidence'].append({**value['evidence'][0], 'evidence_id': 'unread',
                'url': 'https://example.test/not-returned', 'verification': 'unverified'})
            value['facts'][0]['evidence_ids'].append('unread')
            responses.append(json.dumps(value))
            return GroundedResponse(responses[-1], reply.usage, reply.sources, reply.search_calls)
        settings = deepcopy(self.runtime.settings)
        self.ai.research_market = partial
        await self.run_once()
        latest = await self.runtime.latest()
        run = latest['run']
        self.assertEqual('completed', run['status'])
        self.assertTrue(latest['context']['available'])
        self.assertEqual(responses[0], run['raw_response'])
        self.assertEqual(['e1','unread'], run['result']['facts'][0]['evidence_ids'])
        self.assertTrue(any(item['field'] == 'unread' for item in run['result']['unknowns']))
        self.assertEqual(settings, self.runtime.settings)
        self.assertEqual('already_attempted', (await self.runtime.request())['status'])
        self.assertEqual(1, len(self.ai.calls))

    async def test_concurrent_manual_dedup_and_settings_snapshot(self):
        self.ai.release = asyncio.Event()
        await asyncio.gather(self.runtime.request(), self.runtime.request(manual=True))
        for _ in range(10):
            if self.ai.calls: break
            await asyncio.sleep(.01)
        self.assertEqual(len(self.ai.calls), 1)
        old_prompt = self.ai.calls[0][0]
        await self.runtime.update({"prompt": DEFAULT_PROMPT+"\n새 조건"}, 0)
        self.assertNotIn("새 조건", old_prompt)
        self.ai.release.set(); await self.runtime._run_task
        self.assertEqual((await self.runtime.latest())["run"]["settings_revision"], 0)

    async def test_cancellation_during_claim_still_launches_one_owned_run(self):
        self.store.claim_release = threading.Event()
        task = asyncio.create_task(self.runtime.request())
        await asyncio.to_thread(self.store.claim_started.wait, 2)
        task.cancel(); self.store.claim_release.set()
        with self.assertRaises(asyncio.CancelledError): await task
        await self.runtime._run_task
        self.assertEqual(len(self.ai.calls), 1)
        self.assertEqual((await self.runtime.latest())["run"]["status"], "completed")

    async def test_shutdown_drains_then_persists_completed(self):
        self.ai.release = asyncio.Event()
        await self.runtime.request()
        closing = asyncio.create_task(self.runtime.close())
        await asyncio.sleep(.01); self.assertFalse(closing.done())
        self.ai.release.set(); await closing
        self.assertEqual((await self.runtime.latest())["run"]["status"], "completed")

    async def test_expired_and_future_availability_never_context(self):
        await self.run_once()
        latest = await self.runtime.latest(); run = latest["run"]
        self.assertFalse(context_from_run(run, NOW-timedelta(seconds=1))["available"])
        self.assertFalse(context_from_run(run, NOW+timedelta(minutes=240))["available"])

    async def test_diagnostic_master_child_pause_prevents_new_work(self):
        with patch("kiwoom_monitor.central_server.morning_market_brief.is_paused", return_value=True):
            with self.assertRaises(ValueError): await self.runtime.request()
        self.assertEqual(self.ai.calls, [])

    async def test_authenticated_settings_cas_and_unknown_field_rejection(self):
        app = FastAPI()
        def auth(authorization: str = Header(default="")):
            if authorization != "Bearer test": raise HTTPException(401)
        app.include_router(create_morning_market_brief_router(self.runtime, auth))
        path = "/api/v1/market/morning-brief/settings"
        status,_ = await request(app,"GET",path,authorized=False); self.assertEqual(status,401)
        values = {k:v for k,v in DEFAULT_SETTINGS.items() if k not in {"revision","timezone","catchup_minutes"}}
        values["expected_revision"] = 0
        status,body = await request(app,"PUT",path,values); self.assertEqual(status,200); self.assertEqual(body["revision"],1)
        status,_ = await request(app,"PUT",path,values); self.assertEqual(status,409)
        values["expected_revision"] = 1; values["api_key"] = "forbidden"
        status,_ = await request(app,"PUT",path,values); self.assertEqual(status,422)
