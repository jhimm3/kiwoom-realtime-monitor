"""PC-owned v2 archive construction; never calls NAS processing or network BODY.

The v1 serving artifact and unfinished v1 builders remain explicit versions.
This module owns the v2 input/target ledger and bounded construction lifecycle;
article/BODY and event policy are reused from the existing finalizers.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time
from typing import Any, Iterator
import uuid
from urllib.parse import urlsplit
from scripts.prepared_historical_archive_inputs import (
    check_control, component_connections, generation, prepared_batch, select_source,
)

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from scripts.stage_prepared_historical_news_archive import _input_hash, _sha256
from scripts.finalize_prepared_historical_article_bodies import _complete_one, _CompletionBatch, _insert_many, _chunks
from scripts.finalize_prepared_historical_news_events import _resolve_event, _group_candidates
from scripts.build_prepared_historical_search_projection import _publication
from scripts.verify_prepared_historical_news_rules import _code_digest as _rule_digest
from scripts.preprocess_historical_news_to_nas import prepare_job, _prepare_rule_job
from kiwoom_monitor.application.news_rules import SUPPLY_CONTRACT_RULE_VERSION
from kiwoom_monitor.infrastructure.article_text import clean_article_text

SEARCH = "historical_backfill"
MARKET = "historical_market_backfill"
SCOPES = (SEARCH, MARKET)
POLICY = "historical-news-archive/v2"
EVENT_DEPENDENCY_CACHE_LIMIT = 65536
WORLD_EXCLUSION = 'world-service-before-2021-01-01'
EVENT_REVIEW_PREFIX = 'event_conflict_review_v1_'
EVENT_DECISION_PREFIX = 'event_conflict_decision_v1_'


def world_service_excluded(source: str, published_at: str | None) -> bool:
    if source != 'world' or not published_at:
        return False
    try:
        stamp=datetime.fromisoformat(published_at.replace('Z','+00:00'))
        kst=timezone(timedelta(hours=9))
        if stamp.tzinfo is None:
            stamp=stamp.replace(tzinfo=kst)
        return stamp.astimezone(kst).date().isoformat()<'2021-01-01'
    except (ValueError,OverflowError):
        return False


def _seed_world_excluded(encoded: str) -> int:
    document=json.loads(encoded)
    known=False
    for key in ('link','original_link'):
        value=urlsplit(str(document.get(key) or ''))
        known |= value.hostname=='stock.naver.com' and value.path.startswith('/news/worldnews/')
    return int(world_service_excluded('world' if known else '',document.get('published_at')))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _review_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _review_rows(db: sqlite3.Connection, query: str, args=()) -> list[dict[str, Any]]:
    cursor = db.execute(query, args)
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor]


def _review_one(db: sqlite3.Connection, table: str, column: str, key: str) -> dict[str, Any]:
    rows = _review_rows(db, f'SELECT * FROM {table} WHERE {column}=?', (key,))
    if len(rows) != 1:
        raise ValueError('event review referenced row is missing or ambiguous')
    return rows[0]


def _event_conflict_snapshot(db: sqlite3.Connection, key: list[str]) -> dict[str, Any]:
    if len(key) != 4 or not all(isinstance(item, str) and item for item in key):
        raise ValueError('invalid event review target key')
    where = ' WHERE scope=? AND stock_code=? AND identity=? AND target_id=?'
    targets = _review_rows(db, 'SELECT * FROM archive_targets_v2'+where, key)
    ready = _review_rows(db, 'SELECT * FROM archive_event_ready_v2'+where, key)
    if len(targets) != 1 or len(ready) != 1:
        raise ValueError('event review target or ready row missing')
    target = targets[0]
    result = json.loads(target['computed_json']).get('rule_result')
    if not isinstance(result, dict) or not result.get('event_key'):
        raise ValueError('event review requires a rule event key')
    reason, candidates = _group_candidates(db, key[3], key[2], result['event_key'])
    candidates = tuple(sorted(candidates))
    if reason != 'event_key' or len(candidates) < 2:
        raise ValueError('only ambiguous event-key reviews are supported')
    histories = []
    for event_id in candidates:
        histories.append({
            'event_id': event_id,
            'events': _review_rows(db, 'SELECT * FROM central_news_event_revisions WHERE event_id=? ORDER BY accepted_sequence', (event_id,)),
            'memberships': _review_rows(db, 'SELECT * FROM central_news_event_membership_revisions WHERE event_id=? ORDER BY accepted_sequence', (event_id,)),
        })
    selected = db.execute(
        'SELECT event_id FROM central_news_event_revisions WHERE stock_code=? AND event_key=? ORDER BY accepted_sequence DESC LIMIT 1',
        (key[3], result['event_key']),
    ).fetchone()[0]
    article = _review_one(db, 'central_news_article_revisions', 'article_revision_id', target['article_revision_id'])
    body = _review_one(db, 'central_news_body_revisions', 'body_revision_id', target['body_revision_id'])
    events = json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()[0])
    return {'target': target, 'ready': ready[0], 'events_manifest': events,
            'candidate_event_ids': list(candidates), 'histories': histories,
            'selected_event_id': selected, 'article_sha256': _review_hash(article),
            'body_sha256': _review_hash(body)}


def _check_review_evidence(db: sqlite3.Connection, evidence: dict[str, Any], snapshot: dict[str, Any]) -> None:
    payload = dict(evidence)
    if payload.pop('review_payload_sha256', None) != _review_hash(payload):
        raise ValueError('event review evidence payload hash differs')
    if (evidence.get('schema') != 'historical-event-conflict-review-evidence/v1' or
            evidence.get('status') != 'design_review_only_not_applied' or
            evidence.get('target_before') != snapshot['target'] or
            evidence.get('ready_before') != snapshot['ready'] or
            evidence.get('manifest', {}).get('events_v2') != snapshot['events_manifest'] or
            evidence.get('candidate_event_ids') != snapshot['candidate_event_ids'] or
            evidence.get('selected_event_id') != snapshot['selected_event_id'] or
            _review_hash(evidence.get('target_article')) != snapshot['article_sha256'] or
            evidence.get('target_body_sha256') != snapshot['body_sha256']):
        raise ValueError('event review evidence differs from the conflict snapshot')
    reviews = evidence.get('candidates', [])
    if [item.get('event_id') for item in reviews] != snapshot['candidate_event_ids']:
        raise ValueError('event review evidence candidate set differs')
    for evidence_row, history in zip(reviews, snapshot['histories']):
        article = _review_one(db, 'central_news_article_revisions', 'article_revision_id', evidence_row['evidence_article']['article_revision_id'])
        body = _review_one(db, 'central_news_body_revisions', 'body_revision_id', evidence_row['evidence_body_id'])
        if (evidence_row['event_rows'] != history['events'] or
                evidence_row['membership_rows'] != history['memberships'] or
                evidence_row['evidence_article'] != article or
                evidence_row['evidence_body_sha256'] != _review_hash(body) or
                body['article_revision_id'] != article['article_revision_id'] or
                not any(row['article_revision_id'] == article['article_revision_id'] and
                        row['body_revision_id'] == body['body_revision_id'] for row in history['events'])):
            raise ValueError('reviewed candidate history or BODY evidence changed')


def _decision_identity(decision: dict[str, Any]) -> str:
    value = dict(decision)
    identity = value.pop('decision_id', None)
    if (value.get('schema') != 'historical-event-conflict-decision/v1' or
            identity != _review_hash(value)):
        raise ValueError('event conflict decision identity invalid')
    return identity


def validate_event_conflict_decision(db: sqlite3.Connection, decision: dict[str, Any], *, parent_identity=None) -> None:
    _decision_identity(decision)
    current = report(db)
    expected = parent_identity or (current.get('dataset_id'), current.get('builder_code_digest'))
    if (decision.get('parent_dataset'), decision.get('parent_digest')) != tuple(expected):
        raise ValueError('event conflict decision parent mismatch')
    filename = db.execute('PRAGMA database_list').fetchone()[2]
    if str(Path(filename).resolve()) != decision.get('archive') or current.get('seed_sha256') != decision.get('seed_sha256'):
        raise ValueError('event conflict decision archive mismatch')
    snapshot = _event_conflict_snapshot(db, decision['target_key'])
    if snapshot != decision.get('snapshot'):
        raise ValueError('event conflict decision snapshot changed')
    target, ready, events = snapshot['target'], snapshot['ready'], snapshot['events_manifest']
    progress = db.execute('SELECT progress_state,input_hash,article_revision_id,body_revision_id FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?',decision['target_key'][:3]).fetchone()
    if progress != ('assessment_done',target['input_hash'],target['article_revision_id'],target['body_revision_id']):
        raise ValueError('event conflict input progress differs')
    conflicts = db.execute("SELECT scope,stock_code,identity,target_id FROM archive_event_ready_v2 WHERE state='conflict' LIMIT 2").fetchall()
    if (conflicts != [tuple(decision['target_key'])] or target['event_state'] != 'conflict' or
            ready['state'] != 'conflict' or not ready['ready'] or
            target['event_reason'] != 'ambiguous_event_key' or events.get('state') != 'building' or
            target['ordinal'] != events['next_ordinal']-1 or
            json.loads(target['candidate_event_ids_json']) != snapshot['candidate_event_ids'] or
            any(target[name] is not None for name in ('event_id','event_revision_id','membership_revision_id')) or
            not 1 <= events['next_ordinal'] <= events['target_count']+1):
        raise ValueError('event conflict checkpoint is not the reviewed boundary')
    evidence_path = Path(decision['evidence_path']).resolve(strict=True)
    if _sha256(evidence_path) != decision['evidence_sha256']:
        raise ValueError('event conflict evidence file changed')
    _check_review_evidence(db, json.loads(evidence_path.read_text(encoding='utf-8')), snapshot)


def create_event_conflict_decision(path: Path, evidence_path: Path, output: Path) -> dict[str, Any]:
    """Freeze an already reviewed, single-target choice; never mutate SQLite."""
    evidence_path = evidence_path.resolve(strict=True)
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    key = [evidence['target_before'][name] for name in ('scope','stock_code','identity','target_id')]
    with closing(sqlite3.connect(path.resolve(strict=True).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        current = report(db)
        decision = {'schema': 'historical-event-conflict-decision/v1', 'archive': str(path.resolve()),
                    'parent_dataset': current['dataset_id'], 'parent_digest': current['builder_code_digest'],
                    'seed_sha256': current.get('seed_sha256'), 'target_key': key,
                    'evidence_path': str(evidence_path), 'evidence_sha256': _sha256(evidence_path),
                    'snapshot': _event_conflict_snapshot(db, key)}
        decision['decision_id'] = _review_hash(decision)
        validate_event_conflict_decision(db, decision)
    with output.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(_json(decision)+'\n')
    return {'decision_id': decision['decision_id'], 'path': str(output.resolve()), 'state': 'reviewed_not_applied'}


def _event_review_plan(db: sqlite3.Connection, decision: dict[str, Any], dataset: str, digest: str) -> None:
    identity = _decision_identity(decision)
    registered = db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(EVENT_DECISION_PREFIX+identity,)).fetchone()
    if registered:
        expected = {'schema':'historical-event-conflict-registration/v1','dataset_id':dataset,
                    'code_digest':digest,'decision':decision}
        if json.loads(registered[0]) != expected or (decision['parent_dataset'],decision['parent_digest']) != (dataset,digest):
            raise ValueError('registered event review identity differs')
        return
    row = db.execute('SELECT parent_dataset,parent_digest,plan_path,plan_sha256,state FROM archive_execution_transitions_v1 WHERE dataset_id=? AND code_digest=?', (dataset,digest)).fetchone()
    if not row or row[4] != 'complete' or row[:2] != (decision['parent_dataset'],decision['parent_digest']):
        raise ValueError('reviewed event transition identity missing')
    encoded = Path(row[2]).read_bytes()
    plan = json.loads(encoded)
    if (hashlib.sha256(encoded).hexdigest() != row[3] or
            plan.get('schema') != 'historical-active-events-execution/v1' or
            (plan.get('dataset_id'),plan.get('code_digest')) != (dataset,digest) or
            plan.get('reviewed_event_resolution') != decision):
        raise ValueError('reviewed event decision is not pinned to its transition')


def register_event_conflict_decision(path: Path, decision_path: Path) -> dict[str, Any]:
    """Pin another independently reviewed target without changing frozen code."""
    decision=json.loads(decision_path.read_text(encoding='utf-8'));identity=_decision_identity(decision)
    with _writer(path) as db:
        with _transaction(db):
            current=report(db);key=EVENT_DECISION_PREFIX+identity
            registration={'schema':'historical-event-conflict-registration/v1','dataset_id':current['dataset_id'],
                          'code_digest':current['builder_code_digest'],'decision':decision}
            existing=db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(key,)).fetchone()
            if existing:
                if json.loads(existing[0]) != registration:
                    raise ValueError('different event review registration exists')
                _event_review_plan(db,decision,current['dataset_id'],current['builder_code_digest'])
                return {'state':'registered','decision_id':identity,'replayed':True}
            if db.execute("SELECT 1 FROM archive_build_manifest WHERE key IN ('projection_v2','audit_v2','seal_v2') LIMIT 1").fetchone():
                raise ValueError('a later archive phase has already started')
            validate_event_conflict_decision(db,decision)
            db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',(key,_json(registration)))
    return {'state':'registered','decision_id':identity,'replayed':False}


def validate_event_review_receipt(db: sqlite3.Connection, receipt: dict[str, Any]) -> str:
    """Validate both sides of the sparse recovery ledger during replay/audit."""
    decision = receipt['decision']; identity = _decision_identity(decision)
    _event_review_plan(db, decision, receipt['dataset_id'], receipt['code_digest'])
    if receipt.get('schema') != 'historical-event-conflict-receipt/v1' or receipt.get('state') != 'complete':
        raise ValueError('event conflict receipt state invalid')
    snapshot = decision['snapshot']; before = snapshot['target']; after = receipt['target_after']
    changed = {'event_state','event_id','event_revision_id','membership_revision_id','event_reason'}
    if ({k:v for k,v in before.items() if k not in changed} != {k:v for k,v in after.items() if k not in changed} or
            after['event_state'] != 'connected' or after['event_reason'] != 'reviewed_event_key:'+identity or
            after['event_id'] != snapshot['selected_event_id'] or
            receipt['events_manifest_after'] != snapshot['events_manifest']):
        raise ValueError('event review changed immutable target or cursor')
    key = decision['target_key']
    actual = _review_rows(db, 'SELECT * FROM archive_targets_v2 WHERE scope=? AND stock_code=? AND identity=? AND target_id=?', key)
    if actual != [after]:
        raise ValueError('event review receipt does not match its target')
    ready = _review_rows(db, 'SELECT * FROM archive_event_ready_v2 WHERE scope=? AND stock_code=? AND identity=? AND target_id=?', key)
    if ready != [{**snapshot['ready'], 'state': 'connected'}]:
        raise ValueError('event review ready ledger differs')
    for table, column, value in (
            ('central_news_event_revisions','event_revision_id',receipt['event_after']),
            ('central_news_event_membership_revisions','membership_revision_id',receipt['membership_after'])):
        if _review_one(db,table,column,value[column]) != value:
            raise ValueError('event review output revision changed')
    event, member = receipt['event_after'], receipt['membership_after']
    from kiwoom_monitor.application.news_rules import SupplyContractRuleResult, rule_input_hash
    rule = SupplyContractRuleResult(**json.loads(before['computed_json'])['rule_result'])
    if (event['event_id'] != after['event_id'] or event['stock_code'] != before['target_id'] or
            event['article_revision_id'] != before['article_revision_id'] or event['body_revision_id'] != before['body_revision_id'] or
            event['rule_version'] != rule.rule_version or event['input_hash'] != rule_input_hash(before['article_revision_id'],before['body_revision_id'],rule) or
            event['event_revision_id'] != after['event_revision_id'] or member['membership_revision_id'] != after['membership_revision_id'] or
            (member['event_id'],member['event_revision_id'],member['article_revision_id'],member['body_revision_id']) !=
            (event['event_id'],event['event_revision_id'],event['article_revision_id'],event['body_revision_id'])):
        raise ValueError('event review output relations invalid')
    selected_history = next(h for h in snapshot['histories'] if h['event_id']==snapshot['selected_event_id'])
    previous = selected_history['events'][-1]
    if event['revision_of'] != previous['event_revision_id']:
        raise ValueError('event review parent revision differs')
    original = json.loads(before['computed_json'])['rule_result']
    saved = json.loads(event['result_json'])
    writer_fields = {'novelty','novelty_score','possible_related_event_ids','article_revision_id','body_revision_id'}
    if ({k:v for k,v in saved.items() if k not in writer_fields} != {k:v for k,v in original.items() if k not in writer_fields} or
            any(event[field] != original[field] for field in ('event_key','event_type','role','scope','certainty','amount_won','counterparty','importance_score','confidence_score','ai_required'))):
        raise ValueError('event review changed the prepared rule result')
    for history in snapshot['histories']:
        for table, column, items in (
                ('central_news_event_revisions','event_revision_id',history['events']),
                ('central_news_event_membership_revisions','membership_revision_id',history['memberships'])):
            for value in items:
                if _review_one(db,table,column,value[column]) != value:
                    raise ValueError('event review changed a pre-existing revision')
    for table, column, value, hashed in (
            ('central_news_article_revisions','article_revision_id',before['article_revision_id'],snapshot['article_sha256']),
            ('central_news_body_revisions','body_revision_id',before['body_revision_id'],snapshot['body_sha256'])):
        if _review_hash(_review_one(db,table,column,value)) != hashed:
            raise ValueError('event review changed its article or body')
    evidence_path = Path(decision['evidence_path'])
    if _sha256(evidence_path) != decision['evidence_sha256']:
        raise ValueError('event review evidence file changed')
    evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
    for value in evidence['candidates']:
        article = _review_one(db,'central_news_article_revisions','article_revision_id',value['evidence_article']['article_revision_id'])
        body = _review_one(db,'central_news_body_revisions','body_revision_id',value['evidence_body_id'])
        if article != value['evidence_article'] or _review_hash(body) != value['evidence_body_sha256']:
            raise ValueError('event review candidate evidence changed')
    return identity


def resolve_event_conflict(path: Path, decision_path: Path) -> dict[str, Any]:
    decision = json.loads(decision_path.read_text(encoding='utf-8'))
    identity = _decision_identity(decision)
    with _writer(path) as db:
        with _transaction(db):
            current = report(db)
            _event_review_plan(db,decision,current['dataset_id'],current['builder_code_digest'])
            key = EVENT_REVIEW_PREFIX+identity
            prior = db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(key,)).fetchone()
            if prior:
                validate_event_review_receipt(db,json.loads(prior[0]))
                return {'state':'complete','decision_id':identity,'replayed':True}
            validate_event_conflict_decision(db,decision,parent_identity=(decision['parent_dataset'],decision['parent_digest']))
            before = decision['snapshot']['target']; target_key = decision['target_key']
            result = _resolve_event(db,before['target_id'],before['identity'],before['article_revision_id'],before['body_revision_id'],
                                    json.loads(before['computed_json']),SUPPLY_CONTRACT_RULE_VERSION,
                                    reviewed_choice=decision['snapshot'])
            outcome, _, event_id, event_revision, membership, _, _, candidates = result
            if outcome != 'connected':
                raise ValueError('reviewed conflict did not connect an event')
            reason = 'reviewed_event_key:'+identity
            updated = db.execute("UPDATE archive_targets_v2 SET event_state=?,event_id=?,event_revision_id=?,membership_revision_id=?,event_reason=? WHERE scope=? AND stock_code=? AND identity=? AND target_id=? AND event_state='conflict'",
                                 (outcome,event_id,event_revision,membership,reason,*target_key)).rowcount
            readied = db.execute("UPDATE archive_event_ready_v2 SET state=? WHERE scope=? AND stock_code=? AND identity=? AND target_id=? AND state='conflict'",(outcome,*target_key)).rowcount
            if updated != 1 or readied != 1 or list(candidates) != decision['snapshot']['candidate_event_ids']:
                raise ValueError('reviewed conflict lost its checkpoint')
            if not db.execute("SELECT 1 FROM archive_targets_v2 WHERE article_revision_id=? AND event_state IN ('pending','conflict') LIMIT 1",(before['article_revision_id'],)).fetchone():
                db.execute("UPDATE archive_event_ready_v2 SET ready=1 WHERE parent_id=? AND state='pending'",(before['article_revision_id'],))
            if not db.execute("SELECT 1 FROM archive_targets_v2 WHERE scope=? AND stock_code=? AND identity=? AND event_state IN ('pending','conflict') LIMIT 1",target_key[:3]).fetchone():
                if db.execute("UPDATE archive_input_progress SET progress_state='event_done' WHERE scope=? AND stock_code=? AND identity=? AND progress_state='assessment_done'",target_key[:3]).rowcount != 1:
                    raise ValueError('reviewed event input progress is inconsistent')
            receipt = {'schema':'historical-event-conflict-receipt/v1','state':'complete','decision':decision,
                       'dataset_id':current['dataset_id'],'code_digest':current['builder_code_digest'],
                       'target_after':_review_rows(db,'SELECT * FROM archive_targets_v2 WHERE scope=? AND stock_code=? AND identity=? AND target_id=?',target_key)[0],
                       'events_manifest_after':json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()[0]),
                       'event_after':_review_one(db,'central_news_event_revisions','event_revision_id',event_revision),
                       'membership_after':_review_one(db,'central_news_event_membership_revisions','membership_revision_id',membership)}
            validate_event_review_receipt(db,receipt)
            db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',(key,_json(receipt)))
    return {'state':'complete','decision_id':identity,'event_id':event_id,'event_revision_id':event_revision,
            'membership_revision_id':membership,'next_ordinal':receipt['events_manifest_after']['next_ordinal'],'replayed':False}


def code_digest() -> str:
    digest = hashlib.sha256(_rule_digest().encode())
    for name in ("scripts/prepared_historical_archive_v2.py",
                 "scripts/transition_historical_archive_execution.py",
                 "scripts/prepared_historical_archive_inputs.py",
                 "scripts/stage_prepared_historical_news_archive.py",
                 "scripts/materialize_historical_news_seed.py",
                 "scripts/export_historical_news_seed.py",
                 "scripts/build_prepared_historical_search_projection.py",
                 "scripts/snapshot_prepared_historical_news.py",
                 "scripts/finalize_prepared_historical_article_bodies.py",
                 "scripts/finalize_prepared_historical_news_events.py",
                 "src/kiwoom_monitor/central_server/database_news_revisions.py",
                 "src/kiwoom_monitor/central_server/database.py",
                 "src/kiwoom_monitor/central_server/central_schema.py",
                 "src/kiwoom_monitor/central_server/database_news_sources.py",
                 "src/kiwoom_monitor/domain/news_observation.py",
                 "src/kiwoom_monitor/central_server/database_codec.py",
                 "scripts/capture_historical_archive_sources_v2.py",
                 "scripts/audit_prepared_historical_archive_v2.py",
            "scripts/seal_prepared_historical_archive_v2.py",
                 "src/kiwoom_monitor/infrastructure/historical_backfill.py",
                 "src/kiwoom_monitor/infrastructure/naver_stock_market_news.py",
                 "src/kiwoom_monitor/infrastructure/article_text.py"):
        data = (ROOT / name).read_bytes()
        digest.update(name.encode()); digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    return digest.hexdigest()


def report(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute("SELECT value_json FROM archive_build_manifest WHERE key='report'").fetchone()
    if not row:
        raise ValueError("archive seed manifest missing")
    return json.loads(row[0])


def _building(db: sqlite3.Connection) -> dict[str, Any]:
    value = report(db)
    if value.get("build_state") != "building" or value.get("archive_schema_version") != 2:
        raise ValueError("a building v2 archive is required")
    if value.get("builder_code_digest") != code_digest():
        raise ValueError("archive construction code changed; create a new generation")
    if value.get('construction_contract'):
        inputs = generation(db)
        if not inputs or inputs.get('state') != 'complete' or inputs.get('dataset_id') != value.get('dataset_id'):
            raise ValueError('input generation adoption unfinished')
    return value


@contextmanager
def _transaction(db: sqlite3.Connection, *, check_capacity: bool=True) -> Iterator[None]:
    if db.in_transaction:
        raise ValueError("nested archive transaction")
    if check_capacity:
        check_control(db)
    filename=db.execute('PRAGMA database_list').fetchone()[2]
    if check_capacity and filename and shutil.disk_usage(Path(filename).parent).free<2*1024**3:
        raise ValueError('archive build paused: less than 2GiB free; committed batches are resumable')
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """OS lock disappears on process death; persistent ledger ownership is evidence."""
    with path.with_name(path.name + ".builder.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("another archive builder owns the generation") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _require_local(path: Path) -> None:
    if str(path).startswith('\\\\'):
        raise ValueError('writable archive must be on a PC local filesystem')
    if os.name=='nt':
        import ctypes
        drive_type=ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor))
        if drive_type not in (2,3):  # removable or fixed local disk
            raise ValueError('writable archive must be on a PC local filesystem')


@contextmanager
def _writer(path: Path, *, cache_kib: int | None = None) -> Iterator[sqlite3.Connection]:
    path = path.resolve(strict=True)
    _require_local(path)
    with _exclusive_lock(path), closing(sqlite3.connect(path, timeout=5)) as db:
        db.execute("PRAGMA busy_timeout=5000")
        if cache_kib is not None:
            if type(cache_kib) is not int or not 2000 <= cache_kib <= 131072:
                raise ValueError('invalid archive cache capacity')
            db.execute('PRAGMA cache_size=-'+str(cache_kib))
        _building(db)
        from scripts.transition_historical_archive_execution import finalization_lineage
        finalization_lineage(db, check_file=True)
        owner = uuid.uuid4().hex
        with _transaction(db):
            # Holding the OS lock proves a stale persistent owner no longer
            # owns this generation. No PID guessing or active-builder takeover.
            db.execute("UPDATE archive_builder_v2 SET owner=?,started_at=? WHERE id=1", (owner, time.time()))
        try:
            yield db
        finally:
            if db.in_transaction:
                db.rollback()
            released = db.execute("SELECT owner FROM archive_builder_v2 WHERE id=1").fetchone() == (None,)
            if not (released and report(db).get('build_state')=='sealed'):
                with _transaction(db,check_capacity=False):
                    if db.execute("UPDATE archive_builder_v2 SET owner=NULL WHERE id=1 AND owner=?", (owner,)).rowcount != 1:
                        raise ValueError("archive builder lost ownership")


def initialize(path: Path, *, dataset_id: str) -> dict[str, Any]:
    """Select v2 only on a new materialized seed, never migrate a v1 build."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", dataset_id):
        raise ValueError("invalid dataset ID")
    _require_local(path.resolve(strict=True))
    with closing(sqlite3.connect(path.resolve(strict=True))) as db:
        value = report(db)
        if value.get("archive_schema_version") == 2:
            _building(db)
            if value.get("dataset_id") != dataset_id:
                raise ValueError("dataset ID changed")
            return value
        if value.get("build_state") != "building":
            raise ValueError("only a new seed may select v2")
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='archive_input_progress'").fetchone():
            raise ValueError("existing v1 input ledger cannot be migrated")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='archive_seed_roles'").fetchone():
            raise ValueError("materialized seed roles missing")
        statements = (
            "CREATE TABLE archive_builder_v2(id INTEGER PRIMARY KEY CHECK(id=1),owner TEXT,started_at REAL)",
            "INSERT INTO archive_builder_v2(id) VALUES(1)",
            "CREATE TABLE archive_sources_v2(scope TEXT PRIMARY KEY,source_path TEXT NOT NULL,source_sha256 TEXT NOT NULL,expected_rows INTEGER NOT NULL,staged_rows INTEGER NOT NULL,last_stock TEXT,last_identity TEXT,state TEXT NOT NULL)",
            "CREATE TABLE archive_input_progress(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,input_hash TEXT NOT NULL,source_state TEXT NOT NULL,progress_state TEXT NOT NULL,error TEXT NOT NULL,article_revision_id TEXT,body_revision_id TEXT,PRIMARY KEY(scope,stock_code,identity))",
            "CREATE INDEX archive_input_progress_state ON archive_input_progress(scope,progress_state,stock_code,identity)",
            "CREATE TABLE archive_prepared_scope_origins_v2(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,original_scope TEXT NOT NULL,input_hash TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))",
            "CREATE TABLE archive_targets_v2(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,target_id TEXT NOT NULL,target_name TEXT NOT NULL,input_hash TEXT NOT NULL,article_revision_id TEXT NOT NULL,body_revision_id TEXT NOT NULL,fingerprint TEXT NOT NULL,prepared_json TEXT NOT NULL,computed_json TEXT NOT NULL,verification_state TEXT NOT NULL,computed_at REAL NOT NULL,event_state TEXT NOT NULL DEFAULT 'pending',ordinal INTEGER,event_id TEXT,event_revision_id TEXT,membership_revision_id TEXT,event_reason TEXT,candidate_event_ids_json TEXT,PRIMARY KEY(scope,stock_code,identity,target_id))",
            "CREATE INDEX archive_targets_v2_article ON archive_targets_v2(article_revision_id,event_state)",
            "CREATE TABLE archive_body_provenance(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,article_revision_id TEXT NOT NULL,body_revision_id TEXT NOT NULL,source_status TEXT NOT NULL,extractor_version TEXT NOT NULL,provider TEXT NOT NULL,office_id TEXT NOT NULL,source_article_id TEXT NOT NULL,raw_body_sha256 TEXT NOT NULL,prepared_input_hash TEXT NOT NULL,fetched_at REAL NOT NULL,source_url TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))",
            "CREATE TABLE archive_clean_bodies_v2(body_revision_id TEXT PRIMARY KEY,text TEXT NOT NULL)",
            "CREATE TABLE archive_market_sources_v2(identity TEXT NOT NULL,source TEXT NOT NULL,source_item_json TEXT NOT NULL,PRIMARY KEY(identity,source))",
            "CREATE TABLE archive_projection_v2(kind TEXT NOT NULL,owner TEXT NOT NULL,identity TEXT NOT NULL,article_revision_id TEXT,body_revision_id TEXT,sort_missing INTEGER NOT NULL,sort_us INTEGER NOT NULL,display_json TEXT NOT NULL,PRIMARY KEY(kind,owner,identity))",
            "CREATE INDEX archive_projection_v2_page ON archive_projection_v2(kind,owner,sort_missing,sort_us DESC,identity)",
            "CREATE INDEX archive_targets_v2_body ON archive_targets_v2(article_revision_id,body_revision_id,target_id)",
            "CREATE TABLE archive_expected_targets_v2(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,targets_json TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))",
            "CREATE TABLE archive_failures_v2(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,error TEXT NOT NULL,article_json TEXT NOT NULL,body_json TEXT NOT NULL,rules_json TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))",
            "CREATE TABLE archive_input_context_v2(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,article_revision_id TEXT NOT NULL,body_revision_id TEXT NOT NULL,context_json TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))",
            "CREATE INDEX archive_input_context_v2_ids ON archive_input_context_v2(article_revision_id,body_revision_id)",
        )
        with _transaction(db):
            for statement in statements:
                db.execute(statement)
            value.update(archive_schema_version=2, dataset_id=dataset_id,
                         builder_code_digest=code_digest(), required_scopes=list(SCOPES))
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'", (_json(value),))
        return value


