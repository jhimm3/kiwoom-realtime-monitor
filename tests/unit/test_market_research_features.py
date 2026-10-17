from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from kiwoom_monitor.application.market_research_features import (
    MarketFeatureParameters,
    MarketMember,
    MarketPriceTradeInputs,
    compute_price_trade_candidates,
    MarketMinuteTradeInputs, compute_minute_trade_candidates,
    MarketResearchFrame,
    MatureBreakoutSummary,
    assess_market_for_strategy,
    classify_market,
    compute_market_context_changes,
    compute_market_research_features,
    market_frames_from_observations,
)
from kiwoom_monitor.application.research_factors import (
    compute_market_regime,
    get_factor_definition,
)


UTC = timezone.utc
PARAMETERS = MarketFeatureParameters(
    top_k=3, history_days=5, max_gap_seconds=40,
    min_history_observations=3, min_mature_breakouts=3,
)


def _frame(
    second: int,
    codes: tuple[str, ...],
    values: tuple[int | None, ...],
    *,
    changes: tuple[int | None, ...] | None = None,
    themes: tuple[str, ...] | None = None,
    quality: str = "complete",
    index_return_bps: int | None = None,
    day: str = "2026-09-13",
    minute: str = "09:20",
) -> MarketResearchFrame:
    changes = changes or tuple(100 for _ in codes)
    themes = themes or tuple(f"theme-{code}" for code in codes)
    at = datetime(2026, 9, 13, 0, 20, tzinfo=UTC) + timedelta(seconds=second)
    if day != "2026-09-13":
        at -= timedelta(days=1)
    return MarketResearchFrame(
        revision_id=f"rank-{day}-{second}", available_at=at.isoformat(),
        trading_date=day, session_minute=minute, venue="COMBINED",
        window="cumulative_session",
        members=tuple(
            MarketMember(code, value, change, (theme,) if theme else ())
            for code, value, change, theme in zip(codes, values, changes, themes, strict=True)
        ),
        capture_quality=quality, index_return_bps=index_return_bps,
        theme_revision_id="theme-v1",
    )


