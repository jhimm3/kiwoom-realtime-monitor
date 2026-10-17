"""Read-only v2 gate. Source coverage/history and seed proof are mandatory.

Construction success alone is never publication permission. Missing raw-source
review or failure review remains a blocker, even if a manifest says complete.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from itertools import groupby
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
for item in (ROOT,ROOT/'src'):
    if str(item) not in sys.path:
        sys.path.insert(0,str(item))

from scripts.prepared_historical_archive_v2 import SCOPES,POLICY,MARKET,WORLD_EXCLUSION,world_service_excluded,code_digest,report,_json
from scripts.prepared_historical_archive_inputs import generation, contract_blockers, input_counts, missing_coverage
from scripts.materialize_historical_news_seed import _sqlite_value
from scripts.export_historical_news_seed import TABLE_KEYS
from scripts.stage_prepared_historical_news_archive import _sha256
from kiwoom_monitor.central_server.database import stable_document_hash
from kiwoom_monitor.central_server.database_news_sources import _news_article_content_hash


def _target_sets(db):
    """Compare complete target sets with one bounded-memory ordered stream."""
    rows = db.execute(
        'SELECT e.scope,e.stock_code,e.identity,e.targets_json,t.target_id,t.target_name '
        'FROM archive_expected_targets_v2 e LEFT JOIN archive_targets_v2 t '
        'ON t.scope=e.scope AND t.stock_code=e.stock_code AND t.identity=e.identity '
        'ORDER BY e.scope,e.stock_code,e.identity'
    )
    count = 0
    for _, members in groupby(rows, key=lambda row: row[:3]):
        first = next(members)
        expected = json.loads(first[3])
        actual = {} if first[4] is None else {first[4]: first[5]}
        actual.update((row[4], row[5]) for row in members if row[4] is not None)
        count += len(expected)
        if expected != actual:
            return count, False
    return count, True


def audit(path: Path, *, seed: Path | None=None, full_integrity_check: bool=False,
          allow_sealed: bool=False) -> dict:
    blockers=[]
    path=path.resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        value=report(db)
        if value.get('archive_schema_version')!=2 or value.get('build_state') not in (('building','sealed') if allow_sealed else ('building',)):
            blockers.append('not_building_v2')
        if value.get('profile_only'):
            blockers.append('capacity_sample_not_publishable')
        if not value.get('dataset_id') or set(value.get('required_scopes',[]))!=set(SCOPES):
            blockers.append('dataset_or_scope_manifest_invalid')
        if value.get('builder_code_digest')!=code_digest():
            blockers.append('construction_code_changed')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        sources=db.execute('SELECT scope,source_path,source_sha256,expected_rows,staged_rows,state FROM archive_sources_v2').fetchall()
        if {r[0] for r in sources}!=set(SCOPES):
            blockers.append('scope_input_missing')
        progress={}
        blockers.extend(contract_blockers(db,check_files=full_integrity_check))
        if generation(db) and generation(db).get('state')!='complete':
            blockers.append('input_generation_adoption_unfinished')
        for scope,source_path,source_hash,expected,staged,state in sources:
            actual=dict(db.execute('SELECT progress_state,COUNT(*) FROM archive_input_progress WHERE scope=? GROUP BY progress_state',(scope,)))
            progress[scope]=actual
            origin_manifest=db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',('prepared_scope_origins_v2_'+scope,)).fetchone()
            origin_count=db.execute('SELECT COUNT(*) FROM archive_prepared_scope_origins_v2 WHERE scope=?',(scope,)).fetchone()[0]
            if not origin_manifest or json.loads(origin_manifest[0]).get('expected_rows')!=origin_count:
                blockers.append(f'{scope}_prepared_origin_count_mismatch')
            effective_expected,effective_staged=input_counts(db,scope)
            if state!='complete' or expected!=staged or effective_expected!=effective_staged or effective_expected!=sum(actual.values()):
                blockers.append(f'{scope}_input_count_mismatch')
            if set(actual)-{'event_done','source_failed','source_excluded'}:
                blockers.append(f'{scope}_input_unfinished')
            if db.execute("SELECT 1 FROM archive_input_progress WHERE scope=? AND ((source_state='ready' AND progress_state='source_failed') OR (source_state='failed' AND (progress_state NOT IN ('source_failed','source_excluded') OR TRIM(error)=''))) LIMIT 1",(scope,)).fetchone():
                blockers.append(f'{scope}_source_state_mismatch')
            if full_integrity_check:
                try:
                    source_wal=Path(source_path+'-wal')
                    if (source_wal.exists() and source_wal.stat().st_size) or _sha256(Path(source_path))!=source_hash:
                        blockers.append(f'{scope}_frozen_source_changed')
                except OSError:
                    blockers.append(f'{scope}_frozen_source_unavailable')
        ready_inputs=db.execute("SELECT COUNT(*) FROM archive_input_progress WHERE progress_state='event_done'").fetchone()[0]
        if db.execute("SELECT 1 FROM archive_prepared_scope_origins_v2 o LEFT JOIN archive_input_progress p ON p.scope=o.scope AND p.stock_code=o.stock_code AND p.identity=o.identity WHERE o.scope<>? OR o.original_scope<>'historical_market' OR o.input_hash IS NOT p.input_hash LIMIT 1",(MARKET,)).fetchone():
            blockers.append('prepared_origin_relation_invalid')
        for scope,owner,identity,reason in db.execute("SELECT scope,stock_code,identity,error FROM archive_input_progress WHERE progress_state='source_excluded'"):
            original=db.execute('SELECT article_json FROM archive_failures_v2 WHERE scope=? AND stock_code=? AND identity=?',(scope,owner,identity)).fetchone()
            article=json.loads(original[0]) if original else {}
            proven=world_service_excluded(article.get('source',''),article.get('document',{}).get('published_at'))
            if not proven and 'archive_source_history_v2' in tables:
                rows=db.execute("SELECT payload_json FROM archive_source_history_v2 WHERE scope=? AND source_table='market_news_articles' AND json_extract(payload_json,'$.article_url')=? AND state='source_excluded'",(scope,identity))
                proven=any(world_service_excluded((data:=json.loads(row[0])).get('source',''),data.get('published_at')) for row in rows)
            if scope!=MARKET or owner!='GLOBAL' or reason!=WORLD_EXCLUSION or not proven:
                blockers.append('source_exclusion_without_policy_evidence')
        expected_inputs=db.execute('SELECT COUNT(*) FROM archive_expected_targets_v2').fetchone()[0]
        if ready_inputs!=expected_inputs:
            blockers.append('expected_target_input_count_mismatch')
        if db.execute('SELECT COUNT(*) FROM archive_input_context_v2').fetchone()[0]!=ready_inputs or db.execute("SELECT 1 FROM archive_input_context_v2 c LEFT JOIN archive_input_progress p ON p.scope=c.scope AND p.stock_code=c.stock_code AND p.identity=c.identity WHERE p.progress_state IS NOT 'event_done' OR c.article_revision_id IS NOT p.article_revision_id OR c.body_revision_id IS NOT p.body_revision_id LIMIT 1").fetchone():
            blockers.append('input_context_relation_invalid')
        # Stream per input; DISTINCT over full BODY text would create a huge
        # temporary sort and invalidate the construction capacity budget.
        for scope,encoded,digest,body,body_hash in db.execute("SELECT p.scope,a.document_json,a.content_hash,b.body_text,b.content_hash FROM archive_input_progress p JOIN central_news_article_revisions a ON a.article_revision_id=p.article_revision_id JOIN central_news_body_revisions b ON b.body_revision_id=p.body_revision_id WHERE p.progress_state='event_done'"):
            document=json.loads(encoded)
            actual=_news_article_content_hash(document) if scope==MARKET else stable_document_hash(document)
            if actual!=digest or stable_document_hash({'body':body})!=body_hash:
                blockers.append('canonical_article_or_body_hash_invalid'); break
        if db.execute("SELECT 1 FROM archive_projection_v2 p LEFT JOIN central_news_article_revisions a ON a.article_revision_id=p.article_revision_id LEFT JOIN central_news_body_revisions b ON b.body_revision_id=p.body_revision_id WHERE p.article_revision_id IS NOT NULL AND (a.article_revision_id IS NULL OR b.body_revision_id IS NULL OR b.article_revision_id<>a.article_revision_id) LIMIT 1").fetchone():
            blockers.append('projection_article_body_relation_invalid')
        target_rows, target_sets_match = _target_sets(db)
        if not target_sets_match:
            blockers.append('target_set_mismatch')
        if target_rows!=db.execute('SELECT COUNT(*) FROM archive_targets_v2').fetchone()[0]:
            blockers.append('target_count_mismatch')
        from scripts.transition_historical_archive_execution import (
            finalization_lineage, fingerprint_digest, _prefix_rows, INPUT_FIELDS, TARGET_FIELDS,
        )
        from scripts.prepared_historical_archive_inputs import _root
        lineage = None
        try:
            lineage = finalization_lineage(db, check_file=True)
            if lineage:
                for scope, entry in lineage['scopes'].items():
                    if _root(_prefix_rows(db,'archive_input_progress',INPUT_FIELDS,scope,entry['last']))!=entry['inputs']:
                        blockers.append('finalized_prefix_inputs_changed')
        except (ValueError,KeyError,OSError,TypeError,sqlite3.DatabaseError):
            lineage = None
            blockers.append('finalization_lineage_invalid')
        prefix_hashes={scope:hashlib.sha256() for scope in SCOPES}
        prefix_counts={scope:0 for scope in SCOPES}
        reviewed_event_ids=set()
        target_verification_failures=[]
        # Event-only execution transitions preserve finalized target rows. The
        # verified lineage identifies the producer of the suffix; the latest
        # event runner digest is not a new BODY/RULE producer.
        finalization_digest=lineage['code_digest'] if lineage else value.get('builder_code_digest')
        for values in db.execute('SELECT '+TARGET_FIELDS+',event_reason FROM archive_targets_v2 ORDER BY scope,stock_code,identity,target_id'):
            row, event_reason = values[:-1], values[-1]
            if event_reason and event_reason.startswith('reviewed_event_key:'):
                review_id=event_reason.removeprefix('reviewed_event_key:')
                if review_id in reviewed_event_ids:
                    blockers.append('event_conflict_review_invalid')
                reviewed_event_ids.add(review_id)
            scope,owner,identity,target,name,input_hash,article_id,body_id,fingerprint,prepared_json,computed_json,state,_ = row
            computed=json.loads(computed_json)
            digest=fingerprint_digest(lineage,scope,owner,identity,finalization_digest)
            expected_hash=hashlib.sha256(_json((POLICY,input_hash,article_id,body_id,target,name,digest,computed)).encode()).hexdigest()
            equal=_json(json.loads(prepared_json))==_json(computed)
            if fingerprint!=expected_hash or (state=='verified_equal')!=equal:
                if not target_verification_failures:
                    blockers.append('target_verification_fingerprint_invalid')
                if len(target_verification_failures)<5:
                    target_verification_failures.append({'scope':scope,'stock_code':owner,
                        'identity':identity,'target_id':target,'producer_digest':digest,
                        'fingerprint_matches':fingerprint==expected_hash,
                        'verification_state_matches':(state=='verified_equal')==equal})
                # Keep collecting review IDs and prefix roots from this same
                # stream, even when one target fails its independent gate.
            if lineage and digest==lineage['parent_digest']:
                encoded=_json(row).encode()
                prefix_hashes[scope].update(len(encoded).to_bytes(8,'big'));prefix_hashes[scope].update(encoded)
                prefix_counts[scope]+=1
        if lineage:
            for scope,entry in lineage['scopes'].items():
                if {'rows':prefix_counts[scope],'sha256':prefix_hashes[scope].hexdigest()}!=entry['targets']:
                    blockers.append('finalized_prefix_targets_changed')
        if db.execute("SELECT 1 FROM archive_targets_v2 t LEFT JOIN archive_input_progress p ON p.scope=t.scope AND p.stock_code=t.stock_code AND p.identity=t.identity LEFT JOIN central_news_body_revisions b ON b.body_revision_id=t.body_revision_id WHERE p.input_hash IS NULL OR t.input_hash<>p.input_hash OR t.article_revision_id<>p.article_revision_id OR t.body_revision_id<>p.body_revision_id OR b.article_revision_id<>t.article_revision_id OR b.article_revision_id IS NULL OR t.event_state IN ('pending','conflict') OR t.verification_state NOT IN ('verified_equal','recomputed_posthoc') LIMIT 1").fetchone():
            blockers.append('target_relation_or_resolution_invalid')
        if db.execute("SELECT 1 FROM archive_targets_v2 t LEFT JOIN central_news_event_revisions e ON e.event_revision_id=t.event_revision_id LEFT JOIN central_news_event_membership_revisions m ON m.membership_revision_id=t.membership_revision_id WHERE t.event_revision_id IS NOT NULL AND (e.event_revision_id IS NULL OR e.event_id<>t.event_id OR e.article_revision_id<>t.article_revision_id OR e.body_revision_id<>t.body_revision_id OR e.stock_code<>t.target_id OR m.membership_revision_id IS NULL OR m.event_revision_id<>t.event_revision_id OR m.article_revision_id<>t.article_revision_id OR m.body_revision_id<>t.body_revision_id) LIMIT 1").fetchone():
            blockers.append('event_membership_relation_invalid')
        manifests={key:json.loads(doc) for key,doc in db.execute('SELECT key,value_json FROM archive_build_manifest')}
        event_review_errors=[]
        try:
            from scripts.prepared_historical_archive_v2 import EVENT_REVIEW_PREFIX, EVENT_DECISION_PREFIX, validate_event_review_receipt
            receipts={key.removeprefix(EVENT_REVIEW_PREFIX):document for key,document in manifests.items() if key.startswith(EVENT_REVIEW_PREFIX)}
            if set(receipts)!=reviewed_event_ids:
                raise ValueError('event review receipt set differs from reviewed targets')
            registrations={key.removeprefix(EVENT_DECISION_PREFIX) for key in manifests if key.startswith(EVENT_DECISION_PREFIX)}
            if not registrations.issubset(receipts):
                raise ValueError('registered event review has no completion receipt')
            for identity, receipt in receipts.items():
                if validate_event_review_receipt(db,receipt)!=identity:
                    raise ValueError('event review receipt key differs')
        except (ValueError,KeyError,TypeError,OSError,sqlite3.DatabaseError) as exc:
            blockers.append('event_conflict_review_invalid')
            event_review_errors.append(str(exc))
        projection=manifests.get('projection_v2',{})
        actual_counts=dict(db.execute('SELECT kind,COUNT(*) FROM archive_projection_v2 GROUP BY kind'))
        if projection.get('state')!='complete' or projection.get('counts')!=actual_counts:
            blockers.append('projection_count_or_state_mismatch')
        if 'archive_projection_sources_v2' not in tables:
            blockers.append('projection_sources_missing')
        elif db.execute("SELECT 1 FROM archive_projection_sources_v2 s LEFT JOIN archive_projection_v2 p ON p.kind=s.kind AND p.owner=s.owner AND p.identity=s.identity WHERE s.state<>'complete' OR p.identity IS NULL OR p.article_revision_id IS NOT s.article_revision_id OR p.body_revision_id IS NOT s.body_revision_id LIMIT 1").fetchone():
            blockers.append('projection_source_relation_invalid')
        # These are actual per-key ledgers, not a boolean approval override.
        for name,blocker in (('archive_source_coverage_v2','raw_source_coverage_missing'),
                             ('archive_source_history_v2','source_history_review_missing'),
                             ('archive_failure_review_v2','source_failure_review_missing')):
            if name not in tables:
                blockers.append(blocker)
        coverage=manifests.get('coverage_v2',{})
        if coverage.get('state') not in ('complete','complete_with_source_failures') or coverage.get('schema')!='historical-source-coverage/v2':
            blockers.append('raw_coverage_manifest_unverified')
        elif all(name in tables for name in ('archive_source_coverage_v2','archive_source_history_v2','archive_failure_review_v2')):
            for scope in SCOPES:
                current=next((r for r in sources if r[0]==scope),None)
                receipt=coverage.get('scopes',{}).get(scope,{})
                n=db.execute('SELECT COUNT(*) FROM archive_source_coverage_v2 WHERE scope=?',(scope,)).fetchone()[0]
                if not current or receipt.get('prepared_sha256')!=current[2] or receipt.get('source_keys')!=n:
                    blockers.append(f'{scope}_coverage_receipt_mismatch')
                capture=manifests.get('source_capture_v2_'+scope,{})
                if 'source_capture_v2_'+scope+'_failure' in manifests:
                    blockers.append(f'{scope}_source_capture_failure_unresolved')
                if capture.get('state')!='complete' or capture.get('raw_fingerprint')!=receipt.get('raw_fingerprint'):
                    blockers.append(f'{scope}_source_capture_receipt_invalid')
                names={'news_articles','source_pages','news_article_body_snapshots'} if scope==SCOPES[0] else {'market_news_articles','market_news_pages'}
                actual_names=set(capture.get('source_rows_expected',{}))
                allowed=names|({'news_article_fetch_attempts'} if scope==SCOPES[0] else set())
                if not names.issubset(actual_names) or actual_names-allowed:
                    blockers.append(f'{scope}_source_row_counts_missing')
                if full_integrity_check:
                    try:
                        fp=capture['raw_fingerprint']
                        raw_path=Path(fp['path']).resolve(strict=True)
                        raw_wal=Path(str(raw_path)+'-wal')
                        if raw_path.stat().st_size!=fp['bytes'] or raw_path.stat().st_mtime_ns!=fp['mtime_ns'] or (raw_wal.exists() and raw_wal.stat().st_size):
                            blockers.append(f'{scope}_raw_source_changed')
                        with closing(sqlite3.connect(raw_path.as_uri()+'?mode=ro',uri=True)) as raw:
                            has_attempts=raw.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_article_fetch_attempts'").fetchone()
                        if scope==SCOPES[0] and bool(has_attempts)!=('news_article_fetch_attempts' in actual_names):
                            blockers.append(f'{scope}_fetch_attempt_history_missing')
                    except (OSError,KeyError,sqlite3.DatabaseError):
                        blockers.append(f'{scope}_raw_source_unavailable')
                for table,expected in capture.get('source_rows_expected',{}).items():
                    actual=db.execute('SELECT COUNT(*) FROM archive_source_history_v2 WHERE scope=? AND source_table=?',(scope,table)).fetchone()[0]
                    if actual!=expected:
                        blockers.append(f'{scope}_raw_history_count_mismatch')
            if missing_coverage(db):
                blockers.append('received_source_without_finalized_input')
            if db.execute("SELECT 1 FROM archive_input_progress p WHERE p.progress_state<>'source_excluded' AND NOT EXISTS(SELECT 1 FROM archive_source_coverage_v2 c WHERE c.scope=p.scope AND c.stock_code=p.stock_code AND c.identity=p.identity) LIMIT 1").fetchone():
                blockers.append('prepared_input_without_raw_mapping')
            if db.execute("SELECT 1 FROM archive_input_progress p LEFT JOIN archive_failure_review_v2 r ON r.scope=p.scope AND r.stock_code=p.stock_code AND r.identity=p.identity WHERE p.progress_state='source_failed' AND (r.input_hash IS NULL OR r.input_hash<>p.input_hash OR TRIM(r.reason)='') LIMIT 1").fetchone():
                blockers.append('unexplained_source_failure')
            if db.execute("SELECT 1 FROM archive_source_history_v2 WHERE state NOT IN ('verified','unmapped_evidence_preserved','source_excluded') LIMIT 1").fetchone():
                blockers.append('raw_history_unfinished')
            for encoded,digest in db.execute('SELECT payload_json,payload_sha256 FROM archive_source_history_v2'):
                if hashlib.sha256(encoded.encode()).hexdigest()!=digest:
                    blockers.append('raw_evidence_payload_hash_changed'); break
        seed_rows=0
        if seed is None:
            blockers.append('seed_preservation_not_verified')
        elif value.get('seed_sha256')!=_sha256(seed.resolve(strict=True)):
            blockers.append('seed_hash_mismatch')
        else:
            with closing(sqlite3.connect(seed.resolve().as_uri()+'?mode=ro',uri=True)) as original:
                original.execute('PRAGMA query_only=ON')
                table_columns = {}
                for table,key,payload_json,digest,role in original.execute('SELECT table_name,row_key,payload_json,payload_hash,role FROM seed_rows'):
                    if table not in TABLE_KEYS or hashlib.sha256(payload_json.encode()).hexdigest()!=digest:
                        blockers.append('seed_input_invalid'); break
                    payload=json.loads(payload_json)
                    if table not in table_columns:
                        table_columns[table] = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                    columns=table_columns[table]
                    if set(payload)!=columns or key!=_json([payload[k] for k in TABLE_KEYS[table]]):
                        blockers.append('seed_columns_or_key_invalid'); break
                    names=sorted(payload)
                    where=' AND '.join(f'{k}=?' for k in TABLE_KEYS[table])
                    actual=db.execute(f"SELECT {','.join(names)} FROM {table} WHERE {where}",tuple(_sqlite_value(payload[k]) for k in TABLE_KEYS[table])).fetchone()
                    expected=tuple(_sqlite_value(payload[k]) for k in names)
                    stored=db.execute('SELECT payload_hash,role FROM archive_seed_roles WHERE table_name=? AND row_key=?',(table,key)).fetchone()
                    if actual!=expected or stored!=(digest,role):
                        blockers.append('seed_row_payload_or_role_changed'); break
                    seed_rows+=1
            if seed_rows!=value.get('seed_rows'):
                blockers.append('seed_count_mismatch')
        integrity=None
        if full_integrity_check:
            integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity!='ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                blockers.append('sqlite_integrity_or_foreign_key_failed')
        else:
            blockers.append('full_integrity_check_required')
        return {'archive':str(path),'read_only':True,'dataset_id':value.get('dataset_id'),
                'progress':progress,'target_rows':target_rows,'projection_counts':actual_counts,
                'seed_rows_verified':seed_rows,'integrity_check':integrity,
                'target_verification_failures':target_verification_failures,
                'event_review_errors':event_review_errors,
                'blockers':sorted(set(blockers)),'ready_for_seal':not blockers}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--seed',type=Path)
    parser.add_argument('--full-integrity-check',action='store_true')
    args=parser.parse_args()
    print(json.dumps(audit(args.archive,seed=args.seed,full_integrity_check=args.full_integrity_check),ensure_ascii=False))