def _source(path: Path, expected_sha256: str) -> sqlite3.Connection:
    path = path.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("invalid frozen input SHA-256")
    wal = Path(str(path) + "-wal")
    if (wal.exists() and wal.stat().st_size) or _sha256(path) != expected_sha256:
        raise ValueError("input is not the expected frozen snapshot")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    return db


def _prepared_alias_view(db: sqlite3.Connection, scope: str,
                         scope_aliases: tuple[str, ...]) -> None:
    """Normalize the verified legacy scope in this read-only connection only."""
    if not scope_aliases:
        return
    if scope != MARKET or set(scope_aliases) != {"historical_market"}:
        raise ValueError("unsupported prepared scope alias")
    accepted = {scope, "historical_market"}
    scopes = {row[0] for row in db.execute("SELECT DISTINCT scope FROM main.prepared_news")}
    if scopes - accepted:
        raise ValueError("prepared input contains another scope")
    if db.execute("SELECT 1 FROM main.sqlite_master WHERE name='prepared_scope_origins'").fetchone():
        raise ValueError("use the original prepared input for direct alias normalization")
    if db.execute("SELECT 1 FROM main.prepared_news a JOIN main.prepared_news c "
                  "ON c.scope=? AND c.stock_code=a.stock_code AND c.identity=a.identity "
                  "WHERE a.scope=? LIMIT 1", (scope, "historical_market")).fetchone():
        raise ValueError("alias key overlaps canonical input; no row may be discarded")
    db.create_function("original_input_hash", 8, lambda *values: _input_hash(values))
    # query_only also rejects TEMP DDL. Briefly allow only connection-local
    # views; mode=ro continues to prohibit all writes to the original DB.
    db.execute("PRAGMA query_only=OFF")
    try:
        fields = "stock_code,identity,state,article_json,body_json,rules_json,error,updated_at"
        db.execute("CREATE TEMP VIEW prepared_news AS "
                   f"SELECT '{MARKET}' AS scope,{fields} FROM main.prepared_news WHERE scope='{MARKET}' "
                   "UNION ALL "
                   f"SELECT '{MARKET}' AS scope,{fields} FROM main.prepared_news WHERE scope='historical_market'")
        db.execute("CREATE TEMP VIEW prepared_scope_origins AS "
                   f"SELECT '{MARKET}' AS normalized_scope,stock_code,identity,scope AS original_scope,"
                   f"original_input_hash({fields}) AS input_hash FROM main.prepared_news WHERE scope='historical_market'")
    finally:
        db.execute("PRAGMA query_only=ON")