class PriceTradeCandidateTests(unittest.TestCase):
    cut = datetime(2026, 9, 13, 0, 20, 40, tzinfo=UTC)

    def inputs(self, current='000001', previous='000001'):
        from kiwoom_monitor.application.theme_leadership import SecondTradePoint
        codes = tuple(f'{i:06d}' for i in range(1, 21))
        points = []
        for code in codes:
            for second in (1, 19, 21, 39):
                candidate = previous if second <= 20 else current
                price = 120 if code == candidate and second in (19, 39) else 100
                at = self.cut.replace(second=second).isoformat()
                points.append(SecondTradePoint(f'{code}:{second}', code, 'KRX', at, at,
                    price, price, 10, 4000 if code == candidate else 100, 1))
        return MarketPriceTradeInputs('membership', self.cut.replace(second=0).isoformat(), codes,
            'KRX', tuple(points), dict.fromkeys(codes, 'COMPLETE'), 20, 3)

    def compute(self, inputs=None):
        return compute_price_trade_candidates(inputs or self.inputs(), as_of=self.cut)

    def test_retained_candidate_and_market_flow_use_price_and_money(self):
        result = self.compute()
        self.assertEqual('RETAINED', result['state'])
        self.assertEqual(('000001',), result['current_candidates'])
        self.assertEqual(11800, result['market_flow']['current']['trade_value_won'])
        self.assertEqual(50000, result['market_flow']['current']['positive_price_ratio_ppm'])
        self.assertEqual(2000, result['metrics'][0]['current_return_bps'])
        self.assertFalse(result['order_authorized'])

    def test_actual_replacement_does_not_require_lookup_first_change(self):
        result = self.compute(self.inputs(current='000002'))
        self.assertEqual('REPLACED', result['state'])
        self.assertEqual(('000002',), result['entered_candidates'])
        self.assertEqual(('000001',), result['left_candidates'])

    def test_lookup_permutation_cannot_change_price_candidates(self):
        inputs = self.inputs()
        self.assertEqual(self.compute(inputs), self.compute(replace(inputs, member_codes=inputs.member_codes[::-1], points=inputs.points[::-1])))

    def test_money_without_price_and_price_without_money_are_not_candidates(self):
        for axis in ('close', 'trade_value_won'):
            inputs = self.inputs()
            points = tuple(replace(p, **{axis:100}) for p in inputs.points)
            self.assertEqual('NO_CANDIDATE', self.compute(replace(inputs, points=points))['state'])

    def test_tied_money_preserves_candidates_without_code_tiebreak(self):
        inputs = self.inputs()
        points = tuple(replace(p, close=120 if p.observed_at.endswith(('19+00:00', '39+00:00')) else 100,
            trade_value_won=4000) if p.code=='000002' else p for p in inputs.points)
        result = compute_price_trade_candidates(replace(inputs, points=points), as_of=self.cut, top_k=1)
        self.assertEqual(('000001','000002'), result['current_candidates'])

    def test_missing_sample_continuity_and_venue_are_unknown(self):
        inputs = self.inputs()
        variants = (replace(inputs, points=inputs.points[1:]),
            replace(inputs, continuity={}), replace(inputs, venue='NXT'),
            replace(inputs, member_codes=inputs.member_codes[:-1]))
        for value in variants:
            result = self.compute(value)
            self.assertEqual('UNKNOWN', result['state'])
            self.assertEqual((), result['current_candidates'])

    def test_future_quote_cannot_fill_missing_sample(self):
        inputs = self.inputs()
        points = tuple(replace(p, available_at=(self.cut+timedelta(seconds=1)).isoformat())
            if p.code=='000001' and ':39+' in p.observed_at else p for p in inputs.points)
        self.assertEqual('UNKNOWN', self.compute(replace(inputs, points=points))['state'])

    def test_future_membership_does_not_leak_cohort(self):
        result = self.compute(replace(self.inputs(), universe_available_at=(self.cut+timedelta(seconds=1)).isoformat()))
        self.assertEqual((), result['metrics'])
        self.assertEqual((), result['input_refs'])
        self.assertEqual(0,result['quality']['coverage']['evaluated_members'])
        self.assertEqual(0,result['quality']['coverage']['usable_members'])

    def test_coverage_separates_missing_sample_stale_quote_and_source_approval(self):
        inputs=self.inputs()
        points=tuple(replace(p,observed_at=self.cut.replace(second=36).isoformat())
            if p.code=='000003' and ':39+' in p.observed_at else p for p in inputs.points
            if not (p.code=='000002' and ':21+' in p.observed_at))
        result=self.compute(replace(inputs,points=points,continuity={**inputs.continuity,'000004':'UNKNOWN'},
            continuity_reasons={'000004':('trade_source_not_approved',)}))
        coverage=result['quality']['coverage']
        self.assertEqual('UNKNOWN',result['state']);self.assertEqual((),result['current_candidates'])
        self.assertEqual((20,20,17),(coverage['expected_members'],coverage['evaluated_members'],coverage['usable_members']))
        self.assertEqual((1,1,1),(coverage['continuity_unconfirmed_members'],coverage['current_sample_insufficient_members'],coverage['stale_members']))
        by_code={row['code']:row for row in result['metrics']}
        self.assertEqual(('price_trade_continuity_unconfirmed','trade_source_not_approved'),by_code['000004']['reasons'])
        self.assertEqual(('price_observation_stale',),by_code['000003']['reasons'])
        self.assertIn('current_price_sample_insufficient',by_code['000002']['reasons'])

    def test_stale_membership_blocks_fresh_price_candidates(self):
        result=self.compute(replace(self.inputs(),max_membership_age_seconds=40))
        self.assertEqual('UNKNOWN',result['state'])
        self.assertIn('membership_stale',result['quality']['reasons'])

    def test_ram_adapter_requires_approval_retention_and_gap_coverage(self):
        from types import SimpleNamespace
        from kiwoom_monitor.central_server.market_theme_shadow_inputs import market_price_trade_inputs_from_ram
        from zoneinfo import ZoneInfo
        inputs=self.inputs();local=ZoneInfo('Asia/Seoul');floor=self.cut-timedelta(seconds=40)
        context=SimpleNamespace(market_frames=(_frame(0,inputs.member_codes,tuple(None for _ in inputs.member_codes)),),
            market_venue='COMBINED',market_window='cumulative_session',leadership_venue='KRX')
        rows=[]
        for p in inputs.points:
            at=datetime.fromisoformat(p.observed_at).astimezone(local)
            rows.append({'trading_date':at.date().isoformat(),'trade_second':at.strftime('%H:%M:%S'),
                'code':p.code,'market':p.market,'open':p.close,'high':p.high,'low':p.close,'close':p.close,
                'volume':p.volume,'trade_value_won':p.trade_value_won,'trade_count':p.trade_count,
                'available_at':datetime.fromisoformat(p.available_at).timestamp()})
        snapshot={'decision_time':self.cut.isoformat(),'retention_since':floor,'gap_at':None,
            'approved_sources':tuple((c,'KRX') for c in inputs.member_codes),
            'continuous_from':{(c,'KRX'):floor for c in inputs.member_codes},'rows':tuple(rows),
            'source_id':'isolated','generation':1}
        def compute(changes):
            raw=market_price_trade_inputs_from_ram({**snapshot,**changes},context,window_seconds=20,max_data_age_seconds=3)
            return self.compute(raw)
        self.assertEqual('RETAINED',compute({})['state'])
        for changes,reason in (({'gap_at':self.cut-timedelta(seconds=10)},'ram_delivery_gap_in_window'),
                ({'retention_since':None},'ram_retention_history_insufficient'),
                ({'approved_sources':()},'trade_source_not_approved'),
                ({'continuous_from':{}},'trade_source_history_unconfirmed'),
                ({'continuous_from':{(c,'KRX'):self.cut-timedelta(seconds=10) for c in inputs.member_codes}},'trade_source_history_insufficient'),
                ({'rows':tuple({**r,'available_at':self.cut.timestamp()+1} for r in rows)},'current_price_observation_unavailable')):
            result=compute(changes)
            self.assertEqual('UNKNOWN',result['state'])
            self.assertTrue(all(reason in m['reasons'] for m in result['metrics']))

    def test_old_quote_fresh_availability_cannot_extend_expiry(self):
        inputs = self.inputs()
        points = tuple(replace(p, observed_at=self.cut.replace(second=36).isoformat(), available_at=self.cut.isoformat())
            if ':39+' in p.observed_at else p for p in inputs.points)
        self.assertEqual('UNKNOWN', self.compute(replace(inputs, points=points))['state'])

    def test_duplicate_and_later_revision_do_not_double_count(self):
        inputs = self.inputs()
        self.assertEqual(self.compute(inputs), self.compute(replace(inputs, points=inputs.points*2)))
        point = inputs.points[-1]
        newer = replace(point, input_ref='newer', available_at=self.cut.isoformat(), trade_value_won=200)
        result = self.compute(replace(inputs, points=(*inputs.points, newer)))
        self.assertEqual(11900, result['market_flow']['current']['trade_value_won'])

    def test_conflicting_revision_is_rejected(self):
        inputs = self.inputs()
        for point in (replace(inputs.points[-1], close=101), replace(inputs.points[-1], input_ref='conflict', close=101)):
            with self.assertRaises(ValueError):
                self.compute(replace(inputs, points=(*inputs.points, point)))

    def test_exact_expiry_boundary_and_previous_day_are_unknown(self):
        self.assertEqual('UNKNOWN', compute_price_trade_candidates(self.inputs(), as_of=self.cut+timedelta(seconds=2))['state'])
        self.assertEqual('UNKNOWN', self.compute(replace(self.inputs(), universe_available_at=(self.cut-timedelta(days=1)).isoformat()))['state'])


