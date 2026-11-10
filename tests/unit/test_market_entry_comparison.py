from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from kiwoom_monitor.application.market_entry import REGIME_TRIAL, MarketEntryTrial, market_entry_context
from kiwoom_monitor.infrastructure.research_data_source import write_frozen_research_bundle
from kiwoom_monitor.research_process import execute_process_request, research_process_request_from_document
from tests.unit.test_research_bundle_execution import PROFILE, child, rows_for


FAMILIES = ('breakout_study/v1', 'close_betting/v1', 'ipo_close/v1', 'ipo_breakout/v1', 'panic_rebound/v1')
FRIDAY, MONDAY = '2026-09-18', '2026-09-21'


def at(day, clock):
    return f'{day}T{clock}:00+09:00'


def study_document(*, supportive):
    row = {'revision_id': 'fixture:market:friday', 'available_at': at(FRIDAY, '09:00'),
           'expires_at': at(FRIDAY, '15:30'), 'premarket_direction': 'bullish',
           'premarket_available_at': at(FRIDAY, '08:00'), 'turnover_million_won': 20_000_000,
           'net_advancers': 550 if supportive else -550, 'theme_concentration_ppm': 450_000,
           'sustained_rise_ppm': 650_000}
    policy = {'directions': ['bullish'], 'ranges': {'turnover_million_won': [10_000_000, None],
        'net_advancers': [1, None], 'theme_concentration_ppm': [300_000, None], 'sustained_rise_ppm': [500_000, None]},
        'match_bps': 5_000, 'miss_bps': 0, 'unknown_bps': 0}
    return {'version': 'market_entry_trial/v1', 'observations': [row],
            'strategy_policies': {family: policy for family in FAMILIES}}


def regime_document(size_bps, exit_regimes, *, changed_at='09:13', next_regime='DISPERSED_ROTATION'):
    def observation(name, start, end, regime):
        return dict(revision_id=f'fixture:regime:{name}', available_at=at(FRIDAY, start),
                    expires_at=at(FRIDAY, end), regime=regime, evidence_refs=[f'fixture:source:{name}'])

    unchanged = dict(size_bps=10_000, exit_on_regimes=[])
    return dict(version=REGIME_TRIAL, strategy_policies={FAMILIES[0]: {
        'UNKNOWN': unchanged, 'DISPERSED_ROTATION': unchanged,
        'THEME_LED': dict(size_bps=size_bps, exit_on_regimes=exit_regimes)}}, observations=[
            observation('theme', '09:00', changed_at, 'THEME_LED'),
            observation('changed', changed_at, '15:30', next_regime)])


def _bars(day, tape):
    rows = rows_for(date.fromisoformat(day), len(tape))
    rows[0].update(available_at=at(day, '08:58'), observation_key=at(day, '08:58'))
    for row, (clock, o, h, low, c) in zip(rows[1:], tape):
        start = at(day, clock)
        end = (datetime.fromisoformat(start) + timedelta(minutes=1)).isoformat()
        row.update(observation_key=start, available_at=end)
        row['payload'].update(bar_start=start, bar_end=end, open=o, high=h, low=low, close=c,
                              trade_value_million_won=100, volume=1000)
    return rows


def _flat(clock, price):
    return (clock, price, price, price, price)