@contextmanager
def _frozen_source(path: Path, expected_sha256: str, *, scope: str = "",
                   scope_aliases: tuple[str, ...] = ()) -> Iterator[sqlite3.Connection]:
    db = _source(path, expected_sha256)
    failed = False
    try:
        _prepared_alias_view(db, scope, scope_aliases)
        yield db
    except BaseException:
        failed = True
        raise
    finally:
        db.rollback()
        db.close()
        if not failed:
            # Verify once per phase, never once per article/batch. Changes can
            # invalidate a partial build but cannot silently publish mixed input.
            wal = Path(str(path.resolve()) + "-wal")
            if (wal.exists() and wal.stat().st_size) or _sha256(path) != expected_sha256:
                raise ValueError("frozen source changed during archive construction")


def stage(path: Path, prepared_path: Path, scope: str, expected_sha256: str,
          *, max_rows: int = 0, batch_size: int = 250,
          scope_aliases: tuple[str, ...] = ()) -> dict[str, Any]:
    if scope not in SCOPES or max_rows < 0 or not 1 <= batch_size <= 1000:
        raise ValueError("invalid staging arguments")
    if path.resolve() == prepared_path.resolve():
        raise ValueError("archive cannot be its own input")
    with _frozen_source(prepared_path, expected_sha256, scope=scope,
                        scope_aliases=scope_aliases) as source, _writer(path) as db:
        if generation(db):
            raise ValueError('adopted inputs must use registered components')
        if db.execute("SELECT 1 FROM archive_build_manifest WHERE key='events_v2'").fetchone():
            raise ValueError("event inputs are frozen")
        states = dict(source.execute("SELECT state,COUNT(*) FROM prepared_news WHERE scope=? GROUP BY state", (scope,)))
        if not states or set(states) - {"ready", "failed"}:
            raise ValueError("missing or unsupported source states")
        count = sum(states.values())
        has_origins = bool(source.execute("SELECT 1 FROM sqlite_master WHERE name='prepared_scope_origins' "
                                          "UNION ALL SELECT 1 FROM sqlite_temp_master WHERE name='prepared_scope_origins'").fetchone())
        origin_count = source.execute('SELECT COUNT(*) FROM prepared_scope_origins WHERE normalized_scope=?',(scope,)).fetchone()[0] if has_origins else 0
        frozen = (str(prepared_path.resolve()), expected_sha256, count)
        with _transaction(db):
            existing = db.execute("SELECT source_path,source_sha256,expected_rows FROM archive_sources_v2 WHERE scope=?", (scope,)).fetchone()
            if existing is None:
                db.execute("INSERT INTO archive_sources_v2 VALUES(?,?,?,?,0,NULL,NULL,'building')", (scope, *frozen))
            elif existing != frozen:
                raise ValueError("staged scope input changed")
            view_key = 'prepared_scope_view_v2_' + scope
            view_config = {'scope_aliases': sorted(set(scope_aliases))}
            saved_view = db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?', (view_key,)).fetchone()
            if saved_view is None:
                db.execute('INSERT INTO archive_build_manifest VALUES(?,?)', (view_key, _json(view_config)))
            elif json.loads(saved_view[0]) != view_config:
                raise ValueError('prepared scope alias configuration changed')
            origin_key='prepared_scope_origins_v2_'+scope
            saved_origins=db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(origin_key,)).fetchone()
            if saved_origins is None:
                db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',(origin_key,_json({'expected_rows':origin_count})))
            elif json.loads(saved_origins[0])!={'expected_rows':origin_count}:
                raise ValueError('prepared scope origin count changed')
        done = 0
        while not max_rows or done < max_rows:
            state = db.execute("SELECT staged_rows,last_stock,last_identity,state FROM archive_sources_v2 WHERE scope=?", (scope,)).fetchone()
            if state[3] == "complete":
                break
            take = min(batch_size, max_rows-done) if max_rows else batch_size
            rows = source.execute("SELECT stock_code,identity,state,article_json,body_json,rules_json,error,updated_at FROM prepared_news WHERE scope=? AND (stock_code,identity)>(?,?) ORDER BY stock_code,identity LIMIT ?", (scope, state[1] or "", state[2] or "", take)).fetchall()
            if not rows:
                raise ValueError("input ended before expected row count")
            entries = []
            origins = {}
            if has_origins:
                # One bounded key lookup; applies to failed/excluded inputs too.
                for offset in range(0,len(rows),250):
                    chunk=rows[offset:offset+250]
                    placeholders=','.join('(?,?)' for _ in chunk)
                    bindings=[item for row in chunk for item in row[:2]]+[scope]
                    origins.update({(owner,identity):(original,hashed) for owner,identity,original,hashed in source.execute('WITH keys(stock_code,identity) AS (VALUES '+placeholders+') SELECT o.stock_code,o.identity,o.original_scope,o.input_hash FROM keys k CROSS JOIN prepared_scope_origins o WHERE o.normalized_scope=? AND o.stock_code=k.stock_code AND o.identity=k.identity',bindings)})
            excluded_keys=set()
            for row in rows:
                if not row[0] or not row[1] or (row[2] == "failed" and not str(row[6]).strip()):
                    raise ValueError("input has empty identity or unexplained failure")
                article=json.loads(row[3]) if scope==MARKET else {}
                excluded=scope==MARKET and world_service_excluded(article.get('source',''),article.get('document',{}).get('published_at'))
                if excluded:
                    excluded_keys.add((row[0],row[1]))
                origin=origins.get((row[0],row[1]))
                if origin and (scope!=MARKET or origin[0]!='historical_market' or origin[1]!=_input_hash(row)):
                    raise ValueError('prepared scope origin differs from verified input')
                entries.append((scope, str(row[0]), str(row[1]), _input_hash(row), row[2],
                                'source_excluded' if excluded else "source_failed" if row[2] == "failed" else "pending", WORLD_EXCLUSION if excluded else row[6], None, None))
            total = state[0]+len(rows)
            if total > count:
                raise ValueError("staging exceeds frozen source count")
            with _transaction(db):
                db.executemany("INSERT INTO archive_input_progress VALUES(?,?,?,?,?,?,?,?,?)", entries)
                db.executemany('INSERT INTO archive_prepared_scope_origins_v2 VALUES(?,?,?,?,?)',[(scope,owner,identity,original,hashed) for (owner,identity),(original,hashed) in origins.items()])
                db.executemany("INSERT INTO archive_failures_v2 VALUES(?,?,?,?,?,?,?)",[(scope,str(row[0]),str(row[1]),WORLD_EXCLUSION if (row[0],row[1]) in excluded_keys else str(row[6]),row[3],row[4],row[5]) for row in rows if row[2]=='failed' or (row[0],row[1]) in excluded_keys])
                db.execute("UPDATE archive_sources_v2 SET staged_rows=?,last_stock=?,last_identity=?,state=? WHERE scope=?", (total, rows[-1][0], rows[-1][1], "complete" if total == count else "building", scope))
            done += len(rows)
        return {"scope": scope, "staged_now": done, "expected_rows": count}


