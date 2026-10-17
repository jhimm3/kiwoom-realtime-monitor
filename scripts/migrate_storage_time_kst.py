"""One-time storage representation conversion; never shifts an event's instant.

Only explicitly UTC scalar time columns are eligible. Identifiers, epoch values,
naive times and immutable JSON/hash inputs require their owning contract and are
reported separately instead of being guessed. No schema or application runtime
is added by this maintenance tool.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import sqlite3


UTC_ISO = re.compile(
    r'^(\d{4}-\d{2}-\d{2})([T ])(\d{2}:\d{2}:\d{2})(\.\d+)?(Z|\+00(?::00)?)$'
)
TIME_COLUMNS = frozenset({
    'applied_at', 'updated_at', 'created_at', 'received_at', 'available_at',
    'effective_at', 'checked_at', 'naver_checked_at', 'analyzed_at', 'requested_at',
    'linked_at', 'imported_at', 'completed_at', 'published_at', 'fetched_at',
    'confirmed_at', 'captured_at', 'recorded_at', 'deleted_at', 'started_at',
    'finished_at', 'expires_at', 'last_seen_at', 'first_seen_at', 'occurred_at',
})
# strict_restore writes these timestamps into settings.value, not a time column.
# Other settings values may be source documents, IDs or user text.
SETTING_TIME_KEYS = frozenset({
    'google_drive_local_changed_at', 'google_drive_last_upload_success_at',
})


def utc_iso_to_kst(value):
    """Preserve arbitrary fractional precision and leave existing KST verbatim."""
    if not isinstance(value, str):
        return value
    match = UTC_ISO.fullmatch(value)
    if not match:
        return value
    day, separator, clock, fraction, _ = match.groups()
    original = datetime.strptime(day + 'T' + clock, '%Y-%m-%dT%H:%M:%S')
    converted = original + timedelta(hours=9)
    # strftime('%Y') is platform-dependent for years below 1000.
    text = (f'{converted.year:04d}-{converted.month:02d}-{converted.day:02d}'
            f'{separator}{converted.hour:02d}:{converted.minute:02d}:{converted.second:02d}'
            + (fraction or '') + '+09:00')
    return text


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def convert_json_paths(value, paths):
    """Convert only caller-audited time paths, preserving canonical/raw pockets."""
    from copy import deepcopy
    result = deepcopy(value)
    def visit(node, path):
        if not path:
            return utc_iso_to_kst(node)
        key, *rest = path
        if key == '*':
            if isinstance(node, list):
                return [visit(item, rest) for item in node]
        elif isinstance(node, dict) and key in node:
            node[key] = visit(node[key], rest)
        return node
    for path in paths:
        result = visit(result, path)
    return result


def sqlite_plan(connection):
    """Inspect every scalar time column; retain keys and all non-time fields."""
    changes, protected = [], []
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for (table,) in tables:
        columns = connection.execute('PRAGMA table_info(' + quote(table) + ')').fetchall()
        keys = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
        for _, column, kind, _, _, key in columns:
            if column not in TIME_COLUMNS or kind.upper() != 'TEXT':
                continue
            cursor = connection.execute('SELECT ' + ','.join(map(quote, keys + [column]))
                                        + ' FROM ' + quote(table))
            for row in cursor:
                old = row[-1]
                new = utc_iso_to_kst(old)
                if old == new:
                    continue
                item = {'table': table, 'column': column,
                        'key_columns': keys, 'key': list(row[:-1]), 'old': old, 'new': new}
                if key or not keys:
                    protected.append(item)
                else:
                    changes.append(item)
        if table == 'settings' and {'key', 'value'} <= {row[1] for row in columns} and keys == ['key']:
            for name, old in connection.execute('SELECT key,value FROM settings'):
                if name not in SETTING_TIME_KEYS:
                    continue
                new = utc_iso_to_kst(old)
                if new != old:
                    changes.append({'table': table, 'column': 'value', 'key_columns': keys,
                                    'key': [name], 'old': old, 'new': new})
    return changes, protected


def apply_sqlite_plan(connection, changes):
    """Caller owns the transaction and original-value backup."""
    for item in changes:
        where = ' AND '.join(quote(name) + ' IS ?' for name in item['key_columns'])
        sql = ('UPDATE ' + quote(item['table']) + ' SET ' + quote(item['column'])
               + '=? WHERE ' + where + ' AND ' + quote(item['column']) + ' IS ?')
        changed = connection.execute(sql, [item['new'], *item['key'], item['old']]).rowcount
        if changed != 1:
            raise RuntimeError('storage_changed_since_plan')


def migrate_sqlite(path, backup, *, apply=False):
    """Back up the exact changed cells before one atomic, reversible DB update."""
    path, backup = Path(path).resolve(), Path(backup).resolve()
    if not path.is_file():
        raise ValueError('existing_database_required')
    if backup == path:
        raise ValueError('backup_must_be_separate')
    connection = sqlite3.connect(path.as_uri() + '?mode=' + ('rw' if apply else 'ro'), uri=True, timeout=5)
    try:
        connection.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        changes, protected = sqlite_plan(connection)
        result = {'database': path.name, 'changed_cells': len(changes),
                  'protected_key_cells': len(protected), 'applied': False,
                  'epoch_and_naive_values_changed': False, 'json_or_hash_inputs_changed': False}
        if apply and changes:
            backup.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation preserves every previous attempt's originals.
            with backup.open('x', encoding='utf-8') as stream:
                json.dump({'database': str(path), 'changes': changes}, stream, ensure_ascii=False)
                stream.flush()
                import os
                os.fsync(stream.fileno())
            apply_sqlite_plan(connection, changes)
            remaining, still_protected = sqlite_plan(connection)
            if remaining or still_protected != protected:
                raise RuntimeError('conversion_postcheck_failed')
            connection.commit()
            result.update(applied=True, backup=backup.name, second_run_changes=0)
        else:
            connection.rollback()
        return result
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate_sqlite(args.database, args.backup, apply=args.apply)))


if __name__ == '__main__':
    main()
