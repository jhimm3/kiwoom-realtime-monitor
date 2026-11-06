"""Central document persistence and its theme/news sidecars."""
from __future__ import annotations

from kiwoom_monitor.domain.news_observation import news_projection_article_hash

from kiwoom_monitor.domain.research_contract import (
    ThemeGenerationConflict, ReportedResearchResult, ResearchResultConflict,
    REPORTED_RESEARCH_RESULT_COLLECTION, REPORTED_RESEARCH_RESULT_KIND,
)

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import partial
from time import monotonic, time
from typing import Any, Callable
from kiwoom_monitor.domain.time_basis import kst_now, as_kst
from kiwoom_monitor.domain.news_ai_contract import decode_news_ai_provenance
from kiwoom_monitor.domain.execution_activation import (
    StrategyLifecycleStage, stage_revision_from_dict, strategy_stage_revision_sort_key,
)

from kiwoom_monitor.domain.news_observation import (
    ARTICLE_BODY_EXTRACTOR_VERSION,
    stable_document_hash,
)
from kiwoom_monitor.domain.research_contract import ThemeSnapshotSource
from kiwoom_monitor.domain.selected_order_evidence import (
    COLLECTION as OFFLINE_ORDER_EVIDENCE_COLLECTION,
    OWNER as OFFLINE_ORDER_EVIDENCE_OWNER,
    RECOVERY_REQUIRED,
    encode_selected_order_evidence,
)
from kiwoom_monitor.application.journal_enrichment import (
    JOURNAL_ANALYSIS_REVISION_COLLECTION,
    JournalAnalysisRevisionConflict,
    journal_analysis_revision_content,
)

from .database_news_job_writes import (
    _insert_sqlite_news_job, _insert_postgres_news_job, _notify_news_job_wakeup,
)
from .database_news_revisions import (
    _native_news_catalog_values, _protect_reported_news_ai, _select_current_news_ai_results,
)

from .database_codec import (
    bounded_limit,
    document_result_rows,
    document_select_query,
    document_value_rows,
    json_mapping,
)

_CURRENT_RISK_COLLECTION = "execution_mock_automation_current_risk"


def _news_native_authority(store, db, *, postgres: bool) -> str:
    """Opaque scope from the active native DB, excluding credentials and paths on wire."""
    if postgres:
        info = db.connection.info
        scope = {name: getattr(info, name) for name in ("host", "hostaddr", "port", "dbname")}
        return stable_document_hash({"backend": "postgres", "scope": scope})
    return stable_document_hash({"backend": "sqlite", "scope": store._memory_uri or str(store._path.resolve())})


def _news_catalog_values(store, db, collection, values, *, postgres, read=False):
    if collection not in {"news_article", "news_ai", "news_ai_shared"} or not values:
        return values
    authority = _news_native_authority(store, db, postgres=postgres)
    if collection == "news_article":
        copied = values if read else [value for value in values if value.get("collection_scope") == "local_projection_sync"
                                      and value.get("document", {}).get("input_source_contract") == "v1"]
        if not copied:
            return values  # Raw watchlist/source producers keep their original canonical hash contract.
        projected = _native_news_catalog_values(db, copied, postgres=postgres, authority=authority)
        by_key = {(str(value["owner"]), str(value["key"])): value for value in projected}
        return [by_key.get((str(value["owner"]), str(value["key"])), value)
                if read or (value.get("collection_scope") == "local_projection_sync"
                            and value.get("document", {}).get("input_source_contract") == "v1") else value for value in values]
    if read:
        if collection == "news_ai":
            projected = _native_news_catalog_values(db, values, postgres=postgres, authority=authority, article_copy=False)
            return [{key: item for key, item in value.items() if key != "native_source_cut"} for value in projected]
        return values
    if collection == "news_ai":
        values = _select_current_news_ai_results(db, values, postgres=postgres)
    return _protect_reported_news_ai(db, collection, values, postgres=postgres, authority=authority)


def _insert_strategy_stage_revision(
    cursor: Any, document: dict[str, Any], now_provider: Callable[[], datetime], placeholder: str,
) -> bool:
    """Compare the predecessor and append under the caller's native writer lock."""
    value = stage_revision_from_dict(document)
    collection = "execution_strategy_stage_revisions"
    cursor.execute(
        "SELECT document_json FROM central_documents "
        f"WHERE collection={placeholder} AND owner={placeholder} AND document_key={placeholder}",
        (collection, value.strategy_ref, value.revision_id),
    )
    existing = cursor.fetchone()
    if existing is not None:
        if json_mapping(existing[0]) != document:
            raise ValueError(f"immutable document conflict: {collection}/{value.strategy_ref}/{value.revision_id}")
        return False
    cursor.execute(
        "SELECT document_key,document_json FROM central_documents "
        f"WHERE collection={placeholder} AND owner={placeholder} "
        "ORDER BY updated_at DESC,owner,document_key LIMIT 10000", (collection, value.strategy_ref),
    )
    rows = cursor.fetchall()
    revisions = [stage_revision_from_dict(json_mapping(raw)) for _, raw in rows]
    latest = max(revisions, key=strategy_stage_revision_sort_key, default=None)
    current = latest.stage if latest is not None else StrategyLifecycleStage.DRAFT
    if current is not value.previous_stage:
        raise ValueError(f"strategy stage changed: expected {value.previous_stage.value}, found {current.value}")
    row = document_value_rows(collection, [{"owner": value.strategy_ref, "key": value.revision_id,
                                           "document": document}], now_provider().timestamp())[0]
    if json_mapping(row[-1]) != document:
        raise RuntimeError(f"failed to persist immutable document: {collection}/{value.strategy_ref}/{value.revision_id}")
    cursor.execute(
        "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
        f"VALUES({','.join([placeholder] * 5)}) "
        "ON CONFLICT(collection,owner,document_key) DO NOTHING"
        + (" RETURNING document_json" if placeholder == "%s" else ""), row,
    )
    inserted = cursor.fetchone() is not None if placeholder == "%s" else cursor.rowcount == 1
    if not inserted:
        cursor.execute(
            "SELECT document_json FROM central_documents "
            f"WHERE collection={placeholder} AND owner={placeholder} AND document_key={placeholder}",
            (collection, value.strategy_ref, value.revision_id),
        )
        existing = cursor.fetchone()
        if existing is None or json_mapping(existing[0]) != document:
            raise ValueError(f"immutable document conflict: {collection}/{value.strategy_ref}/{value.revision_id}")
    return inserted


