from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts import inspect_expanded_training_baseline as diagnostic
from scripts import summarize_historical_baseline_results as existing
from tests.unit import test_historical_loss_review as fixtures


class ExpandedDiagnosticTests(unittest.TestCase):
    def cohort(self):
        cases = [{'role': 'TRAIN', 'outcome_date': '2024-09-03'},
                 {'role': 'TRAIN', 'outcome_date': '2024-09-04'}]
        return ({'state': 'COMPLETED', 'date_count': 2, 'batch_count': 1, 'batch_reviews': [{}]},
                {'batches': [{'request': 'request.json', 'cases': cases}]},
                {'cohort': cases, 'requests': ['request.json']})

    def test_validation_rejected_before_payload_or_native_query(self):
        index, prepared, plan = self.cohort()
        prepared['batches'][0]['cases'][1]['role'] = 'VALIDATION'
        with self.assertRaisesRegex(ValueError, 'TRAIN 외'):
            diagnostic.validate_cohort(index, prepared, plan)

    def test_duplicate_dates_incomplete_results_and_reordered_requests_rejected(self):
        for change in ('duplicate', 'incomplete', 'request'):
            index, prepared, plan = deepcopy(self.cohort())
            if change == 'duplicate':
                prepared['batches'][0]['cases'][1]['outcome_date'] = '2024-09-03'
            elif change == 'incomplete':
                index['state'] = 'RUNNING'
            else:
                plan['requests'] = ['other.json']
            with self.subTest(change=change), self.assertRaises(ValueError):
                diagnostic.validate_cohort(index, prepared, plan)

    def test_validation_request_cannot_reach_dataset_or_database(self):
        request = SimpleNamespace(request=SimpleNamespace(evaluation=SimpleNamespace(
            folds=[SimpleNamespace(role='VALIDATION')])))
        with patch.object(diagnostic, 'load_development_validation_request', return_value=request), \
             patch.object(diagnostic, 'load_research_input') as source, \
             patch.object(diagnostic, 'ResearchRepository') as repository:
            with self.assertRaisesRegex(ValueError, 'TRAIN 외'):
                diagnostic.inspect_batch({'request': 'request.json'}, {}, 'science', lambda: None)
            source.assert_not_called()
            repository.assert_not_called()

    def test_session_relative_buckets_handle_delayed_open(self):
        for hour, minute, expected in ((10, 0, '개장 후 0~60분'), (11, 0, '개장 후 60~180분'),
                                      (15, 20, '개장 후 300분~진입 마감')):
            self.assertEqual(expected, diagnostic.session_bucket(
                {'opened_at': f'2024-11-14T{hour:02}:{minute:02}:00+09:00'}))
        with self.assertRaisesRegex(ValueError, '연속장 밖'):
            diagnostic.session_bucket({'opened_at': '2024-09-03T15:20:00+09:00'})
        self.assertEqual('개장 후 60~180분', diagnostic.session_bucket(
            {'opened_at': '2024-09-03T01:00:00+00:00'}))

    def test_fixed_strength_boundaries_do_not_duplicate_or_drop_trades(self):
        self.assertEqual(['0~10bp', '10~25bp', '25~50bp', '50bp 이상'],
                         [diagnostic.strength_bucket(value) for value in (0, 10, 25, 50)])
        self.assertEqual(["0~10bp", "10~25bp", "25~50bp", "50bp 이상"],
                         [diagnostic.strength_bucket(value) for value in (9, 24, 49, 51)])
        with self.assertRaises(ValueError):
            diagnostic.strength_bucket(-1)

    def row(self, *, risk=False):
        trade, events, decisions, bars, strategy, cost = fixtures.TrainingTradePathTests().fixture(risk=risk)
        path = existing._training_trade_path(trade, events, decisions, bars, strategy, cost)
        # 기존 경로 fixture의 토요일 시각을 실제 거래일 집계 시각으로만 바꾼다.
        return {**trade, **path, 'outcome_date': '2024-09-03',
                'opened_at': '2024-09-03T09:03:00+09:00'}

    def test_risk_exit_unknown_horizon_and_missing_exit_signal_are_not_zero_observations(self):
        report = diagnostic.summarize_trades([self.row(risk=True)])
        final = report['horizon_summary'][-1]
        self.assertEqual({'RISK_EXIT_BAR_EXCLUDED': 1}, final['status_counts'])
        self.assertIsNone(final['observed_median_close_delta_bps'])
        self.assertEqual(1, report['totals']['exit_signal_unavailable_count'])
        self.assertEqual(0, report['exit_raw_decomposition_count'])

    def test_each_group_sums_to_native_amounts_without_counting_reason_twice(self):
        row = self.row()
        row['exit_reasons'] = ['max_hold_reached', 'other_reason']
        report = diagnostic.summarize_trades([row, self.row(risk=True)])
        self.assertEqual(2, report['totals']['closed_trade_count'])
        for dimension in ('청산 사유', '진입 시간', '결과 월', '돌파 강도'):
            groups = [group for group in report['groups'] if group['dimension'] == dimension]
            self.assertEqual(2, sum(group['closed_trade_count'] for group in groups))
            self.assertEqual(report['totals']['net_pnl_won'], sum(group['net_pnl_won'] for group in groups))


if __name__ == '__main__':
    unittest.main()
