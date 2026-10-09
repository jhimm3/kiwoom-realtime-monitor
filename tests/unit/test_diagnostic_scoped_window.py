from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_trace as trace
from kiwoom_monitor.central_server import diagnostic_recorded_execution as execution
from kiwoom_monitor.central_server.diagnostic_replay_contract import CODEC_VERSION, compile_recorded_plan, freeze_payload


TRACE = '20261008T000000Z-0123456789ab'
ORIGIN = 1_000_000_000
SELECTION = dict(started_mono_ns=ORIGIN, window_start_seconds=0, window_end_seconds=.05,
                 include_workloads=('realtime',), exclude_workloads=(), mode='recorded_operations',
                 collector_components=())


def operation(identifier, workload='realtime', at=.01, sequence=1):
    return dict(operation_id=identifier, method='load_daily_bars', workload_id=workload,
                producer_component='collector', actor_id='flush', actor_known=True,
                actor_sequence=sequence, codec_version=CODEC_VERSION,
                entered_mono_ns=ORIGIN + int(at * 1e9))


def fixture_rows():
    selected, excluded = operation('selected'), operation('excluded', 'top20')
    return [
        dict(event_type='domain', mono_ns=ORIGIN),
        dict(event_type='operation_start', **selected, payload=freeze_payload({'code': '005930'}).value),
        dict(event_type='operation_start', **excluded, payload=freeze_payload({'code': '000660'}).value),
        dict(event_type='input_rejected', workload_id='shadow', reason='payload_budget_exceeded',
             mono_ns=ORIGIN + 20_000_000),
        dict(event_type='call_start', call_id='db-source', input_operation_id='selected', mono_ns=ORIGIN + 12_000_000),
        dict(event_type='operation_end', **excluded, outcome='returned', finished_mono_ns=ORIGIN + 30_000_000),
        dict(event_type='call_end', call_id='db-source', input_operation_id='selected', mono_ns=ORIGIN + 70_000_000),
        dict(event_type='operation_end', **selected, outcome='returned', finished_mono_ns=ORIGIN + 80_000_000),
        dict(event_type='domain', mono_ns=ORIGIN + 150_000_000),
    ]


