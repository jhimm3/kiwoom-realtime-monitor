from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from kiwoom_monitor.central_server.realtime_collector import CentralRealtimeCollector
from kiwoom_monitor.central_server.realtime_hub import RealtimeHub
from kiwoom_monitor.central_server.database import SQLiteQueryStore
from kiwoom_monitor.central_server.market_theme_shadow_inputs import (
    market_theme_shadow_inputs_from_ram, market_price_trade_inputs_from_ram,
    _position_study_observation_from_ram,
)
from kiwoom_monitor.application.theme_leadership import (
    ThemeLeadershipParameters, ThemeMembership, minute_trade_points_from_rows,
)
from kiwoom_monitor.application.market_theme_shadow import evaluate_market_theme_shadow
from kiwoom_monitor.application.market_research_features import compute_price_trade_candidates
from kiwoom_monitor.application.research_factors import compute_minute_theme_leadership
from tests.unit.test_market_research_features import shadow_inputs

KST = ZoneInfo('Asia/Seoul')


def minute_row(code='005930', minute='09:00', **changes):
    return dict(trading_date='2026-09-14', minute=minute, code=code, market='KRX',
        open=100, high=110, low=99, close=105, volume=100, trade_value_million_won=1,
        completeness='complete', value_kind='estimated', source='kiwoom-ka10080;trade_value=ohlcv_estimate',
        available_at=datetime(2026,9,14,18,tzinfo=KST).timestamp(), **changes)


