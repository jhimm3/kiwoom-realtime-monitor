from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.application.research_replay import replay_candidate_universe, ResearchReplayCursor
from kiwoom_monitor.application.market_research_features import market_frames_from_observations
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    load_frozen_research_export, load_research_input, write_frozen_research_bundle,
    research_observation_order,
)
from scripts.run_research import execute_research
from tests.unit.test_research_bundle import write_child
from tests.unit.test_research_execution import _execution, _strategy
from tests.unit.research_test_support import research_request_document as _request_document
from kiwoom_monitor.research_process import execute_process_request, load_research_process_request

KST = timezone(timedelta(hours=9))
PROFILE = 'krx-regular/v1'


def rows_for(day, count=8):
    start = datetime.combine(day, datetime.min.time(), tzinfo=KST).replace(hour=9)
    rows = [{'ordinal': 1, 'revision_id': f'rank-{day}', 'kind': 'top20_membership',
             'available_at': start.isoformat(), 'accepted_sequence': 1,
             'observation_key': start.isoformat(), 'payload': {'codes': ['005930']}}]
    for index in range(count):
        at = start + timedelta(minutes=index)
        end = at + timedelta(minutes=1)
        price = 1000 if index < 2 else 1020
        rows.append({'ordinal': index + 2, 'revision_id': f'bar-{day}-{index}',
                     'kind': 'minute_bar', 'venue': 'KRX', 'subject': '005930:KRX',
                     'observation_key': at.isoformat(), 'accepted_sequence': index + 2,
                     'available_at': (end + timedelta(seconds=2)).isoformat(),
                     'value_kind': 'actual', 'completeness': 'complete', 'payload': {
                         'market': 'KRX', 'code': '005930', 'bar_start': at.isoformat(),
                         'bar_end': end.isoformat(), 'open': price, 'high': price,
                         'low': price, 'close': price, 'volume': 100,
                         'trade_value_million_won': 1, 'window_closed': True,
                         'capture_quality': 'complete', 'finalization_source': 'timer'}})
    return rows


def child(root, day, rows):
    relative = write_child(root, day, rows)
    path = root / relative / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['kinds'] = ['ranking', 'top20_membership', 'minute_bar']
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return relative


def run(dataset, root, suffix):
    repo = ResearchRepository(root / f'{suffix}.sqlite3')
    events = []
    original = repo.append_execution_events
    def record(values):
        events.extend(values)
        return original(values)
    with patch.object(repo, 'append_execution_events', side_effect=record):
        result = execute_research(dataset, repo, root / suffix,
                                  replace(_strategy(), max_hold_minutes=10000),
                                  _execution(), session_profile=PROFILE)
    return result, events


