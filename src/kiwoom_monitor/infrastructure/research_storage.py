"""Ownership, inventory and bounded disposal of incomplete PC research staging.

An inventory is diagnostic, never a deletion authorization. References may be
added after its database snapshot. Disposal requires its own DB reference fence;
every complete manifest remains protected, including external journal evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, date
from pathlib import Path


MARKER_NAME = '.research-storage.json'
MARKER_VERSION = 'pc_research_storage/v1'


class ResearchStagingCleanupYield(Exception):
    """Keep the durable intent for another bounded cleanup pass."""


def inspect_incomplete_research_staging(root, path, source_id, operation_id, *, marker_hash='', checkpoint=lambda: None, max_entries=64):
    """Only known partial export files; any manifest or unknown entry protects all."""
    root, path = Path(root), Path(path)
    validate_research_storage_root(root)
    if path.absolute().parent != root.absolute() or path.resolve().parent != root.resolve() or not re.fullmatch(r'\.nas-preparing-[a-z0-9_]+', path.name):
        raise ValueError('staging_path_outside_root')
    if not path.exists():
        return {'missing': True, 'marker_hash': marker_hash, 'bytes': 0, 'files': [], 'directories': []}
    if _redirected(path) or not path.is_dir():
        raise ValueError('staging_path_redirected')
    checkpoint()
    # A crash after removing the marker can leave only the empty directory.
    if marker_hash and not any(path.iterdir()):
        return {'missing': False, 'marker_hash': marker_hash, 'bytes': 0, 'files': [], 'directories': []}
    marker_path = path / MARKER_NAME
    if not marker_path.is_file():
        raise ValueError('staging_marker_unverified')
    if _redirected(marker_path) or marker_path.stat().st_size > 4096:
        raise ValueError('staging_marker_unverified')
    raw = marker_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    marker = json.loads(raw)
    if not isinstance(marker, dict) or marker.get('version') != MARKER_VERSION or marker.get('kind') != 'preparing' or marker.get('source_id') != source_id or marker.get('operation_id') != operation_id:
        raise ValueError('staging_marker_unverified')
    if marker_hash and digest != marker_hash:
        raise ValueError('staging_marker_changed')
    created = datetime.fromisoformat(marker.get('created_at', ''))
    if created.tzinfo is None:
        raise ValueError('staging_marker_time_unverified')
    files, directories, pending = [], [], [path]
    size, visited = len(raw), 0
    while pending:
        checkpoint()
        parent = pending.pop()
        for child in parent.iterdir():
            checkpoint()
            visited += 1
            if visited > max_entries:
                raise ValueError('staging_inventory_limit')
            if _redirected(child) or path.resolve() not in child.resolve().parents:
                raise ValueError('staging_child_redirected')
            relative = child.relative_to(path)
            if relative == Path(MARKER_NAME):
                continue
            if child.name == 'manifest.json':
                raise ValueError('completed_input_preserved')
            if child.is_dir():
                parts = relative.parts
                allowed = parts in (('payload',), ('payload', 'days'))
                if len(parts) == 3 and parts[:2] == ('payload', 'days'):
                    allowed = date.fromisoformat(parts[2]).isoformat() == parts[2]
                if not allowed:
                    raise ValueError('unknown_staging_directory')
                pending.append(child)
                directories.append(child)
            elif child.is_file() and child.name in ('observations.jsonl', 'theme_snapshots.jsonl') and (relative.parent == Path('payload') or (len(relative.parts) == 4 and relative.parts[:2] == ('payload', 'days'))):
                files.append(child)
                size += child.stat().st_size
            else:
                raise ValueError('unknown_staging_file')
    return {'missing': False, 'marker_hash': digest, 'created_at': created, 'bytes': size,
            'files': files, 'directories': directories}


def delete_incomplete_research_staging(root, path, inspection, *, checkpoint=lambda: None):
    """Caller holds its DB reference fence. Unlink fixed files, marker last; never rmtree."""
    root, path = Path(root), Path(path)
    validate_research_storage_root(root)
    if path.resolve().parent != root.resolve() or _redirected(path):
        raise ValueError('staging_path_redirected')
    for child in inspection['files']:
        checkpoint()
        if _redirected(child) or path.resolve() not in child.resolve().parents:
            raise ValueError('staging_child_redirected')
        child.unlink()
    for child in sorted(inspection['directories'], key=lambda value: len(value.parts), reverse=True):
        checkpoint()
        if _redirected(child) or path.resolve() not in child.resolve().parents:
            raise ValueError('staging_child_redirected')
        child.rmdir()
    checkpoint()
    marker = path / MARKER_NAME
    if marker.exists():
        if _redirected(marker) or marker.stat().st_size > 4096 or hashlib.sha256(marker.read_bytes()).hexdigest() != inspection['marker_hash']:
            raise ValueError('staging_marker_changed')
        # Reject files introduced after preflight, before removing the identity.
        if any(child.name != MARKER_NAME for child in path.iterdir()):
            raise ValueError('unknown_staging_file')
        marker.unlink()
    checkpoint()
    path.rmdir()


def _redirected(path):
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def research_storage_text_bytes(text):
    """Match the existing text writer's platform newline conversion."""
    return len(text.replace('\n', os.linesep).encode('utf-8'))


def validate_research_storage_root(root):
    root = Path(root)
    if root.resolve() != root.absolute():
        raise ValueError('research storage root was redirected')
    if root.exists() and _redirected(root):
        raise ValueError('research storage root was redirected')