class MinuteMarketCandidateTests(unittest.TestCase):
    cut = datetime(2026,10,13,0,20,3,tzinfo=UTC)

    def inputs(self, *, replaced=False):
        codes=tuple(f'{i:06d}' for i in range(1,21));rows=[]
        for i in range(10):
            at=self.cut.replace(minute=10+i,second=0); ready=at+timedelta(seconds=62)
            for code in codes:
                leader=code=='000001';open_price=100+i*2 if leader else 100
                close=open_price+2 if leader else 100; amount=20 if leader else 1
                if replaced and i==9:
                    close=open_price;amount=1
                    if code=='000002':close=105;amount=50
                rows.append({'code':code,'market':'KRX','trading_date':'2026-10-13','minute':f'09:{10+i:02d}',
                    'open':open_price,'high':max(open_price,close),'low':min(open_price,close),'close':close,
                    'volume':100,'trade_value_million_won':amount,'updated_at':ready.timestamp(),
                    'metadata':{'venue':'KRX','origin':'realtime','value_kind':'actual','completeness':'complete',
                        'effective_at':at.isoformat(),'available_at':ready.isoformat(),'source':'kiwoom-websocket-0B','unit':'unknown'}})
        return MarketMinuteTradeInputs('top20',self.cut.replace(second=0).isoformat(),codes,'KRX',tuple(rows))

    def compute(self, inputs=None, cut=None):
        return compute_minute_trade_candidates(inputs or self.inputs(),as_of=cut or self.cut)

    def test_one_minute_candidate_and_five_minute_persistence_without_seconds(self):
        r=self.compute()
        self.assertEqual('RETAINED',r['state']);self.assertEqual(('000001',),r['current_candidates'])
        self.assertEqual(20000000,r['metrics'][0]['current_trade_value_won'])
        self.assertEqual(100000000,r['timeframes']['5m']['metrics'][0]['current_trade_value_won'])
        self.assertEqual('CONFIRMED',r['five_minute_confirmation']['status'])
        self.assertEqual(5,r['five_minute_confirmation']['qualifying_minutes']['000001'])
        self.assertFalse(r['order_authorized'])

    def test_minute_input_snapshot_cannot_be_mutated_through_rows_or_metadata(self):
        raw=self.inputs()
        with self.assertRaises(TypeError):raw.rows[0]['close']=999
        with self.assertRaises(TypeError):raw.rows[0]['metadata']['completeness']='partial'
        self.assertEqual('RETAINED',self.compute(raw)['state'])

    def test_one_minute_replacement_needs_persistence_before_five_minute_confirmation(self):
        r=self.compute(self.inputs(replaced=True))
        self.assertEqual('REPLACED',r['state']);self.assertEqual(('000002',),r['current_candidates'])
        self.assertEqual('NOT_CONFIRMED',r['five_minute_confirmation']['status'])
        self.assertEqual(1,r['five_minute_confirmation']['qualifying_minutes']['000002'])

    def test_missing_five_minute_history_does_not_block_complete_one_minute(self):
        raw=self.inputs();r=self.compute(replace(raw,rows=tuple(row for row in raw.rows if row['minute']>='09:18')))
        self.assertEqual('RETAINED',r['state']);self.assertEqual('UNAVAILABLE',r['five_minute_confirmation']['status'])
        self.assertEqual('UNKNOWN',r['timeframes']['5m']['state'])

    def test_unknown_older_minute_lineage_does_not_block_current_one_and_five_minutes(self):
        raw=self.inputs();rows=tuple({**row,'metadata':{}} if row['minute']=='09:10' else row for row in raw.rows)
        r=self.compute(replace(raw,rows=rows))
        self.assertEqual('RETAINED',r['state']);self.assertEqual('CONFIRMED',r['five_minute_confirmation']['status'])
        self.assertEqual('UNKNOWN',r['timeframes']['5m']['state'])

    def test_current_five_minutes_can_confirm_without_older_five_minute_comparison(self):
        raw=self.inputs();r=self.compute(replace(raw,rows=tuple(row for row in raw.rows if row['minute']>='09:15')))
        self.assertEqual('CONFIRMED',r['five_minute_confirmation']['status'])
        self.assertEqual('UNKNOWN',r['timeframes']['5m']['state'])

    def test_partial_estimated_future_and_wrong_venue_cannot_fill_minute_history(self):
        raw=self.inputs();row=raw.rows[-1]
        for extra in ({'metadata':{**row['metadata'],'completeness':'in_progress'}},
                {'metadata':{**row['metadata'],'value_kind':'estimated'}},
                {'metadata':{**row['metadata'],'available_at':(self.cut+timedelta(seconds=1)).isoformat()}},
                {'market':'NXT'}, {'updated_at':self.cut.timestamp()+1}):
            r=self.compute(replace(raw,rows=(*raw.rows[:-1],{**row,**extra})))
            self.assertEqual('UNKNOWN',r['state']);self.assertEqual((),r['current_candidates'])

    def test_conflicting_duplicate_is_rejected_and_identical_is_not_summed(self):
        raw=self.inputs();self.assertEqual(self.compute(raw),self.compute(replace(raw,rows=raw.rows*2)))
        with self.assertRaises(ValueError):self.compute(replace(raw,rows=(*raw.rows,{**raw.rows[-1],'close':101})))
        with self.assertRaises(ValueError):self.compute(replace(raw,rows=(*raw.rows,{**raw.rows[-1],'close':float(raw.rows[-1]['close'])})))

    def test_later_partial_revision_invalidates_older_closed_bar(self):
        raw=self.inputs();row=raw.rows[-1]
        later={**row,'updated_at':row['updated_at']+.5,'metadata':{**row['metadata'],
            'available_at':(datetime.fromisoformat(row['metadata']['available_at'])+timedelta(seconds=.5)).isoformat(),
            'completeness':'partial'}}
        r=self.compute(replace(raw,rows=(*raw.rows,later)))
        self.assertEqual('UNKNOWN',r['state']);self.assertEqual((),r['current_candidates'])

    def test_lookup_order_permutation_and_h2_seconds_cannot_affect_minute_result(self):
        raw=self.inputs();self.assertEqual(self.compute(raw),self.compute(replace(raw,member_codes=raw.member_codes[::-1],rows=raw.rows[::-1])))

    def test_future_membership_and_previous_day_hide_cohort(self):
        for at in ((self.cut+timedelta(seconds=1)).isoformat(),(self.cut-timedelta(days=1)).isoformat()):
            r=self.compute(replace(self.inputs(),universe_available_at=at))
            self.assertEqual((),r['metrics']);self.assertEqual((),r['input_refs'])