def request_for(root, family, case):
    base = root / family.split('/')[0]
    source = base / 'source'
    listing = family.startswith('ipo_')
    if family == 'breakout_study/v1':
        tape = [(f'09:{i:02}', 10_000, 10_100, 10_000, 10_000) for i in range(11)] + [
            ('09:11', 10_200, 11_000, 10_200, 10_400), _flat('09:12', 10_500),
            _flat('09:13', 10_000), _flat('09:14', 9_950), _flat('09:15', 9_900)]
        if case == 'profit':
            tape[-4:] = [('09:12', 10_500, 10_750, 10_500, 10_750), _flat('09:13', 10_800),
                         _flat('09:14', 9_900), _flat('09:15', 9_850)]
        policy = dict(variant='B0', quantity=4, exit_at_kst='15:18', theme_id=None,
                      theme_role=None, exit_on_dependency_loss=False)
    elif family == 'panic_rebound/v1':
        tape = [(f'09:{i:02}', p, p, p - 50, p) for i, p in enumerate((10_000, 9_900, 9_800, 9_000))] + [
            ('09:04', 9_100, 9_800, 9_050, 9_700), _flat('09:05', 9_650), _flat('09:06', 9_650)]
        policy = dict(variant='P0', lookback_minutes=3, decline_bps=1000, minimum_turnover_won=3_000_000,
                      max_input_age_seconds=10, rebound_target_bps=500, max_hold_minutes=5)
    else:
        tape = [_flat(clock, price) for clock, price in (('15:14', 10_000), ('15:15', 10_000), ('15:16', 10_500),
            ('15:17', 10_500), ('15:18', 10_600), ('15:19', 10_600), ('15:29', 11_000))]
        policy = dict(quantity=4, entry_window_minutes=10, lookback_bars=2, minimum_close_return_bps=200,
                      adverse_gap_bps=500, protection_loss_bps=1000, exit_minutes_after_open=8,
                      ipo_reduction_quantity=2 if listing else None, listing_phases=['listing_day'] if listing else [])
        if family == 'ipo_breakout/v1':
            policy = dict(quantity=4, lookback_bars=2, breakout_buffer_bps=100, protection_loss_bps=1000,
                          exit_minutes_before_close=1, listing_phases=['listing_day'])
    if not (source / 'manifest.json').exists():
        paths = (child(source, date.fromisoformat(FRIDAY), _bars(FRIDAY, tape)), child(source, date.fromisoformat(MONDAY),
                  _bars(MONDAY, [_flat(clock, p) for clock, p in (('10:00', 10_300), ('10:01', 10_200),
                                                                  ('10:02', 8_900), ('10:03', 8_900))])))
        write_frozen_research_bundle(source, paths)

    strategy = {'capital_won': 1_000_000, 'policy': policy}
    if family == 'panic_rebound/v1':
        strategy.update(quantity=4, news_proofs=[dict(symbol='005930', source='fixture:reviewed-news',
            assessment_id='fixture:assessment', revision='fixture:assessment-r1', effective_at=at(FRIDAY, '09:02'),
            available_at=at(FRIDAY, '09:03'), disposition='supported_nonfundamental', evidence_refs=['fixture:news'])])
    document = {'mode': 'single_run', 'family': family, 'dataset': str(source), 'database': str(base / f'{case}.sqlite3'),
                'runs_dir': str(base / case), 'session_profile': PROFILE, 'strategy': strategy,
                'resource_budget': {'memory_mb': 512, 'cpu_duty_percent': 100},
                'execution': {'version': 'completed_close_then_next_open/v1', 'same_bar_path_version': 'no_intrabar_fill/v1',
                              'initial_cash_won': 1_000_000},
                'evaluation': {'version': 'chronological_holdout/v1', 'minimum_closed_trades': 1, 'minimum_active_days': 1,
                    'folds': [{'name': 'comparison', 'role': 'VALIDATION', 'start': at(FRIDAY, '09:00'),
                               'end': at(MONDAY, '16:00')}]}}
    if family in FAMILIES:
        def session(day, start, end, name='KRX_REGULAR', phase='CONTINUOUS'):
            return {'starts_at': at(day, start), 'ends_at': at(day, end), 'session': name,
                    'phase': phase, 'support': 'SUPPORTED', 'schedule_version': 'fixture/v1'}
        windows = [(FRIDAY, [session(FRIDAY, '09:00', '15:20'),
                    session(FRIDAY, '15:29', '15:30', 'KRX_CLOSING_AUCTION', 'AUCTION_ORDER_ENTRY')]),
                   ('2026-09-19', []), ('2026-09-20', []), (MONDAY, [session(MONDAY, '10:00', '15:20')])]
        calendar = [dict(venue='KRX', trading_date=day, available_at=at(FRIDAY, '08:00'),
                         revision_id=day, evidence_ref='fixture-calendar', sessions=sessions) for day, sessions in windows]
        listings = [dict(symbol='005930', listing_date=FRIDAY, listing_type='ipo', available_at=at(FRIDAY, '08:00'),
                         source='fixture-listing', revision='listed', evidence_id='listing-before-entry',
                         completeness='complete')] if listing else []
        reference = base / 'references.json'
        data = json.dumps(dict(version='strategy-reference/v1', calendar=calendar, listings=listings)).encode()
        reference.write_bytes(data)
        document['strategy_references'] = {'path': str(reference), 'sha256': hashlib.sha256(data).hexdigest()}
    return document


