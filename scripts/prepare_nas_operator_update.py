"""Publish an inactive, operator-only test overlay and pinned admin update bundle.

Never select a release, alter runtime contracts, or change operational data.
The existing runtime source is copied verbatim except for these operator files.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILES = ('scripts/nas_operator.py', 'scripts/nas_operator_worker.py',
         'scripts/nas_operator_install.py', 'tests/unit/test_nas_operator.py',
         'tests/integration/test_nas_operator_linux.py')
SHELL_FILES = ('deploy/synology/check-nas-operator.sh',)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalize_shell(data):
    """Return POSIX shell source with LF endings, including mixed-EOL input."""
    return data.replace(b'\r\n', b'\n').replace(b'\r', b'\n')


def bundle_fingerprint(files):
    """Fingerprint every published bundle file, including normalized shell gates."""
    hashes = {name: digest(data) for name, data in files.items()}
    return digest(json.dumps(hashes, sort_keys=True).encode())


def safe_file(root, name):
    if not name or '\\' in name or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('unsafe bundle path')
    path = root.joinpath(name)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError('bundle path escaped source')
    return path


def prepare(nas_root, workspace_commit=None):
    nas_root = nas_root.resolve()
    store = nas_root / 'source-runtime'
    active_bytes = (store / 'active.json').read_bytes()
    active = json.loads(active_bytes)['release_id']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,159}', active):
        raise ValueError('invalid active release')
    base = store / 'releases' / active
    manifest = json.loads((base / 'manifest.json').read_bytes())
    overlay = {name: safe_file(ROOT, name).read_bytes() for name in FILES}
    files = dict(manifest['files'])
    files.update({name: digest(data) for name, data in overlay.items()})
    content = digest(json.dumps(files, sort_keys=True).encode())
    release = manifest['server_build'] + '-' + content[:16]
    target = store / 'releases' / release
    target_exists = target.exists()
    if not target_exists:
        target.mkdir(exist_ok=False)
    # Verify the original before each copy, then verify what arrived on the share.
    for name, expected in files.items():
        data = overlay.get(name)
        if data is None:
            data = safe_file(base, name).read_bytes()
        if digest(data) != expected:
            raise ValueError('source changed during publication: ' + name)
        out = safe_file(target, name)
        if target_exists:
            if not out.is_file() or digest(out.read_bytes()) != expected:
                raise ValueError('existing candidate does not match: ' + name)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            if digest(out.read_bytes()) != expected:
                raise ValueError('publication hash mismatch: ' + name)
    if workspace_commit is None:
        workspace_commit = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if not re.fullmatch(r'[0-9a-f]{40,64}', workspace_commit):
        raise ValueError('invalid workspace commit')
    new_manifest = dict(manifest, files=files, release_id=release, working_tree_dirty=True,
                        operator_test_overlay={'base_release': active, 'files': list(FILES),
                                               'workspace_commit': workspace_commit})
    manifest_path = target / 'manifest.json'
    if target_exists:
        existing_manifest = json.loads(manifest_path.read_bytes())
        if (existing_manifest.get('release_id') != release
                or existing_manifest.get('server_build') != manifest.get('server_build')
                or existing_manifest.get('src_hash') != manifest.get('src_hash')
                or existing_manifest.get('files') != files
                or existing_manifest.get('operator_test_overlay') != new_manifest['operator_test_overlay']):
            raise ValueError('existing candidate manifest does not match')
    else:
        manifest_path.write_text(json.dumps(new_manifest, sort_keys=True) + '\n', encoding='utf-8')
    # src_hash/server_build/contract remain those of the unchanged NAS app.
    if digest(json.dumps({k: v for k, v in files.items() if k.startswith('src/')},
                         sort_keys=True).encode()) != manifest['src_hash']:
        raise ValueError('operator overlay changed application source')
    bundle_sources = {relative: safe_file(ROOT, relative).read_bytes()
                      for relative in (*FILES, *SHELL_FILES)}
    bundle_sources.update({relative: normalize_shell(data) for relative, data in
                           tuple(bundle_sources.items()) if relative in SHELL_FILES})
    bundle_id = bundle_fingerprint(bundle_sources)
    name = 'nas-operator-update-replay-pause-' + bundle_id[:16]
    artifact = nas_root / 'artifacts' / name
    artifact.mkdir(exist_ok=False)
    bundle = artifact / 'bundle'
    hashes = {}
    for relative, data in bundle_sources.items():
        out = safe_file(bundle, relative)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        hashes[relative] = digest(data)
    for relative in ('tests/__init__.py', 'tests/unit/__init__.py', 'tests/integration/__init__.py'):
        out = safe_file(bundle, relative)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b'')
        hashes[relative] = digest(b'')
    copy_lines = '\n'.join('copy_verified ' + value + ' ' + key for key, value in hashes.items())
    # This exact reviewed script requires an administrator once. It cannot run
    # through the restricted NOPASSWD launcher, and grants no new command surface.
    script = f'''#!/bin/sh
set -eu
umask 077
PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin
export PATH
SOURCE=/volume1/docker/kiwoom-monitor/artifacts/{name}/bundle
T=$(mktemp -d /tmp/kiwoom-nas-operator-update.XXXXXX)
case "$T" in /tmp/kiwoom-nas-operator-update.*) ;; *) exit 2 ;; esac
trap 'rm -rf "$T"' EXIT HUP INT TERM
mkdir -p "$T/bundle/scripts" "$T/bundle/tests/unit" "$T/bundle/tests/integration" "$T/bundle/deploy/synology"
copy_verified() {{
  expected=$1
  relative=$2
  printf '%s  %s\\n' "$expected" "$SOURCE/$relative" | sha256sum -c -
  install -o root -g root -m 600 "$SOURCE/$relative" "$T/bundle/$relative"
  printf '%s  %s\\n' "$expected" "$T/bundle/$relative" | sha256sum -c -
}}
{copy_lines}
IMAGE=$(/usr/local/bin/docker inspect --format '{{{{.Image}}}}' kiwoom-monitor-server-1)
test -n "$IMAGE"
sh "$T/bundle/deploy/synology/check-nas-operator.sh" "$IMAGE"
PYTHON=$(command -v python3)
"$PYTHON" -I -S "$T/bundle/scripts/nas_operator_install.py" --user k379 --update
/usr/local/bin/kiwoom-nas replay --help
'''
    bootstrap = nas_root / 'artifacts' / (name + '.sh')
    with bootstrap.open('x', encoding='utf-8', newline='\n') as out:
        out.write(script)
    result = {'candidate_release': release, 'base_release': active,
              'bundle_id': bundle_id,
              'application_source_changed': False, 'active_changed': False,
              'bootstrap': '/volume1/docker/kiwoom-monitor/artifacts/' + bootstrap.name,
              'bootstrap_sha256': digest(bootstrap.read_bytes()), 'bundle_files': hashes}
    if (store / 'active.json').read_bytes() != active_bytes:
        raise ValueError('active pointer changed during publication')
    (artifact / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nas-root', required=True, type=Path)
    parser.add_argument('--workspace-commit', help='Git commit of the source overlay (for remote publisher execution)')
    args = parser.parse_args()
    print(json.dumps(prepare(args.nas_root, args.workspace_commit), indent=2))


if __name__ == '__main__':
    main()