class MarketResearchFeatureTests(unittest.TestCase):
    def _features(
        self,
        frames: tuple[MarketResearchFrame, ...],
        outcomes: MatureBreakoutSummary = MatureBreakoutSummary(4, 3, 1),
    ):
        return compute_market_research_features(frames, outcomes, PARAMETERS)

    def test_concentration_turnover_relative_value_and_denominators(self) -> None:
        old = _frame(-20, ("1", "2", "3", "4", "5"), (10, 10, 10, 10, 10), day="2026-09-12")
        first = _frame(0, ("1", "2", "3", "4", "5"), (20, 20, 20, 20, 20))
        second = _frame(20, ("1", "2", "6", "4", "5"), (25, 20, 20, 20, 15))
        current = _frame(40, ("1", "2", "6", "7", "5"), (30, 25, 20, 15, 10))

        features = self._features((old, first, second, current))

        self.assertEqual(100, features.trade_value_denominator_million_won)
        self.assertEqual(300_000, features.top1_concentration_ppm)
        self.assertEqual(1_000_000, features.top5_concentration_ppm)
        self.assertEqual(0, features.fixed_k_turnover_ppm)
        self.assertEqual(333_333, features.jaccard_change_ppm)
        self.assertEqual(1_000_000, features.current_leader_top_k_residency_ppm)
        self.assertEqual(2_000_000, features.relative_trade_value_ppm)
        self.assertEqual(50, features.historical_median_trade_value_million_won)
        self.assertIn(old.revision_id, features.input_refs)
        self.assertEqual(5, features.top20_breadth_denominator)

    def test_healthy_theme_spread_is_theme_led(self) -> None:
        codes = ("1", "2", "3", "4", "5")
        themes = ("alpha", "alpha", "alpha", "beta", "gamma")
        frames = (
            _frame(0, codes, (28, 24, 18, 16, 14), themes=themes),
            _frame(20, codes, (29, 24, 18, 15, 14), themes=themes),
            _frame(40, codes, (30, 25, 15, 15, 15), themes=themes),
        )
        classification = classify_market(self._features(frames))
        self.assertEqual("THEME_LED", classification.market_type)
        self.assertIn("theme_concentration_with_active_spread", classification.reasons)

    def test_single_leader_and_strategy_policy_are_separate(self) -> None:
        codes = ("1", "2", "3", "4", "5")
        changes = (500, -100, -100, 100, -50)
        frames = tuple(
            _frame(second, codes, (50, 15, 15, 10, 10), changes=changes)
            for second in (0, 20, 40)
        )
        classification = classify_market(self._features(frames))
        assessment = assess_market_for_strategy(
            classification, strategy_id="leader_breakout/v1",
            preferred_types=("SINGLE_LEADER",), difficult_types=("DISPERSED_ROTATION",),
        )
        self.assertEqual("SINGLE_LEADER", classification.market_type)
        self.assertEqual("SUPPORTED", assessment.tradability)
        self.assertNotIn("tradability", classification.to_dict())

    def test_fast_failure_rotation_needs_more_than_rank_turnover(self) -> None:
        first = _frame(0, ("1", "2", "3", "4", "5"), (22, 21, 20, 19, 18))
        second = _frame(20, ("6", "7", "3", "4", "5"), (22, 21, 20, 19, 18))
        current = _frame(40, ("8", "9", "10", "4", "5"), (22, 21, 20, 19, 18))
        classification = classify_market(self._features(
            (first, second, current), MatureBreakoutSummary(4, 1, 3),
        ))
        self.assertEqual("DISPERSED_ROTATION", classification.market_type)

        stable_leader = _frame(40, ("3", "8", "9", "4", "5"), (22, 21, 20, 19, 18))
        not_rotation = classify_market(self._features(
            (first, second, stable_leader), MatureBreakoutSummary(4, 1, 3),
        ))
        self.assertNotEqual("DISPERSED_ROTATION", not_rotation.market_type)

    def test_index_only_strength_immature_outcomes_and_data_gap_are_unknown(self) -> None:
        codes = ("1", "2", "3", "4", "5")
        changes = (100, -100, -100, -100, -100)
        frames = tuple(
            _frame(second, codes, (25, 20, 20, 20, 15), changes=changes, index_return_bps=150)
            for second in (0, 20, 40)
        )
        index_only = classify_market(self._features(frames))
        self.assertEqual("UNKNOWN", index_only.market_type)
        self.assertIn("index_strength_without_top20_confirmation", index_only.reasons)

        immature = classify_market(self._features(
            frames, MatureBreakoutSummary(0, 0, 0, pending_count=4),
        ))
        self.assertIn("mature_breakout_sample_insufficient", immature.reasons)

        interrupted = (
            frames[0],
            _frame(20, codes, (25, 20, 20, 20, 15)),
            _frame(100, codes, (25, 20, 20, 20, 15), quality="partial"),
        )
        unavailable = classify_market(self._features(interrupted))
        self.assertEqual("UNKNOWN", unavailable.market_type)
        self.assertIn("current_capture_incomplete", unavailable.reasons)
        self.assertIn("rank_observation_gap", unavailable.reasons)

    def test_zero_denominator_is_missing_and_factor_keeps_reasons(self) -> None:
        codes = ("1", "2", "3", "4", "5")
        frames = tuple(_frame(second, codes, (0, 0, 0, 0, 0)) for second in (0, 20, 40))
        factor = compute_market_regime(frames, MatureBreakoutSummary(3, 2, 1), PARAMETERS)
        self.assertEqual("partial", factor.status)
        self.assertIsNone(factor.value["features"]["top1_concentration_ppm"])
        self.assertIn("trade_value_denominator_unavailable", factor.reason)
        self.assertEqual("market_regime", get_factor_definition("market_regime", "v1").factor_id)

    def test_ranking_adapter_uses_theme_revision_available_at_that_time(self) -> None:
        observation = {
            "kind": "ranking", "revision_id": "rank-1", "venue": "COMBINED",
            "effective_at": "2026-09-13T09:20:00+09:00",
            "available_at": "2026-09-13T00:20:01+00:00", "completeness": "complete",
            "payload": {"query_type": "5", "items": [
                {"stk_cd": "A005930", "trde_prica": "1,234", "flu_rt": "+2.50"},
            ]},
        }
        snapshots = ({
            "snapshot_id": "theme-old", "available_at": 1789258800.0,
            "document": {"active_profile": "기본", "profiles": [{
                "name": "기본", "stock_themes": [
                    {"code": "005930", "theme": "반도체"},
                    {"code": "005930", "theme": "AI"},
                ],
            }]},
        },)
        frames = market_frames_from_observations((observation,), snapshots)
        self.assertEqual(1, len(frames))
        self.assertEqual(1234, frames[0].members[0].trade_value_million_won)
        self.assertEqual(250, frames[0].members[0].change_bps)
        self.assertEqual(("반도체", "AI"), frames[0].members[0].themes)


from dataclasses import asdict, replace
import json
import unittest

from kiwoom_monitor.application.market_research_features import MarketMember, MarketResearchFrame, MatureBreakoutSummary
from kiwoom_monitor.application.market_theme_shadow import (
    MarketThemeShadowInputs, MatureBreakoutEvidence, SHADOW_CONTEXT_VERSION, evaluate_market_theme_shadow,
)
from kiwoom_monitor.application.research_factors import compute_market_regime
from kiwoom_monitor.application.theme_leadership import evaluate_theme_leadership
from scripts.preview_market_theme_shadow import evaluate_document
from tests.unit.test_theme_leadership import DAY as SHADOW_DAY, PARAMETERS as SHADOW_PARAMETERS, initial_points as shadow_points, membership as shadow_membership, point as shadow_point


def shadow_inputs():
    frames = tuple(MarketResearchFrame(
        revision_id=f"market-{second}", available_at=f"{SHADOW_DAY}T09:00:{second:02d}+09:00",
        trading_date=SHADOW_DAY, session_minute="09:00", venue="KRX", window="cumulative_session",
        members=(MarketMember("005930", 100, 200, ("반도체",)),
                 MarketMember("000660", 50, 100, ("반도체",))), theme_revision_id="snapshot-r1",
    ) for second in (0, 10, 20))
    return MarketThemeShadowInputs(
        decision_time=f"{SHADOW_DAY}T09:00:20+09:00", market_venue="KRX", market_window="cumulative_session",
        leadership_venue="KRX", market_frames=frames, theme_membership=shadow_membership(),
        second_points=shadow_points(), continuity_status="COMPLETE",
        breakout_evidence=MatureBreakoutEvidence("outcomes-r1", f"{SHADOW_DAY}T09:00:10+09:00", MatureBreakoutSummary(4, 3, 1)),
    )