class MarketEntryComparisonTests(unittest.TestCase):
    def test_breakout_takes_one_tranche_at_predeclared_resistance_and_protects_remainder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = research_process_request_from_document(request_for(root, FAMILIES[0], 'profit'), root)
            document = regime_document(10_000, [])
            level = dict(symbol='005930', available_at=at(FRIDAY, '09:11'), revision_id='resistance:rev1',
                         reference_price=10_700, quantity=2, reason_ref='fixture:prior-resistance')
            trial = MarketEntryTrial({**document, 'profit_takes': [level]})
            result = execute_process_request(request, market_entry_trial=trial)
            baseline, profit = result['baseline'], result['with_market']
            self.assertEqual([4], [row['quantity'] for row in baseline['exits']])
            self.assertEqual([(2, 2), (2, 0)], [(row['quantity'], row['position_quantity_after'])
                                               for row in profit['exits']])
            self.assertEqual(21_600, profit['exits'][0]['notional_won'])
            self.assertEqual(baseline['exits'][0]['at'], profit['exits'][1]['at'])
            self.assertGreater(profit['folds'][0]['net_realized_pnl_won'], baseline['folds'][0]['net_realized_pnl_won'])
            late = MarketEntryTrial({**document, 'profit_takes': [{**level, 'available_at': at(FRIDAY, '09:13')}]})
            result = execute_process_request(request, market_entry_trial=late)
            self.assertEqual(result['baseline']['exits'], result['with_market']['exits'])

    def test_breakout_uses_explicit_size_and_exits_on_new_market_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = research_process_request_from_document(request_for(root, FAMILIES[0], 'regime'), root)
            trial = MarketEntryTrial(regime_document(5_000, ['DISPERSED_ROTATION']))
            self.assertEqual('UNKNOWN', trial.assess(FAMILIES[0], datetime.fromisoformat(at(FRIDAY, '08:59')))['status'])
            self.assertEqual('THEME_LED', trial.assess(FAMILIES[0], datetime.fromisoformat(at(FRIDAY, '09:12')))['status'])
            self.assertEqual('DISPERSED_ROTATION', trial.assess(FAMILIES[0], datetime.fromisoformat(at(FRIDAY, '09:13')))['status'])
            result = execute_process_request(request, market_entry_trial=trial)
            self.assertFalse(result['order_authorized'])
            self.assertEqual('market_entry_comparison/v2', result['kind'])
            base, filtered = result['baseline'], result['with_market']
            self.assertEqual([4], [row['quantity'] for row in base['entries']])
            self.assertEqual([2], [row['quantity'] for row in filtered['entries']])
            self.assertEqual(base['entries'][0]['at'], filtered['entries'][0]['at'])
            self.assertEqual('09:13', datetime.fromisoformat(filtered['exits'][0]['at']).astimezone(
                datetime.fromisoformat(at(FRIDAY, '09:00')).tzinfo).strftime('%H:%M'))
            self.assertLess(datetime.fromisoformat(filtered['exits'][0]['at']), datetime.fromisoformat(base['exits'][0]['at']))
            self.assertEqual(2, filtered['exits'][0]['quantity'])
            self.assertGreater(filtered['exits'][0]['cost_won'], 0)

    def test_breakout_regime_blocks_entries_without_overriding_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = research_process_request_from_document(request_for(root, FAMILIES[0], 'protection'), root)
            for changed_at, next_regime in [('09:14', 'DISPERSED_ROTATION'), ('09:13', 'UNKNOWN')]:
                trial = MarketEntryTrial(regime_document(10_000, ['DISPERSED_ROTATION'], changed_at=changed_at,
                                                        next_regime=next_regime))
                result = execute_process_request(request, market_entry_trial=trial)
                self.assertEqual(result['baseline']['exits'], result['with_market']['exits'])
            result = execute_process_request(request, market_entry_trial=MarketEntryTrial(regime_document(0, [])))
            self.assertEqual([], result['with_market']['entries'])
            self.assertEqual([], result['with_market']['exits'])

    def test_cut_excludes_future_market_revisions_and_missing_axes_stay_unknown(self):
        document = study_document(supportive=True)
        future = {**document['observations'][0], 'revision_id': 'later', 'available_at': at(FRIDAY, '10:00'), 'net_advancers': -400}
        trial = MarketEntryTrial({**document, 'observations': [future, *document['observations']]})
        assess = lambda hour: trial.assess(FAMILIES[0], datetime.fromisoformat(at(FRIDAY, hour)))
        self.assertEqual('unknown', assess('08:59')['status'])
        self.assertEqual('match', assess('09:59')['status'])
        self.assertEqual('miss', assess('10:00')['status'])
        self.assertEqual('unknown', assess('15:30')['status'])
        cut = datetime.fromisoformat(at(FRIDAY, '09:00'))
        actual = market_entry_context(cut, {}, {'kospi': {'observed_at': future['available_at'],
            'cumulative_trade_value_million_won': 10}}, None, max_age_seconds=90)
        self.assertEqual(4, len(actual['missing_axes']))
        turnover = {market: {'observed_at': cut.isoformat(), 'cumulative_trade_value_million_won': value}
                    for market, value in [('kospi', 100), ('kosdaq', 50)]}
        breadth = {market: {'observed_at': cut.isoformat(), 'advancing_count': up, 'declining_count': down}
                   for market, up, down in [('kospi', 60, 30), ('kosdaq', 20, 30)]}
        market = {'available_at': cut.isoformat(), 'expires_at': future['available_at'], 'evidence_refs': ('revision',),
                  'inferences': {'market_type': 'THEME_LED'},
                  'facts': {'leading_theme_concentration_ppm': 450_000, 'mature_breakout_success_ppm': 650_000}}
        context = market_entry_context(cut, {}, turnover, market, max_age_seconds=90, market_breadth=breadth)
        self.assertEqual((150, 20, 450_000, 650_000), tuple(context[key] for key in (
            'turnover_million_won', 'net_advancers', 'theme_concentration_ppm', 'sustained_rise_ppm')))
        self.assertIsNone(context['premarket_direction'])
        self.assertEqual('THEME_LED', context['observed_market_type'])
        expired = market_entry_context(datetime.fromisoformat(future['available_at']), {}, {}, market, max_age_seconds=90)
        self.assertEqual('UNKNOWN', expired['observed_market_type'])

    def test_paper_compares_four_strategies_and_both_ipo_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for family in FAMILIES:
                for supportive in (True, False):
                    with self.subTest(family=family, supportive=supportive):
                        request = research_process_request_from_document(request_for(root, family, str(supportive)), root)
                        result = execute_process_request(request, market_entry_trial=MarketEntryTrial(study_document(supportive=supportive)))
                        self.assertFalse(result['order_authorized'])
                        base, filtered = result['baseline'], result['with_market']
                        self.assertEqual([4], [row['quantity'] for row in base['entries']])
                        self.assertIsNotNone(base['folds'][0]['net_realized_pnl_won'])
                        self.assertEqual([2] if supportive else [], [row['quantity'] for row in filtered['entries']])
                        if supportive:
                            self.assertIsNotNone(filtered['folds'][0]['net_realized_pnl_won'])
                            self.assertEqual(base['entries'][0]['at'], filtered['entries'][0]['at'])
                        else:
                            self.assertEqual(0, filtered['entry_signals'])
