"""Install non-unique lookup indexes on an owned, unfinished PC archive.

No stored row, construction identity, policy, or serving contract is rewritten.
The caller must use the existing builder lock and an explicit frozen code root.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time


INDEXES = (
    ('archive_perf_article_identity', 'central_news_article_revisions', 'identity,article_revision_id'),
    ('archive_perf_article_recent', 'central_news_article_revisions', 'stock_code,accepted_sequence DESC'),
    ('archive_perf_article_latest', 'central_news_article_revisions', 'stock_code,identity,accepted_sequence DESC'),
    ('archive_perf_body_latest', 'central_news_body_revisions', 'article_revision_id,accepted_sequence DESC'),
    ('archive_perf_event_key', 'central_news_event_revisions', 'stock_code,event_key,accepted_sequence DESC,event_id'),
    ('archive_perf_event_latest', 'central_news_event_revisions', 'event_id,accepted_sequence DESC'),
    ('archive_perf_member_article', 'central_news_event_membership_revisions', 'article_revision_id,event_revision_id,event_id'),
    ('archive_perf_member_latest', 'central_news_event_membership_revisions', 'event_id,accepted_sequence DESC,article_revision_id'),
)

QUERIES = {
    'event_identity': ("SELECT DISTINCT m.event_id FROM central_news_event_membership_revisions m JOIN central_news_article_revisions a ON a.article_revision_id=m.article_revision_id JOIN central_news_event_revisions e ON e.event_revision_id=m.event_revision_id WHERE e.stock_code=? AND a.identity=?", ('005930', 'index-probe')),
    'event_key': ("SELECT DISTINCT event_id FROM central_news_event_revisions WHERE stock_code=? AND event_key=?", ('005930', 'index-probe')),
    'recent100': ("SELECT document_json,identity FROM central_news_article_revisions WHERE stock_code=? AND article_revision_id<>? ORDER BY accepted_sequence DESC LIMIT 100", ('005930', 'index-probe')),
    'previous_article': ("SELECT article_revision_id FROM central_news_article_revisions WHERE stock_code=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1", ('005930', 'index-probe')),
    'previous_event': ("SELECT event_revision_id,certainty FROM central_news_event_revisions WHERE event_id=? ORDER BY accepted_sequence DESC LIMIT 1", ('index-probe',)),
    'previous_membership': ("SELECT membership_revision_id FROM central_news_event_membership_revisions WHERE event_id=? AND article_revision_id IN (SELECT article_revision_id FROM central_news_article_revisions WHERE identity=?) ORDER BY accepted_sequence DESC LIMIT 1", ('index-probe', 'index-probe')),
}


def _sql(item):
    name, table, columns = item
    return f'CREATE INDEX {name} ON {table}({columns})'


def _plans(db):
    return {name: list(db.execute('EXPLAIN QUERY PLAN '+sql, args))
            for name, (sql, args) in QUERIES.items()}


def _install_indices(db, definitions=INDEXES):
    """Requires caller transaction; any failure must roll back the complete set."""
    if not db.in_transaction:
        raise ValueError('caller transaction required')
    created = []
    for item in definitions:
        existing = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (item[0],)).fetchone()
        expected = _sql(item)
        if existing:
            if existing[0] != expected:
                raise ValueError('existing index definition differs: '+item[0])
        else:
            db.execute(expected)
            created.append(item[0])
    return created


def install(path: Path, code_root: Path, dataset: str, digest: str):
    path = path.resolve(strict=True)
    code_root = code_root.resolve(strict=True)
    # This CLI runs in a fresh process. Never borrow mutable workspace imports.
    for source in (code_root/'src', code_root):
        sys.path.insert(0, str(source))
    api = importlib.import_module('scripts.prepared_historical_archive_v2')
    if Path(api.__file__).resolve() != code_root/'scripts/prepared_historical_archive_v2.py':
        raise ValueError('frozen builder import mismatch')
    api._require_local(path)
    if shutil.disk_usage(path.parent).free < 2*1024**3:
        raise ValueError('less than 2GiB free')
    started = time.monotonic()
    with api._exclusive_lock(path), closing(sqlite3.connect(path, timeout=5)) as db:
        before = api._building(db)
        if before.get('dataset_id') != dataset or before.get('builder_code_digest') != digest:
            raise ValueError('explicit construction identity mismatch')
        plans_before = _plans(db)
        tables = tuple(dict.fromkeys(item[1] for item in INDEXES))
        counts_before = {table: db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in tables}
        # Maintenance is intentionally permitted while the builder's phase is
        # paused. The OS lock and matching generation still guard all writes.
        with api._transaction(db, check_capacity=False):
            created = _install_indices(db)
            if api.report(db) != before:
                raise ValueError('construction manifest changed during index installation')
            counts_after = {table: db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in tables}
            if counts_after != counts_before:
                raise ValueError('stored row counts changed during index installation')
        plans_after = _plans(db)
    return {'state': 'complete', 'archive': str(path), 'dataset_id': dataset,
            'construction_digest': digest, 'created': created,
            'rows_before': counts_before, 'rows_after': counts_after,
            'plans_before': plans_before, 'plans_after': plans_after,
            'elapsed_seconds': round(time.monotonic()-started, 3),
            'note': 'query plans verified; end-to-end throughput is not yet measured'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--code-root', type=Path, required=True)
    parser.add_argument('--expected-dataset', required=True)
    parser.add_argument('--expected-digest', required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.archive, args.code_root, args.expected_dataset,
                             args.expected_digest), ensure_ascii=False))