class MarketThemeShadowTests(unittest.TestCase):
    def test_existing_factor_results_and_explicit_scope_are_preserved(self):
        inputs = shadow_inputs()
        result = evaluate_market_theme_shadow(inputs)
        market = compute_market_regime(inputs.market_frames, inputs.breakout_evidence.summary)
        leadership = evaluate_theme_leadership(shadow_membership(), shadow_points(), as_of=inputs.decision_time,
            venue="KRX", continuity_status="COMPLETE", parameters=SHADOW_PARAMETERS)
        self.assertEqual(market.value["features"], result["market"]["facts"])
        self.assertEqual(leadership.raw_leader_id, result["leadership"]["inferences"]["raw_leader_id"])
        self.assertEqual(asdict(leadership.state), result["next_leadership_state"])
        self.assertEqual("TOP20", result["scope"]["market_universe"])
        self.assertEqual("not_verified", result["scope"]["source_durability"])
        self.assertFalse(result["order_authorized"])
        self.assertEqual("COMPLETE", result["quality"]["status"])

    def test_json_consumer_and_typed_consumer_have_identical_output(self):
        inputs = shadow_inputs()
        original = asdict(inputs)
        document = json.loads(json.dumps({"schema": SHADOW_CONTEXT_VERSION, "inputs": original}))
        self.assertEqual(evaluate_market_theme_shadow(inputs), evaluate_document(document))
        self.assertEqual(original, asdict(inputs))

    def test_future_and_other_venue_window_data_do_not_change_the_cut(self):
        inputs = shadow_inputs()
        extra = (replace(inputs.market_frames[-1], revision_id="future", available_at=f"{SHADOW_DAY}T09:00:21+09:00"),
                 replace(inputs.market_frames[-1], revision_id="nxt", venue="NXT"),
                 replace(inputs.market_frames[-1], revision_id="other-window", window="minute"))
        changed = replace(inputs, market_frames=(*inputs.market_frames, *extra),
                          second_points=(*inputs.second_points, shadow_point("005930", 21, 200, 9000),
                                         replace(shadow_point("005930", 19, 200, 9000), market="NXT")))
        self.assertEqual(evaluate_market_theme_shadow(inputs), evaluate_market_theme_shadow(changed))

    def test_missing_or_future_breakout_evidence_remains_partial(self):
        inputs = shadow_inputs()
        for evidence in (None, replace(inputs.breakout_evidence, available_at=f"{SHADOW_DAY}T09:00:21+09:00")):
            with self.subTest(evidence=evidence):
                result = evaluate_market_theme_shadow(replace(inputs, breakout_evidence=evidence))
                self.assertEqual("PARTIAL", result["market"]["quality"]["status"])
                self.assertEqual("UNKNOWN", result["market"]["inferences"]["market_type"])
                self.assertNotIn("outcomes-r1", result["evidence_refs"])

    def test_stale_market_does_not_reuse_its_old_classification(self):
        result = evaluate_market_theme_shadow(replace(shadow_inputs(), decision_time=f"{SHADOW_DAY}T09:02:00+09:00"))
        self.assertEqual("STALE", result["market"]["quality"]["status"])
        self.assertEqual("UNKNOWN", result["market"]["inferences"]["market_type"])

    def test_missing_member_or_continuity_does_not_establish_a_leader(self):
        inputs = shadow_inputs()
        for changed in (replace(inputs, second_points=tuple(p for p in inputs.second_points if p.code == "005930")),
                        replace(inputs, continuity_status="PARTIAL")):
            result = evaluate_market_theme_shadow(changed)
            self.assertEqual("PARTIAL", result["quality"]["status"])
            self.assertEqual("", result["leadership"]["inferences"]["raw_leader_id"])

    def test_unavailable_membership_and_market_return_explicit_missing(self):
        inputs = shadow_inputs()
        result = evaluate_market_theme_shadow(replace(inputs, market_frames=(),
            theme_membership=replace(inputs.theme_membership, available_at=f"{SHADOW_DAY}T09:00:21+09:00")))
        self.assertEqual((), result["evidence_refs"])
        self.assertIsNone(result["next_leadership_state"])
        self.assertFalse(result["order_authorized"])

    def test_future_state_and_naive_clock_are_rejected(self):
        inputs = shadow_inputs()
        state = evaluate_market_theme_shadow(inputs)["next_leadership_state"]
        from kiwoom_monitor.application.theme_leadership import ThemeLeadershipState
        with self.assertRaisesRegex(ValueError, "ahead"):
            evaluate_market_theme_shadow(replace(inputs, previous_leadership_state=ThemeLeadershipState(
                **{**state, "last_observed_at": f"{SHADOW_DAY}T09:00:21+09:00"})))
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            replace(inputs, decision_time=f"{SHADOW_DAY}T09:00:20")

    def test_upper_limit_and_parameter_changes_have_distinct_evidence_and_identity(self):
        from kiwoom_monitor.application.theme_leadership import UpperLimitReference
        inputs = shadow_inputs()
        limit = UpperLimitReference("limit-r1", "005930", 105, f"{SHADOW_DAY}T09:00:10+09:00")
        normal = evaluate_market_theme_shadow(inputs)
        result = evaluate_market_theme_shadow(replace(inputs, upper_limits=(limit,)))
        self.assertIn("limit-r1", result["evidence_refs"])
        self.assertEqual("TOUCHED", result["leadership"]["inferences"]["limit_state"])
        self.assertNotEqual(normal["revision_id"], result["revision_id"])
        changed = evaluate_market_theme_shadow(inputs, leadership_parameters=replace(SHADOW_PARAMETERS, near_limit_bps=50))
        self.assertNotEqual(normal["revision_id"], changed["revision_id"])

    def test_unknown_schema_and_fields_are_rejected(self):
        document = {"schema": SHADOW_CONTEXT_VERSION, "inputs": asdict(shadow_inputs())}
        with self.assertRaises(ValueError):
            evaluate_document({**document, "schema": "unknown"})
        with self.assertRaises(ValueError):
            evaluate_document({**document, "order_authorized": True})

    def test_repeated_sources_do_not_add_rank_history_or_trade_value(self):
        inputs = shadow_inputs()
        repeated = replace(inputs, market_frames=inputs.market_frames * 2, second_points=inputs.second_points * 2)
        self.assertEqual(evaluate_market_theme_shadow(inputs), evaluate_market_theme_shadow(repeated))
        warmup = replace(inputs, market_frames=(inputs.market_frames[-1],) * 3)
        result = evaluate_market_theme_shadow(warmup)
        self.assertIn("rank_history_warmup", result["market"]["quality"]["reasons"])

    def test_same_immutable_ref_with_conflicting_content_is_rejected(self):
        inputs = shadow_inputs()
        for changed in (
            replace(inputs, market_frames=(*inputs.market_frames, replace(inputs.market_frames[-1], capture_quality="partial"))),
            replace(inputs, second_points=(*inputs.second_points, replace(inputs.second_points[-1], trade_value_won=9000))),
        ):
            with self.assertRaisesRegex(ValueError, "conflicting immutable"):
                evaluate_market_theme_shadow(changed)