def validated_targets(article: dict[str, Any], rules: Any, scope: str, owner: str) -> dict[str, dict[str, Any]]:
    targets = article.get("targets")
    if not isinstance(targets, list) or not isinstance(rules, list) or not targets:
        raise ValueError("article has no declared target set")
    expected: dict[str, str] = {}
    for item in targets:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("invalid target tuple")
        code, name = item
        if not isinstance(code, str) or not (code == "GLOBAL" or re.fullmatch(r"[0-9A-Z]{6}", code)) or not isinstance(name, str) or not name:
            raise ValueError("invalid target identity/name")
        if code in expected and expected[code] != name:
            raise ValueError("conflicting duplicate target name")
        expected[code] = name
    if (scope == SEARCH and set(expected) != {owner}) or (scope == MARKET and (owner != "GLOBAL" or "GLOBAL" not in expected)):
        raise ValueError("target set differs from scope/owner")
    actual: dict[str, dict[str, Any]] = {}
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("invalid prepared target result")
        code = rule.get("target_id")
        if code not in expected or rule.get("stock_name") != expected[code]:
            raise ValueError("RULE target differs from declared targets")
        if not isinstance(rule.get("assessment"), dict) or not isinstance(rule.get("core_sentences"), list):
            raise ValueError("invalid prepared assessment")
        result = rule.get("rule_result")
        if result is not None and (not isinstance(result, dict) or result.get("rule_version") != SUPPLY_CONTRACT_RULE_VERSION):
            raise ValueError("unsupported prepared RULE version")
        if code in actual and _json(actual[code]) != _json(rule):
            raise ValueError("conflicting duplicate target result")
        actual[code] = rule
    if set(actual) != set(expected):
        raise ValueError("declared target result missing")
    return actual