class BundleExecutionTests(unittest.TestCase):
    def test_one_day_bundle_has_exact_daily_input_and_logical_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = child(root, date(2026, 9, 14), rows_for(date(2026, 9, 14)))
            write_frozen_research_bundle(root, (relative,))
            daily = load_frozen_research_export(root / relative)
            bundled = load_research_input(root, session_profile=PROFILE)
            self.assertEqual(daily, bundled)
            first, events = run(daily, root, 'daily')
            second, _ = run(bundled, root, 'bundle')
            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual(first.logical_result_hash, second.logical_result_hash)
            self.assertTrue(any(event.event_type == 'FILL' for event in events))

    def test_position_cash_and_costs_continue_without_daily_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = tuple(child(root, date(2026, 9, day), rows_for(date(2026, 9, day)))
                          for day in (14, 15))
            before = {path: (root / path / 'observations.jsonl').read_bytes() for path in paths}
            write_frozen_research_bundle(root, paths)
            dataset = load_research_input(root, session_profile=PROFILE)
            first, events = run(dataset, root, 'first')
            second, _ = run(dataset, root, 'repeat')
            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual(first.logical_result_hash, second.logical_result_hash)
            fills = [event for event in events if event.event_type == 'FILL']
            self.assertEqual(1, len(fills))
            marks = [event for event in events if event.event_type == 'MARK'
                     and event.occurred_at.startswith('2026-09-15')]
            self.assertTrue(marks)
            self.assertTrue(all(event.cash_after_won == fills[0].cash_after_won for event in marks))
            self.assertTrue(all(event.position_quantity_after == 10 for event in marks))
            censored = [event for event in events if event.event_type == 'POSITION_CENSORED']
            self.assertEqual(1, len(censored))
            self.assertTrue(censored[0].occurred_at.startswith('2026-09-15'))
            self.assertEqual(before, {path: (root / path / 'observations.jsonl').read_bytes() for path in paths})

    def test_unfilled_order_is_not_filled_at_next_days_open(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = (child(root, date(2026, 9, 14), rows_for(date(2026, 9, 14), count=3)),
                     child(root, date(2026, 9, 15), rows_for(date(2026, 9, 15), count=1)))
            write_frozen_research_bundle(root, paths)
            _, events = run(load_research_input(root, session_profile=PROFILE), root, 'pending')
            self.assertTrue(any(event.event_type == 'ORDER_SUBMITTED' for event in events))
            self.assertFalse(any(event.event_type == 'FILL' for event in events))
            self.assertTrue(any(event.reason == 'session_boundary_before_fill' for event in events))

    def test_bundle_reader_rejects_wrong_profile_and_legacy_day_still_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = child(root, date(2026, 9, 14), rows_for(date(2026, 9, 14)))
            write_frozen_research_bundle(root, (relative,))
            for profile in (None, 'krx-after/v1'):
                with self.assertRaisesRegex(ValueError, 'matching explicit'):
                    load_research_input(root, session_profile=profile)
            self.assertEqual(load_frozen_research_export(root / relative), load_research_input(root / relative))

    def test_same_revision_is_consumed_once_and_original_day_ordinals_are_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = rows_for(date(2026, 9, 14))[0]
            first = child(root, date(2026, 9, 14), [shared])
            second_rows = rows_for(date(2026, 9, 15), count=0) + [dict(shared, ordinal=2)]
            second = child(root, date(2026, 9, 15), second_rows)
            write_frozen_research_bundle(root, (first, second))
            dataset = load_research_input(root, session_profile=PROFILE)
            self.assertEqual(2, len(dataset.observations))
            self.assertEqual([1, 1], [row['ordinal'] for row in dataset.observations])
            self.assertEqual('unknown', dataset.manifest['quality_summary']['recording_gap'])

    def test_mixed_timezone_rank_order_uses_actual_availability(self):
        earlier = {'kind': 'top20_membership', 'revision_id': 'early',
                   'available_at': '2026-09-14T09:00:00+09:00', 'payload': {'codes': ['005930']}}
        later = {'kind': 'top20_membership', 'revision_id': 'later',
                 'available_at': '2026-09-14T00:01:00+00:00', 'payload': {'codes': ['000660']}}
        self.assertEqual(['early', 'later'], [frame.revision_id for frame in replay_candidate_universe([later, earlier], chronological=True)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = rows_for(date(2026, 9, 14), count=8)
            rows[0]['payload']['codes'] = ['000660']
            rows.insert(1, dict(rows[0], revision_id='later-membership',
                                available_at='2026-09-14T00:00:30+00:00',
                                payload={'codes': ['005930']}))
            for ordinal, row in enumerate(rows, start=1):
                row['ordinal'] = ordinal
                row['accepted_sequence'] = ordinal
            uniform = [dict(row, available_at=datetime.fromisoformat(
                row['available_at']).astimezone(timezone.utc).isoformat()) for row in rows]
            results, decisions = {}, {}
            frozen = {}
            for label, observations in (('uniform', uniform), ('mixed', rows)):
                input_root = root / (label + '-input')
                relative = child(input_root, date(2026, 9, 14), observations)
                for name in ('manifest.json', 'observations.jsonl', 'theme_snapshots.jsonl'):
                    path = input_root / relative / name
                    frozen[path] = path.read_bytes()
                dataset = load_frozen_research_export(input_root / relative)
                results[label], _ = run(dataset, root, label)
                native = ResearchRepository(root / f'{label}.sqlite3', read_only=True)
                decisions[label] = native.load_evaluations(results[label].run_id)
            self.assertEqual(8, sum(row['kind'] == 'minute_bar' for row in rows))
            self.assertEqual(8, results['uniform'].decision_count)
            self.assertEqual(8, len(decisions['uniform']))
            self.assertEqual(8, results['mixed'].decision_count)
            self.assertEqual(8, len(decisions['mixed']))
            self.assertEqual([datetime.fromisoformat(row['decided_at']).astimezone(timezone.utc)
                              for row in decisions['uniform']],
                             [datetime.fromisoformat(row['decided_at']).astimezone(timezone.utc)
                              for row in decisions['mixed']])
            self.assertEqual(frozen, {path: path.read_bytes() for path in frozen})

    def test_app_process_request_accepts_bundle_and_rejects_wrong_profile_before_db_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_root = root / 'dataset'
            paths = tuple(child(dataset_root, date(2026, 9, day), rows_for(date(2026, 9, day)))
                          for day in (14, 15))
            write_frozen_research_bundle(dataset_root, paths)
            document = _request_document()
            document.update(mode='single_run', session_profile='krx-after/v1')
            request_file = root / 'request.json'
            request_file.write_text(json.dumps(document), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'matching explicit'):
                execute_process_request(load_research_process_request(request_file))
            self.assertFalse((root / document['database']).exists())
            document['session_profile'] = PROFILE
            request_file.write_text(json.dumps(document), encoding='utf-8')
            result = execute_process_request(load_research_process_request(request_file))
            self.assertEqual('ok', result['status'])
            self.assertEqual('single_run', result['kind'])

    def test_pre_start_theme_and_period_changes_are_applied_only_when_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prior = {'snapshot_id': 'prior', 'available_at': '2026-09-13T12:00:00+09:00', 'document': {}}
            future = {'snapshot_id': 'future', 'available_at': '2026-09-15T12:00:00+09:00', 'document': {}}
            def ranking(day, hour, ordinal=1):
                return {'ordinal': ordinal, 'revision_id': f'rank-{day}-{hour}', 'kind': 'ranking',
                        'available_at': f'2026-09-{day:02d}T{hour:02d}:00:00+09:00',
                        'payload': {'query_type': '5', 'items': [{'stk_cd': '005930'}]}}
            first = write_child(root, date(2026, 9, 14), [ranking(14, 9)], theme=prior)
            second = write_child(root, date(2026, 9, 15), [ranking(15, 9), ranking(15, 13, 2)], theme=future)
            write_frozen_research_bundle(root, (first, second))
            dataset = load_research_input(root, session_profile=PROFILE)
            # The existing market reader selects the latest snapshot available at each observation.
            frames = market_frames_from_observations(dataset.observations, dataset.theme_snapshots)
            self.assertTrue(frames)
            self.assertEqual(['prior', 'prior', 'future'], [frame.theme_revision_id for frame in frames])

    def test_late_correction_and_ingest_tie_are_only_visible_after_their_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = rows_for(date(2026, 9, 14), count=3)
            original = rows[-1]
            correction = dict(original, ordinal=5, revision_id='correction', accepted_sequence=5,
                              payload=dict(original['payload'], close=1030, high=1030))
            first = child(root, date(2026, 9, 14), rows + [correction])
            second = child(root, date(2026, 9, 15), rows_for(date(2026, 9, 15), count=0))
            write_frozen_research_bundle(root, (first, second))
            frozen = {root / 'manifest.json': (root / 'manifest.json').read_bytes()}
            for relative in (first, second):
                for name in ('manifest.json', 'observations.jsonl', 'theme_snapshots.jsonl'):
                    path = root / relative / name
                    frozen[path] = path.read_bytes()
            seen = []
            advance = ResearchReplayCursor.advance_for_observation
            def record(cursor, observation):
                current, bars, universe = advance(cursor, observation)
                seen.append([bar.revision_id for bar in cursor.latest.values()]
                            + [frame.revision_id for frame in universe])
                return current, bars, universe
            with patch.object(ResearchReplayCursor, 'advance_for_observation', new=record):
                multi_result, _ = run(load_research_input(root, session_profile=PROFILE), root, 'correction')
            self.assertNotIn('correction', seen[2])
            self.assertIn(original['revision_id'], seen[2])
            self.assertIn('correction', seen[3])
            self.assertTrue(all('rank-2026-09-15' not in ids for ids in seen))
            self.assertEqual(4, multi_result.decision_count)
            multi_native = ResearchRepository(root / 'correction.sqlite3', read_only=True)
            self.assertEqual(4, len(multi_native.load_evaluations(multi_result.run_id)))
            expected_seen = list(seen)
            daily = load_frozen_research_export(root / first)
            one_root = root / 'one-day-input'
            one_child = child(one_root, date(2026, 9, 14), rows + [correction])
            write_frozen_research_bundle(one_root, (one_child,))
            one_day = load_research_input(one_root, session_profile=PROFILE)
            self.assertEqual(daily, one_day)
            for path in (one_root / 'manifest.json', *(one_root / one_child / name for name in
                         ('manifest.json', 'observations.jsonl', 'theme_snapshots.jsonl'))):
                frozen[path] = path.read_bytes()
            results = {}
            for label, dataset in (('daily', daily), ('one-day-bundle', one_day)):
                seen.clear()
                with patch.object(ResearchReplayCursor, 'advance_for_observation', new=record):
                    results[label], _ = run(dataset, root, label)
                self.assertEqual(expected_seen, seen)
                self.assertEqual(4, results[label].decision_count)
                native = ResearchRepository(root / f'{label}.sqlite3', read_only=True)
                self.assertEqual(4, len(native.load_evaluations(results[label].run_id)))
            self.assertEqual(results['daily'].run_id, results['one-day-bundle'].run_id)
            self.assertEqual(results['daily'].logical_result_hash,
                             results['one-day-bundle'].logical_result_hash)
            self.assertEqual(frozen, {path: path.read_bytes() for path in frozen})

    def test_final_market_regime_uses_latest_actual_rank_and_native_mature_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stamps = ('2026-09-14T09:05:20+09:00', '2026-09-14T00:06:20+00:00',
                      '2026-09-14T00:07:20+00:00')
            rankings = [{'kind': 'ranking', 'subject': '5', 'revision_id': f'rank-market-{index}',
                'observation_key': stamp, 'available_at': stamp, 'effective_at': stamp,
                'accepted_sequence': index + 10, 'venue': 'COMBINED', 'completeness': 'complete',
                'payload': {'query_type': '5', 'items': [{'stk_cd': '005930', 'trde_prica': '100', 'flu_rt': '+1.00'}]}}
                for index, stamp in enumerate(stamps)]
            cutoff = max(datetime.fromisoformat(stamp).astimezone(timezone.utc) for stamp in stamps)
            uniform = [dict(row, available_at=datetime.fromisoformat(row['available_at']).astimezone(timezone.utc).isoformat())
                       for row in rankings]
            counts = {}
            for label, ranks in (('uniform', uniform), ('mixed', rankings)):
                rows = rows_for(date(2026, 9, 14), count=8) + ranks
                for ordinal, row in enumerate(rows, start=1):
                    row['ordinal'] = ordinal
                input_root = root / (label + '-input')
                relative = child(input_root, date(2026, 9, 14), rows)
                frozen = {path: path.read_bytes() for path in (input_root / relative).iterdir() if path.is_file()}
                dataset = load_frozen_research_export(input_root / relative)
                result, _ = run(dataset, root, label)
                native = ResearchRepository(root / f'{label}.sqlite3', read_only=True)
                labels = native.load_outcome_labels(result.run_id)
                completed = [row for row in labels if row['horizon_seconds'] == 180 and row['status'] == 'COMPLETE'
                    and isinstance(row['values'], dict)
                    and datetime.fromisoformat(row['available_at']).astimezone(timezone.utc) <= cutoff]
                pending = sum(row['horizon_seconds'] == 180 and row['status'] == 'PENDING'
                    and datetime.fromisoformat(row['available_at']).astimezone(timezone.utc) <= cutoff for row in labels)
                self.assertTrue(completed)
                self.assertEqual(8, result.decision_count)
                self.assertEqual(8, len(native.load_evaluations(result.run_id)))
                self.assertIsNotNone(result.market_regime)
                self.assertEqual(cutoff, datetime.fromisoformat(result.market_regime['as_of']).astimezone(timezone.utc))
                features = result.market_regime['value']['features']
                self.assertEqual(cutoff, datetime.fromisoformat(features['as_of']).astimezone(timezone.utc))
                self.assertEqual(len(completed), features['mature_breakout_denominator'])
                self.assertEqual(pending, features['pending_breakout_count'])
                success = sum(row['values']['return_bps'] > 0 for row in completed)
                self.assertEqual(success * 1_000_000 // len(completed), features['mature_breakout_success_ppm'])
                self.assertEqual((len(completed) - success) * 1_000_000 // len(completed), features['mature_breakout_failure_ppm'])
                self.assertEqual(frozen, {path: path.read_bytes() for path in frozen})
                counts[label] = (len(completed), pending)
            self.assertEqual(counts['uniform'], counts['mixed'])


if __name__ == '__main__':
    unittest.main()