class MarketContextChangeTests(unittest.TestCase):
    """완성된 TOP20 fixture만으로 변화 관측의 시점·범위·결측을 검증한다."""

    codes = tuple(f"{number:06d}" for number in range(1, 21))

    def frame(self, second, *, codes=None, values=None, **kwargs):
        codes = self.codes if codes is None else codes
        return _frame(second, codes, values if values is not None else (100,) * len(codes),
                      themes=("",) * len(codes), **kwargs)

    def context(self, frames, *, as_of=None, **kwargs):
        return compute_market_context_changes(
            frames, PARAMETERS,
            as_of=as_of or datetime.fromisoformat(frames[-1].available_at),
            venue="COMBINED", window="cumulative_session", **kwargs,
        )

    def test_concentration_without_themes_news_or_breakout_results(self):
        frames = (self.frame(0), self.frame(20, values=(1800,) + (100,) * 19),
                  self.frame(40, values=(1900,) + (100,) * 19))
        result = self.context(frames)
        self.assertEqual("LEADER_CONCENTRATION", result.pattern)
        self.assertEqual("COMPLETE", result.quality["status"])
        self.assertEqual("INCREASED", result.changes["concentration_direction"])
        self.assertEqual(13_514, result.changes["top1_concentration_delta_ppm"])
        self.assertEqual("UNKNOWN", result.theme_evidence["status"])
        self.assertEqual("UNKNOWN", result.classification.market_type)
        self.assertIn("mature_breakout_sample_insufficient", result.classification.reasons)
        self.assertEqual("TOP20", result.universe)
        self.assertIn("top20_not_whole_market", result.limitations)
        self.assertIn("lookup_rank_not_trade_value_rank_or_buy_eligibility", result.limitations)

    def test_first_rank_replacement_and_repeated_dispersed_rotation_are_distinct(self):
        first, second = self.frame(0), self.frame(20)
        switched = self.frame(40, codes=(self.codes[1], self.codes[0], *self.codes[2:]))
        replacement = self.context((first, second, switched))
        self.assertEqual("LEADER_REPLACEMENT", replacement.pattern)
        self.assertEqual("000001", replacement.changes["previous_leader_code"])
        self.assertEqual("000002", replacement.changes["current_leader_code"])
        rotation = self.context(tuple(self.frame(second, codes=tuple(
            f"{number:06d}" for number in range(start, start + 20)
        )) for second, start in ((0, 1), (20, 21), (40, 41))))
        self.assertEqual("DISPERSED_ROTATION", rotation.pattern)
        self.assertEqual("UNKNOWN", rotation.classification.market_type)
        self.assertIn("without_breakout_outcome_claim", rotation.reasons[0])

    def test_membership_memory_is_bounded_to_supplied_frames(self):
        frames = (self.frame(0), self.frame(20, codes=("000021", *self.codes[1:])),
                  self.frame(40, codes=("000001", "000021", *self.codes[2:])))
        result = self.context(frames)
        candidates = {item.code: item for item in result.candidates}
        self.assertEqual("REAPPEARED", candidates["000001"].state)
        self.assertEqual("LEFT", candidates["000002"].state)
        self.assertEqual("RETAINED", candidates["000021"].state)
        self.assertEqual((2, 3), (candidates["000001"].presence_count,
                                  candidates["000001"].observation_count))
        self.assertEqual(frames[0].available_at, candidates["000001"].first_seen_at)
        self.assertEqual(frames[-1].available_at, candidates["000001"].last_seen_at)
        self.assertIsNone(candidates["000001"].trade_value_delta_million_won)
        self.assertEqual(tuple(frame.revision_id for frame in frames), candidates["000001"].input_refs)
        # 같은 입력의 짧은 suffix에서는 이전 출현을 추측하지 않는다.
        short = self.context(frames[1:])
        self.assertEqual("ENTERED", next(item for item in short.candidates if item.code == "000001").state)
        self.assertEqual("UNKNOWN", short.pattern)

    def test_entered_and_absent_are_top20_membership_facts(self):
        frames = (self.frame(0), self.frame(20, codes=("000021", *self.codes[1:])),
                  self.frame(40, codes=("000022", *self.codes[1:])))
        candidates = {item.code: item for item in self.context(frames).candidates}
        self.assertEqual("ENTERED", candidates["000022"].state)
        self.assertEqual("ABSENT", candidates["000001"].state)
        self.assertEqual("LEFT", candidates["000021"].state)
        self.assertIn("not_market_exit", candidates["000021"].reasons[0])

    def test_rank_money_and_daily_change_delta_keep_units(self):
        frames = tuple(self.frame(second, values=(value,) * 20, changes=(change,) * 20)
                       for second, value, change in ((0, 100, 100), (20, 120, 150), (40, 140, 200)))
        result = self.context(frames)
        member = result.candidates[0]
        self.assertEqual("LEADER_RETAINED", result.pattern)
        self.assertEqual("UNCHANGED", result.changes["concentration_direction"])
        self.assertEqual((1, 1, 20, 50), (member.previous_rank, member.current_rank,
                                        member.trade_value_delta_million_won, member.change_bps_delta))

    def test_missing_money_blocks_concentration_but_not_confirmed_membership(self):
        result = self.context((self.frame(0), self.frame(20),
                               self.frame(40, values=(None,) + (100,) * 19)))
        self.assertEqual("UNKNOWN", result.pattern)
        self.assertIn("trade_value_denominator_unavailable", result.quality["reasons"])
        self.assertIsNone(result.changes["top1_concentration_delta_ppm"])
        self.assertEqual("RETAINED", result.candidates[0].state)
        self.assertIsNone(result.candidates[0].trade_value_delta_million_won)

    def test_missing_price_does_not_block_rank_and_money_observations(self):
        result = self.context(tuple(self.frame(second, changes=(None,) * 20) for second in (0, 20, 40)))
        self.assertEqual("COMPLETE", result.quality["status"])
        self.assertEqual("LEADER_RETAINED", result.pattern)
        self.assertIsNone(result.candidates[0].change_bps_delta)
        self.assertIn("price_change_unavailable", result.candidates[0].reasons)
        self.assertEqual("UNKNOWN", result.classification.market_type)

    def test_counter_decrease_and_other_windows_are_not_trade_flow(self):
        frames = (self.frame(0), self.frame(20), self.frame(40, values=(90,) + (100,) * 19))
        candidate = self.context(frames).candidates[0]
        self.assertIsNone(candidate.trade_value_delta_million_won)
        self.assertIn("trade_value_counter_decreased", candidate.reasons)
        minute_frames = tuple(replace(frame, window="minute") for frame in frames)
        result = compute_market_context_changes(minute_frames, PARAMETERS,
            as_of=datetime.fromisoformat(frames[-1].available_at), venue="COMBINED", window="minute")
        self.assertIsNone(result.candidates[0].trade_value_delta_million_won)
        self.assertIn("trade_value_window_not_cumulative", result.candidates[0].reasons)

    def test_partial_short_and_interrupted_top20_cannot_confirm_transitions(self):
        for current in (self.frame(40, quality="partial"), self.frame(40, codes=self.codes[:-1]),
                        self.frame(100)):
            with self.subTest(current=current):
                result = self.context((self.frame(0), self.frame(20), current))
                self.assertEqual("UNKNOWN", result.pattern)
                self.assertEqual("PARTIAL", result.quality["status"])
                self.assertTrue(all(item.state == "UNKNOWN" for item in result.candidates))
                self.assertIsNone(result.changes["top1_concentration_delta_ppm"])

    def test_earlier_gap_or_partial_history_does_not_establish_reappearance(self):
        for first in (self.frame(0, quality="partial"), self.frame(-80)):
            result = self.context((first, self.frame(20, codes=("000021", *self.codes[1:])), self.frame(40)))
            candidates = {item.code: item for item in result.candidates}
            self.assertEqual("UNKNOWN", candidates["000001"].state)
            self.assertIn("earlier_membership_history_unconfirmed", candidates["000001"].reasons)
            self.assertEqual("RETAINED", candidates["000002"].state)

    def test_exact_expiry_and_new_trading_day_do_not_revive_old_judgment(self):
        frames = tuple(self.frame(second) for second in (0, 20, 40))
        current = datetime.fromisoformat(frames[-1].available_at)
        for cutoff in (current + timedelta(seconds=40), current + timedelta(days=1)):
            result = self.context(frames, as_of=cutoff)
            self.assertEqual("STALE", result.quality["status"])
            self.assertEqual("UNKNOWN", result.pattern)
            self.assertEqual("UNKNOWN", result.classification.market_type)
            self.assertTrue(all(item.state == "UNKNOWN" for item in result.candidates))
            self.assertEqual(current.isoformat(), result.observed_at)
        self.assertEqual("COMPLETE", self.context(frames, as_of=current + timedelta(seconds=39)).quality["status"])

    def test_cut_scope_sorting_deduplication_and_input_immutability(self):
        frames = tuple(self.frame(second) for second in (0, 20, 40))
        expected = self.context(frames)
        extras = (self.frame(41), replace(self.frame(39), venue="NXT"), replace(self.frame(38), window="minute"))
        original = asdict(frames[-1])
        actual = self.context((*reversed(frames), *extras, *frames),
                              as_of=datetime.fromisoformat(frames[-1].available_at))
        self.assertEqual(expected, actual)
        self.assertEqual(original, asdict(frames[-1]))
        self.assertEqual(3, actual.quality["observation_count"])
        warmup = self.context((frames[-1],) * 3)
        self.assertEqual("UNKNOWN", warmup.pattern)
        self.assertEqual(1, warmup.candidates[0].presence_count)
        self.assertEqual("OBSERVED", warmup.candidates[0].state)

    def test_aware_time_sorting_and_ambiguous_or_conflicting_revisions(self):
        frames = tuple(self.frame(second) for second in (0, 20, 40))
        offset = replace(frames[0], available_at=datetime.fromisoformat(frames[0].available_at)
                         .astimezone(timezone(timedelta(hours=9))).isoformat())
        self.assertEqual("LEADER_RETAINED", self.context((frames[1], frames[2], offset),
                         as_of=datetime.fromisoformat(frames[2].available_at)).pattern)
        with self.assertRaisesRegex(ValueError, "conflicting immutable"):
            self.context((*frames, replace(frames[-1], capture_quality="partial")))
        with self.assertRaisesRegex(ValueError, "distinct observation times"):
            self.context((*frames, replace(frames[-1], revision_id="same-time-other-revision")))

    def test_empty_and_future_only_inputs_remain_unknown_and_naive_cut_is_rejected(self):
        cutoff = datetime.fromisoformat(self.frame(0).available_at)
        for frames in ((), (self.frame(20),)):
            result = self.context(frames, as_of=cutoff)
            self.assertEqual("MISSING", result.quality["status"])
            self.assertIsNone(result.features)
            self.assertIsNone(result.expires_at)
            self.assertEqual((), result.input_refs)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.context((self.frame(0),), as_of=cutoff.replace(tzinfo=None))

    def test_earlier_days_support_features_not_same_day_candidate_memory(self):
        old = self.frame(0, codes=tuple(f"{number:06d}" for number in range(21, 41)), day="2026-09-12")
        frames = tuple(self.frame(second) for second in (0, 20, 40))
        result = self.context((old, *frames))
        self.assertIn(old.revision_id, result.input_refs)
        self.assertEqual(1, result.features.historical_day_count)
        self.assertEqual(set(self.codes), {item.code for item in result.candidates})
        self.assertEqual(3, result.quality["observation_count"])

    def test_optional_theme_evidence_and_v1_classification_strategy_contract(self):
        frames = tuple(_frame(second, self.codes, (1900,) + (100,) * 19,
                              changes=(100,) + (-100,) * 19) for second in (0, 20, 40))
        summary = MatureBreakoutSummary(4, 3, 1)
        evidence = {"breakout_summary": summary,
                    "breakout_available_at": datetime.fromisoformat(frames[-1].available_at),
                    "breakout_input_ref": "outcomes-r1"}
        result = self.context(frames, **evidence)
        features = compute_market_research_features(frames, summary, PARAMETERS)
        self.assertEqual(features, result.features)
        self.assertEqual(classify_market(features), result.classification)
        self.assertEqual("SINGLE_LEADER", result.classification.market_type)
        assessment = assess_market_for_strategy(result.classification, strategy_id="test/v1",
                                                preferred_types=("SINGLE_LEADER",))
        self.assertEqual("SUPPORTED", assessment.tradability)
        self.assertEqual("OBSERVED", result.theme_evidence["status"])
        self.assertEqual("theme-v1", result.theme_evidence["revision_id"])
        self.assertIn("theme-v1", result.input_refs)
        self.assertEqual(PARAMETERS, result.parameters)
        self.assertIn("outcomes-r1", result.input_refs)
        stale = self.context(frames, **evidence,
                             as_of=datetime.fromisoformat(frames[-1].available_at) + timedelta(seconds=40))
        self.assertEqual("STALE", stale.theme_evidence["status"])
        self.assertIsInstance(json.loads(json.dumps(result.to_dict())), dict)

    def test_future_or_unattributed_breakout_results_cannot_supply_v1_classification(self):
        frames = tuple(_frame(second, self.codes, (1900,) + (100,) * 19,
                              changes=(100,) + (-100,) * 19) for second in (0, 20, 40))
        summary = MatureBreakoutSummary(4, 3, 1)
        cutoff = datetime.fromisoformat(frames[-1].available_at)
        result = self.context(frames, breakout_summary=summary, breakout_available_at=cutoff + timedelta(seconds=1),
                              breakout_input_ref="future-outcomes")
        self.assertEqual("UNKNOWN", result.classification.market_type)
        self.assertEqual("FUTURE", result.quality["breakout_evidence_status"])
        self.assertNotIn("future-outcomes", result.input_refs)
        self.assertEqual("LEADER_CONCENTRATION", result.pattern)
        with self.assertRaisesRegex(ValueError, "requires available_at and input_ref"):
            self.context(frames, breakout_summary=summary)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.context(frames, breakout_summary=summary, breakout_available_at=cutoff.replace(tzinfo=None),
                         breakout_input_ref="naive-outcomes")

    def test_concentration_decreases_and_zero_denominator_remain_explicit(self):
        frames = (self.frame(0), self.frame(20, values=(1900,) + (100,) * 19), self.frame(40))
        result = self.context(frames)
        self.assertEqual("DECREASED", result.changes["concentration_direction"])
        self.assertEqual(-450_000, result.changes["top1_concentration_delta_ppm"])
        zero = self.context(tuple(self.frame(second, values=(0,) * 20) for second in (0, 20, 40)))
        self.assertEqual("UNKNOWN", zero.pattern)
        self.assertIsNone(zero.features.top1_concentration_ppm)
        self.assertEqual("RETAINED", zero.candidates[0].state)

    def test_clean_recent_cut_does_not_erase_earlier_membership_uncertainty(self):
        frames = (self.frame(0, quality="partial"), self.frame(20),
                  self.frame(40, codes=("000021", *self.codes[1:])), self.frame(60))
        result = self.context(frames)
        self.assertEqual("COMPLETE", result.quality["status"])
        self.assertEqual("PARTIAL", result.quality["candidate_history_status"])
        self.assertEqual("UNKNOWN", next(item for item in result.candidates if item.code == "000001").state)

    def test_runtime_sized_history_and_normalized_codes_preserve_observation_counts(self):
        frames = tuple(self.frame(index * 20, codes=tuple("A" + code for code in self.codes)
                                  if index % 2 else self.codes) for index in range(1000))
        result = self.context(frames)
        self.assertEqual(1000, result.quality["observation_count"])
        self.assertEqual(20, len(result.candidates))
        self.assertTrue(all(item.presence_count == 1000 and item.state == "RETAINED" for item in result.candidates))
        self.assertEqual(1000, len(next(item for item in result.candidates if item.code == "000001").input_refs))