def _evidence(raw: sqlite3.Connection | None, cutoff: int, article: dict[str, Any]) -> dict:
    source = article.get("document", {}).get("historical_source")
    if raw is None or not isinstance(source, dict):
        return {}
    key = tuple(str(source.get(k) or "") for k in ("provider", "office_id", "article_id"))
    found = set()
    for version, digest, fetched in raw.execute("SELECT extractor_version,body_sha256,fetched_at FROM news_article_body_snapshots WHERE provider=? AND office_id=? AND article_id=? AND rowid<=?", (*key, cutoff)):
        try:
            epoch = datetime.fromisoformat(str(fetched)).timestamp()
        except (ValueError, OverflowError):
            epoch = None
        found.add((str(version), str(digest), epoch))
    return {key: found} if found else {}


def finalize_inputs(path: Path, prepared_path: Path, scope: str, expected_sha256: str,
                    *, raw_path: Path | None = None, raw_cutoff: int = 0,
                    max_rows: int = 0, batch_size: int = 80,
                    scope_aliases: tuple[str, ...] = (), cache_kib: int | None = None) -> dict[str, Any]:
    if scope not in SCOPES or max_rows < 0 or not 1 <= batch_size <= 250 or raw_cutoff < 0:
        raise ValueError("invalid finalization arguments")
    if path.resolve() == prepared_path.resolve() or (raw_path and path.resolve() == raw_path.resolve()):
        raise ValueError("archive cannot be a source")
    raw = sqlite3.connect(raw_path.resolve(strict=True).as_uri()+"?mode=ro", uri=True) if raw_path else None
    try:
        if raw is not None:
            raw.execute("PRAGMA query_only=ON")
            raw.execute("BEGIN")
            maximum = raw.execute("SELECT COALESCE(MAX(rowid),0) FROM news_article_body_snapshots").fetchone()[0]
            if raw_cutoff > maximum:
                raise ValueError("raw BODY cutoff outside source")
        with _frozen_source(prepared_path, expected_sha256, scope=scope,
                             scope_aliases=scope_aliases) as source, _writer(path, cache_kib=cache_kib) as db, component_connections(db, scope) as components:
            if db.execute("SELECT 1 FROM archive_build_manifest WHERE key='events_v2'").fetchone():
                raise ValueError("event inputs are frozen")
            staged = db.execute("SELECT source_path,source_sha256,state FROM archive_sources_v2 WHERE scope=?", (scope,)).fetchone()
            if staged != (str(prepared_path.resolve()), expected_sha256, "complete"):
                raise ValueError("scope is not completely staged from this input")
            saved_view = db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',
                                    ('prepared_scope_view_v2_' + scope,)).fetchone()
            if saved_view is None or json.loads(saved_view[0]) != {'scope_aliases': sorted(set(scope_aliases))}:
                raise ValueError('prepared scope alias configuration changed')
            config={"raw_path":str(raw_path.resolve()) if raw_path else None,
                    "body_rowid_cutoff":raw_cutoff,"policy":"append-only-body-snapshots/v1"}
            evidence_key='raw_evidence_v2_'+scope
            with _transaction(db):
                saved=db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(evidence_key,)).fetchone()
                if saved is None:
                    db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',(evidence_key,_json(config)))
                elif json.loads(saved[0])!=config:
                    raise ValueError('raw BODY evidence configuration changed within generation')
            digest = code_digest()
            from scripts.transition_historical_archive_execution import finalization_lineage, fingerprint_digest
            lineage = finalization_lineage(db, check_file=True)
            done = targets_done = 0
            while not max_rows or done < max_rows:
                take = min(batch_size, max_rows-done) if max_rows else batch_size
                pending = db.execute("SELECT stock_code,identity,input_hash FROM archive_input_progress WHERE scope=? AND progress_state='pending' ORDER BY stock_code,identity LIMIT ?", (scope, take)).fetchall()
                if not pending:
                    break
                if any(fingerprint_digest(lineage,scope,o,i,digest)!=digest for o,i,_ in pending):
                    raise ValueError('pending input is inside a closed finalized prefix')
                origins={}
                for offset in range(0,len(pending),250):
                    chunk=pending[offset:offset+250]
                    placeholders=','.join('(?,?)' for _ in chunk)
                    values=[scope]+[value for owner,identity,_ in chunk for value in (owner,identity)]
                    origins.update({(owner,identity):(original,hashed) for owner,identity,original,hashed in db.execute('SELECT stock_code,identity,original_scope,input_hash FROM archive_prepared_scope_origins_v2 WHERE scope=? AND (stock_code,identity) IN ('+placeholders+')',values)})
                with _transaction(db):
                    prepared_rows = prepared_batch(db, source, components, scope, pending)
                    completion = _CompletionBatch(db, scope, pending, prepared_rows)
                    metadata = {}
                    def queue(sql, values):
                        prefix, suffix = sql.split(' VALUES', 1)
                        ending = suffix[suffix.index(')')+1:]
                        metadata.setdefault((prefix, ending), []).append(tuple(values))
                    for owner, identity, input_hash in pending:
                        row = prepared_rows.get((owner, identity))
                        if not row or row[2] != "ready":
                            raise ValueError("prepared row changed after staging")
                        article, body, rules = (json.loads(row[i]) for i in (3,4,5))
                        target_rules = validated_targets(article, rules, scope, owner)
                        queue("INSERT INTO archive_expected_targets_v2 VALUES(?,?,?,?)",(scope,owner,identity,_json({key:value['stock_name'] for key,value in target_rules.items()})))
                        provenance={}
                        _complete_one(db, source, scope, owner, identity, input_hash,
                                      _evidence(raw, raw_cutoff, article) if body["body_status"] == "fulltext" else {}, manage_transaction=False,
                                      provenance_out=provenance, prepared_row=row, batch_store=completion)
                        article_id, body_id = completion.updates[-1][:2]
                        original_scope=scope
                        origin=origins.get((owner,identity))
                        if origin:
                            if scope!=MARKET or origin[0]!='historical_market' or origin[1]!=input_hash:
                                raise ValueError('prepared scope origin differs from verified input')
                            original_scope=origin[0]
                        context={'original_prepared_scope':original_scope,
                                 'article':{key:value for key,value in article.items() if key not in ('document','source_item')},
                                 'body':{key:value for key,value in body.items() if key!='body_text'},
                                 'prepared_rule_metadata':[{key:value for key,value in rule.items() if key not in ('assessment','core_sentences','rule_result')} for rule in rules],
                                 'body_provenance':provenance,
                                 'prepared_updated_at':row[7],'prepared_error':row[6]}
                        queue('INSERT INTO archive_input_context_v2 VALUES(?,?,?,?,?,?)',(scope,owner,identity,article_id,body_id,_json(context)))
                        cleaned = clean_article_text(body["body_text"])
                        clean = cleaned if body["body_status"] == "fulltext" else ""
                        queue("INSERT INTO archive_clean_bodies_v2 VALUES(?,?) ON CONFLICT(body_revision_id) DO NOTHING", (body_id, clean if len(clean) >= 40 else ""))
                        for target_id, prepared in sorted(target_rules.items()):
                            computed = _prepare_rule_job({"job_key":"archive-v2", "attempts":1, "stage":"RULE", "target_id":target_id, "payload":{"stock_code":target_id,"stock_name":prepared["stock_name"]}}, article, {"body_text":body["body_text"],"status":body["body_status"]}, cleaned)
                            keys = ("assessment","core_sentences","rule_result")
                            original = {key: prepared[key] for key in keys}
                            result = {key: computed[key] for key in keys}
                            if result["rule_result"] is not None and result["rule_result"].get("rule_version") != SUPPLY_CONTRACT_RULE_VERSION:
                                raise ValueError("computed RULE version invalid")
                            fingerprint = hashlib.sha256(_json((POLICY,input_hash,article_id,body_id,target_id,prepared["stock_name"],digest,result)).encode()).hexdigest()
                            queue("INSERT INTO archive_targets_v2(scope,stock_code,identity,target_id,target_name,input_hash,article_revision_id,body_revision_id,fingerprint,prepared_json,computed_json,verification_state,computed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (scope,owner,identity,target_id,prepared["stock_name"],input_hash,article_id,body_id,fingerprint,_json(original),_json(result),"verified_equal" if _json(original)==_json(result) else "recomputed_posthoc",time.time()))
                        if scope == MARKET:
                            channel = str(article.get("source") or "").lower()
                            if channel not in {"flash","world"}:
                                raise ValueError("unknown prepared market source")
                            queue("INSERT INTO archive_market_sources_v2 VALUES(?,?,?) ON CONFLICT(identity,source) DO UPDATE SET source_item_json=excluded.source_item_json", (identity,channel,_json(article.get("source_item",{}))))
                        targets_done += len(target_rules)
                    completion.flush()
                    for (prefix, suffix), rows in metadata.items():
                        # Preserve each table's first/last input semantics before merging rows.
                        if prefix.startswith('INSERT INTO archive_clean_bodies_v2'):
                            first = {}
                            for row in rows:first.setdefault(row[0],row)
                            rows = list(first.values())
                        elif prefix.startswith('INSERT INTO archive_market_sources_v2'):
                            rows = list({r[:2]: r for r in rows}.values())
                        _insert_many(db,prefix,rows,suffix)
                    for chunk in _chunks(db,[(scope,o,i,h) for o,i,h in pending],4):
                        sql = ('WITH changes(scope,stock,identity,hash) AS (VALUES '+','.join('(?,?,?,?)' for _ in chunk)+') '
                               "UPDATE archive_input_progress AS p SET progress_state='assessment_done' FROM changes c "
                               "WHERE p.scope=c.scope AND p.stock_code=c.stock AND p.identity=c.identity AND p.input_hash=c.hash AND p.progress_state='article_body_done' RETURNING scope,stock_code,identity")
                        changed = db.execute(sql,[v for r in chunk for v in r]).fetchall()
                        if len(changed)!=len(chunk) or set(changed)!={r[:3] for r in chunk}:
                            raise ValueError('assessment progress update lost its ownership')
                done += len(pending)
            return {"scope":scope,"inputs_finalized_now":done,"targets_finalized_now":targets_done,"build_state":"building"}
    finally:
        if raw is not None:
            raw.close()


def _freeze_events(db: sqlite3.Connection) -> None:
    if db.execute("SELECT 1 FROM archive_build_manifest WHERE key='events_v2'").fetchone():
        return
    sources = db.execute("SELECT scope,state,expected_rows,staged_rows FROM archive_sources_v2 ORDER BY scope").fetchall()
    if {row[0] for row in sources} != set(SCOPES) or any(row[1] != "complete" or row[2] != row[3] for row in sources):
        raise ValueError("both scopes must be completely staged")
    if db.execute("SELECT 1 FROM archive_input_progress WHERE progress_state NOT IN ('assessment_done','source_failed','source_excluded') LIMIT 1").fetchone():
        raise ValueError("all input targets must be finalized before event ordering")
    if db.execute("SELECT 1 FROM archive_input_progress p WHERE progress_state='assessment_done' AND NOT EXISTS(SELECT 1 FROM archive_targets_v2 t WHERE t.scope=p.scope AND t.stock_code=p.stock_code AND t.identity=p.identity) LIMIT 1").fetchone():
        raise ValueError("finalized input has no target results")
    db.create_function("archive_sort_missing",1,lambda value: _publication(value)[0],deterministic=True)
    db.create_function("archive_sort_us",1,lambda value: _publication(value)[1],deterministic=True)
    with _transaction(db):
        db.execute("CREATE TABLE archive_event_ready_v2(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,target_id TEXT NOT NULL,article_revision_id TEXT NOT NULL,parent_id TEXT,sort_missing INTEGER NOT NULL,sort_us INTEGER NOT NULL,ready INTEGER NOT NULL,state TEXT NOT NULL DEFAULT 'pending',PRIMARY KEY(scope,stock_code,identity,target_id))")
        db.execute("CREATE INDEX archive_event_ready_v2_next ON archive_event_ready_v2(state,ready,sort_missing,sort_us,scope,target_id,identity,article_revision_id)")
        db.execute("CREATE INDEX archive_event_ready_v2_parent ON archive_event_ready_v2(parent_id,state)")
        db.execute("INSERT INTO archive_event_ready_v2(scope,stock_code,identity,target_id,article_revision_id,parent_id,sort_missing,sort_us,ready) SELECT t.scope,t.stock_code,t.identity,t.target_id,t.article_revision_id,a.revision_of,archive_sort_missing(a.published_at),archive_sort_us(a.published_at),CASE WHEN EXISTS(SELECT 1 FROM archive_targets_v2 parent WHERE parent.article_revision_id=a.revision_of) THEN 0 ELSE 1 END FROM archive_targets_v2 t JOIN central_news_article_revisions a ON a.article_revision_id=t.article_revision_id")
        n=db.execute("SELECT COUNT(*) FROM archive_targets_v2").fetchone()[0]
        if db.execute("SELECT COUNT(*) FROM archive_event_ready_v2").fetchone()[0] != n:
            raise ValueError("event input article relation missing")
        db.execute("INSERT INTO archive_build_manifest VALUES('events_v2',?)",(_json({"policy":POLICY,"target_count":n,"next_ordinal":1,"state":"building"}),))


def finalize_events(path: Path, *, max_rows: int = 0, batch_size: int = 80) -> dict[str, Any]:
    if max_rows < 0 or not 1 <= batch_size <= 250:
        raise ValueError("invalid event batch arguments")
    with _writer(path) as db:
        _freeze_events(db)
        if db.execute("SELECT 1 FROM archive_event_ready_v2 WHERE state='conflict' LIMIT 1").fetchone():
            raise ValueError("ambiguous event requires explicit resolution")
        done=0
        blocked=None
        # Dependency checks used to query the targets table for every resolved
        # target. Large prepared news inputs can have many targets per article
        # and per input identity, so retain the pending counts for each active
        # group and refresh them only on first use. The counters are derived
        # from committed rows each time this function is entered, which keeps
        # crash/restart and max_rows resume behavior unchanged.
        pending_by_article: dict[str, int] = {}
        pending_by_input: dict[tuple[str, str, str], int] = {}
        while not max_rows or done < max_rows:
            take=min(batch_size,max_rows-done) if max_rows else batch_size
            processed=0
            with _transaction(db):
                manifest=json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()[0])
                for _ in range(take):
                    row=db.execute("SELECT q.scope,q.stock_code,q.identity,q.target_id,t.article_revision_id,t.body_revision_id,t.computed_json,t.fingerprint FROM archive_event_ready_v2 q JOIN archive_targets_v2 t ON t.scope=q.scope AND t.stock_code=q.stock_code AND t.identity=q.identity AND t.target_id=q.target_id WHERE q.state='pending' AND q.ready=1 ORDER BY q.sort_missing,q.sort_us,q.scope,q.target_id,q.identity,q.article_revision_id LIMIT 1").fetchone()
                    if not row:
                        break
                    scope,owner,identity,target,article_id,body_id,computed,fingerprint=row
                    input_key=(scope,owner,identity)
                    if article_id not in pending_by_article:
                        pending_count=db.execute(
                            "SELECT COUNT(*) FROM archive_targets_v2 WHERE article_revision_id=? AND event_state IN ('pending','conflict')",
                            (article_id,),
                        ).fetchone()[0]
                        if pending_count > 1:
                            if len(pending_by_article) >= EVENT_DEPENDENCY_CACHE_LIMIT:
                                pending_by_article.clear()
                            pending_by_article[article_id]=pending_count
                    if input_key not in pending_by_input:
                        pending_input_count=db.execute(
                            "SELECT COUNT(*) FROM archive_targets_v2 WHERE scope=? AND stock_code=? AND identity=? AND event_state IN ('pending','conflict')",
                            input_key,
                        ).fetchone()[0]
                        if pending_input_count > 1:
                            if len(pending_by_input) >= EVENT_DEPENDENCY_CACHE_LIMIT:
                                pending_by_input.clear()
                            pending_by_input[input_key]=pending_input_count
                    if pending_by_article.get(article_id,1) < 1 or pending_by_input.get(input_key,1) < 1:
                        raise ValueError('event dependency counters disagree with pending target')
                    resolution=_resolve_event(db,target,identity,article_id,body_id,json.loads(computed),SUPPLY_CONTRACT_RULE_VERSION)
                    outcome,reason,event_id,event_rev,member_id,_,_,candidates=resolution
                    ordinal=manifest["next_ordinal"]
                    manifest["next_ordinal"]+=1
                    db.execute("UPDATE archive_targets_v2 SET event_state=?,ordinal=?,event_id=?,event_revision_id=?,membership_revision_id=?,event_reason=?,candidate_event_ids_json=? WHERE scope=? AND stock_code=? AND identity=? AND target_id=? AND fingerprint=? AND event_state='pending'",(outcome,ordinal,event_id,event_rev,member_id,reason,_json(candidates),scope,owner,identity,target,fingerprint))
                    db.execute("UPDATE archive_event_ready_v2 SET state=? WHERE scope=? AND stock_code=? AND identity=? AND target_id=?",(outcome,scope,owner,identity,target))
                    processed+=1
                    if outcome == "conflict":
                        blocked={"scope":scope,"owner":owner,"identity":identity,"target_id":target,"reason":reason}
                        break
                    if article_id in pending_by_article:
                        pending_by_article[article_id]-=1
                        article_complete=pending_by_article[article_id] == 0
                        if article_complete:
                            del pending_by_article[article_id]
                    else:
                        article_complete=True
                    if article_complete:
                        db.execute("UPDATE archive_event_ready_v2 SET ready=1 WHERE parent_id=? AND state='pending'",(article_id,))
                    if input_key in pending_by_input:
                        pending_by_input[input_key]-=1
                        input_complete=pending_by_input[input_key] == 0
                        if input_complete:
                            del pending_by_input[input_key]
                    else:
                        input_complete=True
                    if input_complete:
                        db.execute("UPDATE archive_input_progress SET progress_state='event_done' WHERE scope=? AND stock_code=? AND identity=? AND progress_state='assessment_done'",(scope,owner,identity))
                # Both counters advance with the same batch transaction; do
                # not rescan millions of remaining targets after every batch.
                remaining=manifest['target_count']-manifest['next_ordinal']+1
                if remaining<0:
                    raise ValueError('event progress exceeds frozen target count')
                if not remaining and not blocked:
                    manifest["state"]="complete"
                if not processed and remaining:
                    raise ValueError("event parent dependency missing or cyclic")
                db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='events_v2'",(_json(manifest),))
            done+=processed
            if not processed or blocked or not remaining:
                break
        return {"targets_resolved_now":done,"remaining_targets":remaining,"blocked":blocked,"build_state":"building"}