def _reported_news_ai_document(collection: str, document: dict[str, Any]) -> dict[str, Any]:
    """Generic copies cannot claim the reserved native acceptance origin.

    Keep reported IDs/timestamps verbatim; this is not a new AI acceptance.
    """
    if collection not in {"news_ai", "news_ai_shared"}:
        return document
    reference = decode_news_ai_provenance(document.get("provenance_json"))
    if reference.get("origin") != "reported_copy":
        reference["reported_origin"] = reference.get("origin", "UNKNOWN")
    reference.setdefault("reported_status", reference["status"])
    reference["origin"] = "reported_copy"
    if any(reference.get(name) for name in ("analysis_revision_id", "article_revision_id", "body_revision_id",
                                          "unassigned_analysis_revision_ids")):
        reference["status"] = "REPORTED"
    return {**document, "provenance_json": json.dumps(reference, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}


def _validate_journal_generic_write(collection: str) -> None:
    if collection == "execution_mock_plan_positions":
        raise ValueError("use the native execution claim or event command for plan positions")
    if collection in {"preserved_rank_targets", "data_transition_receipts"}:
        raise ValueError("use the native preserved rank command for offline target and receipt documents")
    if collection == JOURNAL_ANALYSIS_REVISION_COLLECTION:
        raise ValueError("use accept_journal_analysis_revisions for immutable journal analysis")
    if collection == REPORTED_RESEARCH_RESULT_COLLECTION:
        raise ValueError("use accept_research_result for immutable reported research results")
    if collection == OFFLINE_ORDER_EVIDENCE_COLLECTION:
        raise ValueError("use accept_selected_order_evidence for immutable offline order evidence")


def _accept_research_result(cursor, owner: str, document: dict[str, Any], received_at: datetime,
                            placeholder: str) -> dict[str, Any]:
    value = ReportedResearchResult.from_document(document)
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("authenticated research result owner is required")
    accepted_at = as_kst(received_at)
    stored_document = {
        "receipt": {"status": "stored", "owner": owner, "result_id": value.result_id,
                    "kind": REPORTED_RESEARCH_RESULT_KIND, "content_hash": value.content_hash,
                    "accepted_at": accepted_at.isoformat(), "storage_only": True,
                    "scientific_verified": False, "order_authorized": False},
        "result": value.document,
    }
    row = document_value_rows(REPORTED_RESEARCH_RESULT_COLLECTION,
        [{"owner": owner, "key": value.result_id, "document": stored_document}], accepted_at.timestamp())[0]
    cursor.execute(
        "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
        f"VALUES({','.join([placeholder] * 5)}) ON CONFLICT(collection,owner,document_key) DO NOTHING", row,
    )
    cursor.execute(
        "SELECT document_json FROM central_documents "
        f"WHERE collection={placeholder} AND owner={placeholder} AND document_key={placeholder}", row[:3],
    )
    stored = cursor.fetchone()
    if stored is None:
        raise RuntimeError("failed to read committed research result")
    accepted = json_mapping(stored[0])
    if ReportedResearchResult.from_document(accepted["result"]).content_hash != value.content_hash:
        raise ResearchResultConflict("immutable reported research result conflict")
    return accepted


def _accept_journal_analysis_revisions(
    cursor, values: list[dict[str, Any]], received_at: float, placeholder: str,
) -> dict[str, Any]:
    """Accept one wire batch under the backend-owned native transaction."""
    prepared = []
    for index, value in enumerate(values):
        document = value["document"]
        content = journal_analysis_revision_content(document)
        if str(value["key"]) != str(document["revision_id"]) or str(value["owner"]) != str(document["origin_account_ref"]):
            raise ValueError("journal analysis revision owner/key does not match its original identity")
        prepared.append((index, value, content))
    stored_values: list[dict[str, Any] | None] = [None] * len(values)
    inserted = 0
    # All concurrent batches take identity locks in the same order. Duplicate
    # keys retain request order and are compared inside this same transaction.
    for index, value, content in sorted(prepared, key=lambda item: (str(item[1]["owner"]), str(item[1]["key"]))):
        row = document_value_rows(JOURNAL_ANALYSIS_REVISION_COLLECTION, [value], received_at)[0]
        cursor.execute(
            "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
            f"VALUES({','.join([placeholder] * 5)}) "
            "ON CONFLICT(collection,owner,document_key) DO NOTHING", row,
        )
        inserted += int(cursor.rowcount == 1)
        cursor.execute(
            "SELECT owner,document_key,updated_at,document_json FROM central_documents "
            f"WHERE collection={placeholder} AND owner={placeholder} AND document_key={placeholder}", row[:3],
        )
        stored = cursor.fetchone()
        if stored is None:
            raise RuntimeError("failed to read accepted immutable journal analysis revision")
        envelope = document_result_rows((stored,))[0]
        if journal_analysis_revision_content(envelope["document"]) != content:
            raise JournalAnalysisRevisionConflict(
                f"immutable journal analysis revision conflict: {value['owner']}/{value['key']}",
            )
        stored_values[index] = envelope
    return {"saved": len(values), "inserted": inserted, "replayed": len(values) - inserted, "documents": stored_values}


def _single_document_key_query(collection, owner, key, placeholder):
    return (
        "SELECT owner,document_key,updated_at,document_json "
        "FROM central_documents WHERE collection=" + placeholder + " AND owner=" + placeholder
        + " AND document_key=" + placeholder,
        (collection, owner, key),
    )


def _scoped_document_row(cursor, collection, owner, key, placeholder):
    cursor.execute(*_single_document_key_query(collection, owner, key, placeholder))
    rows = cursor.fetchall()  # Exact PK: consume zero or one row, ending this statement.
    return rows[0] if rows else None


def _accept_selected_order_evidence(cursor, p, packet, *, expected_sha256, account_ref, run_id, now_provider):
    """Preserve immutable offline data under either existing native transaction."""
    encoded, digest = encode_selected_order_evidence(packet)
    packet = json.loads(encoded)
    if digest != expected_sha256:
        raise ValueError('selected-order evidence digest changed')
    if (packet['account_ref'], packet['run_id']) != (account_ref, run_id):
        raise ValueError('selected-order evidence crossed the expected account and run')
    operation_id = packet['operation_id']
    row = _scoped_document_row(cursor, OFFLINE_ORDER_EVIDENCE_COLLECTION, OFFLINE_ORDER_EVIDENCE_OWNER, operation_id, p)
    if row is not None:
        previous = document_result_rows((row,))[0]['document']
        receipt = previous['receipt']
        if previous['packet'] != packet or receipt['sha256'] != digest:
            raise ValueError('immutable selected-order evidence conflict')
        return {'receipt': receipt, 'replayed': True}
    cursor.execute('SELECT 1 FROM central_execution_intents UNION ALL SELECT 1 FROM central_execution_events '
        'UNION ALL SELECT 1 FROM central_execution_runtime_leases UNION ALL SELECT 1 FROM central_documents '
        "WHERE collection IN ('execution_mock_automation_control','execution_mock_plan_positions') LIMIT 1")
    if cursor.fetchone() is not None:
        raise ValueError('selected-order evidence requires a target with empty execution state')
    accepted_at = as_kst(now_provider())
    receipt = dict(operation_id=operation_id, sha256=digest, accepted_at=accepted_at.isoformat(),
        status=RECOVERY_REQUIRED, order_authority=False)
    row = document_value_rows(OFFLINE_ORDER_EVIDENCE_COLLECTION, [dict(owner=OFFLINE_ORDER_EVIDENCE_OWNER,
        key=operation_id, document=dict(receipt=receipt, packet=packet))], accepted_at.timestamp())[0]
    cursor.execute('INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) '
        f'VALUES({",".join([p] * 5)})', row)
    return {'receipt': receipt, 'replayed': False}


def _selected_order_identity_context(store, placeholder, *, account_ref, credential_profile_id,
                                     binding_revision=None, source_packet=None):
    """Read native identity facts; aliases and requester identity flags are absent."""
    def original_bytes(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    with store.mock_automation_read_scope() as reader:
        reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY' if placeholder == '%s' else 'BEGIN')
        binding = store.load_latest_account_binding(credential_profile_id, 'kiwoom', 'mock', _reader=reader)
        if (binding is None or binding['account_ref'] != account_ref
                or binding_revision is not None and binding['binding_revision'] != binding_revision):
            raise ValueError('selected recovery requires the current native verified binding')
        reader.execute('SELECT identity_fingerprint FROM central_account_registry '
            f"WHERE account_ref={placeholder} AND broker='kiwoom' AND environment='mock' AND status='active'", (account_ref,))
        row = reader.fetchone()
        if row is None:
            raise ValueError('selected recovery native account identity is missing')
        if source_packet is not None:
            from kiwoom_monitor.domain.selected_order_evidence import PLAN_MANAGEMENT_VERSION, selected_plan_orders
            plan_ledger = source_packet.get('version') == PLAN_MANAGEMENT_VERSION
            control = store.load_mock_automation_control(account_ref, _reader=reader)
            if (control is None or (control['desired_state'], control['execution_run_id'], control['changed_at'])
                    != ('STOPPED', source_packet['run_id'], source_packet['stopped_at'])):
                raise ValueError('source identity proof requires its exact stopped native control')
            reader.execute(f'SELECT 1 FROM central_execution_runtime_leases WHERE owner_key={placeholder}', (f'mock:{account_ref}',))
            if reader.fetchone() is not None:
                raise ValueError('source identity proof requires the drained native owner')
            native = store.load_execution_intent(source_packet['order']['intent_id'], _reader=reader)
            if native is None or any(native.get(key) != value for key, value in source_packet['order'].items()):
                raise ValueError('source identity proof packet changed native order facts')
            if native.get('mock_account_member_spec_id') != source_packet.get('member_spec_id'):
                raise ValueError('source identity proof cannot discard native member attribution')
            if source_packet['source_spec_id'] != (native.get('mock_account_member_spec_id') or control['active_spec_id']):
                raise ValueError('source identity proof packet changed native spec attribution')
            position = store.load_document('execution_mock_plan_positions', account_ref,
                source_packet['order']['plan']['plan_id'], _reader=reader)
            if position is None or any(position['document'].get(key) != value for key, value in source_packet['plan_position'].items()):
                raise ValueError('source identity proof packet changed native plan facts')
            plan_id = "document_json->'plan'->>'plan_id'" if placeholder == '%s' else "json_extract(document_json,'$.plan.plan_id')"
            if plan_ledger:
                reader.execute('SELECT document_json FROM central_execution_intents '
                    f'WHERE environment=\'mock\' AND account_ref={placeholder} AND {plan_id}={placeholder}',
                    (account_ref, native['plan']['plan_id']))
                originals = [item[0] if isinstance(item[0], dict) else json.loads(item[0]) for item in reader.fetchall()]
                wanted = selected_plan_orders(source_packet)
                if original_bytes({item['intent_id']: item for item in originals}) != original_bytes({item['intent_id']: item for item in wanted}):
                    raise ValueError('source management closure does not cover the exact complete plan ledger')
                joined_plan_id = "i.document_json->'plan'->>'plan_id'" if placeholder == '%s' else "json_extract(i.document_json,'$.plan.plan_id')"
                reader.execute('SELECT e.accepted_sequence,e.document_json FROM central_execution_events e '
                    f'JOIN central_execution_intents i ON i.intent_id=e.intent_id WHERE i.account_ref={placeholder} '
                    f'AND i.environment=\'mock\' AND {joined_plan_id}={placeholder} ORDER BY e.accepted_sequence',
                    (account_ref, native['plan']['plan_id']))
            else:
                reader.execute('SELECT e.accepted_sequence,e.document_json FROM central_execution_events e '
                    f'WHERE e.intent_id={placeholder} ORDER BY e.accepted_sequence', (native['intent_id'],))
            events = [(int(item[0]), item[1] if isinstance(item[1], dict) else json.loads(item[1])) for item in reader.fetchall()]
            if len(events) != len(source_packet['events']) or any(
                    sequence != event['accepted_sequence']
                    or (original_bytes(raw) != original_bytes({key: value for key, value in event.items() if key != 'accepted_sequence'}) if plan_ledger
                        else any(raw.get(key) != value for key, value in event.items() if key != 'accepted_sequence'))
                    for (sequence, raw), event in zip(events, source_packet['events'], strict=True)):
                raise ValueError('source identity proof packet changed native event facts')
            reader.execute('SELECT COALESCE(MAX(e.accepted_sequence),0) FROM central_execution_events e '
                f'JOIN central_execution_intents i ON i.intent_id=e.intent_id WHERE i.environment=\'mock\' AND i.account_ref={placeholder}', (account_ref,))
            if int(reader.fetchone()[0]) != source_packet['source_cursor']:
                raise ValueError('source identity proof packet changed native source cut')
            if source_packet.get('member_spec_id') is not None:
                admission = store.load_document('execution_mock_automation_admission_by_spec', account_ref,
                    source_packet['member_spec_id'], _reader=reader)
                if (native.get('mock_account_member_spec_id') != source_packet['member_spec_id'] or admission is None
                        or admission['document']['admission_id'] != source_packet['member_admission_id']
                        or admission['document']['account_selection_id'] != source_packet['account_selection_id']
                        or control['active_spec_id'] != source_packet['anchor_spec_id']):
                    raise ValueError('source identity proof packet changed native member attribution')
            if 'management_closure' in source_packet:
                from kiwoom_monitor.application.selected_order_recovery import (
                    validate_management_closure, management_checkpoint_matches, management_checkpoint_references, management_plan_references,
                )
                validate_management_closure(source_packet, allow_missing_checkpoint_dependencies=True)
                closure = source_packet['management_closure']
                plan_id = "document_json->'plan'->>'plan_id'" if placeholder == '%s' else "json_extract(document_json,'$.plan.plan_id')"
                if not plan_ledger:
                    reader.execute('SELECT 1 FROM central_execution_intents '
                        f'WHERE environment=\'mock\' AND account_ref={placeholder} AND intent_id<>{placeholder} AND {plan_id}={placeholder} LIMIT 1',
                        (account_ref, native['intent_id'], native['plan']['plan_id']))
                    if reader.fetchone() is not None:
                        raise ValueError('source management closure does not cover the complete plan ledger')
                if ((original_bytes(closure['source_plan_position']) != original_bytes(position['document']) if plan_ledger
                        else closure['source_plan_position'] != position['document'])
                        or closure['origin_approval_id'] != native['mock_plan_approval']):
                    raise ValueError('source management closure changed the native position or approval reference')
                checkpoint_key = 'member:' + source_packet['member_spec_id'] if 'member_spec_id' in source_packet else 'current'
                checkpoint_collections = {'execution_mock_automation_runner_current', 'execution_mock_automation_clock_current'}
                evidence_collections = {'execution_mock_automation_risk_snapshots', 'execution_mock_automation_stop_revisions',
                    'execution_close_exit_evidence', 'execution_panic_exit_evidence'}
                checkpoint_values, expected = {}, set()
                for collection in sorted(checkpoint_collections):
                    original = store.load_document(collection, account_ref, checkpoint_key, _reader=reader)
                    if original is not None and management_checkpoint_matches(source_packet, original['document']):
                        identity = collection, account_ref, checkpoint_key
                        checkpoint_values[identity] = original['document']
                        expected.add(identity)
                inspected = set()
                while True:
                    values = {(item['collection'], item['owner'], item['key']): item['document']
                        for item in closure['documents']}
                    references = (management_checkpoint_references(source_packet, checkpoint_values)
                        | management_plan_references(source_packet, values)) - inspected
                    if not references: break
                    for collection, owner, key in sorted(references):
                        identity = collection, owner, key
                        inspected.add(identity)
                        original = store.load_document(collection, owner, key, _reader=reader)
                        if original is not None:
                            expected.add(identity)
                            checkpoint_values[identity] = original['document']
                provided = {(item['collection'], item['owner'], item['key']) for item in closure['documents']
                    if item['collection'] in checkpoint_collections | evidence_collections
                    or (item['collection'], item['owner'], item['key']) in management_plan_references(source_packet, values)}
                if provided != expected:
                    raise ValueError('source management closure omitted or invented native checkpoint evidence')
                for item in closure['documents']:
                    original = store.load_document(item['collection'], item['owner'], item['key'], _reader=reader)
                    if original is None or (original_bytes(original['document']) != original_bytes(item['document'])
                            if plan_ledger else original['document'] != item['document']):
                        raise ValueError('source management closure changed native policy facts')
                if closure['shadow_candidate'] is not None:
                    reader.execute(f'SELECT document_json,expires_at FROM central_shadow_candidate_events WHERE event_id={placeholder}',
                        (closure['shadow_candidate']['event_id'],))
                    shadow = reader.fetchone()
                    original = ({**(shadow[0] if isinstance(shadow[0], dict) else json.loads(shadow[0])),
                        'expires_at': shadow[1].isoformat() if isinstance(shadow[1], datetime) else shadow[1]} if shadow else None)
                    if (original_bytes(original) != original_bytes(closure['shadow_candidate']) if plan_ledger
                            else original != closure['shadow_candidate']):
                        raise ValueError('source management closure changed the native Shadow candidate')
        return {'binding': binding, 'identity_fingerprint': str(row[0])}


class SQLiteDocumentStoreMixin:
    def load_selected_order_management_policy(self, context, *, _reader=None):
        from .selected_order_bootstrap import load_selected_order_management_policy
        if _reader is not None:
            return load_selected_order_management_policy(_reader, '?', context)
        with self.mock_automation_read_scope() as reader:
            return load_selected_order_management_policy(reader, '?', context)

    def prepare_selected_order_management(self, operation_id, **kwargs):
        from .selected_order_bootstrap import prepare_selected_order_management
        with self._lock, self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            result = prepare_selected_order_management(connection.cursor(), '?', self, operation_id, **kwargs)
        return result

    def load_selected_management_fences(self, management, *, _reader=None):
        from .selected_order_bootstrap import load_selected_management_fences
        if _reader is not None:
            return load_selected_management_fences(_reader, '?', self, management)
        with self.mock_automation_read_scope() as reader:
            return load_selected_management_fences(reader, '?', self, management)

    def accept_selected_order_management(self, context, **kwargs):
        from .selected_order_bootstrap import accept_selected_order_management
        with self._lock, self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            result = accept_selected_order_management(connection.cursor(), '?', self, context, **kwargs)
        return result

    def load_selected_order_identity_context(self, **kwargs):
        return _selected_order_identity_context(self, '?', **kwargs)

    def install_selected_order_management(self, packet, **kwargs):
        from .selected_order_bootstrap import install_selected_order_management
        with self._lock, self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            result = install_selected_order_management(connection.cursor(), '?', self, packet, **kwargs)
        return result  # Its original receipt is durable before the ACK.

    @contextmanager
    def mock_automation_read_scope(self):
        with self._connection() as connection:
            cursor = connection.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    def accept_research_result(self, owner: str, document: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            accepted = _accept_research_result(connection.cursor(), owner, document, self._now_provider(), "?")
        return accepted  # The native commit finishes before the HTTP ACK.

    def accept_selected_order_evidence(
        self, packet: dict[str, Any], *, expected_sha256: str, account_ref: str, run_id: str,
    ) -> dict[str, Any]:
        if self._memory_uri or not self._path.is_file():
            raise ValueError("initialize a durable offline SQLite target before accepting order evidence")
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = _accept_selected_order_evidence(connection.cursor(), '?', packet, expected_sha256=expected_sha256,
                account_ref=account_ref, run_id=run_id, now_provider=self._now_provider)
        return result

    def load_current_theme_snapshot(self) -> dict[str, Any]:
        with self._lock, self._connection() as connection:
            return _load_current_theme_snapshot(connection.cursor(), "?")

    def accept_journal_analysis_revisions(self, values: list[dict[str, Any]]) -> dict[str, Any]:
        if not values:
            return {"saved": 0, "inserted": 0, "replayed": 0, "documents": []}
        received_at = self._now_provider().timestamp()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = _accept_journal_analysis_revisions(connection.cursor(), values, received_at, "?")
        return receipt  # Commit/close complete before the caller can acknowledge.

    def publish_theme_snapshot(
        self, document: dict[str, Any], *, publication_id: str,
        expected_snapshot_id: str | None, effective_at: str | None = None,
        origin_device: str = "unknown", require_current_projection: bool = False,
    ) -> dict[str, Any]:
        source = _theme_publication_source(
            document, publication_id, expected_snapshot_id, effective_at, origin_device,
        )
        received_at = self._now_provider().timestamp()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            receipt = _publish_theme_snapshot(
                connection.cursor(), source, publication_id=publication_id,
                expected_snapshot_id=expected_snapshot_id, received_at=received_at,
                now_provider=self._now_provider, placeholder="?",
                require_current_projection=require_current_projection,
            )
        return receipt

    def load_accepted_theme_snapshot(
        self, *, snapshot_id: str | None = None, require_current_projection: bool = False,
    ) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            if require_current_projection:
                connection.execute("BEGIN")
            cursor = connection.cursor()
            snapshot = _load_accepted_theme_snapshot(cursor, "?", snapshot_id)
            if require_current_projection:
                _assert_current_theme_projection(cursor, "?", snapshot)
            return snapshot

    def insert_strategy_stage_revision(self, document: dict[str, Any]) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = _insert_strategy_stage_revision(connection.cursor(), document, self._now_provider, "?")
        return inserted

    def insert_immutable_document(
        self, collection: str, owner: str, key: str, document: dict[str, Any],
    ) -> bool:
        _validate_journal_generic_write(collection)
        original_document = document
        document = _reported_news_ai_document(collection, document)
        with self._lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT document_json FROM central_documents "
                "WHERE collection=? AND owner=? AND document_key=?", (collection, owner, key),
            ).fetchone()
            inserted = False
            if existing is None:
                row = document_value_rows(collection, [{"owner": owner, "key": key, "document": document}],
                                          self._now_provider().timestamp())[0]
                if json_mapping(row[-1]) != document:
                    raise RuntimeError(f"failed to persist immutable document: {collection}/{owner}/{key}")
                cursor = connection.execute(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO NOTHING", row,
                )
                inserted = cursor.rowcount == 1
                if not inserted:
                    # Another writer may have committed after the initial lookup.
                    existing = connection.execute(
                        "SELECT document_json FROM central_documents "
                        "WHERE collection=? AND owner=? AND document_key=?", (collection, owner, key),
                    ).fetchone()
            if not inserted:
                if existing is None:
                    raise RuntimeError(f"failed to persist immutable document: {collection}/{owner}/{key}")
                stored = json_mapping(existing[0])
                if stored != original_document:
                    if (collection not in {"news_ai", "news_ai_shared"}
                            or (stored != document and _reported_news_ai_document(collection, stored) != document)):
                        raise ValueError(f"immutable document conflict: {collection}/{owner}/{key}")
        return inserted  # The owning connection context has committed successfully.

    def publish_mock_automation_current_risk(
        self, account_ref: str, document: dict[str, Any], *, expected: dict[str, Any] | None,
    ) -> bool:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = _scoped_document_row(
                connection.cursor(), _CURRENT_RISK_COLLECTION, account_ref, account_ref, "?",
            )
            current = json_mapping(existing[3]) if existing is not None else None
            if current != expected:
                return False
            row = document_value_rows(_CURRENT_RISK_COLLECTION, [{
                "owner": account_ref, "key": account_ref, "document": document,
            }], self._now_provider().timestamp())[0]
            connection.execute(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json", row,
            )
        return True

    def upsert_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        _validate_journal_generic_write(collection)
        original_values = values if collection in {"news_ai", "news_ai_shared"} else None
        if original_values is not None:
            values = [{**value, "document": _reported_news_ai_document(collection, value["document"])}
                      for value in values]
        if not values:
            return
        now = self._now_provider().timestamp()
        with self._lock, self._connection() as connection:
            if collection in {"news_article", "news_ai", "news_ai_shared"}:
                connection.execute("BEGIN IMMEDIATE")
                values = _news_catalog_values(self, connection, collection, values, postgres=False)
                if not values:
                    return
            rows = document_value_rows(collection, values, now)
            original_match = ""
            if original_values is not None:
                by_key = {(str(value["owner"]), str(value["key"])): value for value in original_values}
                original_rows = document_value_rows(collection, [by_key[(str(value["owner"]), str(value["key"]))] for value in values], now)
                rows = [(*row, original[-1]) for row, original in zip(rows, original_rows, strict=True)]
                original_match = " AND central_documents.document_json<>?"
            connection.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(?,?,?,?,?) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=excluded.updated_at,document_json=excluded.document_json "
                "WHERE central_documents.document_json<>excluded.document_json" + original_match,
                rows,
            )
            if collection == "theme_metadata":
                _append_sqlite_theme_snapshot(connection, values, received_at=now, now_provider=self._now_provider)
            elif collection == "news_article":
                _append_sqlite_news_articles(connection, values, received_at=now, now_provider=self._now_provider)
        if collection == "news_article":
            _notify_news_job_wakeup(self)

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        """컬렉션 전체를 한 트랜잭션에서 현재 스냅샷으로 교체한다."""
        _validate_journal_generic_write(collection)
        if collection in {"news_ai", "news_ai_shared"}:
            values = [{**value, "document": _reported_news_ai_document(collection, value["document"])}
                      for value in values]
        now = self._now_provider().timestamp()
        rows = document_value_rows(collection, values, now)
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM central_documents WHERE collection=?", (collection,))
            if rows:
                connection.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(?,?,?,?,?)", rows,
                )
            if collection == "theme_metadata":
                _append_sqlite_theme_snapshot(connection, values, received_at=now, now_provider=self._now_provider)

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        sql, parameters = document_select_query(
            collection, owner, limit, offset, updated_after, placeholder="?",
        )
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
            values = (_news_catalog_values(self, connection, collection, document_result_rows(rows), postgres=False, read=True)
                      if not owner else document_result_rows(rows))
        return values

    def load_document(
        self, collection: str, owner: str, key: str, *, _reader: Any = None,
    ) -> dict[str, Any] | None:
        if _reader is not None:
            with self._lock:
                row = _scoped_document_row(_reader, collection, owner, key, '?')
            return document_result_rows((row,))[0] if row is not None else None
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT owner,document_key,updated_at,document_json "
                "FROM central_documents WHERE collection=? AND owner=? AND document_key=?",
                (collection, owner, key),
            ).fetchone()
        return document_result_rows((row,))[0] if row is not None else None

    def load_theme_snapshots(
        self, *, available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
            "origin_device,revision_of,document_json FROM central_theme_snapshots"
        )
        parameters: list[object] = []
        if available_at is not None:
            sql += " WHERE available_at<=?"
            parameters.append(float(available_at))
        sql += " ORDER BY accepted_sequence DESC LIMIT ?"
        parameters.append(bounded_limit(limit, 1000))
        with self._lock, self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return _theme_snapshot_result_rows(rows)