if __name__ == "__main__":
    unittest.main()


class NativeMarketClockTests(unittest.TestCase):
    def test_native_market_clock_is_kst_before_epoch_storage_and_ram_reads(self):
        import asyncio, tempfile
        from pathlib import Path
        from unittest.mock import MagicMock, patch
        from kiwoom_monitor.central_server.app import create_app
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.central_server.config import CentralServerSettings
        from kiwoom_monitor.central_server.market_observations import KST
        expected=datetime(2026,10,13,9,0,40,tzinfo=KST)
        # server_now's legacy contract is naive KST; aware UTC is also accepted.
        for clock in (expected.replace(tzinfo=None), expected.astimezone(timezone.utc)):
            with self.subTest(clock=clock), tempfile.TemporaryDirectory() as directory:
                store=SQLiteQueryStore(Path(directory)/'clock.sqlite3')
                client=MagicMock();client.server_now.return_value=clock
                client.get_access_token.side_effect=AssertionError('clock test requested broker token')
                client.request.side_effect=AssertionError('clock test requested broker data')
                settings=CentralServerSettings(f"sqlite:///{Path(directory)/'clock.sqlite3'}",'private-token',
                    kiwoom_app_key='fixture-key',kiwoom_secret_key='fixture-secret',
                    autonomous_top20_enabled=False,market_event_collection_enabled=True)
                with patch('kiwoom_monitor.central_server.app.create_query_store',return_value=store), \
                     patch('kiwoom_monitor.infrastructure.kiwoom_rest.KiwoomRestClient',return_value=client):
                    app=create_app(settings)
                try:
                    collector=app.state.realtime_collector
                    self.assertEqual(KST,collector._now_provider().tzinfo)
                    self.assertEqual(expected,collector._now_provider())
                    self.assertEqual(expected,app.state.market_event_service._now())
                    collector.set_ram_observation_retention(20,owner=self)
                    collector._apply_trade_source_approval({('005930','KRX')},expected,reset_all=True)
                    collector._publish_parsed({'trnm':'REAL','data':[{'type':'0B','item':'005930',
                        'values':{'10':'100','13':'2','15':'2','20':'090040'}}]})
                    ram=collector.ram_observation_snapshot(('005930',),seconds=20)
                    self.assertEqual(expected.isoformat(),ram['decision_time'])
                    self.assertEqual(1,len(ram['rows']))
                    self.assertEqual(expected.timestamp(),ram['rows'][0]['available_at'])
                    before=ram['rows'][0]
                    asyncio.run(collector._flush_snapshots())
                    rows=store.load_second_trade_bars(('005930',),'2026-10-13',market='KRX',
                        start_second='09:00:00',end_second='09:00:59',available_at=expected.timestamp())
                    self.assertEqual([],rows)
                    self.assertEqual((100,2,200),(before['price_won'],before['volume'],before['trade_value_won']))
                    self.assertEqual('0B',ram['input_resolution'])
                    client.request.assert_not_called();client.get_access_token.assert_not_called()
                finally:store.close()