def research_storage_remaining_bytes(root, source_id, cap_bytes, *, checkpoint=lambda: None, allow_full=False):
    from kiwoom_monitor.application.research_resources import ResearchResourceBlocked
    if not cap_bytes:
        return None
    inventory = inventory_research_storage(root, source_id, (), checkpoint=checkpoint)
    if not inventory['complete']:
        raise ResearchResourceBlocked('research_storage_inventory_incomplete')
    remaining = cap_bytes - inventory['total_bytes']
    if remaining < 0 or (remaining == 0 and not allow_full):
        raise ResearchResourceBlocked('research_storage_capacity_reached')
    return remaining


def write_research_storage_marker(path, source_id, *, kind, fingerprint='', reserve_bytes=lambda count: None, operation_id=''):
    """Mark only newly created staging/published inputs, without changing evidence."""
    path = Path(path)
    if not re.fullmatch(r'[0-9a-f]{64}', source_id) or kind not in ('preparing', 'published'):
        raise ValueError('invalid research storage ownership')
    if _redirected(path):
        raise ValueError('redirected research storage directory')
    manifest_hash = ''
    if kind == 'published':
        if not re.fullmatch(r'[0-9a-f]{64}', fingerprint):
            raise ValueError('invalid research storage fingerprint')
        manifest = path / 'manifest.json'
        if _redirected(manifest) or manifest.stat().st_size > 1024 * 1024:
            raise ValueError('invalid research storage manifest')
        manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    marker = {'version': MARKER_VERSION, 'source_id': source_id, 'kind': kind,
              'created_at': datetime.now(UTC).isoformat(), 'fingerprint': fingerprint,
              'manifest_hash': manifest_hash}
    if operation_id:
        if not re.fullmatch(r'[0-9a-f-]{36}', operation_id):
            raise ValueError('invalid research storage operation')
        marker['operation_id'] = operation_id
    text = json.dumps(marker, sort_keys=True)
    reserve_bytes(research_storage_text_bytes(text))
    # Exclusive creation: never adopt an existing/manual directory by overwriting.
    with (path / MARKER_NAME).open('x', encoding='utf-8') as handle:
        handle.write(text)


def inventory_research_storage(root, source_id, references, *, checkpoint=lambda: None,
                               max_entries=10000):
    """Bounded metadata walk. Redirected, unknown and referenced inputs are protected."""
    root = Path(root)
    result = {'source_id': source_id, 'complete': True, 'total_bytes': 0, 'entries': [],
              'deletion_authorized': False}
    references = tuple(Path(value).resolve() for value in references)
    visited = 0

    def overlaps(path):
        return any(path == ref or path in ref.parents or ref in path.parents for ref in references)

    def ownership(path):
        marker = path / MARKER_NAME
        if not marker.is_file() or _redirected(marker) or marker.stat().st_size > 4096:
            return None
        try:
            value = json.loads(marker.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or value.get('version') != MARKER_VERSION or value.get('source_id') != source_id:
                return None
            created = datetime.fromisoformat(value.get('created_at', ''))
            if created.tzinfo is None:
                return None
            kind = value.get('kind')
            if kind == 'preparing' and re.fullmatch(r'\.nas-preparing-[a-z0-9_]+', path.name):
                return kind
            fingerprint = value.get('fingerprint', '')
            if kind != 'published' or not re.fullmatch(r'[0-9a-f]{64}', fingerprint) or path.name != f'nas-{source_id[:12]}-{fingerprint}':
                return None
            manifest = path / 'manifest.json'
            if _redirected(manifest) or manifest.stat().st_size > 1024 * 1024:
                return None
            if hashlib.sha256(manifest.read_bytes()).hexdigest() == value.get('manifest_hash'):
                return kind
        except (ValueError, TypeError, OSError):
            pass
        return None

    def size(path):
        nonlocal visited
        total, safe = 0, True
        pending = [path]
        while pending:
            checkpoint()
            current = pending.pop()
            visited += 1
            if visited > max_entries:
                return total, False
            if _redirected(current):
                safe = False
            elif current.is_dir():
                # Do not materialize an unbounded directory before applying limits.
                for child in current.iterdir():
                    checkpoint()
                    if visited + len(pending) >= max_entries:
                        return total, False
                    pending.append(child)
            elif current.is_file():
                total += current.stat().st_size
            else:
                safe = False
        return total, safe

    try:
        if _redirected(root):
            result['complete'] = False
            return result
        for path in root.iterdir():
            checkpoint()
            if visited >= max_entries:
                result['complete'] = False
                break
            reasons = []
            redirected = _redirected(path)
            kind = None if redirected or not path.is_dir() else ownership(path)
            if redirected:
                reasons.append('redirected_path')
            if kind is None:
                reasons.append('unverified_ownership')
            if overlaps(path.resolve()):
                reasons.append('research_reference')
            if kind == 'preparing':
                reasons.append('staging_requires_lease_check')
            count, complete = size(path)
            if not complete:
                reasons.append('incomplete_inventory')
                result['complete'] = False
            result['total_bytes'] += count
            result['entries'].append({'path': str(path.absolute()), 'kind': kind,
                                     'bytes': count, 'protected_reasons': reasons,
                                     'deletion_authorized': False})
    except InterruptedError:
        raise
    except OSError:
        result['complete'] = False
    return result