def write_fixture(root, rows=None, **overrides):
    directory = Path(root) / TRACE
    directory.mkdir(exist_ok=True)
    rows = copy.deepcopy(fixture_rows() if rows is None else rows)
    coverage, reasons, blobs = {}, Counter(), {}
    for seq, row in enumerate(rows, 1):
        row['seq'] = seq
        if 'payload' in row:
            data = json.dumps(row.pop('payload'), separators=(',', ':')).encode()
            digest = hashlib.sha256(data).hexdigest()
            row['payload_ref'] = digest
            blobs[digest] = {'bytes': len(data)}
            (directory / ('payload-' + digest + '.json')).write_bytes(data)
            coverage.setdefault(row['workload_id'], {'accepted': 0, 'rejected': 0})['accepted'] += 1
        if row['event_type'] == 'input_rejected':
            reasons[row.get('reason')] += 1
            coverage.setdefault(row.get('workload_id', 'unsupported'), {'accepted': 0, 'rejected': 0})['rejected'] += 1
    data = b'\n'.join(json.dumps(row, separators=(',', ':')).encode() for row in rows) + b'\n'
    (directory / '000001.jsonl').write_bytes(data)
    manifest = dict(trace_id=TRACE, schema_version=3, state='incomplete', reason='expired',
        coverage='observed_paths_only', payload_capture={'store_inputs': True}, started_mono_ns=ORIGIN,
        finished_mono_ns=ORIGIN + 200_000_000, input_capture_censored=False, known_dropped=0,
        accepted=len(rows), written=len(rows), last_seq=len(rows), payload_accepted=sum(v['accepted'] for v in coverage.values()),
        input_rejected=sum(reasons.values()), input_rejected_reasons=dict(reasons), input_coverage=coverage,
        drop_reasons={}, queued=0, pending_events=0, copy_reserved_bytes=0, charged_bytes=0,
        bytes_written=len(data) + sum(v['bytes'] for v in blobs.values()), blobs=blobs,
        event_counts=dict(Counter(row['event_type'] for row in rows)),
        chunks=[dict(name='000001.jsonl', count=len(rows), first_seq=1, last_seq=len(rows), bytes=len(data),
                     sha256=hashlib.sha256(data).hexdigest())])
    manifest.update(overrides)
    (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return manifest


def read(**options):
    selection = {key: value for key, value in SELECTION.items() if key != 'started_mono_ns'}
    selection.update(options)
    policy = selection.pop('capture_policy', 'scoped-operations')
    return trace.recorded_window_events(TRACE, capture_policy=policy, **selection)


class ScopedWindowTests(unittest.TestCase):
    def test_partial_policy_replays_surviving_calls_and_keeps_missing_input_evidence(self):
        class Store:
            def __init__(self):
                self.codes = []
            def load_daily_bars(self, code):
                self.codes.append(code)
                return []
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            source = fixture_rows()
            source[3].update(workload_id='realtime', operation_id='missing', method='save_minute_bars')
            missing = operation('missing', at=.02, sequence=2)
            source += [dict(event_type='operation_end', **missing, outcome='returned',
                            finished_mono_ns=ORIGIN + 40_000_000),
                       dict(event_type='call_start', call_id='omitted-db', input_operation_id='missing', mono_ns=ORIGIN + 21_000_000),
                       dict(event_type='call_end', call_id='omitted-db', input_operation_id='missing', mono_ns=ORIGIN + 39_000_000)]
            write_fixture(root, source)
            original = (Path(root) / TRACE / 'manifest.json').read_bytes()
            for _ in range(2):
                manifest, rows = read(capture_policy='partial-operations')
                report = manifest['window_read']
                self.assertEqual(1, report['omitted_input_count'])
                self.assertEqual('missing', report['omitted_inputs'][0]['operation_id'])
                self.assertEqual('save_minute_bars', report['omitted_inputs'][0]['method'])
                self.assertFalse(report['selected_input_complete'])
                self.assertFalse(report['missing_load_reconstructed'])
                self.assertEqual('surviving_native_operations_only', report['fidelity'])
                self.assertEqual([4], [row['source_seq'] for row in rows if row['event_type'] == 'omitted_input'])
                store = Store()
                result = asyncio.run(execution._execute_recorded_operations(store, rows, **SELECTION))
                self.assertEqual('complete', result['state'])
                self.assertEqual(['005930'], store.codes)
                self.assertEqual(['db-source'], result['calls'][0]['source_call_ids'])
                self.assertEqual(1, len(result['calls']))
            self.assertEqual(original, (Path(root) / TRACE / 'manifest.json').read_bytes())
            with self.assertRaisesRegex(ValueError, 'selected_input_unsupported'):
                read()

    def test_partial_policy_cannot_replay_rejected_operation_even_if_a_start_has_payload(self):
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            rows = fixture_rows()
            rows[3].update(workload_id='realtime', operation_id='excluded')
            rows[2]['workload_id'] = rows[5]['workload_id'] = 'realtime'
            manifest = write_fixture(root, rows)
            rejected_blob = list(manifest['blobs'])[1]
            (Path(root) / TRACE / ('payload-' + rejected_blob + '.json')).write_bytes(b'not executable')
            report, events = read(capture_policy='partial-operations')
            self.assertEqual(('selected',), compile_recorded_plan(events, **SELECTION).operation_ids)
            self.assertEqual(1, report['window_read']['payload_blobs_loaded'])
            self.assertEqual(1, report['window_read']['omitted_input_count'])

    def test_selected_arguments_source_spans_late_end_and_original_sequence_execute(self):
        class Store:
            def load_daily_bars(self, code):
                self.code = code
                return []
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            write_fixture(root)
            original = (Path(root) / TRACE / 'manifest.json').read_bytes()
            manifest, rows = read()
            self.assertEqual([2, 3, 4, 5, 7, 8], [row['seq'] for row in rows])
            self.assertEqual(1, manifest['window_read']['payload_blobs_loaded'])
            self.assertEqual('incomplete', manifest['state'])
            self.assertEqual(('selected',), compile_recorded_plan(rows, **SELECTION).operation_ids)
            store = Store()
            result = asyncio.run(execution._execute_recorded_operations(store, rows, **SELECTION))
            self.assertEqual('complete', result['state'])
            self.assertEqual('005930', store.code)
            self.assertEqual(['db-source'], result['calls'][0]['source_call_ids'])
            self.assertEqual(original, (Path(root) / TRACE / 'manifest.json').read_bytes())

    def test_excluded_payload_is_not_loaded_and_selected_corruption_fails(self):
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            manifest = write_fixture(root)
            excluded = list(manifest['blobs'])[1]
            (Path(root) / TRACE / ('payload-' + excluded + '.json')).write_bytes(b'broken')
            read()
            selected = list(manifest['blobs'])[0]
            (Path(root) / TRACE / ('payload-' + selected + '.json')).write_bytes(b'broken')
            with self.assertRaisesRegex(ValueError, 'checksum_mismatch'):
                read()

    def test_selected_rejection_or_unknown_attribution_blocks_before_payload(self):
        for change in ({'workload_id': 'realtime'}, {'workload_id': None}, {'mono_ns': None}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root, \
                 patch.object(trace, '_directory', return_value=Path(root)):
                rows = fixture_rows()
                rows[3].update(change)
                write_fixture(root, rows)
                with patch.object(trace, '_payload_bytes_from_manifest') as payload:
                    with self.assertRaises(ValueError):
                        read()
                    payload.assert_not_called()

    def test_durable_accounting_tail_reason_and_schema_fail_closed(self):
        variants = [dict(known_dropped=1), dict(input_capture_censored=True), dict(queued=1),
                    dict(packing_events=1), dict(copy_reserved_bytes=1), dict(charged_bytes=1),
                    dict(unknown_tail_loss=True), dict(state='persisting'), dict(reason='disk_failed'),
                    dict(schema_version=1), dict(accepted=1), dict(input_rejected=2),
                    dict(input_rejected_reasons={'payload_budget_exceeded': 2}), dict(input_coverage={}),
                    dict(finished_mono_ns=ORIGIN + 40_000_000), dict(payload_capture=None)]
        for values in variants:
            with self.subTest(values=values), tempfile.TemporaryDirectory() as root, \
                 patch.object(trace, '_directory', return_value=Path(root)):
                write_fixture(root, **values)
                with self.assertRaises(ValueError):
                    read()

    def test_sparse_proof_cannot_change_selection_payload_or_manifest_identity(self):
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            write_fixture(root)
            for changed in ('selection', 'payload', 'manifest'):
                with self.subTest(changed=changed):
                    _, rows = read()
                    options = dict(SELECTION)
                    if changed == 'selection':
                        options['window_end_seconds'] = .04
                    elif changed == 'payload':
                        rows[0]['method'] = 'save_query'
                    else:
                        rows._manifest_hash = 'a' * 64
                    with self.assertRaisesRegex(ValueError, 'proof_mismatch'):
                        compile_recorded_plan(rows, **options)
            _, rows = read()
            with self.assertRaisesRegex(ValueError, 'sequence_gap'):
                compile_recorded_plan(list(rows), **SELECTION)

    def test_invalid_pairs_caps_and_unknown_workloads_are_rejected(self):
        for removed in (6, 7):
            with self.subTest(removed=removed), tempfile.TemporaryDirectory() as root, \
                 patch.object(trace, '_directory', return_value=Path(root)):
                rows = fixture_rows()
                rows.pop(removed)
                write_fixture(root, rows)
                with self.assertRaisesRegex(ValueError, 'pair_invalid|censored'):
                    read()
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            write_fixture(root)
            for options in ({'include_workloads': ()}, {'include_workloads': ('missing',)},
                            {'mode': 'collector_with_background'}, {'collector_components': ('collector',)}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    read(**options)
            with patch.object(trace, '_MAX_TOP20_WINDOW_ROWS', 2), self.assertRaisesRegex(ValueError, 'metadata_limit'):
                read()
            with patch.object(trace, '_MAX_WINDOW_PAYLOAD_BYTES', 1), self.assertRaisesRegex(ValueError, 'limit'):
                read()

    def test_manifest_change_and_chunk_sequence_corruption_fail(self):
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            write_fixture(root)
            original_payload = trace._payload_bytes_from_manifest
            def changed(*args):
                data = original_payload(*args)
                path = Path(root) / TRACE / 'manifest.json'
                path.write_bytes(path.read_bytes() + b' ')
                return data
            with patch.object(trace, '_payload_bytes_from_manifest', side_effect=changed), \
                 self.assertRaisesRegex(ValueError, 'manifest_changed'):
                read()
            rows = fixture_rows()
            manifest = write_fixture(root, rows)
            path = Path(root) / TRACE / '000001.jsonl'
            data = path.read_bytes().replace(b'"seq":2', b'"seq":1')
            path.write_bytes(data)
            manifest['chunks'][0]['sha256'] = hashlib.sha256(data).hexdigest()
            (Path(root) / TRACE / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'sequence_invalid'):
                read()

    def test_large_unselected_prefix_retains_only_the_bounded_frontier(self):
        with tempfile.TemporaryDirectory() as root, patch.object(trace, '_directory', return_value=Path(root)):
            rows = [dict(event_type='domain', mono_ns=ORIGIN) for _ in range(6000)] + fixture_rows()
            write_fixture(root, rows)
            manifest, retained = read()
            self.assertEqual(6009, manifest['window_read']['events_verified'])
            self.assertEqual(6, len(retained))
            self.assertLess(manifest['window_read']['scalar_bytes_retained'], 10_000)
            # This valid partial policy does not open the existing strict reader.
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                trace.recorded_window_events(TRACE, window_start_seconds=0, window_end_seconds=.05,
                                             mode='recorded_operations')


if __name__ == '__main__':
    unittest.main()