def build_projection(path: Path, *, max_rows: int = 0, batch_size: int = 250) -> dict[str, Any]:
    if max_rows < 0 or not 1 <= batch_size <= 1000:
        raise ValueError("invalid projection arguments")
    with _writer(path) as db:
        events=db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()
        if not events or json.loads(events[0]).get('state')!='complete':
            raise ValueError('event resolution incomplete')
        if db.execute("SELECT 1 FROM archive_input_progress WHERE progress_state NOT IN ('event_done','source_failed','source_excluded') LIMIT 1").fetchone():
            raise ValueError('input finalization incomplete')
        db.create_function('archive_world_excluded',1,_seed_world_excluded,deterministic=True)
        if not db.execute("SELECT 1 FROM archive_build_manifest WHERE key='projection_v2'").fetchone():
            with _transaction(db):
                db.execute('CREATE TABLE archive_projection_sources_v2(kind TEXT NOT NULL,owner TEXT NOT NULL,identity TEXT NOT NULL,article_revision_id TEXT,body_revision_id TEXT,accepted_sequence INTEGER NOT NULL,state TEXT NOT NULL DEFAULT \'pending\',PRIMARY KEY(kind,owner,identity))')
                db.execute('CREATE INDEX archive_projection_sources_v2_next ON archive_projection_sources_v2(state,kind,owner,identity)')
                # Explicit seed role owns list inclusion; support-only rows are
                # retained for exact-ID detail but never create list entries.
                db.execute("INSERT INTO archive_projection_sources_v2(kind,owner,identity,article_revision_id,body_revision_id,accepted_sequence) SELECT CASE WHEN a.stock_code='GLOBAL' THEN 'market' ELSE 'search' END,a.stock_code,a.identity,a.article_revision_id,(SELECT body_revision_id FROM central_news_body_revisions b WHERE b.article_revision_id=a.article_revision_id ORDER BY accepted_sequence DESC LIMIT 1),a.accepted_sequence FROM central_news_article_revisions a JOIN archive_seed_roles r ON r.table_name='central_news_article_revisions' AND r.row_key=json_array(a.article_revision_id) AND r.role='historical' WHERE NOT archive_world_excluded(a.document_json) ORDER BY a.accepted_sequence ON CONFLICT(kind,owner,identity) DO UPDATE SET article_revision_id=excluded.article_revision_id,body_revision_id=excluded.body_revision_id,accepted_sequence=excluded.accepted_sequence WHERE excluded.accepted_sequence>archive_projection_sources_v2.accepted_sequence")
                db.execute("INSERT INTO archive_projection_sources_v2(kind,owner,identity,article_revision_id,body_revision_id,accepted_sequence) SELECT CASE WHEN p.scope=? THEN 'market' ELSE 'search' END,p.stock_code,p.identity,p.article_revision_id,p.body_revision_id,a.accepted_sequence FROM archive_input_progress p JOIN central_news_article_revisions a ON a.article_revision_id=p.article_revision_id WHERE p.progress_state='event_done' ORDER BY a.accepted_sequence ON CONFLICT(kind,owner,identity) DO UPDATE SET article_revision_id=excluded.article_revision_id,body_revision_id=excluded.body_revision_id,accepted_sequence=excluded.accepted_sequence WHERE excluded.accepted_sequence>=archive_projection_sources_v2.accepted_sequence",(MARKET,))
                db.execute("INSERT INTO archive_projection_sources_v2(kind,owner,identity,article_revision_id,body_revision_id,accepted_sequence) SELECT CASE WHEN f.scope=? THEN 'market' ELSE 'search' END,f.stock_code,f.identity,NULL,NULL,0 FROM archive_failures_v2 f JOIN archive_input_progress p ON p.scope=f.scope AND p.stock_code=f.stock_code AND p.identity=f.identity WHERE p.progress_state='source_failed' ON CONFLICT(kind,owner,identity) DO NOTHING",(MARKET,))
                db.execute("INSERT INTO archive_build_manifest VALUES('projection_v2',?)",(_json({'state':'building','schema':'historical-news-projection/v2'}),))
        done=0
        while not max_rows or done<max_rows:
            take=min(batch_size,max_rows-done) if max_rows else batch_size
            rows=db.execute("SELECT s.kind,s.owner,s.identity,s.article_revision_id,s.body_revision_id,a.article_revision_id,a.published_at,a.document_json,b.article_revision_id,b.status,c.body_revision_id,CASE WHEN c.body_revision_id IS NULL THEN b.body_text END FROM archive_projection_sources_v2 s LEFT JOIN central_news_article_revisions a ON a.article_revision_id=s.article_revision_id LEFT JOIN central_news_body_revisions b ON b.body_revision_id=s.body_revision_id LEFT JOIN archive_clean_bodies_v2 c ON c.body_revision_id=s.body_revision_id WHERE s.state='pending' ORDER BY s.kind,s.owner,s.identity LIMIT ?",(take,)).fetchall()
            if not rows:
                break
            with _transaction(db):
                for kind,owner,identity,article_id,body_id,stored_article,published_at,encoded_article,body_article,body_status,clean_id,body_text in rows:
                    if article_id is None:
                        failed=db.execute('SELECT error,article_json FROM archive_failures_v2 WHERE scope=? AND stock_code=? AND identity=?',(MARKET if kind=='market' else SEARCH,owner,identity)).fetchone()
                        if not failed:
                            raise ValueError('source failure detail missing')
                        data=json.loads(failed[1]); document=data.get('document',{}) if isinstance(data,dict) else {}
                        display={key:str(document.get(key) or '') for key in ('title','description','link','original_link','publisher_name','publisher_domain')}
                        display.update(body_status='source_failed',error=failed[0],published_at=document.get('published_at'))
                        missing,sort_us=_publication(document.get('published_at'))
                        db.execute('INSERT INTO archive_projection_v2 VALUES(?,?,?,?,?,?,?,?)',(kind,owner,identity,None,None,missing,sort_us,_json(display)))
                        db.execute("UPDATE archive_projection_sources_v2 SET state='complete' WHERE kind=? AND owner=? AND identity=?",(kind,owner,identity))
                        continue
                    if stored_article is None:
                        raise ValueError('projection article missing')
                    document=json.loads(encoded_article)
                    if body_id and body_article != article_id:
                        raise ValueError('projection body relation missing')
                    if body_id and clean_id is None:
                        clean=clean_article_text(body_text) if body_status=='fulltext' else ''
                        db.execute('INSERT INTO archive_clean_bodies_v2 VALUES(?,?) ON CONFLICT(body_revision_id) DO NOTHING',(body_id,clean if len(clean)>=40 else ''))
                    missing,sort_us=_publication(published_at)
                    display={key:str(document.get(key) or '') for key in ('title','description','link','original_link','publisher_name','publisher_domain')}
                    display.update(published_at=published_at,body_status=body_status if body_article is not None else 'missing')
                    db.execute('INSERT INTO archive_projection_v2 VALUES(?,?,?,?,?,?,?,?)',(kind,owner,identity,article_id,body_id,missing,sort_us,_json(display)))
                    db.execute("UPDATE archive_projection_sources_v2 SET state='complete' WHERE kind=? AND owner=? AND identity=?",(kind,owner,identity))
            done+=len(rows)
        remaining=db.execute("SELECT COUNT(*) FROM archive_projection_sources_v2 WHERE state='pending'").fetchone()[0]
        if not remaining:
            counts=dict(db.execute('SELECT kind,COUNT(*) FROM archive_projection_v2 GROUP BY kind'))
            with _transaction(db):
                db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='projection_v2'",(_json({'state':'complete','schema':'historical-news-projection/v2','counts':counts}),))
        return {'projected_now':done,'remaining':remaining,'build_state':'building'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("initialize")
    init.add_argument("--archive",type=Path,required=True)
    init.add_argument("--dataset-id",required=True)
    events=commands.add_parser("events")
    events.add_argument("--archive",type=Path,required=True)
    events.add_argument("--max-rows",type=int,default=0)
    review=commands.add_parser('review-event-conflict')
    review.add_argument('--archive',type=Path,required=True)
    review.add_argument('--evidence',type=Path,required=True)
    review.add_argument('--output',type=Path,required=True)
    resolve=commands.add_parser('resolve-event-conflict')
    resolve.add_argument('--archive',type=Path,required=True)
    resolve.add_argument('--decision',type=Path,required=True)
    register=commands.add_parser('register-event-conflict')
    register.add_argument('--archive',type=Path,required=True)
    register.add_argument('--decision',type=Path,required=True)
    projection=commands.add_parser('projection')
    projection.add_argument('--archive',type=Path,required=True)
    projection.add_argument('--max-rows',type=int,default=0)
    for name in ("stage","finalize"):
        p=commands.add_parser(name)
        p.add_argument("--archive",type=Path,required=True)
        p.add_argument("--prepared",type=Path,required=True)
        p.add_argument("--scope",choices=SCOPES,required=True)
        p.add_argument("--expected-sha256",required=True)
        p.add_argument("--scope-alias",action="append",default=[])
        p.add_argument("--max-rows",type=int,default=0)
        if name == "finalize":
            p.add_argument("--raw",type=Path)
            p.add_argument("--raw-cutoff",type=int,default=0)
    args=parser.parse_args()
    if args.command == "initialize":
        value=initialize(args.archive,dataset_id=args.dataset_id)
    elif args.command == "events":
        value=finalize_events(args.archive,max_rows=args.max_rows)
    elif args.command == 'review-event-conflict':
        value=create_event_conflict_decision(args.archive,args.evidence,args.output)
    elif args.command == 'resolve-event-conflict':
        value=resolve_event_conflict(args.archive,args.decision)
    elif args.command == 'register-event-conflict':
        value=register_event_conflict_decision(args.archive,args.decision)
    elif args.command == 'projection':
        value=build_projection(args.archive,max_rows=args.max_rows)
    elif args.command == "stage":
        value=stage(args.archive,args.prepared,args.scope,args.expected_sha256,max_rows=args.max_rows,
                    scope_aliases=tuple(args.scope_alias))
    else:
        value=finalize_inputs(args.archive,args.prepared,args.scope,args.expected_sha256,
                              raw_path=args.raw,raw_cutoff=args.raw_cutoff,max_rows=args.max_rows,
                              scope_aliases=tuple(args.scope_alias))
    print(_json(value))


if __name__ == "__main__":
    main()
