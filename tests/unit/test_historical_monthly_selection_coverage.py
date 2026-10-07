from __future__ import annotations

import unittest

from scripts.audit_historical_monthly_selection_coverage import audit


def fixtures():
    values = (
        ('2024-09-02', 40, 0, 15000),
        ('2024-09-03', 45, 1, 15800),
        ('2024-10-01', 38, 2, 12500),
        ('2024-10-02', 42, 0, 15500),
    )
    readiness = {'version': 'historical_minute_readiness_audit/v1', 'cases': [
        {'selection_date': day, 'outcome_date': day, 'candidate_codes': count,
         'one_minute_labelled_bars': bars, 'readiness': 'READY' if not excluded else 'BLOCKED'}
        for day, count, excluded, bars in values
    ]}
    projection = {'version': 'historical_minute_exclusion_projection/v1',
                  'source_audit_sha256': 'a' * 64, 'cases': [
        {'selection_date': day, 'outcome_date': day, 'candidate_codes_original': count,
         'candidate_codes_excluded': excluded, 'candidate_codes_after_exclusion': count - excluded,
         'excluded_codes': [f'{index:06d}' for index in range(excluded)]}
        for day, count, excluded, _ in values
    ]}
    selection = {'version': 'historical_monthly_case_selection/v1',
                 'source_projection_sha256': 'b' * 64,
                 'oos': {'status': 'SEALED', 'results_included': False},
                 'cases': [dict(projection['cases'][index], role=role)
                           for index, role in ((0, 'TRAIN'), (2, 'VALIDATION'))]}
    return readiness, projection, selection


class HistoricalMonthlySelectionCoverageTests(unittest.TestCase):
    def test_counts_selected_dates_without_outcomes_or_oos(self):
        report = audit(*interleaved(fixtures()))
        self.assertEqual(report['selected_dates']['date_count'], 2)
        self.assertEqual(report['selected_dates']['candidate_days_after_exclusion'], 76)
        self.assertEqual(report['unselected_dates']['candidate_days_after_exclusion'], 86)
        self.assertEqual(report['monthly_comparison'][0]['selected_count_percentile_le'], 0.5)
        self.assertFalse(report['outcomes_read'])
        self.assertFalse(report['oos_results_read'])

    def test_source_binding_and_calendar_first_are_required(self):
        readiness, projection, selection = fixtures()
        projection['source_audit_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'bind'):
            audit(*interleaved((readiness, projection, selection)))
        projection['source_audit_sha256'] = 'a' * 64
        selection['cases'][0] = dict(projection['cases'][1], role='TRAIN')
        with self.assertRaisesRegex(ValueError, 'first development date'):
            audit(*interleaved((readiness, projection, selection)))


def interleaved(documents):
    hashes = ('a' * 64, 'b' * 64, 'c' * 64)
    return tuple(item for document, digest in zip(documents, hashes) for item in (document, digest))


if __name__ == '__main__':
    unittest.main()