class Live0BTransitionTests(unittest.TestCase):
    def fixture(self, store=None):
        now = [datetime(2026,9,14,9,tzinfo=KST)]
        collector = CentralRealtimeCollector(lambda: 'no-network', 'real', RealtimeHub(), lambda: now[0], store)
        collector._apply_trade_source_approval({('005930','KRX'),('000660','KRX')},now[0],reset_all=True)
        collector.set_ram_observation_retention(60)
        return now, collector

    def tick(self, now, collector, second, *, code='005930', price=100, cumulative=1, microsecond=0):
        now[0] = now[0].replace(second=second, microsecond=microsecond)
        collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':code,
            'values':{'10':str(price),'13':str(cumulative*10),'14':str(cumulative),
                      '15':'10','20':now[0].strftime('%H%M%S')}}]})

    def test_individual_same_second_receipts_units_duplicates_and_no_second_state(self):
        now,c = self.fixture()
        self.tick(now,c,1,cumulative=10)
        self.tick(now,c,1,cumulative=11,price=101,microsecond=100)
        self.tick(now,c,1,cumulative=12,price=102,microsecond=200)
        self.tick(now,c,1,cumulative=12,price=102,microsecond=300)
        snapshot = c.ram_observation_snapshot(('005930',),seconds=60)
        self.assertEqual('0B',snapshot['input_resolution'])
        self.assertEqual(3,len(snapshot['rows']))
        self.assertEqual([1000,1010,1020],[r['trade_value_won'] for r in snapshot['rows']])
        self.assertEqual([None,1_000_000,1_000_000],[r['cumulative_trade_value_delta_won'] for r in snapshot['rows']])
        self.assertEqual([100,101,102],[r['price_won'] for r in snapshot['rows']])
        self.assertFalse(hasattr(c,'_second_trades'))
        self.assertFalse(hasattr(c,'_pending_second_retries'))
        self.assertFalse(any('trade_second' in r for r in snapshot['rows']))

    def test_missing_reset_gap_approval_and_lifetime_do_not_fabricate_turnover(self):
        now,c = self.fixture()
        self.tick(now,c,1,cumulative=10)
        self.tick(now,c,2,cumulative=9)
        self.assertIsNone(c._ram_trades[-1]['cumulative_trade_value_delta_won'])
        self.assertEqual(1000,c._ram_trades[-1]['trade_value_won'])
        self.assertEqual(now[0],c._ram_observation_gap_at)
        c._mark_capture_gap()
        self.assertFalse(c._ram_trades)
        self.tick(now,c,3,cumulative=12)
        self.assertIsNone(c._ram_trades[-1]['cumulative_trade_value_delta_won'])
        self.assertEqual(1000,c._ram_trades[-1]['trade_value_won'])
        c._apply_trade_source_approval(set(),now[0],reset_all=True)
        self.tick(now,c,4,cumulative=13)
        self.assertFalse(c._ram_trades)
        c.set_ram_observation_retention(0)
        self.assertFalse(c._ram_trade_baselines)

    def test_capacity_drop_marks_gap_and_retains_bounded_original_ticks(self):
        now,c = self.fixture()
        for i in range(10003):
            self.tick(now,c,1,cumulative=i+1,microsecond=i)
        self.assertEqual(10000,len(c._ram_trades))
        self.assertEqual(now[0],c._ram_observation_gap_at)
        self.assertEqual(4,c._ram_trades[0]['sequence'])

    def test_h2_and_market_preserve_all_same_second_0b_receipts(self):
        now,c = self.fixture()
        for second in range(40):
            for index,code in enumerate(('005930','000660')):
                self.tick(now,c,second,code=code,price=100+second,cumulative=second*2+1,microsecond=index*200)
                self.tick(now,c,second,code=code,price=101+second,cumulative=second*2+2,microsecond=index*200+100)
        snapshot=c.ram_observation_snapshot(('005930','000660'),seconds=20)
        context=market_theme_shadow_inputs_from_ram(snapshot,shadow_inputs(),window_seconds=10)
        self.assertEqual((),context.second_points)
        self.assertEqual('0B',context.input_resolution)
        self.assertTrue(context.observation_points)
        self.assertEqual('COMPLETE',context.continuity_status)
        result=evaluate_market_theme_shadow(context)
        self.assertEqual('0B',result['scope']['input_resolution'])
        self.assertEqual('COMPLETE',result['leadership']['quality']['status'])
        market=market_price_trade_inputs_from_ram(snapshot,shadow_inputs(),window_seconds=10,max_data_age_seconds=3)
        price=compute_price_trade_candidates(market,as_of=now[0])
        self.assertEqual(len(market.points),sum(m['previous_observation_count']+m['current_observation_count'] for m in price['metrics']))

    def test_h2_matches_historical_price_basis_without_losing_receipts_peak_or_turnover(self):
        from kiwoom_monitor.central_server.minute_bars import SecondTradeAccumulator
        from kiwoom_monitor.infrastructure.kiwoom_rest.realtime import TradeTick
        from kiwoom_monitor.application.theme_leadership import evaluate_theme_leadership, second_trade_points_from_rows
        for first_prices in ((90,100),(150,100),(100,100,100),(100,)):
            with self.subTest(first_prices=first_prices):
                now,c=self.fixture()
                self.addCleanup(lambda collector=c: asyncio.run(collector.close()))
                historical=SecondTradeAccumulator();historical.set_ram_retention(60)
                cumulative={'005930':0,'000660':0};sequence=0
                for second,prices in ((1,((100,),(100,))),(9,((100,),(100,))),
                        (12,(first_prices,(100,)*len(first_prices))),(19,((100,),(105,)))):
                    for code,values in zip(cumulative,prices):
                        for price in values:
                            sequence+=1;volume=6300//price;cumulative[code]+=volume
                            now[0]=now[0].replace(second=second,microsecond=sequence*1000)
                            c._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':code,
                                'values':{'10':str(price),'13':str(cumulative[code]),'15':str(volume),'20':now[0].strftime('%H%M%S')}}]})
                            historical.add(TradeTick(code,price,cumulative[code],None,volume,None,
                                now[0].strftime('%H%M%S'),market='KRX'),now[0],now[0].timestamp())
                now[0]=now[0].replace(second=20,microsecond=0)
                snapshot=c.ram_observation_snapshot(tuple(cumulative),seconds=20)
                context=market_theme_shadow_inputs_from_ram(snapshot,shadow_inputs(),window_seconds=10)
                live=evaluate_market_theme_shadow(context)
                oracle=evaluate_theme_leadership(context.theme_membership,
                    second_trade_points_from_rows(historical.observation_snapshot(tuple(cumulative),at=now[0],seconds=20)),
                    as_of=now[0].isoformat(),venue='KRX',continuity_status='COMPLETE',parameters=ThemeLeadershipParameters())
                self.assertEqual(tuple(asdict(member) for member in oracle.members),live['leadership']['facts']['members'])
                self.assertEqual(oracle.raw_leader_id,live['leadership']['inferences']['raw_leader_id'])
                self.assertEqual(oracle.displayed_leader_id,live['leadership']['inferences']['displayed_leader_id'])
                self.assertEqual('first_observed_second_last_received_price/v1',live['leadership']['facts']['price_return_basis'])
                self.assertEqual(sequence,len(context.observation_points))
                self.assertEqual((),context.second_points)
                self.assertFalse(live['order_authorized'])
                self.assertFalse(hasattr(c,'_second_trades'))

    def test_flush_close_preserves_old_second_rows_and_never_calls_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SQLiteQueryStore(Path(directory)/'db.sqlite3');store.initialize()
            original=dict(trading_date='2026-09-14',trade_second='08:59:00',code='005930',market='KRX',
                open=90,high=91,low=89,close=90,volume=5,trade_value_won=450,trade_count=2,available_at=1.0)
            store.save_second_trade_bars([original])
            now,c=self.fixture(store)
            with patch.object(store,'save_second_trade_bars',side_effect=AssertionError('new second write')):
                self.tick(now,c,1,cumulative=1)
                self.tick(now,c,2,cumulative=2)
                asyncio.run(c._flush_snapshots(periodic=True))
                asyncio.run(c.close())
            with closing(sqlite3.connect(Path(directory)/'db.sqlite3')) as db:
                self.assertEqual([(5,450,2)],db.execute('SELECT volume,trade_value_won,trade_count FROM central_second_trade_bars').fetchall())
            self.assertEqual(0,c.operational_status()['persistence']['pending_by_kind']['second_bars']['records'])
            store.close()

    def test_minute_research_uses_final_completed_values_without_rewriting_receipts(self):
        row=minute_row(); original=dict(row)
        points=minute_trade_points_from_rows([row],as_of='2026-09-14T09:01:00+09:00')
        self.assertEqual(1,len(points))
        self.assertEqual('2026-09-14T09:01:00+09:00',points[0].available_at)
        self.assertEqual(1_000_000,points[0].trade_value_won)
        self.assertTrue(points[0].input_ref.startswith('minute-trade:'))
        self.assertEqual(original,row)
        self.assertFalse(minute_trade_points_from_rows([row],as_of='2026-09-14T09:00:59+09:00'))
        self.assertFalse(minute_trade_points_from_rows([{**row,'completeness':'in_progress'}],as_of='2026-09-14T09:02:00+09:00'))

    def test_minute_factor_requires_explicit_minute_policy_and_keeps_legacy_separate(self):
        membership=ThemeMembership('t','반도체',('005930','000660'),'2026-09-14T08:00:00+09:00')
        args=dict(as_of='2026-09-14T09:02:00+09:00',venue='KRX',continuity_status='COMPLETE')
        with self.assertRaisesRegex(ValueError,'whole-minute'):
            compute_minute_theme_leadership(membership,[],parameters=ThemeLeadershipParameters(),**args)
        rows=[minute_row(code,minute) for code in membership.member_codes for minute in ('09:00','09:01')]
        result=compute_minute_theme_leadership(membership,rows,
            parameters=ThemeLeadershipParameters(window_seconds=60,max_data_age_seconds=90),**args)
        self.assertEqual('v2',result.version)
        self.assertEqual('1m',result.value['input_resolution'])
        self.assertEqual('valid',result.status)

    def test_0b_resolution_alone_does_not_block_separate_minute_study_validation(self):
        result=_position_study_observation_from_ram(None,None,None,{'input_resolution':'0B'})
        self.assertEqual((None,['current_account_study_basis_unavailable'],[]),result)

    def test_short_reader_does_not_discard_other_consumers_long_window(self):
        now,c=self.fixture()
        self.tick(now,c,1,cumulative=10)
        self.tick(now,c,20,cumulative=11)
        self.assertEqual(1,len(c.ram_observation_snapshot(('005930',),seconds=3)['rows']))
        self.assertEqual(2,len(c.ram_observation_snapshot(('005930',),seconds=60)['rows']))

    def test_known_received_price_volume_is_usable_without_cumulative_money(self):
        now,c=self.fixture()
        for second in range(40):
            now[0]=now[0].replace(second=second)
            c._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':code,
                'values':{'10':str(100+second),'15':'10','20':now[0].strftime('%H%M%S')}}
                for code in ('005930','000660')]})
        snapshot=c.ram_observation_snapshot(('005930','000660'),seconds=20)
        self.assertTrue(all(row['trade_value_won']==row['price_won']*row['volume'] for row in snapshot['rows']))
        result=evaluate_market_theme_shadow(market_theme_shadow_inputs_from_ram(snapshot,shadow_inputs(),window_seconds=10))
        self.assertEqual('COMPLETE',result['leadership']['quality']['status'])
        now[0]=now[0].replace(second=40)
        c._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':'005930','values':{'10':'140','20':'090040'}}]})
        self.assertIsNone(c._ram_trades[-1]['volume'])
        self.assertIsNone(c._ram_trades[-1]['trade_value_won'])
        self.assertEqual(140,c._ram_trades[-1]['price_won'])

    def test_read_minute_source_hashes_never_queries_second_archive_or_writes(self):
        from tests.unit.test_entry_thesis import StockThesisSourceCheckTests
        from kiwoom_monitor.application.theme_leadership import evaluate_theme_leadership,build_entry_thesis,theme_memberships_from_snapshot
        from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import read_minute_stock_thesis_source_check
        helper=StockThesisSourceCheckTests();helper.setUp();self.addCleanup(helper.doCleanups)
        rows=[minute_row(code,minute) for code in ('005930','000660') for minute in ('09:00','09:01')]
        cut='2026-09-14T09:02:00+09:00'
        membership=theme_memberships_from_snapshot(helper.snapshot)[0]
        leadership=evaluate_theme_leadership(membership,minute_trade_points_from_rows(rows,as_of=cut),
            as_of=cut,venue='KRX',continuity_status='COMPLETE',
            parameters=ThemeLeadershipParameters(window_seconds=60,max_data_age_seconds=90))
        thesis=build_entry_thesis(run_id='minutes',symbol='005930',leadership=leadership,
            factor_refs=(),hypothesis_refs=(),created_at=cut,available_at=cut)
        repo=ResearchRepository(helper.path.parent/'minute-research.sqlite3')
        repo.start_run('minutes',{'mode':'fixture'},{'dataset_id':'fixture'})
        repo.append_theme_leadership(leadership);repo.append_entry_thesis(thesis)
        saved=repo.load_entry_thesis_evidence('minutes',thesis.thesis_id)
        before=helper.path.read_bytes()
        with patch.object(helper.store,'load_minute_bars',side_effect=lambda code,*args:[row for row in rows if row['code']==code]), \
             patch.object(helper.store,'load_second_trade_bars',side_effect=AssertionError('new research read old seconds')):
            report=read_minute_stock_thesis_source_check(helper.store,saved,thesis_policy_version='PRICE_ONLY',
                leadership_venue='KRX',lookback_minutes=2)
        self.assertEqual('stock-thesis-source-check/v2',report['version'])
        self.assertEqual((4,4),(report['minutes']['expected'],report['minutes']['matched']))
        self.assertFalse(report['order_authorized'])
        self.assertEqual(before,helper.path.read_bytes())

    def test_minute_source_client_uses_versioned_route_and_explicit_minute_budget(self):
        from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient
        client=CentralContentClient('http://offline','token')
        with patch.object(client,'_request',return_value={}) as request:
            client.check_ram_shadow_minute_sources(expected_revision=3,lookback_minutes=2)
            self.assertEqual('GET',request.call_args.args[0])
            self.assertIn('/api/v2/research/ram-shadow/source-check?',request.call_args.args[1])
            self.assertIn('lookback_minutes=2',request.call_args.args[1])
            with self.assertRaises(ValueError):client.check_ram_shadow_minute_sources(expected_revision=3,lookback_minutes=0)
            self.assertEqual(1,request.call_count)


class MinuteSourceApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_v2_auth_selection_budget_and_no_second_read(self):
        import httpx
        from tests.unit.test_candidate_monitor import RamShadowSelectionTests,saved_thesis_selection_fixture
        helper=RamShadowSelectionTests()
        with tempfile.TemporaryDirectory() as directory:
            app,store,now,collector=helper.app_fixture(directory)
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline') as client:
                    url='/api/v2/research/ram-shadow/source-check'
                    headers={'Authorization':'Bearer offline-token'}
                    params={'expected_revision':1,'lookback_minutes':2}
                    self.assertEqual(401,(await client.get(url,params=params)).status_code)
                    selected=await client.put('/api/v1/research/ram-shadow/selection',headers=headers,
                        json={'expected_revision':0,'theme_ids':['반도체'],'stock_thesis':saved_thesis_selection_fixture(directory)})
                    self.assertEqual(200,selected.status_code,selected.text)
                    with patch.object(store,'load_second_trade_bars',side_effect=AssertionError('v2 reads second data')), \
                         patch.object(store,'load_minute_bars',return_value=[]) as reads, \
                         patch.object(store,'upsert_documents',side_effect=AssertionError('source check writes')):
                        response=await client.get(url,headers=headers,params=params)
                        self.assertEqual(200,response.status_code,response.text)
                        self.assertEqual('stock-thesis-source-check/v2',response.json()['version'])
                        self.assertNotIn('seconds',response.json())
                        self.assertEqual(2,reads.call_count)
                        self.assertEqual(409,(await client.get(url,headers=headers,params={**params,'expected_revision':0})).status_code)
                        self.assertEqual(422,(await client.get(url,headers=headers,params={**params,'lookback_minutes':0})).status_code)
                        self.assertEqual(2,reads.call_count)
            finally:await helper.cleanup_app(app,store)


class CandidateHealthTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the health HTTP boundary without optional HTTP test clients."""
    def app(self, config=None):
        from kiwoom_monitor.application.leader_breakout import document_leader_breakout_policy
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.config import CentralServerSettings
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        store = SQLiteQueryStore(root / 'health.sqlite3')
        self.addCleanup(store.close)
        selected = document_leader_breakout_policy() if config is None else config
        settings = CentralServerSettings(
            f'sqlite:///{root / "health.sqlite3"}', 'offline-token',
            credential_directory='',
            autonomous_top20_enabled=False, market_event_collection_enabled=False,
            shadow_candidate_enabled=config is not False,
            shadow_candidate_config_json=json.dumps(selected.to_dict()) if selected is not False else '',
        )
        with patch('kiwoom_monitor.central_server.app.create_query_store', return_value=store):
            return create_app(settings), store

    async def get_health(self, app):
        messages = []
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await asyncio.Event().wait()
        async def send(message):
            messages.append(message)
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
            'http_version': '1.1', 'method': 'GET', 'scheme': 'http', 'path': '/health',
            'raw_path': b'/health', 'query_string': b'', 'root_path': '', 'headers': [],
            'client': ('127.0.0.1', 1), 'server': ('offline', 80)}
        await asyncio.wait_for(app(scope, receive, send), timeout=5)
        self.assertEqual(200, next(m['status'] for m in messages if m['type'] == 'http.response.start'))
        return json.loads(b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body'))

    async def test_leader_health_keeps_optional_universe_absent_and_evaluation_quality(self):
        app, store = self.app()
        monitor = app.state.candidate_monitor
        missing = monitor.operational_status()['configuration_missing']
        self.assertNotIn('universe', monitor.operational_status())
        for quality, expected in (('BLOCKED', 'unknown'), ('READY', 'unknown'),
                                  ('STALE', 'degraded'), ('ERROR', 'degraded')):
            with self.subTest(quality=quality), patch.object(
                    store, 'load_documents', side_effect=AssertionError('health must not read DB')):
                monitor._quality['status'] = quality
                body = await self.get_health(app)
                status = body['operational']['candidate_inputs']
                self.assertEqual(('ok', expected), (body['status'], body['operational']['state']))
                self.assertEqual('leader_breakout_0B', status['scope'])
                self.assertNotIn('universe', status)
                self.assertEqual(missing, status['configuration_missing'])
                self.assertEqual(quality, status['last_evaluation']['status'])

    async def test_legacy_health_still_reports_missing_universe_as_degraded(self):
        from kiwoom_monitor.application.breakout_strategy import default_shadow_breakout_config
        app, _ = self.app(default_shadow_breakout_config())
        body = await self.get_health(app)
        self.assertEqual('degraded', body['operational']['state'])
        self.assertEqual('missing', body['operational']['candidate_inputs']['universe']['state'])

    async def test_disabled_candidate_health_remains_unknown(self):
        app, _ = self.app(False)
        body = await self.get_health(app)
        self.assertEqual('unknown', body['operational']['state'])
        self.assertIsNone(body['operational']['candidate_inputs'])


class Shared0BInputTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_candidate_monitor import CollectorRamShadowRuntimeTests as _Collector,DynamicAllocationRamTests as _Allocation
    fixture=_Collector.fixture
    trades=_Collector.trades
    prepare=_Allocation.prepare

    async def test_copy_of_shared_cut_preserves_row_isolation_and_shared_immutable_points(self):
        from copy import deepcopy
        runtime,*_=self.prepare(1)
        original=runtime._snapshot()
        copied=deepcopy(original)
        self.assertIsNot(original['rows'],copied['rows'])
        self.assertIs(copied['rows'],copied['_trade_cut'].rows)
        row=copied['rows'][0]
        indexed=copied['_trade_cut'].rows_by_source[(row['code'],row['market'])]
        self.assertTrue(any(r is row for r in indexed))
        ref=row['input_ref']
        self.assertIs(original['_trade_cut'].points_by_ref[ref],copied['_trade_cut'].points_by_ref[ref])
        row['price_won']+=1
        self.assertNotEqual(original['rows'][0]['price_won'],row['price_won'])
        await runtime.close()

    async def test_market_theme_and_allocation_share_one_index_conversion_and_immutable_points(self):
        from copy import deepcopy
        from kiwoom_monitor.central_server import market_theme_shadow_inputs as inputs
        from kiwoom_monitor.application import theme_leadership as leadership
        for count in (1,8):
            with self.subTest(consumers=count):
                runtime,collector,now,source,*_=self.prepare(count)
                with patch.object(inputs,'prepare_ram_trade_observations',wraps=inputs.prepare_ram_trade_observations) as builds, \
                     patch.object(leadership,'realtime_trade_points_from_rows',wraps=leadership.realtime_trade_points_from_rows) as converts, \
                     patch('sqlite3.connect',side_effect=AssertionError('shared hot path reads DB')):
                    snapshot=runtime._snapshot()
                    await runtime._dispatch(snapshot)
                    self.assertEqual((1,1),(builds.call_count,converts.call_count))
                    common=runtime._monitors[0]._last_offered.observation_points
                    self.assertIs(common,deepcopy(common))
                    for monitor in runtime._monitors:
                        self.assertIs(common,monitor._last_offered.observation_points)
                        market=monitor._last_offered.market_price_trade_inputs
                        points={p.input_ref:p for p in common}
                        self.assertTrue(market.points)
                        self.assertTrue(all(points[p.input_ref] is p for p in market.points))
                    receipt=runtime.latest_results['position_allocation']
                    self.assertTrue(all(f['recent_turnover_won'] is not None for f in receipt['facts']))
                    self.assertIn('allocation_ev_unavailable',receipt['quality']['reasons'])
                    for _ in range(5):runtime.latest_results
                    self.assertEqual((1,1),(builds.call_count,converts.call_count))
                    self.assertFalse(hasattr(collector,'_second_trades'))
                await runtime.close()


if __name__ == '__main__':
    unittest.main()
