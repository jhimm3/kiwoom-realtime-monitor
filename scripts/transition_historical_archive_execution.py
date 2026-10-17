"""Explicit code-generation transition before any input finalization.

Only construction manifests change. All input/raw/seed rows stay in place;
the previous code, manifests and external plan remain recoverable evidence.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

CHANGES = {'scripts/prepared_historical_archive_inputs.py',
           'scripts/prepared_historical_archive_v2.py',
           'scripts/finalize_prepared_historical_article_bodies.py'}

PREFIX_KEY = 'finalization_execution_v1'
TARGET_FIELDS = ('scope,stock_code,identity,target_id,target_name,input_hash,'
                 'article_revision_id,body_revision_id,fingerprint,prepared_json,'
                 'computed_json,verification_state,computed_at')
INPUT_FIELDS = 'scope,stock_code,identity,input_hash,article_revision_id,body_revision_id'


def _prefix_rows(db, table, fields, scope, last):
    if last is None:
        return iter(())
    return db.execute('SELECT '+fields+' FROM '+table+
                      ' WHERE scope=? AND (stock_code,identity)<=(?,?)'+
                      (" AND progress_state IN ('assessment_done','event_done')" if table=='archive_input_progress' else '')+
                      ' ORDER BY '+('scope,stock_code,identity,target_id' if table=='archive_targets_v2' else 'scope,stock_code,identity'),
                      (scope,*last))


def _prefix_snapshot(db):
    from scripts.prepared_historical_archive_inputs import _root
    from scripts.prepared_historical_archive_v2 import SCOPES
    if db.execute("SELECT 1 FROM archive_build_manifest WHERE key IN ('events_v2','projection_v2')").fetchone():
        raise ValueError('events/projection already started')
    if db.execute("SELECT 1 FROM archive_input_progress WHERE progress_state NOT IN ('pending','assessment_done','source_failed','source_excluded') LIMIT 1").fetchone():
        raise ValueError('partial or invalid finalized input state')
    # Row comparisons must have the exact default BINARY key contract.
    for table in ('archive_input_progress','archive_targets_v2'):
        schema=db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone()[0]
        if 'COLLATE' in schema.upper():
            raise ValueError('unsupported prefix key collation')
    scopes={}
    for scope in SCOPES:
        last=db.execute("SELECT stock_code,identity FROM archive_input_progress WHERE scope=? AND progress_state='assessment_done' ORDER BY stock_code DESC,identity DESC LIMIT 1",(scope,)).fetchone()
        if last and db.execute("SELECT 1 FROM archive_input_progress WHERE scope=? AND progress_state='pending' AND (stock_code,identity)<=(?,?) LIMIT 1",(scope,*last)).fetchone():
            raise ValueError('completed inputs do not form a closed prefix')
        if db.execute("SELECT 1 FROM archive_targets_v2 t LEFT JOIN archive_input_progress p USING(scope,stock_code,identity) WHERE t.scope=? AND (p.progress_state IS NOT 'assessment_done' OR t.event_state<>'pending') LIMIT 1",(scope,)).fetchone():
            raise ValueError('target is outside finalized input prefix')
        scopes[scope]={'last':list(last) if last else None,
                       'inputs':_root(_prefix_rows(db,'archive_input_progress',INPUT_FIELDS,scope,last)),
                       'targets':_root(_prefix_rows(db,'archive_targets_v2',TARGET_FIELDS,scope,last))}
    return scopes


def finalization_lineage(db, *, check_file=False):
    row=db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?',(PREFIX_KEY,)).fetchone()
    if not row:
        return None
    value=json.loads(row[0])
    from scripts.prepared_historical_archive_v2 import report,SCOPES
    current=report(db)
    lineage_identity=(value.get('dataset_id'),value.get('code_digest'))
    current_identity=(current.get('dataset_id'),current.get('builder_code_digest'))
    ancestors=_active_event_ancestors(db,current,stop_identity=lineage_identity,check_file=check_file)
    if (value.get('schema')!='historical-finalization-execution/v1' or
            lineage_identity not in ({current_identity} | ancestors) or
            value.get('parent_digest')==value.get('code_digest') or set(value.get('scopes',{}))!=set(SCOPES)):
        raise ValueError('finalization lineage identity mismatch')
    if not re.fullmatch('[0-9a-f]{64}',value.get('parent_digest','')):
        raise ValueError('invalid parent constructor digest')
    for entry in value['scopes'].values():
        last=entry['last']
        if last is not None and (not isinstance(last,list) or len(last)!=2 or not all(isinstance(k,str) for k in last)):
            raise ValueError('invalid finalization prefix key')
        for kind in ('inputs','targets'):
            root=entry[kind]
            if type(root['rows']) is not int or root['rows']<0 or not re.fullmatch('[0-9a-f]{64}',root['sha256']):
                raise ValueError('invalid finalization prefix root')
        if last is None and (entry['inputs']['rows'] or entry['targets']['rows']):
            raise ValueError('empty prefix has finalized rows')
    stored=db.execute('SELECT plan_path,plan_sha256,parent_digest,code_digest,state FROM archive_execution_transitions_v1 WHERE dataset_id=?',(value['dataset_id'],)).fetchone()
    if stored!=(value['plan_path'],value['plan_sha256'],value['parent_digest'],value['code_digest'],'complete'):
        raise ValueError('finalization transition ledger mismatch')
    if check_file:
        plan=Path(value['plan_path']).read_bytes()
        if hashlib.sha256(plan).hexdigest()!=value['plan_sha256']:
            raise ValueError('finalization plan changed')
        decoded=json.loads(plan)
        if any(decoded.get(k)!=value[k] for k in ('schema','dataset_id','code_digest','parent_digest','parent_dataset','scopes')):
            raise ValueError('finalization plan boundary mismatch')
    return value


def _active_event_ancestors(db, current, *, stop_identity=None, check_file=False):
    """Return verified parent identities for the metadata-only event transition chain."""
    identity=(current.get('dataset_id'),current.get('builder_code_digest'))
    ancestors=set()
    visited=set()
    while identity not in visited:
        visited.add(identity)
        dataset,digest=identity
        row=db.execute('SELECT parent_dataset,parent_digest,plan_path,plan_sha256,old_report_json,old_generation_json,state '
                       'FROM archive_execution_transitions_v1 WHERE dataset_id=? AND code_digest=?',(dataset,digest)).fetchone()
        if not row:
            break
        parent_dataset,parent_digest,plan_path,plan_sha256,old_report_json,old_generation_json,state=row
        if state!='complete':
            raise ValueError('active event transition is not complete')
        old_report=json.loads(old_report_json); old_generation=json.loads(old_generation_json)
        if (old_report.get('dataset_id'),old_report.get('builder_code_digest'))!=(parent_dataset,parent_digest) or \
                (old_generation.get('dataset_id'),old_generation.get('code_digest'))!=(parent_dataset,parent_digest):
            raise ValueError('active event transition parent identity mismatch')
        if not plan_path or not re.fullmatch('[0-9a-f]{64}',plan_sha256):
            raise ValueError('active event transition plan identity invalid')
        plan=Path(plan_path).read_bytes()
        if hashlib.sha256(plan).hexdigest()!=plan_sha256:
            raise ValueError('active event transition plan changed')
        decoded=json.loads(plan)
        if decoded.get('schema')!='historical-active-events-execution/v1':
            break
        if (decoded.get('dataset_id'),decoded.get('code_digest'),decoded.get('parent_dataset'),decoded.get('parent_digest')) != (
                dataset,digest,parent_dataset,parent_digest):
            raise ValueError('active event transition plan boundary mismatch')
        identity=(parent_dataset,parent_digest)
        if identity in visited:
            raise ValueError('active event transition cycle')
        ancestors.add(identity)
        if identity==stop_identity:
            break
    return ancestors


def fingerprint_digest(lineage, scope, owner, identity, current):
    if lineage is None:
        return current
    last=lineage['scopes'][scope]['last']
    # UTF-8 byte ordering matches SQLite BINARY, including non-ASCII keys.
    return lineage['parent_digest'] if last is not None and tuple(x.encode() for x in (owner,identity))<=tuple(x.encode() for x in last) else current


def _apply_finalized(db,parent_dataset,parent_digest,dataset,digest,plan_path,plan_hash,scopes):
    from scripts import prepared_historical_archive_v2 as api
    from scripts import prepared_historical_archive_inputs as inputs
    if not db.in_transaction:
        raise ValueError('caller transaction required')
    before=api.report(db);gen=inputs.generation(db)
    if (before.get('dataset_id'),before.get('builder_code_digest'),before.get('build_state'))!=(parent_dataset,parent_digest,'building'):
        raise ValueError('explicit parent construction mismatch')
    if not gen or (gen.get('dataset_id'),gen.get('code_digest'),gen.get('state'))!=(parent_dataset,parent_digest,'complete'):
        raise ValueError('completed parent input generation required')
    if dataset==parent_dataset or digest==parent_digest or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',dataset):
        raise ValueError('distinct finalized generation required')
    if db.execute('SELECT owner FROM archive_builder_v2 WHERE id=1').fetchone()!=(None,):
        raise ValueError('persistent builder owner still present')
    if db.execute('SELECT 1 FROM archive_build_manifest WHERE key=?',(PREFIX_KEY,)).fetchone():
        raise ValueError('only one finalized execution transition is supported')
    if _prefix_snapshot(db)!=scopes:
        raise ValueError('finalization prefix changed before transition')
    coverage=db.execute("SELECT value_json FROM archive_build_manifest WHERE key='coverage_v2'").fetchone()
    if not coverage or json.loads(coverage[0]).get('state') not in ('complete','complete_with_source_failures'):
        raise ValueError('complete coverage boundary required')
    lineage={'schema':'historical-finalization-execution/v1','parent_dataset':parent_dataset,'parent_digest':parent_digest,
             'dataset_id':dataset,'code_digest':digest,'plan_path':str(plan_path),'plan_sha256':plan_hash,'scopes':scopes}
    # No canonical/input/provenance mutation is allowed during the metadata switch.
    allowed={'archive_build_manifest','archive_execution_transitions_v1','sqlite_master'}
    def authorize(action,table,*rest):
        if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE) and table not in allowed:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.set_authorizer(authorize)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS archive_execution_transitions_v1(dataset_id TEXT PRIMARY KEY,parent_dataset TEXT NOT NULL,parent_digest TEXT NOT NULL,code_digest TEXT NOT NULL,old_report_json TEXT NOT NULL,old_generation_json TEXT NOT NULL,plan_path TEXT NOT NULL,plan_sha256 TEXT NOT NULL,state TEXT NOT NULL)')
        db.execute('INSERT INTO archive_execution_transitions_v1 VALUES(?,?,?,?,?,?,?,?,?)',(dataset,parent_dataset,parent_digest,digest,_encoded(before),_encoded(gen),str(plan_path),plan_hash,'complete'))
        db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(_encoded({**before,'dataset_id':dataset,'builder_code_digest':digest}),))
        inputs._save(db,{**gen,'dataset_id':dataset,'code_digest':digest,'prior_generation_plan':{'path':gen['plan_path'],'sha256':gen['plan_sha256']},'plan_path':str(plan_path),'plan_sha256':plan_hash,'execution_transitions':[*gen.get('execution_transitions',[]),lineage]})
        db.execute('INSERT INTO archive_build_manifest VALUES(?,?)',(PREFIX_KEY,_encoded(lineage)))
    finally:
        db.set_authorizer(None)
    return lineage


def _apply_active_events(db,parent_dataset,parent_digest,dataset,digest,plan_path,plan_hash, *, reviewed_event_resolution=None):
    """Change only execution identity while preserving a live event cursor."""
    from scripts import prepared_historical_archive_v2 as api
    from scripts import prepared_historical_archive_inputs as inputs
    if not db.in_transaction:
        raise ValueError('caller transaction required')
    before=api.report(db); gen=inputs.generation(db)
    if (before.get('dataset_id'),before.get('builder_code_digest'),before.get('build_state'))!=(parent_dataset,parent_digest,'building'):
        raise ValueError('explicit parent construction mismatch')
    if not gen or (gen.get('dataset_id'),gen.get('code_digest'),gen.get('state'))!=(parent_dataset,parent_digest,'complete'):
        raise ValueError('completed parent input generation required')
    if dataset==parent_dataset or digest==parent_digest or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',dataset):
        raise ValueError('distinct event execution generation required')
    if db.execute('SELECT owner FROM archive_builder_v2 WHERE id=1').fetchone()!=(None,):
        raise ValueError('persistent builder owner still present')
    event_row=db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()
    if not event_row:
        raise ValueError('event finalization has not started')
    events=json.loads(event_row[0])
    if events.get('state')!='building' or type(events.get('next_ordinal')) is not int or type(events.get('target_count')) is not int or not 1<=events['next_ordinal']<=events['target_count']+(reviewed_event_resolution is not None):
        raise ValueError('event cursor is not resumable')
    if db.execute("SELECT 1 FROM archive_build_manifest WHERE key IN ('projection_v2','audit_v2','seal_v2') LIMIT 1").fetchone():
        raise ValueError('a later archive phase has already started')
    if reviewed_event_resolution is not None:
        encoded = Path(plan_path).read_bytes()
        reviewed_plan=json.loads(encoded)
        if (hashlib.sha256(encoded).hexdigest()!=plan_hash or
                reviewed_plan.get('reviewed_event_resolution')!=reviewed_event_resolution or
                reviewed_plan.get('schema')!='historical-active-events-execution/v1' or
                (reviewed_plan.get('dataset_id'),reviewed_plan.get('code_digest'),reviewed_plan.get('parent_dataset'),reviewed_plan.get('parent_digest')) !=
                (dataset,digest,parent_dataset,parent_digest)):
            raise ValueError('reviewed event transition plan differs')
        api.validate_event_conflict_decision(db,reviewed_event_resolution)
    elif db.execute('SELECT 1 FROM archive_targets_v2 WHERE event_state=\'conflict\' LIMIT 1').fetchone():
        raise ValueError('event conflict requires explicit review')
    if db.execute('SELECT 1 FROM archive_execution_transitions_v1 WHERE dataset_id=?',(dataset,)).fetchone():
        raise ValueError('event execution transition already exists')
    lineage={'schema':'historical-active-events-execution/v1','parent_dataset':parent_dataset,'parent_digest':parent_digest,
             'dataset_id':dataset,'code_digest':digest,'plan_path':str(plan_path),'plan_sha256':plan_hash,
             'event_cursor':events['next_ordinal'],'target_count':events['target_count']}
    allowed={'archive_build_manifest','archive_execution_transitions_v1','sqlite_master'}
    def authorize(action,table,*rest):
        if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE) and table not in allowed:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.set_authorizer(authorize)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS archive_execution_transitions_v1(dataset_id TEXT PRIMARY KEY,parent_dataset TEXT NOT NULL,parent_digest TEXT NOT NULL,code_digest TEXT NOT NULL,old_report_json TEXT NOT NULL,old_generation_json TEXT NOT NULL,plan_path TEXT NOT NULL,plan_sha256 TEXT NOT NULL,state TEXT NOT NULL)')
        db.execute('INSERT INTO archive_execution_transitions_v1 VALUES(?,?,?,?,?,?,?,?,?)',(dataset,parent_dataset,parent_digest,digest,_encoded(before),_encoded(gen),str(plan_path),plan_hash,'complete'))
        db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(_encoded({**before,'dataset_id':dataset,'builder_code_digest':digest}),))
        inputs._save(db,{**gen,'dataset_id':dataset,'code_digest':digest,
                         'prior_generation_plan':{'path':gen['plan_path'],'sha256':gen['plan_sha256']},
                         'plan_path':str(plan_path),'plan_sha256':plan_hash,
                         'execution_transitions':[*gen.get('execution_transitions',[]),lineage]})
    finally:
        db.set_authorizer(None)
    return lineage


def _encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _apply(db, parent_dataset, parent_digest, dataset, digest, plan_path, plan_hash):
    from scripts import prepared_historical_archive_v2 as api
    from scripts import prepared_historical_archive_inputs as inputs
    if not db.in_transaction:
        raise ValueError('caller transaction required')
    if dataset == parent_dataset or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',dataset):
        raise ValueError('distinct new dataset required')
    before=api.report(db); generation=inputs.generation(db)
    if (before.get('dataset_id'),before.get('builder_code_digest'),before.get('build_state'),before.get('archive_schema_version')) != (parent_dataset,parent_digest,'building',2):
        raise ValueError('explicit parent construction mismatch')
    if not generation or (generation.get('dataset_id'),generation.get('code_digest'),generation.get('state')) != (parent_dataset,parent_digest,'complete'):
        raise ValueError('completed parent input generation required')
    inputs._no_derived(db);inputs._seed_core_only(db)
    if db.execute('SELECT owner FROM archive_builder_v2 WHERE id=1').fetchone() != (None,):
        raise ValueError('persistent builder owner still present')
    coverage=db.execute("SELECT value_json FROM archive_build_manifest WHERE key='coverage_v2'").fetchone()
    if not coverage or json.loads(coverage[0]).get('state') not in ('complete','complete_with_source_failures'):
        raise ValueError('complete coverage boundary required')
    next_generation={**generation,'dataset_id':dataset,'code_digest':digest,
                     'prior_generation_plan':{'path':generation['plan_path'],'sha256':generation['plan_sha256']},
                     'plan_path':str(plan_path),'plan_sha256':plan_hash}
    lineage={'parent_dataset':parent_dataset,'parent_digest':parent_digest,'dataset_id':dataset,
             'code_digest':digest,'kind':'equivalent-batch-reads-and-clean-body-reuse'}
    next_generation['execution_transitions']=[*generation.get('execution_transitions',[]),lineage]
    after={**before,'dataset_id':dataset,'builder_code_digest':digest}
    db.execute('CREATE TABLE IF NOT EXISTS archive_execution_transitions_v1(dataset_id TEXT PRIMARY KEY,parent_dataset TEXT NOT NULL,parent_digest TEXT NOT NULL,code_digest TEXT NOT NULL,old_report_json TEXT NOT NULL,old_generation_json TEXT NOT NULL,plan_path TEXT NOT NULL,plan_sha256 TEXT NOT NULL,state TEXT NOT NULL)')
    db.execute('INSERT INTO archive_execution_transitions_v1 VALUES(?,?,?,?,?,?,?,?,?)',
               (dataset,parent_dataset,parent_digest,digest,_encoded(before),_encoded(generation),str(plan_path),plan_hash,'complete'))
    db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(_encoded(after),))
    inputs._save(db,next_generation)
    return lineage


def rollback(db,dataset,digest):
    from scripts import prepared_historical_archive_v2 as api
    from scripts import prepared_historical_archive_inputs as inputs
    if not db.in_transaction:raise ValueError('caller transaction required')
    current=api.report(db)
    if (current.get('dataset_id'),current.get('builder_code_digest'),current.get('build_state')) != (dataset,digest,'building'):
        raise ValueError('matching unfinished transition required')
    inputs._no_derived(db)
    row=db.execute("SELECT old_report_json,old_generation_json FROM archive_execution_transitions_v1 WHERE dataset_id=? AND code_digest=? AND state='complete'",(dataset,digest)).fetchone()
    if not row:raise ValueError('transition history missing')
    db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'",(row[0],))
    inputs._save(db,json.loads(row[1]))
    db.execute("UPDATE archive_execution_transitions_v1 SET state='reverted' WHERE dataset_id=?",(dataset,))


def transition(path,old_root,new_root,parent_dataset,dataset, *, finalized=False,active_events=False,reviewed_event_resolution=None):
    if finalized and active_events:
        raise ValueError('select one transition boundary')
    if reviewed_event_resolution is not None and not active_events:
        raise ValueError('event conflict review requires active event transition')
    decision=json.loads(reviewed_event_resolution.read_text(encoding='utf-8')) if reviewed_event_resolution is not None else None
    for source in (new_root/'src',new_root):sys.path.insert(0,str(source))
    from scripts import prepared_historical_archive_v2 as api
    from scripts import prepared_historical_archive_inputs as inputs
    if Path(api.__file__).resolve()!=new_root/'scripts/prepared_historical_archive_v2.py':
        raise ValueError('new frozen code import mismatch')
    old=json.loads((old_root/'code-files.json').read_text());new=json.loads((new_root/'code-files.json').read_text())
    old_files={r['path']:r['sha256'] for r in old['files']};new_files={r['path']:r['sha256'] for r in new['files']}
    permitted = (CHANGES | {'scripts/preprocess_historical_news_to_nas.py',
                            'scripts/audit_prepared_historical_archive_v2.py',
                            'scripts/transition_historical_archive_execution.py'}) if finalized else CHANGES
    changed = {p for p in old_files.keys() | new_files.keys() if old_files.get(p)!=new_files.get(p)}
    event_changes={'scripts/prepared_historical_archive_v2.py','scripts/transition_historical_archive_execution.py'}
    if decision is not None:
        event_changes |= {'scripts/finalize_prepared_historical_news_events.py','scripts/audit_prepared_historical_archive_v2.py'}
    expected_changes=event_changes if active_events else CHANGES
    if active_events:
        valid_changes=bool(changed) and changed.issubset(event_changes)
    else:
        valid_changes=(changed == expected_changes) if not finalized else (bool(changed) and changed.issubset(permitted))
    if not valid_changes:
        raise ValueError('unexpected execution source differences')
    for root,files in ((old_root,old_files),(new_root,new_files)):
        for name,hashed in files.items():
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=hashed:
                raise ValueError('frozen source changed: '+name)
    digest=api.code_digest();parent_digest=old['construction_digest']
    if digest!=new['construction_digest'] or new.get('parent_construction_digest')!=parent_digest:
        raise ValueError('construction digest lineage mismatch')
    api._require_local(path)
    with api._exclusive_lock(path),closing(sqlite3.connect(path,timeout=5)) as db:
        before=api.report(db);generation=inputs.generation(db)
        plan=Path(str(path)+'.execution-'+dataset+'.json')
        if before.get('dataset_id')==dataset and before.get('builder_code_digest')==digest:
            row=db.execute("SELECT parent_dataset,parent_digest,plan_path,plan_sha256,state FROM archive_execution_transitions_v1 WHERE dataset_id=? AND code_digest=?",(dataset,digest)).fetchone()
            if (not row or row[:2]!=(parent_dataset,parent_digest) or row[4]!='complete' or
                    not generation or generation.get('dataset_id')!=dataset or generation.get('code_digest')!=digest or
                    generation.get('plan_path')!=row[2] or generation.get('plan_sha256')!=row[3] or
                    hashlib.sha256(Path(row[2]).read_bytes()).hexdigest()!=row[3]):
                raise ValueError('committed transition recovery proof mismatch')
            if json.loads(Path(row[2]).read_bytes()).get('reviewed_event_resolution')!=decision:
                raise ValueError('committed event review decision differs')
            return {'state':'complete','dataset_id':dataset,'code_digest':digest,'parent_dataset':parent_dataset,
                    'parent_digest':parent_digest,'plan_path':row[2],'plan_sha256':row[3],'archive':str(path),
                    'recovered_receipt':True}
        scopes=_prefix_snapshot(db) if finalized else None
        if decision is not None:
            api.validate_event_conflict_decision(db,decision)
        schema='historical-finalization-execution/v1' if finalized else ('historical-active-events-execution/v1' if active_events else 'historical-archive-execution-transition/v1')
        content=_encoded({'schema':schema,'archive':str(path),
                          'parent_report':before,'parent_generation':generation,'dataset_id':dataset,
                          'code_digest':digest,'parent_dataset':parent_dataset,'parent_digest':parent_digest,
                          'scopes':scopes,'event_cursor':json.loads(db.execute("SELECT value_json FROM archive_build_manifest WHERE key='events_v2'").fetchone()[0])['next_ordinal'] if active_events else None,
                          'old_code_root':str(old_root),'new_code_root':str(new_root),
                          'changed_files':sorted(changed),
                          **({'reviewed_event_resolution':decision} if decision is not None else {})})
        if plan.exists():
            if plan.read_text(encoding='utf-8')!=content:raise ValueError('different transition plan already exists')
        else:
            with plan.open('x',encoding='utf-8') as f:f.write(content)
        hashed=hashlib.sha256(content.encode()).hexdigest()
        with api._transaction(db,check_capacity=False):
            result=(_apply_finalized(db,parent_dataset,parent_digest,dataset,digest,plan,hashed,scopes)
                    if finalized else (_apply_active_events(db,parent_dataset,parent_digest,dataset,digest,plan,hashed,reviewed_event_resolution=decision)
                                       if active_events else _apply(db,parent_dataset,parent_digest,dataset,digest,plan,hashed)))
    return {**result,'state':'complete','plan_path':str(plan),'plan_sha256':hashed,'archive':str(path)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--old-code',type=Path,required=True)
    p.add_argument('--new-code',type=Path,required=True);p.add_argument('--parent-dataset',required=True);p.add_argument('--dataset',required=True)
    p.add_argument('--finalized-prefix',action='store_true')
    p.add_argument('--active-events',action='store_true')
    p.add_argument('--reviewed-event-resolution',type=Path)
    a=p.parse_args()
    print(json.dumps(transition(a.archive.resolve(strict=True),a.old_code.resolve(strict=True),a.new_code.resolve(strict=True),a.parent_dataset,a.dataset,finalized=a.finalized_prefix,active_events=a.active_events,reviewed_event_resolution=a.reviewed_event_resolution),ensure_ascii=False))
