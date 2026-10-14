"""Explicit, resumable PC construction-generation adoption and input ownership.

Serving tables remain v2. This module owns only the construction input contract;
it never fetches articles, invokes NAS processing, or changes captured evidence.
"""
from __future__ import annotations

from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from typing import Iterator

CONTRACT = "historical-archive-input-components/v1"
KEY = "input_generation_v3"
EXCLUSION_POLICY = "captured-search-exclusions/20261014"
FAILURE_POLICY = 'preserved-local-preparation-failures/20261014'
EXCLUSIONS = {
    ("article_unavailable", "article_unavailable"), ("blocked", "blocked"),
    ("fetch_error", "fetch_error"), ("not_fetched", "publication_time_unverified"),
    ("source_excluded", "source_excluded"), ("time_not_found", "time_not_found"),
    ("title_mismatch", "title_mismatch"),
}
FIELDS = "stock_code,identity,state,article_json,body_json,rules_json,error,updated_at"


class ArchiveBuildPaused(RuntimeError):
    pass


def check_control(db) -> None:
    """Optional PC-only master; pause before BEGIN, never stop a live transaction."""
    filename = db.execute('PRAGMA database_list').fetchone()[2]
    if not filename:
        return
    control = Path(filename + '.input-controls.json')
    if not control.exists():
        return
    value = json.loads(control.read_text(encoding='utf-8'))
    if value.get('schema') != 'historical-archive-pc-controls/v1' or Path(value.get('archive', '')).resolve() != Path(filename).resolve():
        raise ValueError('invalid PC archive control identity')
    phase = value.get('active_phase')
    if not value.get('master') or not value.get('phases', {}).get(phase) or (value.get('expires_at') is not None and time.time() >= value['expires_at']):
        raise ArchiveBuildPaused('PC archive master/phase disabled or expired')


def _api():
    from scripts import prepared_historical_archive_v2 as api
    return api


def _hash(value) -> str:
    return hashlib.sha256(_api()._json(value).encode()).hexdigest()