class PostgresDocumentStoreMixin:
    def load_selected_order_management_policy(self, context, *, _reader=None):
        from .selected_order_bootstrap import load_selected_order_management_policy
        if _reader is not None:
            return load_selected_order_management_policy(_reader, '%s', context)
        with self.mock_automation_read_scope() as reader:
            return load_selected_order_management_policy(reader, '%s', context)

    def prepare_selected_order_management(self, operation_id, **kwargs):
        from .selected_order_bootstrap import prepare_selected_order_management
        from .postgres_access import DBWriterContext, owned_connection_scope
        writer = DBWriterContext(writer_family='execution.runtime_lease', writer_kind='selected_management_prepare',
            operation='prepare_selected_order_management', rows_attempted=3)
        with owned_connection_scope(self._connect, writer) as connection, connection.cursor() as cursor:
            result = prepare_selected_order_management(cursor, '%s', self, operation_id, **kwargs)
        return result

    def load_selected_management_fences(self, management, *, _reader=None):
        from .selected_order_bootstrap import load_selected_management_fences
        if _reader is not None:
            return load_selected_management_fences(_reader, '%s', self, management)
        with self.mock_automation_read_scope() as reader:
            return load_selected_management_fences(reader, '%s', self, management)

    def accept_selected_order_management(self, context, **kwargs):
        from .selected_order_bootstrap import accept_selected_order_management
        from .postgres_access import DBWriterContext, owned_connection_scope
        writer = DBWriterContext(writer_family='execution.runtime_lease', writer_kind='selected_management_accept',
            operation='accept_selected_order_management', rows_attempted=3)
        with owned_connection_scope(self._connect, writer) as connection, connection.cursor() as cursor:
            result = accept_selected_order_management(cursor, '%s', self, context, **kwargs)
        return result

    def load_selected_order_identity_context(self, **kwargs):
        return _selected_order_identity_context(self, '%s', **kwargs)

    def install_selected_order_management(self, packet, **kwargs):
        from .selected_order_bootstrap import install_selected_order_management
        from .postgres_access import DBWriterContext, owned_connection_scope
        writer = DBWriterContext(writer_family='document.immutable',
            writer_kind='document:execution_selected_order_management_installations',
            operation='install_selected_order_management', rows_attempted=len(packet['events']) + 5)
        with owned_connection_scope(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('selected-order-offline',))
            result = install_selected_order_management(cursor, '%s', self, packet, **kwargs)
        return result

    def accept_selected_order_evidence(self, packet, *, expected_sha256, account_ref, run_id):
        from .postgres_access import DBWriterContext, owned_connection_scope
        writer = DBWriterContext(writer_family='document.immutable',
            writer_kind='document:offline_selected_order_evidence', operation='accept_selected_order_evidence', rows_attempted=1)
        with owned_connection_scope(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            cursor.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('selected-order-offline',))
            result = _accept_selected_order_evidence(cursor, '%s', packet, expected_sha256=expected_sha256,
                account_ref=account_ref, run_id=run_id, now_provider=self._now_provider)
        return result

    @contextmanager
    def mock_automation_read_scope(self, *, _connection: Any = None):
        from .postgres_access import DBWriterContext, observe_existing_transaction, owned_connection_scope

        reader = DBWriterContext(
            writer_family="read.mock_automation", writer_kind="evaluation_inputs",
            operation="mock_automation_read_scope", access_mode="read",
        )
        transaction = (owned_connection_scope(self._connect, reader) if _connection is None
                       else observe_existing_transaction(_connection, reader, require_idle=True))
        with transaction as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY")
            yield cursor

    @contextmanager
    def _mock_automation_recovery_scope(self):
        """Reuse the backend for the lineage check and independent recovery transactions."""
        from .postgres_access import DBWriterContext, owned_postgres_session

        context = DBWriterContext(
            writer_family="infrastructure.connection", writer_kind="mock_automation_recovery",
            operation="mock_automation_recovery_scope", access_mode="connection",
        )
        with owned_postgres_session(self._connect, context) as connection:
            yield (
                partial(self.mock_automation_read_scope, _connection=connection),
                partial(self.insert_immutable_document, _connection=connection),
                partial(self.load_document, _connection=connection),
                partial(self.upsert_documents, _connection=connection),
            )

    @contextmanager
    def _mock_automation_risk_write_scope(self):
        """Own one backend for two separately committed Risk writes."""
        from .postgres_access import DBWriterContext, owned_postgres_session

        context = DBWriterContext(
            writer_family="infrastructure.connection", writer_kind="mock_automation_risk",
            operation="mock_automation_risk_write_scope", access_mode="connection",
        )
        with owned_postgres_session(self._connect, context) as connection:
            yield (
                partial(self.insert_immutable_document, _connection=connection),
                partial(self.publish_mock_automation_current_risk, _connection=connection),
            )

    def accept_research_result(self, owner: str, document: dict[str, Any]) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, owned_connection_scope

        writer = DBWriterContext(writer_family="document.immutable",
            writer_kind=f"document:{REPORTED_RESEARCH_RESULT_COLLECTION}",
            operation="accept_research_result", rows_attempted=1)
        with owned_connection_scope(self._connect, writer) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            accepted = _accept_research_result(cursor, owner, document, self._now_provider(), "%s")
        return accepted

    def load_current_theme_snapshot(self) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.document_collection", writer_kind="theme_snapshot:current",
            operation="load_current_theme_snapshot", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            return _load_current_theme_snapshot(cursor, "%s")

    def accept_journal_analysis_revisions(self, values: list[dict[str, Any]]) -> dict[str, Any]:
        if not values:
            return {"saved": 0, "inserted": 0, "replayed": 0, "documents": []}
        from .postgres_access import DBWriterContext, owned_connection_scope

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="document.immutable", writer_kind=f"document:{JOURNAL_ANALYSIS_REVISION_COLLECTION}",
            operation="accept_journal_analysis_revisions", rows_attempted=len(values),
        )
        received_at = self._now_provider().timestamp()
        with owned_connection_scope(self._connect, writer) as connection, connection.cursor() as cursor:
            # INSERT may wait for a competing writer. Its following readback
            # must observe that committed winner, including a lost-ACK replay.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            receipt = _accept_journal_analysis_revisions(cursor, values, received_at, "%s")
        # Observation failure cannot undo or hide the accepted native receipt.
        try:
            from .postgres_access import database_observer
            observer = database_observer()
            if observer is not None:
                observer.record_writer_transaction(
                    f"document:{JOURNAL_ANALYSIS_REVISION_COLLECTION}", len(values),
                    round((monotonic() - started_at) * 1000), db_call_id=writer.call_id,
                )
        except Exception as error:
            logging.getLogger(__name__).warning(
                "journal analysis transaction metric failed: %s", type(error).__name__,
            )
        return receipt

    def publish_theme_snapshot(
        self, document: dict[str, Any], *, publication_id: str,
        expected_snapshot_id: str | None, effective_at: str | None = None,
        origin_device: str = "unknown", require_current_projection: bool = False,
    ) -> dict[str, Any]:
        from .postgres_access import DBWriterContext, open_observed_connection

        source = _theme_publication_source(
            document, publication_id, expected_snapshot_id, effective_at, origin_device,
        )
        received_at = self._now_provider().timestamp()
        writer = DBWriterContext(
            writer_family="document.theme_snapshot", writer_kind="theme_snapshot",
            operation="publish_theme_snapshot", rows_attempted=1,
        )
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            # The predecessor read must start after a competing publication settles.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cursor.execute("LOCK TABLE central_theme_snapshots IN SHARE ROW EXCLUSIVE MODE")
            receipt = _publish_theme_snapshot(
                cursor, source, publication_id=publication_id,
                expected_snapshot_id=expected_snapshot_id, received_at=received_at,
                now_provider=self._now_provider, placeholder="%s",
                require_current_projection=require_current_projection,
            )
        return receipt

    def load_accepted_theme_snapshot(
        self, *, snapshot_id: str | None = None, require_current_projection: bool = False,
    ) -> dict[str, Any] | None:
        from .postgres_access import DBWriterContext, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.theme_snapshots", writer_kind="theme_snapshot",
            operation="load_accepted_theme_snapshot", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            if require_current_projection:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            snapshot = _load_accepted_theme_snapshot(cursor, "%s", snapshot_id)
            if require_current_projection:
                _assert_current_theme_projection(cursor, "%s", snapshot)
            return snapshot

    def insert_strategy_stage_revision(self, document: dict[str, Any]) -> bool:
        from .postgres_access import DBWriterContext, open_observed_connection

        writer = DBWriterContext(writer_family="document.stage", writer_kind="document:execution_strategy_stage_revisions",
                                 operation="insert_strategy_stage_revision", rows_attempted=1)
        with open_observed_connection(self._connect, writer) as connection, connection.cursor() as cursor:
            # A waiting advisory-lock SELECT must not pin an older repeatable-read snapshot.
            cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                           (f"strategy-stage:{document['strategy_ref']}",))
            inserted = _insert_strategy_stage_revision(cursor, document, self._now_provider, "%s")
        return inserted

    def insert_immutable_document(
        self, collection: str, owner: str, key: str, document: dict[str, Any], *, _connection: Any = None,
    ) -> bool:
        _validate_journal_generic_write(collection)
        original_document = document
        document = _reported_news_ai_document(collection, document)
        from .postgres_access import DBWriterContext, observe_existing_transaction, open_observed_connection

        writer = DBWriterContext(writer_family="document.immutable", writer_kind=f"document:{collection}",
                                 operation="insert_immutable_document", rows_attempted=1)
        transaction = (open_observed_connection(self._connect, writer) if _connection is None
                       else observe_existing_transaction(_connection, writer, require_idle=True))
        with transaction as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM central_documents "
                "WHERE collection=%s AND owner=%s AND document_key=%s", (collection, owner, key),
            )
            existing = cursor.fetchone()
            inserted = False
            if existing is None:
                row = document_value_rows(collection, [{"owner": owner, "key": key, "document": document}],
                                          self._now_provider().timestamp())[0]
                if json_mapping(row[-1]) != document:
                    raise RuntimeError(f"failed to persist immutable document: {collection}/{owner}/{key}")
                cursor.execute(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO NOTHING "
                    "RETURNING document_json", row,
                )
                inserted = cursor.fetchone() is not None
                if not inserted:
                    cursor.execute(
                        "SELECT document_json FROM central_documents "
                        "WHERE collection=%s AND owner=%s AND document_key=%s", (collection, owner, key),
                    )
                    existing = cursor.fetchone()
            if not inserted:
                if existing is None:
                    raise RuntimeError(f"failed to persist immutable document: {collection}/{owner}/{key}")
                stored = json_mapping(existing[0])
                if stored != original_document:
                    if (collection not in {"news_ai", "news_ai_shared"}
                            or (stored != document and _reported_news_ai_document(collection, stored) != document)):
                        raise ValueError(f"immutable document conflict: {collection}/{owner}/{key}")
        return inserted

    def publish_mock_automation_current_risk(
        self, account_ref: str, document: dict[str, Any], *, expected: dict[str, Any] | None,
        _connection: Any = None,
    ) -> bool:
        from .postgres_access import (
            DBWriterContext, database_observer, observe_existing_transaction, open_observed_connection,
        )

        started_at = monotonic()
        writer = DBWriterContext(
            writer_family="document.collection", writer_kind=f"document:{_CURRENT_RISK_COLLECTION}",
            operation="publish_mock_automation_current_risk", rows_attempted=1,
        )
        row = document_value_rows(_CURRENT_RISK_COLLECTION, [{
            "owner": account_ref, "key": account_ref, "document": document,
        }], self._now_provider().timestamp())[0]
        if expected is None:
            sql = ("INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                   "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO NOTHING")
            params = row
        else:
            sql = ("UPDATE central_documents SET updated_at=%s,document_json=%s "
                   "WHERE collection=%s AND owner=%s AND document_key=%s AND document_json=%s::jsonb")
            params = (*row[3:], *row[:3], json.dumps(expected, ensure_ascii=False))
        transaction = (open_observed_connection(self._connect, writer) if _connection is None
                       else observe_existing_transaction(_connection, writer, require_idle=True))
        with transaction as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            published = cursor.rowcount == 1
        observer = database_observer()
        if observer is not None:
            observer.record_writer_transaction(writer.writer_kind, 1, round((monotonic() - started_at) * 1000),
                                               db_call_id=writer.call_id)
        return published

    def upsert_documents(
        self, collection: str, values: list[dict[str, Any]], *, _connection: Any = None,
    ) -> None:
        _validate_journal_generic_write(collection)
        original_values = values if collection in {"news_ai", "news_ai_shared"} else None
        if original_values is not None:
            values = [{**value, "document": _reported_news_ai_document(collection, value["document"])}
                      for value in values]
        if not values:
            return
        started_at = monotonic()
        now = self._now_provider().timestamp()
        writer = None
        observed_kinds = {
            "news_article": "document:news_article",
            "news_ai": "document:news_ai",
            "news_ai_shared": "document:news_ai_shared",
            "news_request_usage": "document:news_request_usage",
            "morning_market_brief_settings": "document:morning_market_brief_settings",
            "morning_market_brief_runs": "document:morning_market_brief_runs",
            "journal_news_link": "document:journal_news_link",
            "journal_v2_news_links": "document:journal_v2_news_links",
            "journal_settings": "document:journal_settings",
            "journal_fills": "document:journal_fills",
            "journal_reviews": "document:journal_reviews",
            "journal_setups": "document:journal_setups",
            "journal_cycle_overrides": "document:journal_cycle_overrides",
            "journal_group_overrides": "document:journal_group_overrides",
            "journal_entry_snapshots": "document:journal_entry_snapshots",
            "journal_costs": "document:journal_costs",
            "journal_stocks": "document:journal_stocks",
            "journal_backfill": "document:journal_backfill",
            "journal_v2_fills": "document:journal_v2_fills",
            "journal_v2_reviews": "document:journal_v2_reviews",
            "journal_v2_setups": "document:journal_v2_setups",
            "journal_v2_cycle_overrides": "document:journal_v2_cycle_overrides",
            "journal_v2_group_overrides": "document:journal_v2_group_overrides",
            "journal_v2_entry_snapshots": "document:journal_v2_entry_snapshots",
            "journal_v2_costs": "document:journal_v2_costs",
            "journal_v2_enrichment_tasks": "document:journal_v2_enrichment_tasks",
            "journal_v2_analysis_revisions": "document:journal_v2_analysis_revisions",
            "journal_v2_research_links": "document:journal_v2_research_links",
            "journal_sync_states": "document:journal_sync_states",
            "journal_v2_sync_states": "document:journal_v2_sync_states",
            "app_settings": "document:app_settings",
            "app_column_settings": "document:app_column_settings",
            "news_sync": "document:news_sync",
            "news_watchlist": "document:news_watchlist",
            "news_automation_settings": "document:news_automation_settings",
            "server_operational_settings": "document:server_operational_settings",
            "theme_profile": "document:theme_profile",
            "theme_stock": "document:theme_stock",
            "theme_metadata": "document:theme_metadata",
            "krx_trading_day_observations": "document:krx_trading_day_observations",
            "external_market_roll_state": "document:external_market_roll_state",
            "stock_catalog": "document:stock_catalog",
            "minute_trade_value_comparisons": "document:minute_trade_value_comparisons",
            "stock_nxt_eligibility": "document:stock_nxt_eligibility",
            "stock_fundamentals": "document:stock_fundamentals",
            "account_entry_symbols_daily": "document:account_entry_symbols_daily",
            "stock_price_references": "document:stock_price_references",
            "top20_daily_entrants": "document:top20_daily_entrants",
            "historical_highs": "document:historical_highs",
            "market_index_chart_coverage": "document:market_index_chart_coverage",
            "market_data_coverage_daily": "document:market_data_coverage_daily",
            "daily_bar_history_coverage": "document:daily_bar_history_coverage",
            "market_data_coverage": "document:market_data_coverage",
            "market_data_coverage_intraday": "document:market_data_coverage_intraday",
            "candidate_flow_capture": "document:candidate_flow_capture",
            "candidate_flow_finalization": "document:candidate_flow_finalization",
            "condition_search_status": "document:condition_search_status",
            "market_event_sessions": "document:market_event_sessions",
            "news_original_publication": "document:news_original_publication",
            "external_market_collection_status": "document:external_market_collection_status",
            "news_assessment": "document:news_assessment",
            "execution_forward_profiles": "document:execution_forward_profiles",
            "execution_forward_reports": "document:execution_forward_reports",
            "execution_strategy_stage_revisions": "document:execution_strategy_stage_revisions",
            "execution_feedback_evidence": "document:execution_feedback_evidence",
            "execution_feedback_reviews": "document:execution_feedback_reviews",
            "execution_feedback_improvement_proposals": (
                "document:execution_feedback_improvement_proposals"
            ),
            "execution_feedback_strategy_versions": (
                "document:execution_feedback_strategy_versions"
            ),
            "execution_feedback_revalidation_requests": (
                "document:execution_feedback_revalidation_requests"
            ),
            "execution_feedback_revalidation_receipts": (
                "document:execution_feedback_revalidation_receipts"
            ),
            "execution_mock_automation_specs": "document:execution_mock_automation_specs",
            "execution_mock_automation_admissions": (
                "document:execution_mock_automation_admissions"
            ),
            "execution_mock_automation_admission_by_spec": (
                "document:execution_mock_automation_admission_by_spec"
            ),
            "execution_mock_automation_lease_receipts": (
                "document:execution_mock_automation_lease_receipts"
            ),
            "execution_mock_automation_lease_by_admission": (
                "document:execution_mock_automation_lease_by_admission"
            ),
            "execution_mock_automation_candidate_packages": (
                "document:execution_mock_automation_candidate_packages"
            ),
            "execution_mock_automation_eligibility_policies": (
                "document:execution_mock_automation_eligibility_policies"
            ),
            "execution_mock_automation_eligibility_receipts": (
                "document:execution_mock_automation_eligibility_receipts"
            ),
            "execution_mock_automation_runner_current": (
                "document:execution_mock_automation_runner_current"
            ),
            "credential_vault_state": "document:credential_vault_state",
            "execution_mock_automation_risk_snapshots": (
                "document:execution_mock_automation_risk_snapshots"
            ),
            "execution_mock_automation_current_risk": (
                "document:execution_mock_automation_current_risk"
            ),
            "execution_mock_automation_recovery_decisions": (
                "document:execution_mock_automation_recovery_decisions"
            ),
            "execution_mock_automation_current_recovery": (
                "document:execution_mock_automation_current_recovery"
            ),
            "execution_mock_automation_decision_gates": (
                "document:execution_mock_automation_decision_gates"
            ),
            "execution_mock_automation_approved_gates": (
                "document:execution_mock_automation_approved_gates"
            ),
            "execution_mock_automation_dispatch_receipts": (
                "document:execution_mock_automation_dispatch_receipts"
            ),
            "execution_mock_automation_dispatch_by_intent": (
                "document:execution_mock_automation_dispatch_by_intent"
            ),
            "execution_mock_automation_stop_revisions": (
                "document:execution_mock_automation_stop_revisions"
            ),
            "execution_mock_automation_current_stop": (
                "document:execution_mock_automation_current_stop"
            ),
            "execution_mock_automation_clock_current": (
                "document:execution_mock_automation_clock_current"
            ),
        }
        writer_kind = observed_kinds.get(collection)
        if writer_kind is not None:
            from .postgres_access import DBWriterContext, observe_existing_transaction, open_observed_connection

            writer = DBWriterContext(
                writer_family="document.collection", writer_kind=writer_kind,
                operation="upsert_documents", rows_attempted=len(values),
            )
            db_connection = (open_observed_connection(self._connect, writer) if _connection is None
                             else observe_existing_transaction(_connection, writer, require_idle=True))
        else:
            db_connection = self._connect()
        document_affected_rows = None
        with db_connection as connection, connection.cursor() as cursor:
            if collection in {"news_article", "news_ai", "news_ai_shared"}:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                values = _news_catalog_values(self, cursor, collection, values, postgres=True)
                if not values:
                    return
            if collection in {"theme_profile", "theme_stock", "theme_metadata"}:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cursor.execute("LOCK TABLE central_theme_snapshots IN SHARE ROW EXCLUSIVE MODE")
            rows = document_value_rows(collection, values, now)
            original_match = ""
            if original_values is not None:
                by_key = {(str(value["owner"]), str(value["key"])): value for value in original_values}
                original_rows = document_value_rows(collection, [by_key[(str(value["owner"]), str(value["key"]))] for value in values], now)
                rows = [(*row, original[-1]) for row, original in zip(rows, original_rows, strict=True)]
                original_match = " AND central_documents.document_json IS DISTINCT FROM %s::jsonb"
            cursor.executemany(
                "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(collection,owner,document_key) DO UPDATE SET "
                "updated_at=EXCLUDED.updated_at,document_json=EXCLUDED.document_json "
                "WHERE central_documents.document_json IS DISTINCT FROM EXCLUDED.document_json" + original_match,
                rows,
            )
            if collection == "top20_daily_entrants":
                affected = getattr(cursor, "rowcount", -1)
                if affected is not None and affected >= 0:
                    document_affected_rows = int(affected)
            if collection == "theme_metadata":
                _append_postgres_theme_snapshot(
                    cursor, values, received_at=now, now_provider=self._now_provider,
                    writer_locked=True,
                )
            elif collection == "news_article":
                _append_postgres_news_articles(cursor, values, received_at=now, now_provider=self._now_provider)
        if collection == "news_article":
            _notify_news_job_wakeup(self)
        from .postgres_access import database_observer
        observer = database_observer()
        if observer is not None:
            observer.record_writer_transaction(f"document:{collection}", len(values),
                                      round((monotonic() - started_at) * 1000),
                                      domain_counts=({"affected_rows": document_affected_rows}
                                                     if document_affected_rows is not None else None),
                                      db_call_id=writer.call_id if writer else None)

    def replace_documents(self, collection: str, values: list[dict[str, Any]]) -> None:
        """컬렉션 전체를 한 트랜잭션에서 현재 스냅샷으로 교체한다."""
        _validate_journal_generic_write(collection)
        if collection in {"news_ai", "news_ai_shared"}:
            values = [{**value, "document": _reported_news_ai_document(collection, value["document"])}
                      for value in values]
        now = self._now_provider().timestamp()
        rows = document_value_rows(collection, values, now)
        db_connection = self._connect
        if collection in {"theme_profile", "theme_stock", "theme_metadata"}:
            from .postgres_access import DBWriterContext, open_observed_connection

            writer = DBWriterContext(
                writer_family="document.collection",
                writer_kind=f"document:{collection}",
                operation="replace_documents", rows_attempted=len(values),
            )
            connection_context = open_observed_connection(db_connection, writer)
        else:
            connection_context = db_connection()
        with connection_context as connection, connection.cursor() as cursor:
            if collection in {"theme_profile", "theme_stock", "theme_metadata"}:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                cursor.execute("LOCK TABLE central_theme_snapshots IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute("DELETE FROM central_documents WHERE collection=%s", (collection,))
            if rows:
                cursor.executemany(
                    "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
                    "VALUES(%s,%s,%s,%s,%s)", rows,
                )
            if collection == "theme_metadata":
                _append_postgres_theme_snapshot(
                    cursor, values, received_at=now, now_provider=self._now_provider,
                    writer_locked=True,
                )

    def load_documents(
        self, collection: str, owner: str = "", limit: int = 1000, offset: int = 0,
        updated_after: float = 0.0,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql, parameters = document_select_query(
            collection, owner, limit, offset, updated_after, placeholder="%s",
        )
        reader = DBWriterContext(
            writer_family="read.document_collection",
            writer_kind=f"document:{collection}",
            operation="load_documents", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
            values = (_news_catalog_values(self, cursor, collection, document_result_rows(rows), postgres=True, read=True)
                      if not owner else document_result_rows(rows))
        return values

    def load_document(
        self, collection: str, owner: str, key: str, *, _reader: Any = None, _connection: Any = None,
    ) -> dict[str, Any] | None:
        if _reader is not None:
            row = _scoped_document_row(_reader, collection, owner, key, '%s')
            return document_result_rows((row,))[0] if row is not None else None
        from .postgres_access import DBWriterContext, observe_existing_transaction, open_observed_connection

        reader = DBWriterContext(
            writer_family="read.document_collection",
            writer_kind=f"document:{collection}:single",
            operation="load_document", access_mode="read",
        )
        transaction = (open_observed_connection(self._connect, reader) if _connection is None
                       else observe_existing_transaction(_connection, reader, require_idle=True))
        with transaction as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT owner,document_key,updated_at,document_json "
                "FROM central_documents WHERE collection=%s AND owner=%s AND document_key=%s",
                (collection, owner, key),
            )
            row = cursor.fetchone()
        return document_result_rows((row,))[0] if row is not None else None

    def load_theme_snapshots(
        self, *, available_at: float | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        from .postgres_access import DBWriterContext, open_observed_connection

        sql = (
            "SELECT snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
            "origin_device,revision_of,document_json FROM central_theme_snapshots"
        )
        parameters: list[object] = []
        if available_at is not None:
            sql += " WHERE available_at<=%s"
            parameters.append(float(available_at))
        sql += " ORDER BY accepted_sequence DESC LIMIT %s"
        parameters.append(bounded_limit(limit, 1000))
        reader = DBWriterContext(
            writer_family="read.theme_snapshots", writer_kind="theme_snapshots",
            operation="load_theme_snapshots", access_mode="read",
        )
        with open_observed_connection(self._connect, reader) as connection, connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            rows = cursor.fetchall()
        return _theme_snapshot_result_rows(rows)




def _load_current_theme_snapshot(cursor: Any, placeholder: str) -> dict[str, Any]:
    """One statement reads actual legacy-compatible rows, never a historical head."""
    grouped_rows: dict[str, list[tuple]] = {
        "theme_profile": [], "theme_stock": [], "theme_metadata": [],
    }
    cursor.execute(
        "SELECT collection,owner,document_key,updated_at,document_json FROM central_documents "
        "WHERE collection IN (" + ",".join([placeholder] * 3) + ") "
        "ORDER BY updated_at DESC,owner,document_key", tuple(grouped_rows),
    )
    for row in cursor.fetchall():
        grouped_rows[str(row[0])].append(row[1:])
    collections = {name: document_result_rows(rows) for name, rows in grouped_rows.items()}
    completed_at: float | None = None
    for value in collections["theme_metadata"]:
        try:
            updated_at = float(value.get("updated_at"))
        except (TypeError, ValueError):
            continue
        completed_at = updated_at if completed_at is None else max(completed_at, updated_at)
    return {"collections": collections, "completed_at": completed_at}


_THEME_SNAPSHOT_COLUMNS = (
    "snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
    "origin_device,revision_of,document_json"
)


def _theme_publication_source(
    document: dict[str, Any], publication_id: str, expected_snapshot_id: str | None,
    effective_at: str | None, origin_device: str,
) -> ThemeSnapshotSource:
    if not isinstance(publication_id, str) or not publication_id.strip():
        raise ValueError("theme publication_id is required")
    if expected_snapshot_id is not None and (
        not isinstance(expected_snapshot_id, str) or not expected_snapshot_id.strip()
    ):
        raise ValueError("theme expected_snapshot_id must be a generation ID or None")
    # Pin nested caller data before hash, projection and immutable storage use it.
    pinned = json.loads(json.dumps(document, ensure_ascii=False))
    return ThemeSnapshotSource.from_document(
        pinned, effective_at=effective_at, origin_device=origin_device,
    )


def _load_accepted_theme_snapshot(
    cursor: Any, placeholder: str, snapshot_id: str | None = None,
) -> dict[str, Any] | None:
    sql = f"SELECT {_THEME_SNAPSHOT_COLUMNS} FROM central_theme_snapshots"
    parameters: tuple[object, ...] = ()
    if snapshot_id is None:
        sql += " ORDER BY accepted_sequence DESC LIMIT 1"
    else:
        sql += f" WHERE snapshot_id={placeholder}"
        parameters = (snapshot_id,)
    cursor.execute(sql, parameters)
    row = cursor.fetchone()
    return _theme_snapshot_result_rows([row])[0] if row is not None else None


def _theme_projection_documents(source: ThemeSnapshotSource) -> dict[str, list[dict[str, Any]]]:
    profiles: list[dict[str, Any]] = []
    stocks: list[dict[str, Any]] = []
    for profile in source.document["profiles"]:
        if not isinstance(profile, dict) or not str(profile.get("name", "")).strip():
            continue
        owner = str(profile["name"]).strip()
        for theme in profile.get("themes", []):
            if not isinstance(theme, dict) or not str(theme.get("name", "")).strip():
                continue
            name = str(theme["name"]).strip()
            profiles.append({"owner": owner, "key": name, "document": {
                "profile_name": owner, "theme_name": name,
                "default_color": str(theme.get("color") or "#DCE6F1"),
            }})
        for stock in profile.get("stock_themes", []):
            if not isinstance(stock, dict):
                continue
            code, name = str(stock.get("code", "")), str(stock.get("theme", "")).strip()
            if not code or not name:
                continue
            stocks.append({"owner": owner, "key": f"{code}|{name}", "document": {
                "profile_name": owner, "stock_code": code, "theme_name": name,
                "custom_color": stock.get("color"),
            }})
    return {"theme_profile": profiles, "theme_stock": stocks, "theme_metadata": [
        {"owner": "default", "key": "full", "document": source.document},
    ]}


def _assert_current_theme_projection(cursor: Any, placeholder: str,
                                     snapshot: dict[str, Any] | None) -> None:
    """Legacy writes cannot silently authorize a stale accepted head."""
    current = _load_current_theme_snapshot(cursor, placeholder)["collections"]
    expected = ({name: [] for name in current} if snapshot is None else
                _theme_projection_documents(ThemeSnapshotSource.from_document(snapshot["document"])))
    def documents(rows):
        return {(row["owner"], row["key"]): row["document"] for row in rows}
    if any(documents(current[name]) != documents(expected[name]) for name in current):
        raise ThemeGenerationConflict(
            "current_projection_conflict", "current theme projection requires explicit reconciliation",
        )


def _publish_theme_snapshot(
    cursor: Any, source: ThemeSnapshotSource, *, publication_id: str,
    expected_snapshot_id: str | None, received_at: float,
    now_provider: Callable[[], datetime], placeholder: str,
    require_current_projection: bool = False,
) -> dict[str, Any]:
    previous_publication = _load_accepted_theme_snapshot(cursor, placeholder, publication_id)
    if previous_publication is not None:
        if (previous_publication["content_hash"] != source.content_hash
                or previous_publication["profile_id"] != source.profile_id
                or previous_publication["effective_at"] != source.effective_at
                or previous_publication["origin_device"] != source.origin_device
                or previous_publication["revision_of"] != expected_snapshot_id):
            raise ThemeGenerationConflict("immutable_publication", "immutable theme publication conflict")
        return {"snapshot": previous_publication, "changed": False, "replayed": True}
    previous = _load_accepted_theme_snapshot(cursor, placeholder)
    previous_id = previous["snapshot_id"] if previous is not None else None
    if require_current_projection:
        _assert_current_theme_projection(cursor, placeholder, previous)
    if previous_id != expected_snapshot_id:
        raise ThemeGenerationConflict("revision_conflict", "theme snapshot revision conflict")
    if previous is not None and previous["content_hash"] == source.content_hash:
        return {"snapshot": previous, "changed": False, "replayed": False}
    collections = _theme_projection_documents(source)
    cursor.execute(
        "DELETE FROM central_documents WHERE collection IN ("
        + ",".join([placeholder] * 3) + ")", tuple(collections),
    )
    rows = [row for collection, documents in collections.items()
            for row in document_value_rows(collection, documents, received_at)]
    cursor.executemany(
        "INSERT INTO central_documents(collection,owner,document_key,updated_at,document_json) "
        f"VALUES({','.join([placeholder] * 5)})", rows,
    )
    cursor.execute(
        f"INSERT INTO central_theme_snapshots({_THEME_SNAPSHOT_COLUMNS}) "
        f"VALUES({','.join([placeholder] * 9)}) RETURNING {_THEME_SNAPSHOT_COLUMNS}",
        (publication_id, source.profile_id, source.content_hash, source.effective_at,
         received_at, now_provider().timestamp(), source.origin_device, previous_id,
         json.dumps(source.document, ensure_ascii=False, separators=(",", ":"))),
    )
    snapshot = _theme_snapshot_result_rows([cursor.fetchone()])[0]
    return {"snapshot": snapshot, "changed": True, "replayed": False}


def _theme_snapshot_source(values: list[dict[str, Any]]) -> ThemeSnapshotSource | None:
    full = next(
        (
            value for value in reversed(values)
            if str(value.get("owner", "")) == "default"
            and str(value.get("key", "")) == "full"
        ),
        None,
    )
    if full is None:
        return None
    document = full.get("document")
    if not isinstance(document, dict):
        raise ValueError("테마 이력 원본 문서는 JSON 객체여야 합니다.")
    return ThemeSnapshotSource.from_document(
        document,
        effective_at=full.get("effective_at"),
        origin_device=full.get("origin_device"),
    )


def _append_sqlite_theme_snapshot(
    connection: sqlite3.Connection, values: list[dict[str, Any]], *, received_at: float,
    now_provider: Callable[[], datetime] = kst_now,
) -> None:
    source = _theme_snapshot_source(values)
    if source is None:
        return
    previous = connection.execute(
        "SELECT snapshot_id,content_hash FROM central_theme_snapshots "
        "ORDER BY accepted_sequence DESC LIMIT 1"
    ).fetchone()
    if previous is not None and str(previous[1]) == source.content_hash:
        return
    connection.execute(
        "INSERT INTO central_theme_snapshots("
        "snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
        "origin_device,revision_of,document_json) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex, source.profile_id, source.content_hash, source.effective_at,
            received_at, now_provider().timestamp(), source.origin_device,
            str(previous[0]) if previous is not None else None,
            json.dumps(source.document, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def _append_postgres_theme_snapshot(
    cursor: Any, values: list[dict[str, Any]], *, received_at: float,
    now_provider: Callable[[], datetime] = kst_now,
    writer_locked: bool = False,
) -> None:
    source = _theme_snapshot_source(values)
    if source is None:
        return
    # 동시에 도착한 여러 PC의 같은 문서도 직전 hash를 한 순서로 비교한다.
    if not writer_locked:
        cursor.execute("LOCK TABLE central_theme_snapshots IN SHARE ROW EXCLUSIVE MODE")
    cursor.execute(
        "SELECT snapshot_id,content_hash FROM central_theme_snapshots "
        "ORDER BY accepted_sequence DESC LIMIT 1"
    )
    previous = cursor.fetchone()
    if previous is not None and str(previous[1]) == source.content_hash:
        return
    cursor.execute(
        "INSERT INTO central_theme_snapshots("
        "snapshot_id,profile_id,content_hash,effective_at,received_at,available_at,"
        "origin_device,revision_of,document_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            uuid.uuid4().hex, source.profile_id, source.content_hash, source.effective_at,
            received_at, now_provider().timestamp(), source.origin_device,
            str(previous[0]) if previous is not None else None,
            json.dumps(source.document, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def _theme_snapshot_result_rows(rows: list[tuple[object, ...]]) -> list[dict[str, Any]]:
    keys = (
        "snapshot_id", "profile_id", "content_hash", "effective_at", "received_at",
        "available_at", "origin_device", "revision_of",
    )
    return [
        {
            **dict(zip(keys, row[:8], strict=True)),
            "document": row[8] if isinstance(row[8], dict) else json.loads(str(row[8])),
        }
        for row in rows
    ]


def _article_source(value: dict[str, Any]) -> tuple[str, str, dict[str, Any], str, str]:
    document = value.get("document")
    if not isinstance(document, dict):
        raise ValueError("뉴스 기사 문서는 JSON 객체여야 합니다.")
    stock_code = str(document.get("stock_code") or value.get("owner") or "").strip()
    identity = str(document.get("identity") or value.get("key") or "").strip()
    if not stock_code or not identity:
        raise ValueError("뉴스 기사에는 종목코드와 identity가 필요합니다.")
    return (
        stock_code, identity, dict(document),
        str(value.get("collector_id") or "unknown").strip() or "unknown",
        str(value.get("collection_scope") or "watchlist").strip() or "watchlist",
    )


def _append_sqlite_news_articles(
    connection: sqlite3.Connection, values: list[dict[str, Any]], *, received_at: float,
    now_provider: Callable[[], datetime] = kst_now,
) -> None:
    for value in values:
        stock, identity, document, collector, scope = _article_source(value)
        content_hash = news_projection_article_hash(document, scope)
        previous = connection.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=? AND identity=? ORDER BY accepted_sequence DESC LIMIT 1",
            (stock, identity),
        ).fetchone()
        same_source = connection.execute(
            "SELECT 1 FROM central_news_article_revisions WHERE stock_code=? AND identity=? "
            "AND collector_id=? AND content_hash=? LIMIT 1",
            (stock, identity, collector, content_hash),
        ).fetchone()
        if same_source is not None:
            continue
        revision_id = uuid.uuid4().hex
        accepted_at = now_provider().timestamp()
        connection.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
            "received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (revision_id, stock, identity, content_hash, collector, document.get("published_at"),
             received_at, accepted_at, scope, str(previous[0]) if previous else None,
             json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
        )
        _insert_sqlite_news_job(
            connection, revision_id, stock, stock, "BODY", content_hash,
            ARTICLE_BODY_EXTRACTOR_VERSION, document, accepted_at,
        )


def _append_postgres_news_articles(cursor: Any, values: list[dict[str, Any]], *, received_at: float,
                                   now_provider: Callable[[], datetime] = kst_now) -> None:
    cursor.execute("LOCK TABLE central_news_article_revisions IN SHARE ROW EXCLUSIVE MODE")
    for value in values:
        stock, identity, document, collector, scope = _article_source(value)
        content_hash = news_projection_article_hash(document, scope)
        cursor.execute(
            "SELECT article_revision_id,content_hash FROM central_news_article_revisions "
            "WHERE stock_code=%s AND identity=%s ORDER BY accepted_sequence DESC LIMIT 1",
            (stock, identity),
        )
        previous = cursor.fetchone()
        cursor.execute(
            "SELECT 1 FROM central_news_article_revisions WHERE stock_code=%s AND identity=%s "
            "AND collector_id=%s AND content_hash=%s LIMIT 1",
            (stock, identity, collector, content_hash),
        )
        if cursor.fetchone() is not None:
            continue
        revision_id = uuid.uuid4().hex
        accepted_at = now_provider().timestamp()
        cursor.execute(
            "INSERT INTO central_news_article_revisions("
            "article_revision_id,stock_code,identity,content_hash,collector_id,published_at,"
            "received_at,available_at,collection_scope,revision_of,document_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (revision_id, stock, identity, content_hash, collector, document.get("published_at"),
             received_at, accepted_at, scope, str(previous[0]) if previous else None,
             json.dumps(document, ensure_ascii=False, separators=(",", ":"))),
        )
        _insert_postgres_news_job(
            cursor, revision_id, stock, stock, "BODY", content_hash,
            ARTICLE_BODY_EXTRACTOR_VERSION, document, accepted_at,
        )
