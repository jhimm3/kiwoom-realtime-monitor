"""Publish an inactive replay experiment on the verified active NAS source.

Only explicit replay files and the candidate build marker change. Operational
source selection, runtime contracts, capture files and database data stay intact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BUILD = '2026.10.09-partial-store-replay-v2'
FILES = (
    'src/kiwoom_monitor/central_server/diagnostic_trace.py',
    'src/kiwoom_monitor/central_server/diagnostic_replay_contract.py',
    'src/kiwoom_monitor/central_server/diagnostic_replay_database_cli.py',
    'scripts/nas_operator.py', 'scripts/nas_operator_worker.py',
    'tests/unit/test_nas_operator.py', 'tests/unit/test_diagnostic_scoped_window.py',
    'tests/unit/test_diagnostic_replay_database_cli.py',
)
APP = 'src/kiwoom_monitor/central_server/app.py'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked(root, name):
    if (type(name) is not str or '\\' in name or name.startswith('/')
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('unsafe_release_path')
    path = root.joinpath(name)
    path.resolve().relative_to(root.resolve())
    if any(parent.is_symlink() for parent in [path, *path.parents] if parent != root.parent):
        raise ValueError('symlink_release_path')
    return path


def prepare(nas_root):
    store = nas_root.resolve() / 'source-runtime'
    active_raw = (store / 'active.json').read_bytes()
    active = json.loads(active_raw)['release_id']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}', active):
        raise ValueError('invalid_active_release')
    base = store / 'releases' / active
    source_manifest_raw = (base / 'manifest.json').read_bytes()
    manifest = json.loads(source_manifest_raw)
    overlay = {name: checked(ROOT, name).read_bytes() for name in FILES}
    app = checked(base, APP).read_bytes()
    if digest(app) != manifest['files'][APP]:
        raise ValueError('active_source_hash_mismatch')
    overlay[APP], count = re.subn(rb'^SERVER_BUILD = "[A-Za-z0-9._-]+"$',
                                 ('SERVER_BUILD = "' + BUILD + '"').encode(), app, flags=re.MULTILINE)
    if count != 1:
        raise ValueError('build_marker_missing')
    files = {**manifest['files'], **{name: digest(data) for name, data in overlay.items()}}
    content_hash = digest(json.dumps(files, sort_keys=True).encode())
    release = BUILD + '-' + content_hash[:16]
    target = store / 'releases' / release
    existing = target.exists()
    if not existing:
        target.mkdir(exist_ok=False)
    for name, expected in files.items():
        data = overlay.get(name)
        if data is None:
            data = checked(base, name).read_bytes()
        if digest(data) != expected:
            raise ValueError('source_changed_during_publication')
        out = checked(target, name)
        if existing:
            if not out.is_file() or digest(out.read_bytes()) != expected:
                raise ValueError('candidate_conflict')
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
        if digest(out.read_bytes()) != expected:
            raise ValueError('candidate_copy_checksum_mismatch')
    candidate = {**manifest, 'release_id': release, 'server_build': BUILD, 'files': files,
        'src_hash': digest(json.dumps({key: value for key, value in files.items() if key.startswith('src/')},
                                     sort_keys=True).encode()),
        'working_tree_dirty': True,
        'replay_experiment_overlay': {'base_release': active, 'files': [*FILES, APP],
                                     'source_state_equivalent': False, 'deployed': False}}
    path = target / 'manifest.json'
    if existing:
        if json.loads(path.read_bytes()) != candidate:
            raise ValueError('candidate_manifest_conflict')
    else:
        path.write_text(json.dumps(candidate, sort_keys=True) + '\n', encoding='utf-8')
    if ((store / 'active.json').read_bytes() != active_raw
            or (base / 'manifest.json').read_bytes() != source_manifest_raw):
        raise ValueError('active_source_changed_during_publication')
    return dict(release_id=release, server_build=BUILD, base_release=active,
                files=len(files), active_changed=False, runtime_contract_changed=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nas-root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.nas_root), sort_keys=True))