def generation(db: sqlite3.Connection) -> dict | None:
    row = db.execute("SELECT value_json FROM archive_build_manifest WHERE key=?", (KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def _save(db, value):
    db.execute("INSERT INTO archive_build_manifest VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
               (KEY, _api()._json(value)))


def _root(rows) -> dict:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        encoded = _api()._json(row).encode()
        digest.update(len(encoded).to_bytes(8, 'big')); digest.update(encoded)
        count += 1
    return {'rows': count, 'sha256': digest.hexdigest()}


def _protected_roots(db) -> dict:
    """Bind parent evidence keys/digests; full audit also hashes payload bytes."""
    failures = ('SELECT f.* FROM archive_failures_v2 f LEFT JOIN archive_input_bindings_v3 b USING(scope,stock_code,identity) WHERE b.component_id IS NULL OR b.before_json IS NOT NULL ORDER BY f.scope,f.stock_code,f.identity'
                if generation(db) else 'SELECT * FROM archive_failures_v2 ORDER BY scope,stock_code,identity')
    return {
        'history': _root(db.execute('SELECT scope,source_table,source_row_key,state,payload_sha256 FROM archive_source_history_v2 ORDER BY scope,source_table,source_row_key')),
        'coverage': _root(db.execute('SELECT scope,stock_code,identity,input_hash,metadata_json FROM archive_source_coverage_v2 ORDER BY scope,stock_code,identity')),
        'seed_roles': _root(db.execute('SELECT * FROM archive_seed_roles ORDER BY table_name,row_key')),
        'failures': _root(db.execute(failures)),
    }


def _external_plan(path: Path, value: dict, db) -> tuple[str, str]:
    """An immutable plan is evidence, not a claim that migration committed."""
    target = path.with_name(path.name + '.input-generation-plan.json')
    encoded = _api()._json({'schema': CONTRACT, 'kind': 'planned_transition',
                            'archive': str(path), 'generation': value,
                            'parent_manifests': list(db.execute('SELECT * FROM archive_build_manifest ORDER BY key'))})
    if target.exists():
        if target.read_text(encoding='utf-8') != encoded:
            raise ValueError('external generation plan differs')
    else:
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf-8', newline='') as output:
            output.write(encoded); output.flush()
        temporary.replace(target)
    return str(target), hashlib.sha256(encoded.encode()).hexdigest()


def _base_progress_root(db, *, adopted=False) -> dict:
    def rows():
        sql = ('SELECT p.*,b.before_json,b.component_id FROM archive_input_progress p LEFT JOIN archive_input_bindings_v3 b USING(scope,stock_code,identity) ORDER BY p.scope,p.stock_code,p.identity'
               if adopted else 'SELECT * FROM archive_input_progress ORDER BY scope,stock_code,identity')
        for row in db.execute(sql):
            if adopted:
                if row[10] is not None:
                    if row[9] is None:
                        continue
                    row = json.loads(row[9])
            # Completion advances mutable state/IDs, not the input definition.
            yield tuple(row[:5]) + (row[6],)
    return _root(rows())


def _no_derived(db):
    for table in ("archive_targets_v2", "archive_input_context_v2", "archive_expected_targets_v2", "archive_projection_v2"):
        if db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone():
            raise ValueError("generation adoption/rollback requires no finalized input")
    if db.execute("SELECT 1 FROM archive_input_progress WHERE progress_state NOT IN ('pending','source_failed','source_excluded') OR article_revision_id IS NOT NULL OR body_revision_id IS NOT NULL LIMIT 1").fetchone():
        raise ValueError("generation adoption/rollback requires no finalized input")
    if db.execute("SELECT 1 FROM archive_build_manifest WHERE key IN ('events_v2','projection_v2')").fetchone():
        raise ValueError("event or projection inputs already frozen")


def _seed_core_only(db):
    from scripts.export_historical_news_seed import TABLE_KEYS
    for table in TABLE_KEYS:
        actual = db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
        expected = db.execute('SELECT COUNT(*) FROM archive_seed_roles WHERE table_name=?', (table,)).fetchone()[0]
        if actual != expected:
            raise ValueError('unaccounted core rows before generation adoption: ' + table)


@contextmanager
def _adoption_writer(path: Path) -> Iterator[sqlite3.Connection]:
    api = _api()
    path = path.resolve(strict=True)
    api._require_local(path)
    with api._exclusive_lock(path), closing(sqlite3.connect(path, timeout=5)) as db:
        value = generation(db)
        report = api.report(db)
        if not value or value.get("contract") != CONTRACT or value.get("state") != "adopting" or report.get("build_state") != "adopting" or report.get("builder_code_digest") != api.code_digest():
            raise ValueError("matching adopting generation required")
        _no_derived(db)
        yield db


def adopt(path: Path, *, parent_dataset: str, parent_digest: str, dataset_id: str) -> dict:
    """Only this entry point admits an explicitly named previous code digest.

    It preserves the complete parent manifest in the same atomic transaction.
    Source hashes are not relabeled. No serving/core/raw row is written.
    """
    api = _api()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", dataset_id) or dataset_id == parent_dataset or not re.fullmatch(r"[a-f0-9]{64}", parent_digest):
        raise ValueError("invalid parent or new generation identity")
    path = path.resolve(strict=True)
    api._require_local(path)
    with api._exclusive_lock(path), closing(sqlite3.connect(path, timeout=5)) as db:
        current = generation(db)
        if current:
            if (current.get("parent_dataset"), current.get("parent_digest"), current.get("dataset_id"), current.get("code_digest")) != (parent_dataset, parent_digest, dataset_id, api.code_digest()):
                raise ValueError("different adoption already registered")
            return current
        before = api.report(db)
        if (before.get("build_state"), before.get("archive_schema_version"), before.get("dataset_id"), before.get("builder_code_digest")) != ("building", 2, parent_dataset, parent_digest):
            raise ValueError("parent generation identity mismatch")
        _no_derived(db)
        _seed_core_only(db)
        sources = db.execute("SELECT * FROM archive_sources_v2 ORDER BY scope").fetchall()
        if {r[0] for r in sources} != set(api.SCOPES) or any(r[3] != r[4] or r[7] != "complete" for r in sources):
            raise ValueError("both base sources must be completely staged")
        for scope in api.SCOPES:
            row = db.execute("SELECT value_json FROM archive_build_manifest WHERE key=?", ("source_capture_v2_" + scope,)).fetchone()
            if not row or json.loads(row[0]).get("state") != "complete":
                raise ValueError("both raw captures must be complete")
        for scope, filename, digest, expected, *_ in sources:
            view = db.execute('SELECT value_json FROM archive_build_manifest WHERE key=?', ('prepared_scope_view_v2_' + scope,)).fetchone()
            aliases = tuple(json.loads(view[0]).get('scope_aliases', ())) if view else ()
            with api._frozen_source(Path(filename), digest, scope=scope, scope_aliases=aliases) as source:
                if source.execute('SELECT COUNT(*) FROM prepared_news WHERE scope=?', (scope,)).fetchone()[0] != expected:
                    raise ValueError('base source count changed')
        value = dict(contract=CONTRACT, state="adopting", parent_dataset=parent_dataset,
                     parent_digest=parent_digest, dataset_id=dataset_id, code_digest=api.code_digest(),
                     base_sources=sources, exclusions_cursor=0, exclusions_complete=False,
                     protected_roots=_protected_roots(db), base_progress_root=_base_progress_root(db))
        previous_index = db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='archive_source_coverage_missing_v2'").fetchone()
        value['exclusion_index'] = previous_index[0] if previous_index else 'archive_source_coverage_missing_v3'
        value['plan_path'], value['plan_sha256'] = _external_plan(path, value, db)
        with api._transaction(db):
            for sql in (
                "CREATE TABLE archive_parent_manifests_v3(key TEXT PRIMARY KEY,value_json TEXT NOT NULL)",
                "INSERT INTO archive_parent_manifests_v3 SELECT * FROM archive_build_manifest",
                "CREATE TABLE archive_parent_failure_reviews_v3 AS SELECT * FROM archive_failure_review_v2",
                "CREATE TABLE archive_input_components_v3(component_id TEXT PRIMARY KEY,kind TEXT NOT NULL,scope TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,aliases_json TEXT NOT NULL,expected INTEGER NOT NULL,staged INTEGER NOT NULL DEFAULT 0,last_stock TEXT,last_identity TEXT,state TEXT NOT NULL)",
                "CREATE TABLE archive_component_members_v3(component_id TEXT NOT NULL,scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,input_hash TEXT NOT NULL,PRIMARY KEY(component_id,scope,stock_code,identity))",
                "CREATE TABLE archive_binding_history_v3(component_id TEXT NOT NULL,scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,previous_binding_json TEXT NOT NULL,previous_progress_json TEXT NOT NULL,PRIMARY KEY(component_id,scope,stock_code,identity))",
                "CREATE TABLE archive_input_bindings_v3(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,component_id TEXT NOT NULL,input_hash TEXT NOT NULL,before_json TEXT,coverage_hash TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))",
                "CREATE INDEX archive_input_bindings_component_v3 ON archive_input_bindings_v3(component_id)",
                "CREATE TABLE archive_source_exclusions_v3(scope TEXT NOT NULL,stock_code TEXT NOT NULL,identity TEXT NOT NULL,metadata_hash TEXT NOT NULL,reason TEXT NOT NULL,policy TEXT NOT NULL,raw_key TEXT NOT NULL,raw_hash TEXT NOT NULL,PRIMARY KEY(scope,stock_code,identity))",
                "CREATE INDEX archive_captured_article_hash_v3 ON archive_source_history_v2(scope,payload_sha256,source_row_key) WHERE source_table='news_articles'",
            ):
                db.execute(sql)
            if not previous_index:
                db.execute('CREATE INDEX archive_source_coverage_missing_v3 ON archive_source_coverage_v2(scope) WHERE input_hash IS NULL')
            before = {**before, "build_state": "adopting", "dataset_id": dataset_id,
                      "builder_code_digest": api.code_digest(), "construction_contract": CONTRACT}
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'", (api._json(before),))
            _save(db, value)
        return value


def stage_component(path: Path, prepared: Path, scope: str, digest: str, *, component_id: str,
                    kind: str, max_rows: int = 0, batch_size: int = 250) -> dict:
    api = _api()
    if kind not in ("supplement", "repair") or scope not in api.SCOPES or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", component_id) or not 1 <= batch_size <= 1000 or max_rows < 0:
        raise ValueError("invalid input component")
    if path.resolve() == prepared.resolve():
        raise ValueError("archive cannot be its own input")
    with api._frozen_source(prepared, digest, scope=scope) as source, _adoption_writer(path) as db:
        states = dict(source.execute("SELECT state,COUNT(*) FROM prepared_news GROUP BY state"))
        if not states or set(states) - {"ready", "failed"} or source.execute("SELECT 1 FROM prepared_news WHERE scope<>? LIMIT 1", (scope,)).fetchone():
            raise ValueError("unsupported component scope or state")
        count = sum(states.values())
        frozen = (kind, scope, str(prepared.resolve()), digest, "[]", count)
        saved = db.execute("SELECT kind,scope,path,sha256,aliases_json,expected FROM archive_input_components_v3 WHERE component_id=?", (component_id,)).fetchone()
        if saved and saved != frozen:
            raise ValueError("component identity changed")
        if not saved:
            with api._transaction(db):
                db.execute("INSERT INTO archive_input_components_v3(component_id,kind,scope,path,sha256,aliases_json,expected,state) VALUES(?,?,?,?,?,?,?,'building')", (component_id, *frozen))
        done = 0
        while not max_rows or done < max_rows:
            state = db.execute("SELECT staged,last_stock,last_identity,state FROM archive_input_components_v3 WHERE component_id=?", (component_id,)).fetchone()
            if state[3] == "complete":
                break
            rows = source.execute("SELECT " + FIELDS + " FROM prepared_news WHERE scope=? AND (stock_code,identity)>(?,?) ORDER BY stock_code,identity LIMIT ?", (scope, state[1] or "", state[2] or "", min(batch_size, max_rows - done) if max_rows else batch_size)).fetchall()
            if not rows:
                raise ValueError("component ended before frozen count")
            with api._transaction(db):
                for row in rows:
                    owner, identity, row_state = row[:3]
                    key = (scope, owner, identity)
                    if not owner or not identity or (row_state == "failed" and not str(row[6]).strip()):
                        raise ValueError("unexplained component row")
                    previous_binding = db.execute("SELECT * FROM archive_input_bindings_v3 WHERE scope=? AND stock_code=? AND identity=?", key).fetchone()
                    if previous_binding and kind != 'repair':
                        raise ValueError("overlapping input components")
                    coverage = db.execute("SELECT input_hash,metadata_json FROM archive_source_coverage_v2 WHERE scope=? AND stock_code=? AND identity=?", key).fetchone()
                    if not coverage:
                        raise ValueError("component key has no raw coverage")
                    old = db.execute("SELECT * FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?", key).fetchone()
                    if kind == "supplement" and (old or coverage[0] is not None):
                        raise ValueError("supplement overlaps base input")
                    if kind == "repair":
                        expected_old = previous_binding[4] if previous_binding else coverage[0]
                        if not old or old[4] != 'failed' or old[5] != "source_failed" or old[3] != expected_old or row_state != "ready":
                            raise ValueError("repair requires exact unfinalized failed base input")
                        failure = db.execute("SELECT error,article_json,body_json,rules_json FROM archive_failures_v2 WHERE scope=? AND stock_code=? AND identity=?", key).fetchone()
                        if not failure or failure[0] != old[6]:
                            raise ValueError("original failure evidence missing")
                    hashed = api._input_hash(row)
                    new = (*key, hashed, row_state, "pending" if row_state == "ready" else "source_failed", row[6], None, None)
                    before = previous_binding[5] if previous_binding else api._json(old) if old else None
                    if previous_binding:
                        db.execute('INSERT INTO archive_binding_history_v3 VALUES(?,?,?,?,?,?)', (component_id, *key, api._json(previous_binding), api._json(old)))
                        db.execute('DELETE FROM archive_input_bindings_v3 WHERE scope=? AND stock_code=? AND identity=?', key)
                    db.execute("INSERT INTO archive_input_bindings_v3 VALUES(?,?,?,?,?,?,?)", (*key, component_id, hashed, before, _hash(coverage)))
                    db.execute('INSERT INTO archive_component_members_v3 VALUES(?,?,?,?,?)', (component_id, *key, hashed))
                    if old:
                        db.execute("UPDATE archive_input_progress SET input_hash=?,source_state=?,progress_state=?,error=?,article_revision_id=NULL,body_revision_id=NULL WHERE scope=? AND stock_code=? AND identity=? AND input_hash=?", (*new[3:7], *key, old[3]))
                    else:
                        db.execute("INSERT INTO archive_input_progress VALUES(?,?,?,?,?,?,?,?,?)", new)
                    if row_state == "failed":
                        db.execute("INSERT INTO archive_failures_v2 VALUES(?,?,?,?,?,?,?)", (*key, row[6], row[3], row[4], row[5]))
                total = state[0] + len(rows)
                if total > count:
                    raise ValueError("component count exceeded")
                db.execute("UPDATE archive_input_components_v3 SET staged=?,last_stock=?,last_identity=?,state=? WHERE component_id=?", (total, rows[-1][0], rows[-1][1], "complete" if total == count else "building", component_id))
            done += len(rows)
        return {"component_id": component_id, "staged_now": done, "expected": count}


def exclusion_reason(metadata: dict) -> str | None:
    reason = (str(metadata.get("article_fetch_status") or ""), str(metadata.get("training_exclusion_reason") or ""))
    # Captured source fields, not a newly supplied caller approval flag.
    if str(metadata.get("training_eligible")) == "0" and reason in EXCLUSIONS:
        return reason[0] + ":" + reason[1]
    return None


def _failure_facts(db, scope, owner, identity, hashed, error) -> dict:
    api = _api()
    row = db.execute('SELECT metadata_json FROM archive_source_coverage_v2 WHERE scope=? AND stock_code=? AND identity=?', (scope, owner, identity)).fetchone()
    failure = db.execute('SELECT error,body_json FROM archive_failures_v2 WHERE scope=? AND stock_code=? AND identity=?', (scope, owner, identity)).fetchone()
    if not row or not failure or str(failure[0]) != str(error) or str(json.loads(failure[1]).get('body_text') or '').strip():
        raise ValueError('unexplained failure or available prepared BODY requires repair')
    metadata = json.loads(row[0])
    no_title = not str(metadata.get('title') or '').strip()
    no_summary = not str(metadata.get('summary') or '').strip()
    if scope == api.MARKET and no_title and str(error).endswith('시황뉴스 원자료 identity가 일치하지 않습니다.'):
        classification = 'malformed_market_raw_missing_title'
    elif no_summary and str(error).endswith('기사 본문과 목록 요약을 가져오지 못했습니다.'):
        classification = 'no_local_body_or_summary'
    else:
        raise ValueError('failure has available summary or unknown cause; repair required')
    if scope == api.SEARCH:
        cached = db.execute("SELECT 1 FROM archive_source_history_v2 WHERE scope=? AND source_table='news_article_body_snapshots' AND json_extract(payload_json,'$.provider')=? AND json_extract(payload_json,'$.office_id')=? AND json_extract(payload_json,'$.article_id')=? AND LENGTH(TRIM(COALESCE(json_extract(payload_json,'$.body_text'),'')))>0 LIMIT 1", (scope, metadata.get('provider'), metadata.get('office_id'), metadata.get('article_id'))).fetchone()
        if cached:
            raise ValueError('available captured BODY requires repair')
    # Only preserved seed bodies count here. A later derived result must not
    # silently rewrite what was available at the frozen input boundary.
    seed_body = db.execute("""SELECT 1 FROM central_news_article_revisions a
        JOIN central_news_body_revisions b USING(article_revision_id)
        JOIN archive_seed_roles r ON r.table_name='central_news_body_revisions' AND r.row_key=json_array(b.body_revision_id)
        WHERE a.stock_code=? AND a.identity=? AND LENGTH(TRIM(b.body_text))>0 LIMIT 1""", (owner, identity)).fetchone()
    if seed_body:
        raise ValueError('available seed BODY requires repair')
    return {'policy': FAILURE_POLICY, 'class': classification, 'input_hash': hashed,
            'error_hash': _hash(error), 'metadata_hash': _hash(metadata),
            'title_present': not no_title, 'summary_present': not no_summary,
            'meaning': 'original failure preserved; no claim of permanent website unavailability'}


def review_local_failures(path: Path, *, max_rows: int = 0, batch_size: int = 100) -> dict:
    """Per-key evidence review leaves failures as failures, never successes."""
    api = _api()
    if max_rows < 0 or not 1 <= batch_size <= 250:
        raise ValueError('invalid failure review batch')
    done = 0
    with _adoption_writer(path) as db:
        while not max_rows or done < max_rows:
            rows = db.execute("""SELECT p.scope,p.stock_code,p.identity,p.input_hash,p.error
                FROM archive_input_progress p LEFT JOIN archive_failure_review_v2 r USING(scope,stock_code,identity)
                WHERE p.progress_state='source_failed' AND r.identity IS NULL
                ORDER BY p.scope,p.stock_code,p.identity LIMIT ?""", (min(batch_size, max_rows-done) if max_rows else batch_size,)).fetchall()
            if not rows:
                break
            with api._transaction(db):
                for row in rows:
                    facts = _failure_facts(db, *row)
                    db.execute('INSERT INTO archive_failure_review_v2 VALUES(?,?,?,?,?)', (*row[:4], api._json(facts)))
            done += len(rows)
    return {'reviewed_now': done, 'failures_preserved': True}


def stage_exclusions(path: Path, *, max_rows: int = 0, batch_size: int = 500) -> dict:
    api = _api()
    if max_rows < 0 or not 1 <= batch_size <= 1000:
        raise ValueError("invalid exclusion batch")
    done = inserted = 0
    with _adoption_writer(path) as db:
        value = generation(db)
        index = value.get('exclusion_index')
        if index not in ('archive_source_coverage_missing_v2', 'archive_source_coverage_missing_v3'):
            raise ValueError('invalid source exclusion range index')
        while not value["exclusions_complete"] and (not max_rows or done < max_rows):
            rows = db.execute("SELECT c.rowid,c.scope,c.stock_code,c.identity,c.metadata_json FROM archive_source_coverage_v2 c INDEXED BY " + index + " WHERE c.scope=? AND c.input_hash IS NULL AND c.rowid>? AND NOT EXISTS(SELECT 1 FROM archive_input_progress p WHERE p.scope=c.scope AND p.stock_code=c.stock_code AND p.identity=c.identity) ORDER BY c.rowid LIMIT ?", (api.SEARCH, value["exclusions_cursor"], min(batch_size, max_rows - done) if max_rows else batch_size)).fetchall()
            batch_added = 0
            with api._transaction(db):
                for rowid, scope, owner, identity, encoded in rows:
                    metadata = json.loads(encoded)
                    reason = exclusion_reason(metadata) if scope == api.SEARCH else None
                    if reason and not db.execute("SELECT 1 FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?", (scope, owner, identity)).fetchone():
                        raw_hash = _hash({k: v for k, v in metadata.items() if k != 'stock_name'})
                        evidence = db.execute("SELECT source_row_key FROM archive_source_history_v2 INDEXED BY archive_captured_article_hash_v3 WHERE scope=? AND source_table='news_articles' AND payload_sha256=? LIMIT 1", (scope, raw_hash)).fetchone()
                        if not evidence:
                            raise ValueError('excluded metadata has no captured raw article evidence')
                        db.execute("INSERT INTO archive_source_exclusions_v3 VALUES(?,?,?,?,?,?,?,?)", (scope, owner, identity, _hash(metadata), reason, EXCLUSION_POLICY, evidence[0], raw_hash))
                        inserted += 1
                        batch_added += 1
                if rows:
                    value["exclusions_cursor"] = rows[-1][0]
                    value['exclusions_staged'] = value.get('exclusions_staged', 0) + batch_added
                else:
                    value["exclusions_complete"] = True
                _save(db, value)
            done += len(rows)
        return {"scanned_now": done, "excluded_now": inserted, "complete": value["exclusions_complete"]}


def input_counts(db, scope: str) -> tuple[int, int]:
    """Expected effective union count; repairs do not increase cardinality."""
    base = db.execute("SELECT expected_rows,staged_rows FROM archive_sources_v2 WHERE scope=?", (scope,)).fetchone()
    if not generation(db):
        return base
    added = db.execute("SELECT COALESCE(SUM(expected),0),COALESCE(SUM(staged),0) FROM archive_input_components_v3 WHERE scope=? AND kind='supplement'", (scope,)).fetchone()
    return base[0] + added[0], base[1] + added[1]


def contract_blockers(db, *, check_files: bool = False) -> list[str]:
    api = _api()
    value = generation(db)
    if not value:
        return []
    problems = []
    if value.get("contract") != CONTRACT or value.get("code_digest") != api.code_digest() or value.get("state") not in ("adopting", "complete"):
        return ["input_generation_invalid"]
    if list(map(list, db.execute("SELECT * FROM archive_sources_v2 ORDER BY scope"))) != value["base_sources"]:
        problems.append("base_source_manifest_changed")
    if check_files and (_protected_roots(db) != value.get('protected_roots') or _base_progress_root(db, adopted=True) != value.get('base_progress_root')):
        problems.append('parent_evidence_or_base_inputs_changed')
    if check_files:
        try:
            plan = Path(value['plan_path']).read_bytes()
            if hashlib.sha256(plan).hexdigest() != value['plan_sha256']:
                problems.append('generation_plan_changed')
        except (OSError, KeyError):
            problems.append('generation_plan_unavailable')
    if db.execute("SELECT 1 FROM archive_component_members_v3 m LEFT JOIN archive_input_components_v3 c USING(component_id) WHERE c.component_id IS NULL OR c.scope<>m.scope LIMIT 1").fetchone():
        problems.append('input_component_member_orphan')
    for cid, kind, scope, filename, digest, expected, staged, state in db.execute("SELECT component_id,kind,scope,path,sha256,expected,staged,state FROM archive_input_components_v3"):
        actual = db.execute("SELECT COUNT(*) FROM archive_component_members_v3 WHERE component_id=?", (cid,)).fetchone()[0]
        if kind not in ("supplement", "repair") or state != "complete" or expected != staged or actual != expected:
            problems.append("input_component_incomplete")
        if check_files:
            try:
                with api._frozen_source(Path(filename), digest, scope=scope) as source:
                    if source.execute("SELECT COUNT(*) FROM prepared_news WHERE scope=?", (scope,)).fetchone()[0] != expected:
                        problems.append("input_component_count_changed")
                    for owner, identity, hashed in db.execute("SELECT stock_code,identity,input_hash FROM archive_component_members_v3 WHERE component_id=?", (cid,)):
                        row = source.execute("SELECT " + FIELDS + " FROM prepared_news WHERE scope=? AND stock_code=? AND identity=?", (scope, owner, identity)).fetchone()
                        if not row or api._input_hash(row) != hashed:
                            problems.append("input_component_row_changed"); break
            except (OSError, ValueError, sqlite3.DatabaseError):
                problems.append("input_component_changed_or_unavailable")
    for scope, owner, identity, cid, hashed, before, captured_hash in db.execute("SELECT * FROM archive_input_bindings_v3"):
        coverage = db.execute("SELECT input_hash,metadata_json FROM archive_source_coverage_v2 WHERE scope=? AND stock_code=? AND identity=?", (scope, owner, identity)).fetchone()
        progress = db.execute("SELECT input_hash FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?", (scope, owner, identity)).fetchone()
        component = db.execute("SELECT kind,scope FROM archive_input_components_v3 WHERE component_id=?", (cid,)).fetchone()
        if not coverage or _hash(coverage) != captured_hash or progress != (hashed,) or not component or component[1] != scope:
            problems.append("input_binding_invalid"); break
        original = json.loads(before) if before else None
        repaired_supplement = db.execute('SELECT previous_binding_json,previous_progress_json FROM archive_binding_history_v3 WHERE component_id=? AND scope=? AND stock_code=? AND identity=?', (cid, scope, owner, identity)).fetchone()
        if (component[0] == "repair" and not repaired_supplement and (not original or original[:3] != [scope, owner, identity] or original[3] != coverage[0] or original[5] != "source_failed")) or (component[0] == "supplement" and (original or coverage[0] is not None)):
            problems.append("input_binding_parent_invalid"); break
        if db.execute('SELECT input_hash FROM archive_component_members_v3 WHERE component_id=? AND scope=? AND stock_code=? AND identity=?', (cid, scope, owner, identity)).fetchone() != (hashed,):
            problems.append('input_binding_member_invalid'); break
    for cid, scope, owner, identity, old_binding_json, old_progress_json in db.execute('SELECT * FROM archive_binding_history_v3'):
        previous = json.loads(old_binding_json); old = json.loads(old_progress_json)
        key = [scope, owner, identity]
        previous_member = db.execute('SELECT input_hash FROM archive_component_members_v3 WHERE component_id=? AND scope=? AND stock_code=? AND identity=?', (previous[3], *key)).fetchone()
        if previous[:3] != key or old[:3] != key or old[3] != previous[4] or old[5] != 'source_failed' or previous_member != (previous[4],):
            problems.append('repair_component_history_invalid'); break
    if db.execute("""SELECT 1 FROM archive_component_members_v3 m
          WHERE NOT EXISTS(SELECT 1 FROM archive_input_bindings_v3 b WHERE b.scope=m.scope AND b.stock_code=m.stock_code AND b.identity=m.identity AND b.component_id=m.component_id AND b.input_hash=m.input_hash)
          AND NOT EXISTS(SELECT 1 FROM archive_binding_history_v3 h WHERE h.scope=m.scope AND h.stock_code=m.stock_code AND h.identity=m.identity AND json_extract(h.previous_binding_json,'$[3]')=m.component_id AND json_extract(h.previous_binding_json,'$[4]')=m.input_hash) LIMIT 1""").fetchone():
        problems.append('input_component_member_unresolved')
    for row in db.execute("SELECT p.scope,p.stock_code,p.identity,p.input_hash,p.error,r.reason FROM archive_input_progress p LEFT JOIN archive_failure_review_v2 r USING(scope,stock_code,identity) WHERE p.progress_state='source_failed'"):
        try:
            if not row[5] or json.loads(row[5]) != _failure_facts(db, *row[:5]):
                problems.append('source_failure_evidence_unreviewed'); break
        except (ValueError, TypeError, sqlite3.DatabaseError):
            problems.append('source_failure_evidence_invalid'); break
    for scope, owner, identity, digest, reason, policy, raw_key, raw_hash, encoded, stored_raw_hash, prepared_identity in db.execute("SELECT e.*,c.metadata_json,h.payload_sha256,p.identity FROM archive_source_exclusions_v3 e LEFT JOIN archive_source_coverage_v2 c ON c.scope=e.scope AND c.stock_code=e.stock_code AND c.identity=e.identity LEFT JOIN archive_source_history_v2 h ON h.scope=e.scope AND h.source_table='news_articles' AND h.source_row_key=e.raw_key LEFT JOIN archive_input_progress p ON p.scope=e.scope AND p.stock_code=e.stock_code AND p.identity=e.identity"):
        metadata = json.loads(encoded) if encoded is not None else {}
        if scope != api.SEARCH or policy != EXCLUSION_POLICY or _hash(metadata) != digest or exclusion_reason(metadata) != reason or stored_raw_hash != raw_hash or _hash({k: v for k, v in metadata.items() if k != 'stock_name'}) != raw_hash or prepared_identity is not None:
            problems.append("source_exclusion_evidence_invalid"); break
    return sorted(set(problems))


def missing_coverage(db, scope: str | None = None) -> int:
    if not generation(db):
        sql = "SELECT COUNT(*) FROM archive_source_coverage_v2 c LEFT JOIN archive_input_progress p USING(scope,stock_code,identity) WHERE (p.input_hash IS NULL OR p.input_hash IS NOT c.input_hash)"
    else:
        sql = """SELECT COUNT(*) FROM archive_source_coverage_v2 c
            LEFT JOIN archive_input_progress p USING(scope,stock_code,identity)
            LEFT JOIN archive_input_bindings_v3 b USING(scope,stock_code,identity)
            LEFT JOIN archive_source_exclusions_v3 e USING(scope,stock_code,identity)
            WHERE NOT ((p.input_hash IS NOT NULL AND ((b.input_hash IS NULL AND p.input_hash IS c.input_hash)
                OR (b.input_hash IS NOT NULL AND p.input_hash IS b.input_hash)))
                OR (p.input_hash IS NULL AND e.identity IS NOT NULL))"""
    return db.execute(sql + (" AND c.scope=?" if scope else ""), (scope,) if scope else ()).fetchone()[0]


def complete_adoption(path: Path) -> dict:
    api = _api()
    with _adoption_writer(path) as db:
        value = generation(db)
        if not value["exclusions_complete"]:
            raise ValueError("exclusion staging unfinished")
        blockers = contract_blockers(db, check_files=True)
        if blockers or missing_coverage(db):
            raise ValueError("input adoption validation failed: " + ",".join(blockers or ["unexplained_missing_input"]))
        with api._transaction(db):
            value["state"] = "complete"
            _save(db, value)
            report = api.report(db)
            report["build_state"] = "building"
            db.execute("UPDATE archive_build_manifest SET value_json=? WHERE key='report'", (api._json(report),))
        return value


def rollback_adoption(path: Path) -> dict:
    api = _api()
    path = path.resolve(strict=True)
    api._require_local(path)
    with api._exclusive_lock(path), closing(sqlite3.connect(path, timeout=5)) as db:
        value = generation(db)
        if not value or value.get("code_digest") != api.code_digest() or api.report(db).get("build_state") not in ("adopting", "building"):
            raise ValueError("matching unsealed generation required")
        _no_derived(db)
        with api._transaction(db):
            for scope, owner, identity, before in db.execute("SELECT scope,stock_code,identity,before_json FROM archive_input_bindings_v3"):
                key = (scope, owner, identity)
                db.execute("DELETE FROM archive_input_progress WHERE scope=? AND stock_code=? AND identity=?", key)
                if before:
                    db.execute("INSERT INTO archive_input_progress VALUES(?,?,?,?,?,?,?,?,?)", json.loads(before))
                else:
                    db.execute("DELETE FROM archive_failures_v2 WHERE scope=? AND stock_code=? AND identity=?", key)
            if _base_progress_root(db) != value.get('base_progress_root') or _protected_roots(db) != value.get('protected_roots'):
                raise ValueError('parent evidence changed; rollback refused')
            db.execute('DELETE FROM archive_failure_review_v2')
            db.execute('INSERT INTO archive_failure_review_v2 SELECT * FROM archive_parent_failure_reviews_v3')
            db.execute("DELETE FROM archive_build_manifest")
            db.execute("INSERT INTO archive_build_manifest SELECT * FROM archive_parent_manifests_v3")
            for table in ("archive_input_bindings_v3", "archive_component_members_v3", "archive_binding_history_v3", "archive_input_components_v3", "archive_source_exclusions_v3", "archive_parent_failure_reviews_v3", "archive_parent_manifests_v3"):
                db.execute("DROP TABLE " + table)
            db.execute('DROP INDEX archive_captured_article_hash_v3')
            if value.get('exclusion_index') == 'archive_source_coverage_missing_v3':
                db.execute('DROP INDEX archive_source_coverage_missing_v3')
        return {"state": "parent_restored", "dataset_id": value["parent_dataset"], "byte_identical": False}


@contextmanager
def component_connections(db, scope: str):
    api = _api()
    with ExitStack() as stack:
        connections = {}
        if generation(db):
            for cid, filename, digest in db.execute("SELECT component_id,path,sha256 FROM archive_input_components_v3 WHERE scope=? AND state='complete'", (scope,)):
                connections[cid] = stack.enter_context(api._frozen_source(Path(filename), digest, scope=scope))
        yield connections


def select_source(db, base, connections, scope: str, owner: str, identity: str):
    if not generation(db):
        return base
    binding = db.execute("SELECT component_id FROM archive_input_bindings_v3 WHERE scope=? AND stock_code=? AND identity=?", (scope, owner, identity)).fetchone()
    if binding and binding[0] not in connections:
        raise ValueError("input component unavailable")
    return connections[binding[0]] if binding else base


def prepared_batch(db, base, connections, scope, pending):
    """Read each exact input once, using at most 501 binds per statement."""
    grouped = {}
    bindings = {}
    if generation(db):
        for offset in range(0, len(pending), 250):
            keys = pending[offset:offset+250]
            placeholders = ','.join('(?,?)' for _ in keys)
            args = [scope] + [v for owner, identity, _ in keys for v in (owner, identity)]
            bindings.update({(owner, identity): cid for owner, identity, cid in db.execute(
                'SELECT stock_code,identity,component_id FROM archive_input_bindings_v3 WHERE scope=? AND (stock_code,identity) IN ('+placeholders+')', args)})
    for owner, identity, _ in pending:
        cid = bindings.get((owner, identity))
        if cid is not None and cid not in connections:
            raise ValueError('input component unavailable')
        grouped.setdefault(cid, []).append((owner, identity))
    result = {}
    for cid, keys in grouped.items():
        source = base if cid is None else connections[cid]
        for offset in range(0, len(keys), 250):
            chunk = keys[offset:offset+250]
            placeholders = ','.join('(?,?)' for _ in chunk)
            args = [scope] + [v for key in chunk for v in key]
            for row in source.execute('SELECT '+FIELDS+' FROM prepared_news WHERE scope=? AND (stock_code,identity) IN ('+placeholders+')', args):
                key = tuple(row[:2])
                if key in result:
                    raise ValueError('duplicate prepared batch key')
                result[key] = tuple(row)
    return result
