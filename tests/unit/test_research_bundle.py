from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kiwoom_monitor.application.market_session_schedule import research_session_profile_document
from kiwoom_monitor.infrastructure.research_data_source import (
    load_frozen_research_bundle, load_frozen_research_export, write_frozen_research_bundle,
)

KST = timezone(timedelta(hours=9))
SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/export_research_dataset.py'
spec = importlib.util.spec_from_file_location('bundle_export_script', SCRIPT)
export_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_script)


def write_child(root, day, rows=None, *, profile='krx-regular/v1', truncated=False, theme=None):
    start = datetime.combine(day, datetime.min.time(), tzinfo=KST)
    rows = rows if rows is not None else [{'ordinal': 1, 'revision_id': day.isoformat(),
                                        'available_at': start.isoformat(), 'kind': 'ranking'}]
    path = root / 'days' / day.isoformat()
    path.mkdir(parents=True)
    encoded = ''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows).encode()
    theme_rows = [theme] if theme else []
    encoded_themes = ''.join(json.dumps(row, sort_keys=True) + '\n' for row in theme_rows).encode()
    (path / 'observations.jsonl').write_bytes(encoded)
    (path / 'theme_snapshots.jsonl').write_bytes(encoded_themes)
    manifest = {'dataset_id': 'dataset-' + day.isoformat(), 'schema_version': 1,
                'captured_range': {'start': start.isoformat(), 'end': (start + timedelta(days=1)).isoformat()},
                'kinds': ['ranking'], 'subject': '', 'fixed_watermark': 'watermark-' + day.isoformat(),
                'universe_rule': 'top20_membership-v1', 'order_policy_version': 'available_at-ingest_sequence-revision_id-v1',
                'revision_count': len(rows),
                'revision_ids_hash': hashlib.sha256('\n'.join(row['revision_id'] for row in rows).encode()).hexdigest(),
                'observations_file': 'observations.jsonl', 'observations_file_hash': hashlib.sha256(encoded).hexdigest(),
                'theme_snapshots_file': 'theme_snapshots.jsonl', 'theme_snapshots_file_hash': hashlib.sha256(encoded_themes).hexdigest(),
                'theme_snapshot_count': len(theme_rows), 'theme_snapshot_history_may_be_truncated': truncated,
                'research_session_profile': research_session_profile_document(profile),
                'quality_summary': {'flags': ['coverage_not_evaluated'], 'recording_gap': 'unknown'}}
    (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return path.relative_to(root).as_posix()


class BundleTests(unittest.TestCase):
    def test_daily_ids_ordinals_and_files_are_preserved_and_index_is_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = tuple(write_child(root, date(2026, 9, day)) for day in (14, 15))
            before = {path: (root / path / 'manifest.json').read_bytes() for path in paths}
            bundle = write_frozen_research_bundle(root, paths)
            self.assertEqual(2, bundle.manifest['unique_revision_count'])
            self.assertEqual([1, 1], [dataset.observations[0]['ordinal'] for dataset in bundle.datasets])
            self.assertEqual(bundle, load_frozen_research_bundle(root))
            self.assertEqual(bundle, write_frozen_research_bundle(root, paths))
            self.assertEqual(before, {path: (root / path / 'manifest.json').read_bytes() for path in paths})

    def test_single_child_keeps_exact_daily_dataset_and_legacy_reader_rejects_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = write_child(root, date(2026, 9, 14))
            daily = load_frozen_research_export(root / child)
            self.assertEqual(daily, write_frozen_research_bundle(root, (child,)).datasets[0])
            with self.assertRaisesRegex(ValueError, 'bundle reader'):
                load_frozen_research_export(root)

    def test_missing_or_changed_child_is_rejected(self):
        for change in ('missing', 'changed', 'manifest'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                child = write_child(root, date(2026, 9, 14))
                write_frozen_research_bundle(root, (child,))
                if change == 'missing':
                    (root / child / 'observations.jsonl').unlink()
                elif change == 'changed':
                    (root / child / 'observations.jsonl').write_bytes(b'{}\n')
                else:
                    path = root / child / 'manifest.json'
                    manifest = json.loads(path.read_text())
                    manifest['quality_summary'] = {}
                    path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    load_frozen_research_bundle(root)

    def test_unknown_policy_and_counts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = write_child(root, date(2026, 9, 14))
            bundle = write_frozen_research_bundle(root, (child,))
            for field, value in (('boundary_policy', 'force_close'), ('unique_revision_count', 123)):
                manifest = dict(bundle.manifest, **{field: value})
                (root / 'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'does not match'):
                    load_frozen_research_bundle(root)

    def test_relative_escape_and_absolute_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ('../outside', '/outside', 'C:/outside', 'days\\outside', ''):
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'relative directory'):
                    write_frozen_research_bundle(Path(directory), (path,))

    def test_duplicate_or_out_of_order_daily_ranges_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = (write_child(root, date(2026, 9, day)) for day in (14, 15))
            for paths in ((first, first), (second, first)):
                with self.assertRaisesRegex(ValueError, 'date order'):
                    write_frozen_research_bundle(root, paths)

    def test_different_profiles_and_truncated_theme_history_are_rejected(self):
        for scenario in ('profile', 'truncated'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                first = write_child(root, date(2026, 9, 14))
                second = write_child(root, date(2026, 9, 15),
                                     profile='krx-after/v1' if scenario == 'profile' else 'krx-regular/v1',
                                     truncated=scenario == 'truncated')
                with self.assertRaises(ValueError):
                    write_frozen_research_bundle(root, (first, second))
                self.assertFalse((root / 'manifest.json').exists())

    def test_shared_revision_dedup_ignores_only_export_ordinal_and_conflict_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = {'ordinal': 1, 'revision_id': 'shared', 'payload': {'price': 1000}}
            first = write_child(root, date(2026, 9, 14), [shared])
            second = write_child(root, date(2026, 9, 15), [{'ordinal': 1, 'revision_id': 'other'}, dict(shared, ordinal=2)])
            bundle = write_frozen_research_bundle(root, (first, second))
            self.assertEqual(2, bundle.manifest['unique_revision_count'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = write_child(root, date(2026, 9, 14), [shared])
            second = write_child(root, date(2026, 9, 15), [dict(shared, payload={'price': 2000})])
            with self.assertRaisesRegex(ValueError, 'conflicting content'):
                write_frozen_research_bundle(root, (first, second))

    def test_missing_theme_file_and_conflicting_theme_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = write_child(root, date(2026, 9, 14), theme={'snapshot_id': 'theme', 'document': {}})
            second = write_child(root, date(2026, 9, 15), theme={'snapshot_id': 'theme', 'document': {'changed': True}})
            with self.assertRaisesRegex(ValueError, 'theme id'):
                write_frozen_research_bundle(root, (first, second))
            (root / first / 'theme_snapshots.jsonl').unlink()
            with self.assertRaisesRegex(ValueError, 'cannot be read'):
                write_frozen_research_bundle(root, (first,))

    def test_empty_day_keeps_unknown_coverage_and_does_not_imply_holiday(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = write_child(root, date(2026, 9, 14), [])
            bundle = write_frozen_research_bundle(root, (child,))
            self.assertEqual(0, bundle.manifest['unique_revision_count'])
            self.assertEqual('unknown', bundle.manifest['children'][0]['quality_summary']['recording_gap'])

    def test_legacy_or_naive_child_remains_unusable_as_bundle_input(self):
        for field in ('research_session_profile', 'captured_range'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                child = write_child(root, date(2026, 9, 14))
                path = root / child / 'manifest.json'
                manifest = json.loads(path.read_text())
                if field == 'research_session_profile':
                    manifest.pop(field)
                    path.write_text(json.dumps(manifest))
                    self.assertEqual(1, len(load_frozen_research_export(root / child).observations))
                else:
                    manifest[field]['start'] = '2026-09-14T00:00:00'
                    path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    write_frozen_research_bundle(root, (child,))


class BundleExportTests(unittest.TestCase):
    def test_new_daily_inputs_export_and_resume_preserve_previous_watermarks(self):
        class Client:
            def __init__(self):
                self.calls = []
                self.fail_date = date(2026, 9, 15)
            def load_research_observations_page(self, start, end, kinds, **kwargs):
                self.calls.append(start.date())
                if start.date() == self.fail_date:
                    raise RuntimeError('temporary fixture failure')
                revision_id = start.date().isoformat()
                manifest = {'dataset_id': revision_id, 'fixed_watermark': revision_id, 'schema_version': 1,
                            'revision_count': 1, 'revision_ids_hash': hashlib.sha256(revision_id.encode()).hexdigest(),
                            'captured_range': {'start': start.isoformat(), 'end': end.isoformat()},
                            'kinds': list(kinds), 'subject': kwargs['subject'],
                            'quality_summary': {'recording_gap': 'unknown'}}
                return {'manifest': manifest, 'watermark': revision_id, 'next_cursor': None,
                        'observations': [{'ordinal': 1, 'revision_id': revision_id, 'kind': 'ranking'}]}
            def load_theme_history(self, **kwargs):
                return {'snapshots': []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'
            dates = [date(2026, 9, 14), date(2026, 9, 15)]
            client = Client()
            with self.assertRaisesRegex(RuntimeError, 'temporary'):
                export_script.export_date_bundle(client, dates, ('ranking',), '', root, 'krx-regular/v1')
            self.assertFalse((root / 'manifest.json').exists())
            original = (root / 'days/2026-09-14/manifest.json').read_bytes()
            client.fail_date = None
            bundle = export_script.export_date_bundle(client, dates, ('ranking',), '', root,
                                                       'krx-regular/v1', reuse_days=True)
            self.assertEqual(2, bundle.manifest['child_count'])
            self.assertEqual([dates[0], dates[1], dates[1]], client.calls)
            self.assertEqual(original, (root / 'days/2026-09-14/manifest.json').read_bytes())
            before = list(client.calls)
            export_script.export_date_bundle(client, dates, ('ranking',), '', root,
                                             'krx-regular/v1', reuse_days=True)
            self.assertEqual(before, client.calls)

    def test_matching_daily_exports_are_reused_without_network_and_new_input_is_not_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dates = [date(2026, 9, 14), date(2026, 9, 15)]
            for day in dates:
                write_child(root, day)
            class NoNetwork:
                def __getattr__(self, name):
                    raise AssertionError('matching daily input must not use the network')
            bundle = export_script.export_date_bundle(NoNetwork(), list(reversed(dates)), ('ranking',), '', root,
                                                       'krx-regular/v1', reuse_days=True)
            self.assertEqual(bundle, export_script.export_date_bundle(NoNetwork(), dates, ('ranking',), '', root,
                                                                      'krx-regular/v1', reuse_days=True))
            with self.assertRaisesRegex(ValueError, '날짜는 변경'):
                export_script.export_date_bundle(NoNetwork(), dates + [date(2026, 9, 16)], ('ranking',), '', root,
                                                 'krx-regular/v1', reuse_days=True)
            with self.assertRaisesRegex(ValueError, '요청 조건'):
                export_script.export_date_bundle(NoNetwork(), dates, ('ranking',), '5', root,
                                                 'krx-regular/v1', reuse_days=True)

    def test_reuse_requires_explicit_flag_and_duplicate_selector_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            day = date(2026, 9, 14)
            for dates, reuse in (([day], False), ([day, day], True)):
                with self.assertRaises(ValueError):
                    export_script.export_date_bundle(object(), dates, ('ranking',), '', Path(directory),
                                                     'krx-regular/v1', reuse_days=reuse)

    def test_legacy_reuse_requires_profile_and_output_cannot_be_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = date(2026, 9, 14)
            child = write_child(root, day)
            path = root / child / 'manifest.json'
            manifest = json.loads(path.read_text())
            manifest.pop('research_session_profile')
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, '요청 조건'):
                export_script.export_date_bundle(object(), [day], ('ranking',), '', root,
                                                 'krx-regular/v1', reuse_days=True)
            file_output = root / 'file'
            file_output.write_text('keep')
            with self.assertRaisesRegex(ValueError, '디렉터리'):
                export_script.export_date_bundle(object(), [day], ('ranking',), '', file_output,
                                                 'krx-regular/v1', reuse_days=True)
            self.assertEqual('keep', file_output.read_text())


if __name__ == '__main__':
    unittest.main()
