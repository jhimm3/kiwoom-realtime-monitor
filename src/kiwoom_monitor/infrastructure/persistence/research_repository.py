"""연구 실행·Snapshot·Decision·후보 사건을 분리 저장하는 SQLite 원장."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from kiwoom_monitor.application.breakout_strategy import StrategyEvaluation
from kiwoom_monitor.application.research_queue import ResearchCampaignPolicy, build_research_job_identity
from kiwoom_monitor.application.research_search import ExperimentSpec, generate_trials
from kiwoom_monitor.application.research_splits import DevelopmentPartitionSpec, ResearchEvaluationSpec, FinalHoldoutBatchSpec
from kiwoom_monitor.application.context_candidates import ContextHypothesis
from kiwoom_monitor.application.research_evaluation import (
    OutcomeLabel,
    PerformanceSummary,
    ResearchComparison,
    ResearchReport,
    IndependentDevelopmentComparison,
    build_independent_development_comparison,
)
from kiwoom_monitor.application.research_execution import ExecutionEvent
from kiwoom_monitor.application.research_hypotheses import (
    DevelopmentEvidenceSnapshot,
    ResearchHypothesis,
)
from kiwoom_monitor.application.theme_leadership import (
    EntryThesis,
    ThemeLeadershipEvaluation,
    ThesisPolicyDecision,
)
from kiwoom_monitor.infrastructure.persistence.schema_migrations import (
    SQLiteMigration,
    SQLiteMigrationRunner,
)


RESEARCH_SCHEMA_VERSION = 27
RESEARCH_STORAGE_PREPARATION_SECONDS = 120


def _final_holdout_timestamp(value: str, window_end: str) -> str:
    if not isinstance(value, str):
        raise ValueError('final event timestamp must be timezone aware')
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('final event timestamp must be timezone aware')
    moment = moment.astimezone(UTC)
    if moment < datetime.fromisoformat(window_end):
        raise ValueError('final access cannot precede the window end')
    return moment.isoformat(timespec='microseconds')


def _final_holdout_request_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or '\x00' in value:
        raise ValueError('final event request ID must be 1 to 256 characters')
    return value


def _final_holdout_event(connection, request_id, window_id, event_type, timestamp, reason):
    event = ('final_event_' + hashlib.sha256(request_id.encode()).hexdigest(),
             window_id, request_id, event_type, timestamp, reason)
    old = connection.execute('SELECT * FROM research_final_holdout_events WHERE request_id=?', (request_id,)).fetchone()
    if old is not None:
        if tuple(old) != event:
            raise ValueError('final event request ID is already bound to another operation')
        return False
    last = connection.execute('SELECT MAX(accessed_at) FROM research_final_holdout_events WHERE window_id=?', (window_id,)).fetchone()[0]
    if last is not None and timestamp < last:
        raise ValueError('final events must not move backwards in time')
    return event


def _check_final_development_history(connection, batch, checkpoint):
    """Conservatively inspect local run footprints, never their outcomes."""
    final_start, final_end = datetime.fromisoformat(batch.start), datetime.fromisoformat(batch.end)
    for row in connection.execute('SELECT r.run_id,r.spec_json,r.input_manifest_json,f.batch_id AS final_batch_id '
                                  'FROM research_runs r LEFT JOIN research_final_holdout_executions f ON f.run_id=r.run_id'):
        checkpoint()
        if row['final_batch_id'] == batch.batch_id:
            continue  # This exact locked batch may be reloaded, but its terminal candidates cannot be reclaimed.
        try:
            spec, manifest = json.loads(row['spec_json']), json.loads(row['input_manifest_json'])
            # A legacy continuous runner consumes the whole input, even between/outside report folds.
            captured = manifest['captured_range']
            intervals = [(datetime.fromisoformat(captured['start']), datetime.fromisoformat(captured['end']))]
            raw = spec.get('evaluation_spec')
            if raw is not None:
                evaluation = ResearchEvaluationSpec.from_dict(raw)
                intervals.extend((datetime.fromisoformat(fold.start) - timedelta(seconds=evaluation.warmup_seconds),
                                  datetime.fromisoformat(fold.end)) for fold in evaluation.folds)
            if not intervals or any(start.tzinfo is None or end.tzinfo is None or end <= start for start, end in intervals):
                raise ValueError('unknown research footprint')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError('cannot prove final window unused: malformed or unknown local research footprint') from exc
        if any(start < final_end and end > final_start for start, end in intervals):
            raise ValueError('final window was already used by a local research run (including warmup)')
    checkpoint()


def _migration_v1(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE research_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            spec_json TEXT NOT NULL,
            input_manifest_json TEXT NOT NULL,
            logical_result_hash TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT ''
        )""",
        """CREATE TABLE research_feature_snapshots (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            decision_time TEXT NOT NULL,
            input_cutoff TEXT NOT NULL,
            symbol TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id)
        )""",
        """CREATE TABLE research_decisions (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            proposal TEXT NOT NULL,
            final_action TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id),
            FOREIGN KEY(snapshot_id) REFERENCES research_feature_snapshots(snapshot_id)
        )""",
        """CREATE TABLE research_candidate_events (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            dedup_key TEXT NOT NULL,
            symbol TEXT NOT NULL,
            available_at TEXT NOT NULL,
            document_json TEXT NOT NULL,
            UNIQUE(run_id, dedup_key),
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id),
            FOREIGN KEY(decision_id) REFERENCES research_decisions(decision_id)
        )""",
        """CREATE INDEX research_feature_snapshots_run_idx
            ON research_feature_snapshots(run_id, accepted_sequence)""",
        """CREATE INDEX research_decisions_run_idx
            ON research_decisions(run_id, accepted_sequence)""",
        """CREATE INDEX research_candidate_events_run_idx
            ON research_candidate_events(run_id, accepted_sequence)""",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_v2(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE research_execution_events (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            intent_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id)
        )""",
        """CREATE TABLE research_outcome_labels (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            label_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            candidate_event_id TEXT NOT NULL,
            horizon_seconds INTEGER NOT NULL,
            status TEXT NOT NULL,
            available_at TEXT NOT NULL,
            document_json TEXT NOT NULL,
            UNIQUE(run_id, candidate_event_id, horizon_seconds),
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id),
            FOREIGN KEY(candidate_event_id) REFERENCES research_candidate_events(event_id)
        )""",
        """CREATE TABLE research_run_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id)
        )""",
        """CREATE INDEX research_execution_events_run_idx
            ON research_execution_events(run_id, accepted_sequence)""",
        """CREATE INDEX research_outcome_labels_run_idx
            ON research_outcome_labels(run_id, accepted_sequence)""",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_v3(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE research_reports (
            report_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id)
        )"""
    )


def _migration_v4(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE research_comparisons (
            comparison_id TEXT PRIMARY KEY,
            baseline_run_id TEXT NOT NULL,
            variant_run_id TEXT NOT NULL,
            changed_condition TEXT NOT NULL,
            status TEXT NOT NULL,
            document_json TEXT NOT NULL,
            UNIQUE(baseline_run_id, variant_run_id, changed_condition),
            FOREIGN KEY(baseline_run_id) REFERENCES research_runs(run_id),
            FOREIGN KEY(variant_run_id) REFERENCES research_runs(run_id)
        )"""
    )


def _migration_v5(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE research_context_hypothesis_revisions (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            revision_id TEXT NOT NULL UNIQUE,
            hypothesis_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            status TEXT NOT NULL,
            revision_available_at TEXT NOT NULL,
            document_json TEXT NOT NULL
        )""",
        """CREATE INDEX research_context_hypothesis_revisions_history_idx
            ON research_context_hypothesis_revisions(
                hypothesis_id, revision_available_at, accepted_sequence
            )""",
        """CREATE INDEX research_context_hypothesis_revisions_target_idx
            ON research_context_hypothesis_revisions(
                target_id, revision_available_at, accepted_sequence
            )""",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_v6(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE research_theme_leadership_revisions (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            revision_id TEXT NOT NULL UNIQUE,
            theme_id TEXT NOT NULL,
            available_at TEXT NOT NULL,
            document_json TEXT NOT NULL
        )""",
        """CREATE TABLE research_entry_theses (
            thesis_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            leadership_revision_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            created_at TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id),
            FOREIGN KEY(leadership_revision_id)
                REFERENCES research_theme_leadership_revisions(revision_id)
        )""",
        """CREATE TABLE research_thesis_decisions (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL UNIQUE,
            run_id TEXT NOT NULL,
            thesis_id TEXT NOT NULL,
            leadership_revision_id TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            final_action TEXT NOT NULL,
            available_at TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES research_runs(run_id),
            FOREIGN KEY(thesis_id) REFERENCES research_entry_theses(thesis_id),
            FOREIGN KEY(leadership_revision_id)
                REFERENCES research_theme_leadership_revisions(revision_id)
        )""",
        """CREATE INDEX research_theme_leadership_history_idx
            ON research_theme_leadership_revisions(theme_id,available_at,accepted_sequence)""",
        """CREATE INDEX research_thesis_decisions_history_idx
            ON research_thesis_decisions(thesis_id,available_at,accepted_sequence)""",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_v7(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE research_search_experiments (
            experiment_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            spec_json TEXT NOT NULL
        )""",
        """CREATE TABLE research_search_trials (
            trial_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            status TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL,
            document_json TEXT NOT NULL,
            UNIQUE(experiment_id, ordinal),
            FOREIGN KEY(experiment_id) REFERENCES research_search_experiments(experiment_id)
        )""",
        """CREATE TABLE research_candidate_cards (
            card_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            trial_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(experiment_id) REFERENCES research_search_experiments(experiment_id),
            FOREIGN KEY(trial_id) REFERENCES research_search_trials(trial_id)
        )""",
        """CREATE INDEX research_search_trials_experiment_idx
            ON research_search_trials(experiment_id, ordinal)""",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_v8(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE research_search_jobs (
            job_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL UNIQUE,
            dataset_id TEXT NOT NULL,
            dataset_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '',
            lease_expires_at TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(experiment_id) REFERENCES research_search_experiments(experiment_id)
        )""",
        """CREATE TABLE research_search_job_events (
            accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            job_id TEXT NOT NULL,
            status TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            document_json TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES research_search_jobs(job_id)
        )""",
        """CREATE INDEX research_search_job_events_job_idx
            ON research_search_job_events(job_id, accepted_sequence)""",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_v9(connection: sqlite3.Connection) -> None:
    statements = (
        "ALTER TABLE research_search_jobs ADD COLUMN owner_token TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE research_search_jobs ADD COLUMN generation INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE research_search_jobs ADD COLUMN heartbeat_at TEXT NOT NULL DEFAULT ''",
        """CREATE TABLE research_trial_attempts (
            attempt_id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            experiment_id TEXT NOT NULL,
            trial_id TEXT NOT NULL,
            owner_token TEXT NOT NULL,
            generation INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(job_id) REFERENCES research_search_jobs(job_id),
            FOREIGN KEY(experiment_id) REFERENCES research_search_experiments(experiment_id)
        )""",
        """CREATE INDEX research_trial_attempts_trial_idx
            ON research_trial_attempts(experiment_id, trial_id, started_at)""",
        """CREATE INDEX research_trial_attempts_job_idx
            ON research_trial_attempts(job_id, started_at)""",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_v10(connection: sqlite3.Connection) -> None:
    statements = (
        '''CREATE TABLE research_campaigns (
            campaign_id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
            desired_state TEXT NOT NULL, operational_state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
            cycle_sequence INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
        '''CREATE TABLE research_campaign_revisions (
            campaign_id TEXT NOT NULL, revision INTEGER NOT NULL, policy_json TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(campaign_id,revision),
            FOREIGN KEY(campaign_id) REFERENCES research_campaigns(campaign_id))''',
        '''CREATE TABLE research_campaign_jobs (
            campaign_id TEXT NOT NULL, job_id TEXT NOT NULL, source_kind TEXT NOT NULL,
            input_path TEXT NOT NULL, request_json TEXT NOT NULL,
            state TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            generation INTEGER NOT NULL DEFAULT 0, owner_token TEXT NOT NULL DEFAULT '',
            lease_expires_at TEXT NOT NULL DEFAULT '', next_attempt_at TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '', accepted_sequence INTEGER NOT NULL,
            PRIMARY KEY(campaign_id,job_id), FOREIGN KEY(campaign_id) REFERENCES research_campaigns(campaign_id),
            FOREIGN KEY(job_id) REFERENCES research_search_jobs(job_id))''',
        '''CREATE TABLE research_campaign_cycles (
            campaign_id TEXT NOT NULL, sequence INTEGER NOT NULL, campaign_revision INTEGER NOT NULL,
            job_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_token TEXT NOT NULL,
            state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '', PRIMARY KEY(campaign_id,sequence),
            FOREIGN KEY(campaign_id,job_id) REFERENCES research_campaign_jobs(campaign_id,job_id),
            FOREIGN KEY(campaign_id,campaign_revision) REFERENCES research_campaign_revisions(campaign_id,revision))''',
        '''CREATE INDEX research_campaign_job_selection_idx
            ON research_campaign_jobs(campaign_id,state,next_attempt_at,accepted_sequence)''',
    )
    for statement in statements:
        connection.execute(statement)


def _migration_v11(connection: sqlite3.Connection) -> None:
    connection.execute('ALTER TABLE research_campaign_jobs ADD COLUMN budget_revision INTEGER NOT NULL DEFAULT 1')
    connection.execute('ALTER TABLE research_campaign_cycles ADD COLUMN budget_revision INTEGER NOT NULL DEFAULT 1')
    connection.execute('''CREATE TABLE research_campaign_job_budgets (
        campaign_id TEXT NOT NULL, job_id TEXT NOT NULL, revision INTEGER NOT NULL,
        budget_json TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(campaign_id,job_id,revision),
        FOREIGN KEY(campaign_id,job_id) REFERENCES research_campaign_jobs(campaign_id,job_id))''')
    rows = connection.execute('SELECT campaign_id,job_id,request_json FROM research_campaign_jobs').fetchall()
    for campaign_id, job_id, request_json in rows:
        document = json.loads(request_json)
        budget = {key: document[key] for key in ('max_trials', 'max_seconds', 'resource_budget')}
        connection.execute('INSERT INTO research_campaign_job_budgets VALUES(?,?,1,?,?)',
                           (campaign_id, job_id, _canonical_json(budget), datetime.now(UTC).isoformat()))


def _migration_v12(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_campaign_workers (
        campaign_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0,
        owner_token TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'IDLE',
        lease_expires_at TEXT NOT NULL DEFAULT '', failure_count INTEGER NOT NULL DEFAULT 0,
        next_retry_at TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
        FOREIGN KEY(campaign_id) REFERENCES research_campaigns(campaign_id))''')
    connection.execute('''CREATE TABLE research_campaign_worker_attempts (
        campaign_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_token TEXT NOT NULL,
        campaign_revision INTEGER NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL,
        finished_at TEXT NOT NULL DEFAULT '', exit_code INTEGER, reason TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(campaign_id,generation),
        FOREIGN KEY(campaign_id) REFERENCES research_campaigns(campaign_id),
        FOREIGN KEY(campaign_id,campaign_revision) REFERENCES research_campaign_revisions(campaign_id,revision))''')
    connection.execute("INSERT INTO research_campaign_workers(campaign_id,updated_at) SELECT campaign_id,updated_at FROM research_campaigns")


def _migration_v13(connection: sqlite3.Connection) -> None:
    connection.execute("CREATE TABLE research_campaign_input_sources (source_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, template_job_id TEXT NOT NULL, root TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, scope_json TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'READY', failure_count INTEGER NOT NULL DEFAULT 0, next_scan_at TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', FOREIGN KEY(campaign_id,template_job_id) REFERENCES research_campaign_jobs(campaign_id,job_id))")
    connection.execute("CREATE TABLE research_campaign_input_acceptances (source_id TEXT NOT NULL REFERENCES research_campaign_input_sources(source_id), fingerprint TEXT NOT NULL, input_path TEXT NOT NULL, job_id TEXT NOT NULL, manifest_hash TEXT NOT NULL, PRIMARY KEY(source_id,fingerprint), UNIQUE(source_id,input_path))")
    connection.execute('CREATE INDEX idx_campaign_input_sources_campaign ON research_campaign_input_sources(campaign_id)')


def _migration_v14(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE research_campaign_input_sources ADD COLUMN nas_auto_prepare INTEGER NOT NULL DEFAULT 0")
    connection.execute("ALTER TABLE research_campaign_input_sources ADD COLUMN nas_config_path TEXT NOT NULL DEFAULT ''")
    connection.execute("ALTER TABLE research_campaign_input_sources ADD COLUMN remote_signature TEXT NOT NULL DEFAULT ''")


def _migration_v15(connection: sqlite3.Connection) -> None:
    connection.execute('ALTER TABLE research_campaign_input_sources ADD COLUMN storage_cap_bytes INTEGER NOT NULL DEFAULT 0 CHECK(storage_cap_bytes>=0)')
    connection.execute('''CREATE TABLE research_campaign_storage_operations (
        operation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES research_campaign_input_sources(source_id),
        campaign_id TEXT NOT NULL REFERENCES research_campaigns(campaign_id), root TEXT NOT NULL,
        storage_cap_bytes INTEGER NOT NULL, owner_token TEXT NOT NULL, generation INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('PREPARING','PUBLISHED','UNCHANGED','BLOCKED','FAILED','CANCELLED','ABANDONED')),
        started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '', staging_path TEXT NOT NULL DEFAULT '', input_path TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '')''')
    connection.execute('CREATE INDEX idx_campaign_storage_operations_state ON research_campaign_storage_operations(state)')


def _migration_v16(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_campaign_staging_cleanups (
        operation_id TEXT PRIMARY KEY REFERENCES research_campaign_storage_operations(operation_id),
        source_id TEXT NOT NULL REFERENCES research_campaign_input_sources(source_id),
        path TEXT NOT NULL, marker_hash TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL CHECK(state IN ('READY','DELETED','MISSING','PROTECTED','FAILED')),
        failure_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT NOT NULL DEFAULT '',
        bytes_expected INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL)''')
    connection.execute('CREATE INDEX idx_campaign_staging_cleanups_source ON research_campaign_staging_cleanups(source_id)')


def _migration_v17(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE research_campaign_jobs ADD COLUMN source_request_json TEXT NOT NULL DEFAULT ''")


def _migration_v18(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_final_holdout_windows (
        window_id TEXT PRIMARY KEY, start TEXT NOT NULL, end TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('FINAL_RESERVED','EXPOSED_DEVELOPMENT')),
        batch_id TEXT NOT NULL UNIQUE, spec_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE research_final_holdout_events (
        event_id TEXT PRIMARY KEY, window_id TEXT NOT NULL REFERENCES research_final_holdout_windows(window_id),
        request_id TEXT NOT NULL UNIQUE, event_type TEXT NOT NULL CHECK(event_type IN ('FINAL_ACCESS','EXPOSED_DEVELOPMENT')),
        accessed_at TEXT NOT NULL, reason TEXT NOT NULL)''')
    connection.execute('CREATE INDEX idx_final_holdout_events_window ON research_final_holdout_events(window_id,accessed_at,event_id)')


def _migration_v19(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_final_holdout_executions (
        execution_id TEXT PRIMARY KEY,
        window_id TEXT NOT NULL REFERENCES research_final_holdout_windows(window_id),
        batch_id TEXT NOT NULL,
        candidate_spec_hash TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE REFERENCES research_runs(run_id),
        owner_token TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('RUNNING','COMPLETED','FAILED','CANCELLED')),
        started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
        logical_result_hash TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
        UNIQUE(batch_id,candidate_spec_hash))''')
    connection.execute('CREATE INDEX idx_final_holdout_executions_window ON research_final_holdout_executions(window_id,state,candidate_spec_hash)')


def _migration_v20(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE research_final_holdout_executions ADD COLUMN generation INTEGER NOT NULL DEFAULT 1")
    connection.execute('''CREATE TABLE research_final_holdout_recoveries (
        request_id TEXT PRIMARY KEY,
        execution_id TEXT NOT NULL REFERENCES research_final_holdout_executions(execution_id),
        owner_token TEXT NOT NULL,
        reason TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('REQUESTED','CLAIMED')),
        requested_at TEXT NOT NULL, claimed_at TEXT NOT NULL DEFAULT '',
        generation INTEGER NOT NULL,
        UNIQUE(execution_id,generation))''')
    connection.execute('CREATE INDEX idx_final_holdout_recoveries_execution ON research_final_holdout_recoveries(execution_id,requested_at,request_id)')


def _migration_v21(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_hypotheses (
        accepted_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        hypothesis_id TEXT NOT NULL UNIQUE,
        family_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status='READY'),
        created_at TEXT NOT NULL,
        document_json TEXT NOT NULL)''')
    connection.execute('''CREATE TABLE research_hypothesis_parents (
        hypothesis_id TEXT NOT NULL REFERENCES research_hypotheses(hypothesis_id),
        parent_ordinal INTEGER NOT NULL CHECK(parent_ordinal>=0),
        parent_id TEXT NOT NULL REFERENCES research_hypotheses(hypothesis_id),
        PRIMARY KEY(hypothesis_id,parent_ordinal),
        UNIQUE(hypothesis_id,parent_id))''')
    connection.execute('CREATE INDEX idx_research_hypotheses_family ON research_hypotheses(family_id,accepted_sequence)')
    connection.execute('CREATE INDEX idx_research_hypothesis_parents_parent ON research_hypothesis_parents(parent_id,hypothesis_id)')


def _migration_v22(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_campaign_hypotheses (
        campaign_id TEXT NOT NULL REFERENCES research_campaigns(campaign_id),
        hypothesis_id TEXT NOT NULL REFERENCES research_hypotheses(hypothesis_id),
        state TEXT NOT NULL CHECK(state IN ('AVAILABLE','ENQUEUED')),
        job_id TEXT,
        accepted_sequence INTEGER NOT NULL,
        enqueued_sequence INTEGER NOT NULL DEFAULT 0,
        registered_at TEXT NOT NULL,
        enqueued_at TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(campaign_id,hypothesis_id),
        UNIQUE(campaign_id,job_id),
        FOREIGN KEY(campaign_id,job_id) REFERENCES research_campaign_jobs(campaign_id,job_id))''')
    connection.execute('CREATE INDEX idx_campaign_hypotheses_selection ON research_campaign_hypotheses(campaign_id,state,accepted_sequence)')


def _migration_v23(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_campaign_hypothesis_expansions (
        campaign_id TEXT NOT NULL,
        parent_hypothesis_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        policy_revision INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('GENERATED','EXHAUSTED','BLOCKED')),
        generated_count INTEGER NOT NULL CHECK(generated_count>=0),
        reason TEXT NOT NULL DEFAULT '',
        evidence_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(campaign_id,parent_hypothesis_id,evidence_id,policy_revision),
        FOREIGN KEY(campaign_id,parent_hypothesis_id)
            REFERENCES research_campaign_hypotheses(campaign_id,hypothesis_id),
        FOREIGN KEY(campaign_id,policy_revision)
            REFERENCES research_campaign_revisions(campaign_id,revision))''')
    connection.execute(
        'CREATE INDEX idx_campaign_hypothesis_expansions_parent '
        'ON research_campaign_hypothesis_expansions(campaign_id,parent_hypothesis_id,state)'
    )


def _migration_v24(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_independent_run_owners (
        run_id TEXT PRIMARY KEY REFERENCES research_runs(run_id),
        owner_token TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>=1),
        claimed_at TEXT NOT NULL
    )''')
    connection.execute('CREATE INDEX idx_research_independent_run_owners_token '
                       'ON research_independent_run_owners(owner_token)')
    connection.execute('''CREATE TABLE research_independent_run_owner_history (
        run_id TEXT NOT NULL REFERENCES research_runs(run_id),
        owner_token TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>=1),
        claimed_at TEXT NOT NULL,
        PRIMARY KEY(run_id,owner_token),
        UNIQUE(run_id,generation)
    )''')


def _migration_v25(connection: sqlite3.Connection) -> None:
    connection.execute('ALTER TABLE research_campaign_input_sources ADD COLUMN rolling_daily INTEGER NOT NULL DEFAULT 0')


def _migration_v26(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE research_campaign_input_sources ADD COLUMN rolling_next_start TEXT NOT NULL DEFAULT ''")


def _migration_v27(connection: sqlite3.Connection) -> None:
    connection.execute('''CREATE TABLE research_campaign_rolling_empty_days (
        source_id TEXT NOT NULL REFERENCES research_campaign_input_sources(source_id),
        range_start TEXT NOT NULL, attempts INTEGER NOT NULL CHECK(attempts>=1),
        last_checked_at TEXT NOT NULL, next_retry_at TEXT NOT NULL,
        PRIMARY KEY(source_id,range_start))''')
    connection.execute('CREATE INDEX idx_research_campaign_rolling_empty_due '
                       'ON research_campaign_rolling_empty_days(source_id,next_retry_at)')


_MIGRATIONS = (
    SQLiteMigration(1, "research_run_ledger", _migration_v1),
    SQLiteMigration(2, "simulation_execution_and_outcomes", _migration_v2),
    SQLiteMigration(3, "chronological_research_reports", _migration_v3),
    SQLiteMigration(4, "paired_research_comparisons", _migration_v4),
    SQLiteMigration(5, "context_hypothesis_revisions", _migration_v5),
    SQLiteMigration(6, "theme_leadership_and_entry_thesis", _migration_v6),
    SQLiteMigration(7, "limited_research_search", _migration_v7),
    SQLiteMigration(8, "bounded_research_jobs", _migration_v8),
    SQLiteMigration(9, "fenced_research_trial_attempts", _migration_v9),
    SQLiteMigration(10, "persistent_research_campaigns", _migration_v10),
    SQLiteMigration(11, "campaign_operating_budget_revisions", _migration_v11),
    SQLiteMigration(12, "campaign_worker_recovery", _migration_v12),
    SQLiteMigration(13, "campaign_input_discovery", _migration_v13),
    SQLiteMigration(14, "campaign_nas_input_preparation", _migration_v14),
    SQLiteMigration(15, "campaign_storage_capacity_ledger", _migration_v15),
    SQLiteMigration(16, "campaign_incomplete_staging_cleanup", _migration_v16),
    SQLiteMigration(17, "campaign_independent_source_request", _migration_v17),
    SQLiteMigration(18, "final_holdout_access_ledger", _migration_v18),
    SQLiteMigration(19, "final_holdout_execution_ownership", _migration_v19),
    SQLiteMigration(20, "explicit_final_holdout_recovery", _migration_v20),
    SQLiteMigration(21, "immutable_research_hypothesis_lineage", _migration_v21),
    SQLiteMigration(22, "campaign_hypothesis_queue", _migration_v22),
    SQLiteMigration(23, "campaign_hypothesis_expansion_ledger", _migration_v23),
    SQLiteMigration(24, "independent_development_run_ownership", _migration_v24),
    SQLiteMigration(25, "opt_in_daily_development_expansion", _migration_v25),
    SQLiteMigration(26, "nas_rolling_daily_cursor", _migration_v26),
    SQLiteMigration(27, "nas_rolling_empty_day_rechecks", _migration_v27),
)


class ResearchRepository:
    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self._path = Path(path)
        self._read_only = read_only
        if read_only:
            if not self._path.is_file():
                raise ValueError('read-only research database does not exist')
            if self.schema_version() not in (17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27):
                raise ValueError('comparison requires an already migrated research v17-v27 database')
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            SQLiteMigrationRunner(
                connection, table="research_schema_migrations",
            ).apply(_MIGRATIONS)
            connection.commit()

    @property
    def path(self) -> Path:
        return self._path

    def record_final_holdout_access(self, batch: FinalHoldoutBatchSpec, *, request_id: str, accessed_at: str,
                                   check_development_history: bool = False, checkpoint=lambda: None) -> bool:
        if not isinstance(batch, FinalHoldoutBatchSpec):
            raise ValueError('a locked final batch is required')
        if type(check_development_history) is not bool:
            raise ValueError('history check must be boolean')
        timestamp = _final_holdout_timestamp(accessed_at, batch.end)
        request_id = _final_holdout_request_id(request_id)
        document = _canonical_json(batch.to_dict())
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            if check_development_history:
                _check_final_development_history(connection, batch, checkpoint)
            old = connection.execute('SELECT * FROM research_final_holdout_windows WHERE window_id=?', (batch.window_id,)).fetchone()
            if old is not None:
                if old['batch_id'] != batch.batch_id or old['spec_json'] != document:
                    raise ValueError('final window is locked to another candidate/input/policy batch')
                if old['state'] == 'EXPOSED_DEVELOPMENT':
                    raise ValueError('exposed development window cannot be reused for final evaluation')
            elif connection.execute('SELECT 1 FROM research_final_holdout_windows WHERE start < ? AND end > ? LIMIT 1',
                                    (batch.end, batch.start)).fetchone():
                raise ValueError('final window overlaps a reserved or exposed final window')
            event = _final_holdout_event(connection, request_id, batch.window_id, 'FINAL_ACCESS', timestamp, '')
            if event is False:
                return False
            if old is None:
                connection.execute('INSERT INTO research_final_holdout_windows VALUES(?,?,?,?,?,?,?)',
                    (batch.window_id, batch.start, batch.end, 'FINAL_RESERVED', batch.batch_id, document, timestamp))
            connection.execute('INSERT INTO research_final_holdout_events VALUES(?,?,?,?,?,?)', event)
            return True

    def expose_final_holdout(self, window_id: str, *, request_id: str, exposed_at: str, reason: str) -> bool:
        request_id = _final_holdout_request_id(request_id)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('final exposure requires a reason (1 to 2000 characters)')
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            window = connection.execute('SELECT * FROM research_final_holdout_windows WHERE window_id=?', (window_id,)).fetchone()
            if window is None:
                raise ValueError('final window is not registered')
            if connection.execute("SELECT 1 FROM research_final_holdout_executions WHERE window_id=? AND state='RUNNING' LIMIT 1",
                                  (window_id,)).fetchone() is not None:
                raise ValueError('running final execution cannot be exposed to development')
            timestamp = _final_holdout_timestamp(exposed_at, window['end'])
            event = _final_holdout_event(connection, request_id, window_id, 'EXPOSED_DEVELOPMENT', timestamp, reason)
            if event is False:
                return False
            connection.execute("UPDATE research_final_holdout_windows SET state='EXPOSED_DEVELOPMENT' WHERE window_id=?", (window_id,))
            connection.execute('INSERT INTO research_final_holdout_events VALUES(?,?,?,?,?,?)', event)
            return True

    def load_final_holdout_window(self, window_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute('SELECT * FROM research_final_holdout_windows WHERE window_id=?', (window_id,)).fetchone()
        if row is None:
            return None
        result = dict(row); result['spec'] = json.loads(result.pop('spec_json'))
        return result

    def load_final_holdout_events(self, window_id: str, *, limit: int = 1000) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('final event limit must be 1 to 1000')
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT * FROM research_final_holdout_events WHERE window_id=? ORDER BY accessed_at DESC,event_id DESC LIMIT ?',
                                      (window_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def request_final_holdout_recovery(self, batch: FinalHoldoutBatchSpec, *, candidate_spec_hash: str,
                                       request_id: str, owner_token: str, reason: str) -> bool:
        """Record one explicit retry authorization without changing execution state."""
        if not isinstance(batch, FinalHoldoutBatchSpec) or candidate_spec_hash not in batch.candidate_spec_hashes:
            raise ValueError('final recovery candidate is not part of the locked batch')
        request_id = _final_holdout_request_id(request_id)
        owner_token = _final_holdout_request_id(owner_token)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000 or '\x00' in reason:
            raise ValueError('final recovery requires a reason (1 to 2000 characters)')
        now = datetime.now(UTC).isoformat(timespec='microseconds')
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            window = connection.execute('SELECT * FROM research_final_holdout_windows WHERE window_id=?',
                                        (batch.window_id,)).fetchone()
            if (window is None or window['batch_id'] != batch.batch_id
                    or window['spec_json'] != _canonical_json(batch.to_dict())):
                raise ValueError('final recovery requires its locked access batch')
            if window['state'] != 'FINAL_RESERVED':
                raise ValueError('exposed final window cannot be recovered')
            execution = connection.execute(
                'SELECT * FROM research_final_holdout_executions WHERE batch_id=? AND candidate_spec_hash=?',
                (batch.batch_id, candidate_spec_hash)).fetchone()
            if execution is None:
                raise ValueError('final recovery requires an existing terminal execution')
            old = connection.execute('SELECT * FROM research_final_holdout_recoveries WHERE request_id=?',
                                     (request_id,)).fetchone()
            if old is not None:
                if (old['execution_id'], old['owner_token'], old['reason']) != (
                        execution['execution_id'], owner_token, reason):
                    raise ValueError('final recovery request ID is already bound to another operation')
                return False
            run = connection.execute('SELECT status,logical_result_hash,error FROM research_runs WHERE run_id=?',
                                     (execution['run_id'],)).fetchone()
            expected_state = execution['state'].lower()
            if (execution['state'] not in ('FAILED', 'CANCELLED') or run is None
                    or run[0] != expected_state or run[1] != execution['logical_result_hash']
                    or run[2] != execution['reason']):
                raise ValueError('only consistent failed or cancelled final execution can be recovered')
            if connection.execute(
                    'SELECT 1 FROM research_final_holdout_recoveries WHERE execution_id=? AND generation=?',
                    (execution['execution_id'], execution['generation'] + 1)).fetchone() is not None:
                raise ValueError('the next final execution generation already has a recovery request')
            connection.execute('INSERT INTO research_final_holdout_recoveries '
                               '(request_id,execution_id,owner_token,reason,state,requested_at,generation) '
                               "VALUES(?,?,?,?, 'REQUESTED', ?, ?)",
                               (request_id, execution['execution_id'], owner_token, reason, now,
                                execution['generation'] + 1))
            return True

    def claim_final_holdout_execution(self, batch: FinalHoldoutBatchSpec, *, candidate_spec_hash: str,
                                      run_id: str, spec: Mapping[str, Any], input_manifest: Mapping[str, Any],
                                      owner_token: str, recovery_request_id: str | None = None) -> str:
        """Atomically own one fixed candidate and create its immutable research run."""
        if not isinstance(batch, FinalHoldoutBatchSpec) or candidate_spec_hash not in batch.candidate_spec_hashes:
            raise ValueError('final execution candidate is not part of the locked batch')
        if (not isinstance(candidate_spec_hash, str) or len(candidate_spec_hash) != 64
                or any(char not in '0123456789abcdef' for char in candidate_spec_hash)):
            raise ValueError('final execution candidate hash is invalid')
        owner_token = _final_holdout_request_id(owner_token)
        if recovery_request_id is not None:
            recovery_request_id = _final_holdout_request_id(recovery_request_id)
        spec_json, manifest_json = _canonical_json(spec), _canonical_json(input_manifest)
        expected_id = 'run_' + hashlib.sha256(_canonical_json({
            'dataset_id': input_manifest.get('dataset_id', ''),
            'revision_ids_hash': input_manifest.get('revision_ids_hash', ''), 'spec': spec,
        }).encode('utf-8')).hexdigest()
        if (run_id != expected_id or spec.get('execution_scope') != 'independent_final_holdout/v1'
                or input_manifest.get('runtime_input_version') != 'independent_final_holdout_input/v1'):
            raise ValueError('final claim requires its scoped scientific run identity')
        execution_id = 'final_execution_' + hashlib.sha256(
            _canonical_json([batch.batch_id, candidate_spec_hash]).encode('utf-8')).hexdigest()
        now = datetime.now(UTC).isoformat(timespec='microseconds')
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            window = connection.execute('SELECT * FROM research_final_holdout_windows WHERE window_id=?',
                                        (batch.window_id,)).fetchone()
            if (window is None or window['batch_id'] != batch.batch_id
                    or window['spec_json'] != _canonical_json(batch.to_dict())):
                raise ValueError('final execution requires its locked access batch')
            if window['state'] != 'FINAL_RESERVED':
                raise ValueError('exposed final window cannot execute')
            old = connection.execute('SELECT * FROM research_final_holdout_executions WHERE batch_id=? AND candidate_spec_hash=?',
                                     (batch.batch_id, candidate_spec_hash)).fetchone()
            if old is not None:
                run = connection.execute('SELECT spec_json,input_manifest_json,status,logical_result_hash,error '
                                         'FROM research_runs WHERE run_id=?', (old['run_id'],)).fetchone()
                expected_state = old['state'].lower()
                if (old['execution_id'] != execution_id or old['run_id'] != run_id or run is None
                        or run[0] != spec_json or run[1] != manifest_json
                        or run[2] != expected_state or run[3] != old['logical_result_hash'] or run[4] != old['reason']):
                    raise ValueError('final execution ownership ledger is inconsistent')
                if recovery_request_id is not None:
                    recovery = connection.execute(
                        'SELECT * FROM research_final_holdout_recoveries WHERE request_id=?',
                        (recovery_request_id,)).fetchone()
                    if (recovery is None or recovery['execution_id'] != execution_id
                            or recovery['owner_token'] != owner_token):
                        raise ValueError('final recovery request does not authorize this execution owner')
                    if old['state'] == 'RUNNING' and recovery['state'] == 'CLAIMED' \
                            and recovery['generation'] == old['generation']:
                        return 'busy'
                    if old['state'] not in ('FAILED', 'CANCELLED'):
                        raise ValueError('only failed or cancelled final execution can be recovered')
                    if recovery['state'] == 'CLAIMED':
                        if recovery['generation'] != old['generation']:
                            raise ValueError('final recovery request belongs to an older execution generation')
                        return expected_state
                    if recovery['state'] != 'REQUESTED':
                        raise ValueError('final recovery request state is invalid')
                    generation = old['generation'] + 1
                    if recovery['generation'] != generation:
                        raise ValueError('final recovery request does not authorize the next execution generation')
                    connection.execute("UPDATE research_runs SET status='running',started_at=?,finished_at='',"
                                       "logical_result_hash='',error='' WHERE run_id=?", (now, run_id))
                    connection.execute("UPDATE research_final_holdout_executions SET owner_token=?,state='RUNNING',"
                                       "started_at=?,finished_at='',logical_result_hash='',reason='',generation=? "
                                       'WHERE execution_id=?',
                                       (owner_token, now, generation, execution_id))
                    connection.execute("UPDATE research_final_holdout_recoveries SET state='CLAIMED',claimed_at=? "
                                       "WHERE request_id=? AND state='REQUESTED'",
                                       (now, recovery_request_id))
                    return 'claimed'
                return expected_state if expected_state != 'running' else 'busy'
            if recovery_request_id is not None:
                raise ValueError('final recovery cannot authorize an execution that has not run')
            if connection.execute('SELECT 1 FROM research_runs WHERE run_id=?', (run_id,)).fetchone() is not None:
                raise ValueError('final scientific run already exists without final ownership')
            connection.execute('INSERT INTO research_runs(run_id,status,started_at,spec_json,input_manifest_json) '
                               "VALUES(?, 'running', ?, ?, ?)", (run_id, now, spec_json, manifest_json))
            connection.execute('INSERT INTO research_final_holdout_executions '
                               '(execution_id,window_id,batch_id,candidate_spec_hash,run_id,owner_token,state,'
                               'started_at,finished_at,logical_result_hash,reason,generation) '
                               "VALUES(?,?,?,?,?,?,'RUNNING',?,'','','',1)",
                               (execution_id, batch.window_id, batch.batch_id, candidate_spec_hash,
                                run_id, owner_token, now))
            return 'claimed'

    def assert_final_holdout_execution_owner(self, run_id: str, *, batch_id: str,
                                             candidate_spec_hash: str, owner_token: str) -> None:
        with closing(self._connect()) as connection:
            row = connection.execute('SELECT run_id,owner_token,state FROM research_final_holdout_executions '
                                     'WHERE batch_id=? AND candidate_spec_hash=?',
                                     (batch_id, candidate_spec_hash)).fetchone()
        if row is None or tuple(row) != (run_id, owner_token, 'RUNNING'):
            raise ValueError('final execution is not owned by this runner')

    def finish_final_holdout_execution(self, batch_id: str, candidate_spec_hash: str, *, run_id: str,
                                       owner_token: str, outcome: str, logical_result_hash: str = '',
                                       reason: str = '') -> bool:
        if outcome not in ('COMPLETED', 'FAILED', 'CANCELLED'):
            raise ValueError('final execution outcome is invalid')
        owner_token = _final_holdout_request_id(owner_token)
        if outcome == 'COMPLETED':
            if not isinstance(logical_result_hash, str) or len(logical_result_hash) != 64:
                raise ValueError('completed final execution requires its result SHA256')
            reason = ''
        elif not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('failed or cancelled final execution requires a bounded reason')
        now = datetime.now(UTC).isoformat(timespec='microseconds')
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM research_final_holdout_executions '
                                     'WHERE batch_id=? AND candidate_spec_hash=?',
                                     (batch_id, candidate_spec_hash)).fetchone()
            if row is None or row['run_id'] != run_id or row['owner_token'] != owner_token:
                raise ValueError('final execution completion is not owned by this runner')
            if row['state'] != 'RUNNING':
                if (row['state'], row['logical_result_hash'], row['reason']) == (outcome, logical_result_hash, reason):
                    return False
                raise ValueError('terminal final execution is immutable')
            run = connection.execute('SELECT status,logical_result_hash,error FROM research_runs WHERE run_id=?',
                                     (run_id,)).fetchone()
            if run is None:
                raise ValueError('final execution research run is missing')
            if outcome == 'COMPLETED':
                if run[0] != 'completed' or run[1] != logical_result_hash:
                    raise ValueError('final research run is not durably completed')
            else:
                if run[0] == 'completed':
                    raise ValueError('completed final research run cannot become terminal failure')
                if run[0] == 'running':
                    connection.execute('UPDATE research_runs SET status=?,finished_at=?,error=? WHERE run_id=?',
                                       (outcome.lower(), now, reason, run_id))
                elif (run[0], run[2]) != (outcome.lower(), reason):
                    raise ValueError('final research run terminal state is inconsistent')
            connection.execute('UPDATE research_final_holdout_executions SET state=?,finished_at=?,logical_result_hash=?,reason=? '
                               'WHERE execution_id=?', (outcome, now, logical_result_hash, reason, row['execution_id']))
            return True

    def cancel_exited_final_holdout_execution(self, batch: FinalHoldoutBatchSpec, *,
                                              candidate_spec_hash: str, run_id: str,
                                              owner_token: str, generation: int,
                                              reason: str) -> bool:
        """CAS-cancel an orphan whose OS exit and absent artifacts were checked by the caller."""
        if not isinstance(batch, FinalHoldoutBatchSpec) or candidate_spec_hash not in batch.candidate_spec_hashes:
            raise ValueError('orphan candidate is outside the locked final batch')
        owner_token = _final_holdout_request_id(owner_token)
        if type(generation) is not int or generation < 1:
            raise ValueError('orphan execution generation is invalid')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000 or '\x00' in reason:
            raise ValueError('orphan cancellation requires a bounded reason')
        now = datetime.now(UTC).isoformat(timespec='microseconds')
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            window = connection.execute('SELECT * FROM research_final_holdout_windows WHERE window_id=?',
                                        (batch.window_id,)).fetchone()
            if (window is None or window['state'] != 'FINAL_RESERVED'
                    or window['batch_id'] != batch.batch_id
                    or window['spec_json'] != _canonical_json(batch.to_dict())):
                raise ValueError('orphan cancellation requires the locked unexposed window')
            row = connection.execute('SELECT * FROM research_final_holdout_executions '
                                     'WHERE batch_id=? AND candidate_spec_hash=?',
                                     (batch.batch_id, candidate_spec_hash)).fetchone()
            if (row is None or row['window_id'] != batch.window_id or row['run_id'] != run_id
                    or row['owner_token'] != owner_token or row['generation'] != generation):
                raise ValueError('orphan execution owner or generation changed')
            run = connection.execute('SELECT status,spec_json,logical_result_hash,error FROM research_runs '
                                     'WHERE run_id=?', (run_id,)).fetchone()
            if (run is None or json.loads(run['spec_json']).get('execution_scope')
                    != 'independent_final_holdout/v1'):
                raise ValueError('orphan scientific run is missing or out of scope')
            if row['state'] == 'CANCELLED' and run['status'] == 'cancelled' and row['reason'] == run['error'] == reason:
                return False
            if (row['state'] != 'RUNNING' or run['status'] != 'running'
                    or row['logical_result_hash'] or run['logical_result_hash']):
                raise ValueError('orphan final execution is no longer running consistently')
            connection.execute("UPDATE research_runs SET status='cancelled',finished_at=?,error=? "
                               "WHERE run_id=? AND status='running'", (now, reason, run_id))
            connection.execute("UPDATE research_final_holdout_executions SET state='CANCELLED',"
                               "finished_at=?,reason=? WHERE execution_id=? AND state='RUNNING' "
                               "AND generation=? AND owner_token=?",
                               (now, reason, row['execution_id'], generation, owner_token))
            return True

    def load_final_holdout_executions(self, batch_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT * FROM research_final_holdout_executions WHERE batch_id=? '
                                      'ORDER BY candidate_spec_hash', (batch_id,)).fetchall()
        return tuple(dict(row) for row in rows)

    def load_final_holdout_recoveries(self, batch_id: str, *, limit: int = 1000) -> tuple[dict[str, Any], ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('final recovery limit must be 1 to 1000')
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                'SELECT r.*,e.batch_id,e.candidate_spec_hash,e.run_id '
                'FROM research_final_holdout_recoveries r '
                'JOIN research_final_holdout_executions e ON e.execution_id=r.execution_id '
                'WHERE e.batch_id=? ORDER BY r.requested_at DESC,r.request_id DESC LIMIT ?',
                (batch_id, limit)).fetchall()
        return tuple(dict(row) for row in reversed(rows))

    def start_run(
        self,
        run_id: str,
        spec: Mapping[str, Any],
        input_manifest: Mapping[str, Any],
        *, claim_independent: bool = False,
        owner_token: str | None = None,
    ) -> str | None:
        normalized_id = str(run_id).strip()
        if not normalized_id:
            raise ValueError("run_id is required")
        spec_json = _canonical_json(spec)
        manifest_json = _canonical_json(input_manifest)
        independent_scope = spec.get('execution_scope') == 'independent_development_validation/v1'
        if owner_token is not None:
            if not claim_independent:
                raise ValueError('independent owner token requires an independent claim')
            owner_token = _final_holdout_request_id(owner_token)
        if claim_independent:
            expected_id = 'run_' + hashlib.sha256(_canonical_json({
                'dataset_id': input_manifest.get('dataset_id', ''),
                'revision_ids_hash': input_manifest.get('revision_ids_hash', ''), 'spec': spec,
            }).encode('utf-8')).hexdigest()
            if (spec.get('execution_scope') != 'independent_development_validation/v1'
                    or input_manifest.get('runtime_input_version') != 'independent_development_input/v1'
                    or normalized_id != expected_id):
                raise ValueError('independent claim requires its scoped scientific run identity')
        with closing(self._connect()) as connection, connection:
            if claim_independent:
                connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                "SELECT spec_json,input_manifest_json,status FROM research_runs WHERE run_id=?",
                (normalized_id,),
            ).fetchone()
            if row is not None:
                if row[0] != spec_json or row[1] != manifest_json:
                    raise ValueError("run_id already belongs to different immutable inputs")
                if row[2] == "cancelled":
                    if independent_scope and not claim_independent:
                        raise ValueError('cancelled independent run requires an owned claim')
                    owned = connection.execute(
                        'SELECT owner_token,generation FROM research_independent_run_owners WHERE run_id=?',
                        (normalized_id,)).fetchone() if claim_independent else None
                    if owned is not None and owner_token is None:
                        raise ValueError('owned independent run requires a new owner to resume')
                    if owned is not None and owner_token == owned[0]:
                        raise ValueError('resumed independent run requires a different owner token')
                    if owner_token is not None and connection.execute(
                            'SELECT 1 FROM research_independent_run_owner_history '
                            'WHERE run_id=? AND owner_token=?', (normalized_id, owner_token)).fetchone():
                        raise ValueError('independent owner token was already used for this run')
                    now = datetime.now(UTC).isoformat()
                    connection.execute(
                        "UPDATE research_runs SET status='running',started_at=?,finished_at='',error='' "
                        "WHERE run_id=?",
                        (now, normalized_id),
                    )
                    if owner_token is not None:
                        generation = 1 if owned is None else owned[1] + 1
                        if owned is None:
                            connection.execute('INSERT INTO research_independent_run_owners '
                                               '(run_id,owner_token,generation,claimed_at) VALUES(?,?,1,?)',
                                               (normalized_id, owner_token, now))
                        else:
                            connection.execute('UPDATE research_independent_run_owners SET '
                                               'owner_token=?,generation=?,claimed_at=? WHERE run_id=?',
                                               (owner_token, generation, now, normalized_id))
                        connection.execute('INSERT INTO research_independent_run_owner_history '
                                           '(run_id,owner_token,generation,claimed_at) VALUES(?,?,?,?)',
                                           (normalized_id, owner_token, generation, now))
                    return 'claimed' if claim_independent else None
                if claim_independent:
                    return 'completed' if row[2] == 'completed' else 'failed' if row[2] == 'failed' else 'busy'
                return
            if independent_scope and not claim_independent:
                raise ValueError('new independent run requires an owned claim')
            connection.execute(
                "INSERT INTO research_runs(run_id,status,started_at,spec_json,input_manifest_json) "
                "VALUES(?,?,?,?,?)",
                (normalized_id, "running", datetime.now(UTC).isoformat(), spec_json, manifest_json),
            )
            if owner_token is not None:
                now = datetime.now(UTC).isoformat()
                connection.execute('INSERT INTO research_independent_run_owners '
                                   '(run_id,owner_token,generation,claimed_at) VALUES(?,?,1,?)',
                                   (normalized_id, owner_token, now))
                connection.execute('INSERT INTO research_independent_run_owner_history '
                                   '(run_id,owner_token,generation,claimed_at) VALUES(?,?,1,?)',
                                   (normalized_id, owner_token, now))
            return 'claimed' if claim_independent else None

    def load_independent_run_owners(self, owner_token: str) -> tuple[dict[str, Any], ...]:
        owner_token = _final_holdout_request_id(owner_token)
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT o.run_id,o.owner_token,o.generation,r.status '
                                      'FROM research_independent_run_owners o '
                                      'JOIN research_runs r ON r.run_id=o.run_id '
                                      'WHERE o.owner_token=? ORDER BY o.run_id', (owner_token,)).fetchall()
        return tuple(dict(row) for row in rows)

    def cancel_exited_independent_run(self, run_id: str, *, owner_token: str,
                                      generation: int, reason: str) -> bool:
        owner_token = _final_holdout_request_id(owner_token)
        if type(generation) is not int or generation < 1:
            raise ValueError('independent run generation is invalid')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000 or '\x00' in reason:
            raise ValueError('independent run cancellation requires a bounded reason')
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT r.status,r.spec_json,r.logical_result_hash,r.error,'
                                     'o.owner_token,o.generation FROM research_runs r '
                                     'JOIN research_independent_run_owners o ON o.run_id=r.run_id '
                                     'WHERE r.run_id=?', (run_id,)).fetchone()
            if (row is None or row['owner_token'] != owner_token or row['generation'] != generation):
                raise ValueError('independent run owner or generation changed')
            if json.loads(row['spec_json']).get('execution_scope') != 'independent_development_validation/v1':
                raise ValueError('independent run scope is invalid')
            if row['status'] == 'cancelled' and row['error'] == reason:
                return False
            if row['status'] != 'running' or row['logical_result_hash']:
                raise ValueError('independent run is no longer running')
            connection.execute("UPDATE research_runs SET status='cancelled',finished_at=?,error=? "
                               "WHERE run_id=? AND status='running'",
                               (datetime.now(UTC).isoformat(), reason, run_id))
            return True

    def save_search_experiment(self, experiment_id: str, spec: Mapping[str, Any]) -> bool:
        normalized_id = str(experiment_id).strip()
        if not normalized_id:
            raise ValueError("experiment_id is required")
        spec_json = _canonical_json(spec)
        with closing(self._connect()) as connection, connection:
            return _save_search_experiment(connection, normalized_id, spec_json)

    def save_research_hypotheses(self, hypotheses: tuple[ResearchHypothesis, ...]) -> int:
        """Store an ordered lineage batch; repeated identical nodes are harmless."""
        if not isinstance(hypotheses, tuple) or not hypotheses or len(hypotheses) > 1_001:
            raise ValueError('hypothesis batch must contain 1 to 1001 ordered nodes')
        if any(not isinstance(item, ResearchHypothesis) for item in hypotheses):
            raise ValueError('hypothesis batch contains an invalid node')
        if len({item.hypothesis_id for item in hypotheses}) != len(hypotheses):
            raise ValueError('hypothesis batch contains duplicate nodes')
        inserted = 0
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            parent_ids = tuple(dict.fromkeys(
                parent_id for hypothesis in hypotheses for parent_id in hypothesis.parent_ids
            ))
            known: set[str] = set()
            for offset in range(0, len(parent_ids), 500):
                chunk = parent_ids[offset:offset + 500]
                known.update(
                    row[0] for row in connection.execute(
                        f"SELECT hypothesis_id FROM research_hypotheses WHERE hypothesis_id IN "
                        f"({','.join('?' for _ in chunk)})",
                        chunk,
                    )
                )
            for hypothesis in hypotheses:
                missing = [parent for parent in hypothesis.parent_ids if parent not in known]
                if missing:
                    raise ValueError('hypothesis parent must already exist or appear earlier in the batch')
                document_json = _canonical_json(hypothesis.to_dict())
                existing = connection.execute(
                    'SELECT document_json FROM research_hypotheses WHERE hypothesis_id=?',
                    (hypothesis.hypothesis_id,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != document_json:
                        raise ValueError('immutable research hypothesis changed')
                    known.add(hypothesis.hypothesis_id)
                    continue
                connection.execute(
                    'INSERT INTO research_hypotheses(hypothesis_id,family_id,status,created_at,document_json) '
                    'VALUES(?,?,?,?,?)',
                    (hypothesis.hypothesis_id, hypothesis.family_id, hypothesis.status, now, document_json),
                )
                connection.executemany(
                    'INSERT INTO research_hypothesis_parents(hypothesis_id,parent_ordinal,parent_id) '
                    'VALUES(?,?,?)',
                    tuple(
                        (hypothesis.hypothesis_id, ordinal, parent_id)
                        for ordinal, parent_id in enumerate(hypothesis.parent_ids)
                    ),
                )
                known.add(hypothesis.hypothesis_id)
                inserted += 1
        return inserted

    def load_research_hypothesis(self, hypothesis_id: str) -> ResearchHypothesis | None:
        normalized = str(hypothesis_id).strip()
        if not normalized:
            raise ValueError('hypothesis ID is required')
        with closing(self._connect()) as connection:
            row = connection.execute(
                'SELECT document_json FROM research_hypotheses WHERE hypothesis_id=?',
                (normalized,),
            ).fetchone()
        return ResearchHypothesis.from_dict(json.loads(row[0])) if row is not None else None

    def load_research_hypotheses(
        self, *, family_id: str = '', limit: int = 200,
    ) -> tuple[ResearchHypothesis, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError('hypothesis load limit must be between 1 and 1000')
        normalized_family = str(family_id).strip()
        with closing(self._connect()) as connection:
            if normalized_family:
                rows = connection.execute(
                    'SELECT document_json FROM research_hypotheses WHERE family_id=? '
                    'ORDER BY accepted_sequence LIMIT ?',
                    (normalized_family, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    'SELECT document_json FROM research_hypotheses '
                    'ORDER BY accepted_sequence LIMIT ?',
                    (limit,),
                ).fetchall()
        return tuple(ResearchHypothesis.from_dict(json.loads(row[0])) for row in rows)

    def create_campaign(self, campaign_id: str, name: str, policy: ResearchCampaignPolicy) -> bool:
        if not campaign_id.strip() or not name.strip():
            raise ValueError('campaign id/name are required')
        now = datetime.now(UTC).isoformat()
        document = _canonical_json(policy.to_dict())
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute('SELECT name FROM research_campaigns WHERE campaign_id=?', (campaign_id,)).fetchone()
            if existing is not None:
                _, current = _campaign_policy(connection, campaign_id)
                if existing[0] != name or _canonical_json(current.to_dict()) != document:
                    raise ValueError('campaign already exists; use an explicit policy revision')
                return False
            connection.execute('INSERT INTO research_campaigns(campaign_id,name,revision,desired_state,operational_state,created_at,updated_at) '
                               "VALUES(?,?,1,'PAUSED','PAUSED',?,?)", (campaign_id, name, now, now))
            connection.execute('INSERT INTO research_campaign_revisions VALUES(?,1,?,?)', (campaign_id, document, now))
            connection.execute('INSERT INTO research_campaign_workers(campaign_id,updated_at) VALUES(?,?)', (campaign_id, now))
            return True

    def register_campaign_hypotheses(
        self, campaign_id: str, hypothesis_ids: tuple[str, ...], *,
        worker_claim: Mapping[str, Any] | None = None,
    ) -> int:
        """Attach explicit paused nodes or worker-owned automatic follow-up nodes."""
        if (
            not isinstance(hypothesis_ids, tuple)
            or not hypothesis_ids
            or len(hypothesis_ids) > 1_001
            or len(set(hypothesis_ids)) != len(hypothesis_ids)
            or any(not isinstance(value, str) or not value.strip() for value in hypothesis_ids)
        ):
            raise ValueError('campaign hypothesis registration requires 1 to 1001 unique IDs')
        now = datetime.now(UTC).isoformat()
        inserted = 0
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, policy = _campaign_policy(connection, campaign_id)
            if not policy.auto_hypotheses:
                raise ValueError('campaign automatic hypotheses are disabled')
            if worker_claim is None:
                if campaign['desired_state'] != 'PAUSED':
                    raise ValueError('pause campaign before registering hypotheses')
            elif (
                campaign['desired_state'] != 'RUNNING'
                or worker_claim.get('campaign_id') != campaign_id
                or not _campaign_worker_owned(connection, worker_claim, now)
            ):
                raise ValueError('automatic hypothesis worker is no longer active')
            sequence = connection.execute(
                'SELECT COALESCE(MAX(accepted_sequence),0) FROM research_campaign_hypotheses '
                'WHERE campaign_id=?', (campaign_id,),
            ).fetchone()[0]
            for hypothesis_id in hypothesis_ids:
                row = connection.execute(
                    'SELECT document_json FROM research_hypotheses WHERE hypothesis_id=?',
                    (hypothesis_id,),
                ).fetchone()
                if row is None:
                    raise ValueError('unknown research hypothesis')
                hypothesis = ResearchHypothesis.from_dict(json.loads(row[0]))
                if hypothesis.research_scope_id != f'campaign:{campaign_id}':
                    raise ValueError('research hypothesis belongs to another campaign scope')
                old = connection.execute(
                    'SELECT state FROM research_campaign_hypotheses '
                    'WHERE campaign_id=? AND hypothesis_id=?',
                    (campaign_id, hypothesis_id),
                ).fetchone()
                if old is not None:
                    continue
                count = connection.execute(
                    'SELECT COUNT(*) FROM research_campaign_hypotheses WHERE campaign_id=?',
                    (campaign_id,),
                ).fetchone()[0]
                if count >= policy.max_hypotheses:
                    raise ValueError('campaign hypothesis limit reached')
                sequence += 1
                connection.execute(
                    'INSERT INTO research_campaign_hypotheses('
                    'campaign_id,hypothesis_id,state,accepted_sequence,registered_at'
                    ") VALUES(?,?,'AVAILABLE',?,?)",
                    (campaign_id, hypothesis_id, sequence, now),
                )
                inserted += 1
            _refresh_campaign_state(connection, campaign_id, now)
        return inserted

    def load_next_campaign_hypotheses(
        self, campaign_id: str,
    ) -> tuple[ResearchHypothesis, ...]:
        """Return only the oldest AVAILABLE node per Family for bounded rotation."""
        with closing(self._connect()) as connection:
            _campaign_policy(connection, campaign_id)
            rows = connection.execute(
                '''SELECT document_json FROM (
                    SELECT h.document_json,q.accepted_sequence,
                           ROW_NUMBER() OVER(PARTITION BY h.family_id ORDER BY q.accepted_sequence) AS family_ordinal
                    FROM research_campaign_hypotheses q
                    JOIN research_hypotheses h ON h.hypothesis_id=q.hypothesis_id
                    WHERE q.campaign_id=? AND q.state='AVAILABLE'
                ) WHERE family_ordinal=1 ORDER BY accepted_sequence''',
                (campaign_id,),
            ).fetchall()
        return tuple(ResearchHypothesis.from_dict(json.loads(row[0])) for row in rows)

    def load_last_enqueued_campaign_hypothesis(
        self, campaign_id: str,
    ) -> ResearchHypothesis | None:
        with closing(self._connect()) as connection:
            _campaign_policy(connection, campaign_id)
            row = connection.execute(
                '''SELECT h.document_json FROM research_campaign_hypotheses q
                   JOIN research_hypotheses h ON h.hypothesis_id=q.hypothesis_id
                   WHERE q.campaign_id=? AND q.state='ENQUEUED'
                   ORDER BY q.enqueued_sequence DESC LIMIT 1''',
                (campaign_id,),
            ).fetchone()
        return ResearchHypothesis.from_dict(json.loads(row[0])) if row is not None else None

    def load_campaign_hypothesis_bindings(self, campaign_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            _campaign_policy(connection, campaign_id)
            rows = connection.execute(
                'SELECT q.*,h.family_id FROM research_campaign_hypotheses q '
                'JOIN research_hypotheses h ON h.hypothesis_id=q.hypothesis_id '
                'WHERE q.campaign_id=? ORDER BY q.accepted_sequence',
                (campaign_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def load_campaign_registered_hypotheses(
        self, campaign_id: str,
    ) -> tuple[ResearchHypothesis, ...]:
        with closing(self._connect()) as connection:
            _campaign_policy(connection, campaign_id)
            rows = connection.execute(
                'SELECT h.document_json FROM research_campaign_hypotheses q '
                'JOIN research_hypotheses h ON h.hypothesis_id=q.hypothesis_id '
                'WHERE q.campaign_id=? ORDER BY q.accepted_sequence',
                (campaign_id,),
            ).fetchall()
        return tuple(ResearchHypothesis.from_dict(json.loads(row[0])) for row in rows)

    def load_next_expandable_campaign_hypothesis(
        self, campaign_id: str,
    ) -> dict[str, Any] | None:
        """Return the oldest completed scheduled node with no terminal expansion record."""
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            _campaign_policy(connection, campaign_id)
            row = connection.execute(
                '''SELECT q.hypothesis_id,q.job_id,h.document_json,j.*
                   FROM research_campaign_hypotheses q
                   JOIN research_campaigns c ON c.campaign_id=q.campaign_id
                   JOIN research_hypotheses h ON h.hypothesis_id=q.hypothesis_id
                   JOIN research_campaign_jobs j
                     ON j.campaign_id=q.campaign_id AND j.job_id=q.job_id
                   LEFT JOIN research_campaign_hypothesis_expansions x
                     ON x.campaign_id=q.campaign_id
                    AND x.parent_hypothesis_id=q.hypothesis_id
                    AND x.policy_revision=c.revision
                   WHERE q.campaign_id=? AND q.state='ENQUEUED'
                     AND j.state='COMPLETED' AND x.parent_hypothesis_id IS NULL
                   ORDER BY q.enqueued_sequence LIMIT 1''',
                (campaign_id,),
            ).fetchone()
            if row is None:
                return None
            document = dict(row)
            hypothesis = ResearchHypothesis.from_dict(json.loads(document.pop('document_json')))
            return {
                **document,
                'hypothesis': hypothesis,
                'request': _campaign_job_spec(connection, row).to_dict(),
                'source_request': _campaign_source_spec(connection, row).to_dict(),
            }

    def record_campaign_hypothesis_expansion(
        self,
        campaign_id: str,
        parent_hypothesis_id: str,
        evidence: DevelopmentEvidenceSnapshot,
        *,
        state: str,
        generated_count: int,
        reason: str,
        worker_claim: Mapping[str, Any],
    ) -> bool:
        if state not in ('GENERATED', 'EXHAUSTED', 'BLOCKED'):
            raise ValueError('campaign hypothesis expansion state is invalid')
        if isinstance(generated_count, bool) or not isinstance(generated_count, int) or generated_count < 0:
            raise ValueError('campaign hypothesis generated count is invalid')
        if not isinstance(evidence, DevelopmentEvidenceSnapshot):
            raise ValueError('campaign hypothesis expansion requires development evidence')
        document = _canonical_json(evidence.to_dict())
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, policy = _campaign_policy(connection, campaign_id)
            if (
                not policy.auto_hypotheses
                or campaign['desired_state'] != 'RUNNING'
                or worker_claim.get('campaign_id') != campaign_id
                or not _campaign_worker_owned(connection, worker_claim, now)
            ):
                raise ValueError('automatic hypothesis worker is no longer active')
            values = (
                campaign_id, parent_hypothesis_id, evidence.evidence_id,
                campaign['revision'], state, generated_count, str(reason), document, now,
            )
            completed = connection.execute(
                '''SELECT 1 FROM research_campaign_hypotheses q
                   JOIN research_campaign_jobs j
                     ON j.campaign_id=q.campaign_id AND j.job_id=q.job_id
                   WHERE q.campaign_id=? AND q.hypothesis_id=?
                     AND q.state='ENQUEUED' AND j.state='COMPLETED' ''',
                (campaign_id, parent_hypothesis_id),
            ).fetchone()
            if completed is None:
                raise ValueError('only a completed campaign hypothesis can be expanded')
            old = connection.execute(
                'SELECT campaign_id,parent_hypothesis_id,evidence_id,policy_revision,state,generated_count,'
                'reason,evidence_json,created_at FROM research_campaign_hypothesis_expansions '
                'WHERE campaign_id=? AND parent_hypothesis_id=? AND evidence_id=? '
                'AND policy_revision=?',
                (campaign_id, parent_hypothesis_id, evidence.evidence_id, campaign['revision']),
            ).fetchone()
            if old is not None:
                if tuple(old)[:8] != values[:8]:
                    raise ValueError('immutable campaign hypothesis expansion changed')
                return False
            connection.execute(
                'INSERT INTO research_campaign_hypothesis_expansions('
                'campaign_id,parent_hypothesis_id,evidence_id,policy_revision,state,generated_count,'
                'reason,evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                values,
            )
            _refresh_campaign_state(connection, campaign_id, now)
            return True

    def load_campaign_hypothesis_expansions(
        self, campaign_id: str,
    ) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            _campaign_policy(connection, campaign_id)
            rows = connection.execute(
                'SELECT * FROM research_campaign_hypothesis_expansions '
                'WHERE campaign_id=? ORDER BY created_at,parent_hypothesis_id',
                (campaign_id,),
            ).fetchall()
        return tuple({**dict(row), 'evidence': json.loads(row['evidence_json'])} for row in rows)

    def claim_campaign_worker(self, campaign_id, *, owner_token, lease_seconds=30, now=None):
        moment = _campaign_clock(now)
        timestamp = moment.isoformat()
        if not owner_token.strip() or not 1 <= lease_seconds <= 3600:
            raise ValueError('worker owner/lease is invalid')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, _ = _campaign_policy(connection, campaign_id)
            worker = connection.execute('SELECT * FROM research_campaign_workers WHERE campaign_id=?', (campaign_id,)).fetchone()
            if campaign['desired_state'] != 'RUNNING' or worker['state'] == 'NEEDS_ATTENTION':
                return None
            if worker['state'] in ('STARTING', 'RUNNING'):
                if worker['lease_expires_at'] > timestamp:
                    return None
                _finish_campaign_worker(connection, worker, 'FAILED', 'worker_lease_expired', None, moment)
                return None
            if worker['next_retry_at'] > timestamp:
                return None
            generation = worker['generation'] + 1
            connection.execute("UPDATE research_campaign_workers SET generation=?,owner_token=?,state='STARTING',lease_expires_at=?,next_retry_at='',reason='',updated_at=? WHERE campaign_id=?",
                               (generation, owner_token, (moment + timedelta(seconds=lease_seconds)).isoformat(), timestamp, campaign_id))
            connection.execute("INSERT INTO research_campaign_worker_attempts(campaign_id,generation,owner_token,campaign_revision,state,started_at) VALUES(?,?,?,?,'STARTING',?)",
                               (campaign_id, generation, owner_token, campaign['revision'], timestamp))
            _refresh_campaign_state(connection, campaign_id, timestamp)
            return {'campaign_id': campaign_id, 'generation': generation, 'owner_token': owner_token}

    def renew_campaign_worker(self, campaign_id, *, owner_token, generation, lease_seconds=30, now=None):
        moment = _campaign_clock(now)
        timestamp = moment.isoformat()
        if not 1 <= lease_seconds <= 3600:
            raise ValueError('worker lease is invalid')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, _ = _campaign_policy(connection, campaign_id)
            worker = connection.execute('SELECT * FROM research_campaign_workers WHERE campaign_id=?', (campaign_id,)).fetchone()
            if campaign['desired_state'] != 'RUNNING' or worker['owner_token'] != owner_token or worker['generation'] != generation or worker['state'] not in ('STARTING', 'RUNNING') or worker['lease_expires_at'] <= timestamp:
                return False
            attempt = connection.execute('SELECT started_at FROM research_campaign_worker_attempts WHERE campaign_id=? AND generation=?', (campaign_id, generation)).fetchone()
            stable = (moment - datetime.fromisoformat(attempt[0])).total_seconds() >= 60
            connection.execute("UPDATE research_campaign_workers SET state='RUNNING',lease_expires_at=?,failure_count=?,updated_at=? WHERE campaign_id=?",
                               ((moment + timedelta(seconds=lease_seconds)).isoformat(), 0 if stable else worker['failure_count'], timestamp, campaign_id))
            connection.execute("UPDATE research_campaign_worker_attempts SET state='RUNNING' WHERE campaign_id=? AND generation=?", (campaign_id, generation))
            return True

    def finish_campaign_worker(self, campaign_id, *, owner_token, generation, outcome, reason='', exit_code=None, now=None):
        if outcome not in ('EXPECTED_EXIT', 'FAILED'):
            raise ValueError('worker outcome is invalid')
        moment = _campaign_clock(now)
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            _campaign_policy(connection, campaign_id)
            worker = connection.execute('SELECT * FROM research_campaign_workers WHERE campaign_id=?', (campaign_id,)).fetchone()
            if worker['owner_token'] != owner_token or worker['generation'] != generation or worker['state'] not in ('STARTING', 'RUNNING'):
                prior = connection.execute('SELECT owner_token,state FROM research_campaign_worker_attempts WHERE campaign_id=? AND generation=?', (campaign_id, generation)).fetchone()
                return bool(prior is not None and prior[0] == owner_token and prior[1] == outcome)
            _finish_campaign_worker(connection, worker, outcome, reason, exit_code, moment)
            return True

    def retry_campaign_worker(self, campaign_id):
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            _campaign_policy(connection, campaign_id)
            worker = connection.execute('SELECT * FROM research_campaign_workers WHERE campaign_id=?', (campaign_id,)).fetchone()
            if worker['state'] in ('STARTING', 'RUNNING'):
                raise ValueError('active worker cannot be retried manually')
            connection.execute("UPDATE research_campaign_workers SET state='IDLE',failure_count=0,next_retry_at='',reason='',updated_at=? WHERE campaign_id=?", (now, campaign_id))
            _refresh_campaign_state(connection, campaign_id, now)

    def load_campaign_worker(self, campaign_id):
        with closing(self._connect()) as connection:
            _campaign_policy(connection, campaign_id)
            return dict(connection.execute('SELECT * FROM research_campaign_workers WHERE campaign_id=?', (campaign_id,)).fetchone())

    def load_campaign_worker_attempts(self, campaign_id):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            return tuple(dict(row) for row in connection.execute('SELECT * FROM research_campaign_worker_attempts WHERE campaign_id=? ORDER BY generation', (campaign_id,)))

    def revise_campaign_policy(self, campaign_id, policy: ResearchCampaignPolicy, *, expected_revision: int) -> int:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, current = _campaign_policy(connection, campaign_id)
            if campaign['revision'] != expected_revision or campaign['desired_state'] == 'RUNNING':
                raise ValueError('pause campaign and refresh its revision before editing policy')
            if current == policy:
                return expected_revision
            revision = expected_revision + 1
            connection.execute('INSERT INTO research_campaign_revisions VALUES(?,?,?,?)',
                               (campaign_id, revision, _canonical_json(policy.to_dict()), now))
            connection.execute('UPDATE research_campaigns SET revision=?,updated_at=? WHERE campaign_id=?',
                               (revision, now, campaign_id))
            return revision

    def set_campaign_desired_state(self, campaign_id, desired_state: str):
        if desired_state not in ('RUNNING', 'PAUSED', 'STOPPED'):
            raise ValueError('campaign desired state is invalid')
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            _campaign_policy(connection, campaign_id)
            connection.execute('UPDATE research_campaigns SET desired_state=?,updated_at=? WHERE campaign_id=?',
                               (desired_state, now, campaign_id))
            _refresh_campaign_state(connection, campaign_id, now)

    def enqueue_campaign_experiment(
        self, campaign_id, spec: ExperimentSpec, input_path: Path, *,
        source_kind='hypothesis', input_source=None, source_spec=None,
        hypothesis_id='', worker_claim=None,
    ) -> bool:
        if source_kind not in ('hypothesis', 'new_data', 'auto_hypothesis'):
            raise ValueError('campaign job source kind is invalid')
        if source_kind != 'auto_hypothesis' and (hypothesis_id or worker_claim is not None):
            raise ValueError('automatic hypothesis ownership is valid only for automatic jobs')
        if 'development_partition' in spec.research_context:
            if source_spec is None:
                raise ValueError('development campaign requires a separately verified source request')
            partition = DevelopmentPartitionSpec.from_dict(source_spec.research_context.get('development_partition', {}))
            evaluation = ResearchEvaluationSpec.from_dict(source_spec.research_context.get('evaluation', {}))
            context = {**source_spec.research_context, 'evaluation': partition.evaluation_for(evaluation).to_dict()}
            expected = replace(source_spec, dataset_id=spec.dataset_id, dataset_hash=spec.dataset_hash, research_context=context)
            if expected.to_dict() != spec.to_dict() or not spec.dataset_id.startswith('development-'):
                raise ValueError('development campaign execution spec does not match source request')
        elif source_spec is not None:
            raise ValueError('separate source request is supported only for independent development campaigns')
        if spec.final_holdout_accessed_at:
            raise ValueError('campaign automatic final evaluation is disabled; use finite manual requests')
        identity = build_research_job_identity(spec)
        request_json = _canonical_json(spec.to_dict())
        source_json = _canonical_json(source_spec.to_dict()) if source_spec is not None else ''
        input_path = str(Path(input_path).resolve())
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, policy = _campaign_policy(connection, campaign_id)
            hypothesis = None
            if source_kind == 'auto_hypothesis':
                if not policy.auto_hypotheses or not hypothesis_id or worker_claim is None:
                    raise ValueError('automatic hypothesis scheduling is not enabled and owned')
                if (
                    campaign['desired_state'] != 'RUNNING'
                    or worker_claim.get('campaign_id') != campaign_id
                    or not _campaign_worker_owned(connection, worker_claim, now)
                ):
                    raise ValueError('automatic hypothesis worker is no longer active')
                queued = connection.execute(
                    'SELECT state,job_id FROM research_campaign_hypotheses '
                    'WHERE campaign_id=? AND hypothesis_id=?',
                    (campaign_id, hypothesis_id),
                ).fetchone()
                if queued is None:
                    raise ValueError('hypothesis is not registered in this campaign')
                if queued['state'] == 'ENQUEUED':
                    return False
                row = connection.execute(
                    'SELECT document_json FROM research_hypotheses WHERE hypothesis_id=?',
                    (hypothesis_id,),
                ).fetchone()
                hypothesis = ResearchHypothesis.from_dict(json.loads(row[0]))
                if hypothesis.research_scope_id != f'campaign:{campaign_id}':
                    raise ValueError('research hypothesis belongs to another campaign scope')
                _validate_campaign_hypothesis_spec(spec, hypothesis)
                if source_spec is not None:
                    _validate_campaign_hypothesis_spec(source_spec, hypothesis)
            if input_source is not None:
                source_id, fingerprint, worker_claim, manifest_hash = input_source
                source = connection.execute('SELECT * FROM research_campaign_input_sources WHERE source_id=? AND campaign_id=?', (source_id, campaign_id)).fetchone()
                if source is None or not source['enabled'] or worker_claim['campaign_id'] != campaign_id or not _campaign_worker_owned(connection, worker_claim, now) or _campaign_policy(connection, campaign_id)[0]['desired_state'] != 'RUNNING':
                    raise ValueError('input source or worker is no longer active')
                if connection.execute('SELECT 1 FROM research_campaign_input_acceptances WHERE source_id=? AND fingerprint=?', (source_id, fingerprint)).fetchone():
                    return False
                if connection.execute('SELECT 1 FROM research_campaign_input_acceptances WHERE source_id=? AND input_path=?', (source_id, input_path)).fetchone():
                    raise ValueError('accepted frozen input directory was modified')
            old = connection.execute('SELECT request_json,input_path,source_request_json,campaign_id,job_id,budget_revision FROM research_campaign_jobs WHERE campaign_id=? AND job_id=?',
                                     (campaign_id, identity.job_id)).fetchone()
            if old is not None:
                equivalent_source = input_source is not None and source_spec is not None and _canonical_json(_campaign_job_spec(connection, old).to_dict()) == request_json
                if not equivalent_source and tuple(old)[:3] != (request_json, input_path, source_json):
                    raise ValueError('campaign job inputs are immutable')
                if input_source is not None:
                    connection.execute('INSERT INTO research_campaign_input_acceptances VALUES(?,?,?,?,?)', (source_id, fingerprint, input_path, identity.job_id, manifest_hash))
                if hypothesis is not None:
                    _bind_campaign_hypothesis(connection, campaign_id, hypothesis_id, identity.job_id, now)
                    _refresh_campaign_state(connection, campaign_id, now)
                return False
            count = connection.execute("SELECT COUNT(*) FROM research_campaign_jobs WHERE campaign_id=? AND state!='COMPLETED'",
                                       (campaign_id,)).fetchone()[0]
            if count >= policy.max_active_jobs:
                raise ValueError('campaign active backlog limit reached')
            _save_search_experiment(connection, identity.experiment_id, _canonical_json(spec.evidence_dict()))
            _enqueue_search_job(connection, identity.job_id, identity.experiment_id, identity.dataset_id, identity.dataset_hash,
                                max_retained_jobs=policy.max_active_jobs, now=now)
            completed = connection.execute('SELECT status FROM research_search_jobs WHERE job_id=?', (identity.job_id,)).fetchone()[0] == 'completed'
            completion_reason = _campaign_completion_reason(connection, identity.job_id, request_json) if completed else ''
            if completion_reason == 'budget_expansion_required':
                raise ValueError('completed search budget is smaller than campaign target; explicit budget expansion is required')
            sequence = connection.execute('SELECT COALESCE(MAX(accepted_sequence),0)+1 FROM research_campaign_jobs WHERE campaign_id=?',
                                          (campaign_id,)).fetchone()[0]
            connection.execute('INSERT INTO research_campaign_jobs(campaign_id,job_id,source_kind,input_path,request_json,state,accepted_sequence,source_request_json) '
                               'VALUES(?,?,?,?,?,?,?,?)', (campaign_id, identity.job_id, source_kind, input_path, request_json,
                                                       'COMPLETED' if completed else 'PENDING', sequence, source_json))
            if completion_reason:
                connection.execute('UPDATE research_campaign_jobs SET reason=? WHERE campaign_id=? AND job_id=?',
                                   (completion_reason, campaign_id, identity.job_id))
            connection.execute('INSERT INTO research_campaign_job_budgets VALUES(?,?,1,?,?)',
                               (campaign_id, identity.job_id, _canonical_json(_operating_budget(spec)), now))
            if input_source is not None:
                connection.execute('INSERT INTO research_campaign_input_acceptances VALUES(?,?,?,?,?)', (source_id, fingerprint, input_path, identity.job_id, manifest_hash))
            if hypothesis is not None:
                _bind_campaign_hypothesis(connection, campaign_id, hypothesis_id, identity.job_id, now)
            _refresh_campaign_state(connection, campaign_id, now)
            return True

    def save_campaign_input_source(self, campaign_id, template_job_id, root: Path, *, enabled=True, nas_auto_prepare=False, nas_config_path=None, storage_cap_bytes=0, rolling_daily=False):
        root = str(Path(root).resolve())
        if isinstance(storage_cap_bytes, bool) or not isinstance(storage_cap_bytes, int) or not 0 <= storage_cap_bytes <= 1000000 * 1024 ** 3:
            raise ValueError('invalid research storage capacity')
        if nas_auto_prepare and nas_config_path is None:
            raise ValueError('NAS connection config path is required')
        config_path = str(Path(nas_config_path).resolve()) if nas_config_path is not None else ''
        source_id = hashlib.sha256(f'{campaign_id}\n{template_job_id}'.encode()).hexdigest()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, _ = _campaign_policy(connection, campaign_id)
            if campaign['desired_state'] == 'RUNNING' or connection.execute("SELECT 1 FROM research_campaign_workers WHERE campaign_id=? AND state IN ('STARTING','RUNNING') AND lease_expires_at>?", (campaign_id, datetime.now(UTC).isoformat())).fetchone():
                raise ValueError('pause campaign and wait for worker exit before configuring input sources')
            template = connection.execute('SELECT * FROM research_campaign_jobs WHERE campaign_id=? AND job_id=?', (campaign_id, template_job_id)).fetchone()
            if template is None:
                raise ValueError('unknown source template job')
            if rolling_daily:
                spec = _campaign_source_spec(connection, template)
                context = spec.research_context
                evaluation = ResearchEvaluationSpec.from_dict(context['evaluation'])
                if ('development_partition' in context or spec.final_holdout_accessed_at
                        or evaluation.final_holdout_accessed_at or len(evaluation.folds) != 1
                        or evaluation.folds[0].role not in ('TRAIN', 'VALIDATION')):
                    raise ValueError('rolling daily requires one unpartitioned TRAIN or VALIDATION fold without final access')
            if enabled and nas_auto_prepare:
                for other in connection.execute('SELECT source_id,root,storage_cap_bytes FROM research_campaign_input_sources WHERE enabled=1 AND nas_auto_prepare=1'):
                    if other['source_id'] == source_id:
                        continue
                    other_root = Path(other['root'])
                    overlaps = Path(root) == other_root or Path(root) in other_root.parents or other_root in Path(root).parents
                    if overlaps and (storage_cap_bytes or other['storage_cap_bytes']) and (Path(root) != other_root or storage_cap_bytes != other['storage_cap_bytes']):
                        raise ValueError('overlapping NAS research folders must use the same root and capacity; disable the other source before changing')
            connection.execute("INSERT INTO research_campaign_input_sources(source_id,campaign_id,template_job_id,root,enabled) VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET root=excluded.root,enabled=excluded.enabled,state='READY',failure_count=0,next_scan_at='',reason=''", (source_id, campaign_id, template_job_id, root, int(enabled)))
            connection.execute("UPDATE research_campaign_input_sources SET nas_auto_prepare=?,nas_config_path=?,remote_signature='',storage_cap_bytes=?,rolling_daily=?,rolling_next_start='' WHERE source_id=?", (int(nas_auto_prepare), config_path, storage_cap_bytes, int(rolling_daily), source_id))
            connection.execute('DELETE FROM research_campaign_rolling_empty_days WHERE source_id=?', (source_id,))
        return source_id

    def advance_campaign_rolling_day(self, source_id, expected_start, next_start, worker_claim, *, empty_checked_at=None):
        """Commit a closed day only after it was accepted or verified empty."""
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.row_factory = sqlite3.Row
            source = connection.execute('SELECT * FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            now = datetime.now(UTC).isoformat()
            if (source is None or not source['enabled'] or not source['nas_auto_prepare'] or not source['rolling_daily']
                    or source['campaign_id'] != worker_claim['campaign_id'] or not _campaign_worker_owned(connection, worker_claim, now)
                    or _campaign_policy(connection, source['campaign_id'])[0]['desired_state'] != 'RUNNING'):
                raise ValueError('rolling source or worker is no longer active')
            baseline = datetime.fromisoformat(json.loads(source['scope_json'])['captured_range']['start'])
            current = source['rolling_next_start'] or (baseline + timedelta(days=1)).isoformat()
            if current != expected_start or datetime.fromisoformat(next_start) - datetime.fromisoformat(expected_start) != timedelta(days=1):
                raise ValueError('rolling day cursor changed or is not consecutive')
            if empty_checked_at is not None:
                checked = datetime.fromisoformat(empty_checked_at)
                if checked.tzinfo is None:
                    raise ValueError('empty day check timestamp requires a timezone')
                connection.execute('INSERT INTO research_campaign_rolling_empty_days VALUES(?,?,?,?,?)',
                                   (source_id, expected_start, 1, empty_checked_at, (checked + timedelta(days=1)).isoformat()))
            connection.execute('UPDATE research_campaign_input_sources SET rolling_next_start=? WHERE source_id=?', (next_start, source_id))

    def load_due_campaign_rolling_empty_day(self, source_id, *, now):
        """Allow one historical recheck per ten minutes while new days progress."""
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            last = connection.execute('SELECT MAX(last_checked_at) FROM research_campaign_rolling_empty_days WHERE source_id=?', (source_id,)).fetchone()[0]
            if last is not None and datetime.fromisoformat(last) + timedelta(minutes=10) > now:
                return None
            row = connection.execute('SELECT * FROM research_campaign_rolling_empty_days '
                                     'WHERE source_id=? AND next_retry_at<=? ORDER BY next_retry_at,range_start LIMIT 1',
                                     (source_id, now.isoformat())).fetchone()
            return dict(row) if row is not None else None

    def finish_campaign_rolling_empty_day(self, source_id, range_start, worker_claim, *, checked_at, fingerprint=None):
        """Remove a retry only after its frozen evidence has been accepted."""
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.row_factory = sqlite3.Row
            source = connection.execute('SELECT * FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            now = datetime.now(UTC).isoformat()
            if (source is None or not source['enabled'] or not source['nas_auto_prepare'] or not source['rolling_daily']
                    or source['campaign_id'] != worker_claim['campaign_id'] or not _campaign_worker_owned(connection, worker_claim, now)
                    or _campaign_policy(connection, source['campaign_id'])[0]['desired_state'] != 'RUNNING'):
                raise ValueError('rolling source or worker is no longer active')
            row = connection.execute('SELECT * FROM research_campaign_rolling_empty_days WHERE source_id=? AND range_start=?',
                                     (source_id, range_start)).fetchone()
            if row is None:
                raise ValueError('rolling empty day is no longer pending')
            if fingerprint is not None:
                if connection.execute('SELECT 1 FROM research_campaign_input_acceptances WHERE source_id=? AND fingerprint=?',
                                      (source_id, fingerprint)).fetchone() is None:
                    raise ValueError('rolling retry has no accepted frozen input')
                connection.execute('DELETE FROM research_campaign_rolling_empty_days WHERE source_id=? AND range_start=?',
                                   (source_id, range_start))
            else:
                checked = datetime.fromisoformat(checked_at)
                if checked.tzinfo is None:
                    raise ValueError('empty day check timestamp requires a timezone')
                connection.execute('UPDATE research_campaign_rolling_empty_days SET attempts=attempts+1,last_checked_at=?,next_retry_at=? '
                                   'WHERE source_id=? AND range_start=?',
                                   (checked_at, (checked + timedelta(days=1)).isoformat(), source_id, range_start))

    def begin_campaign_storage_preparation(self, source_id, worker_claim):
        """Serialize PC NAS file preparation; stale ownership is abandoned, never deleted."""
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            source = connection.execute('SELECT * FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            if source is None or not source['enabled'] or not source['nas_auto_prepare'] or source['campaign_id'] != worker_claim['campaign_id'] or not _campaign_worker_owned(connection, worker_claim, now):
                raise ValueError('storage preparation worker is no longer active')
            if connection.execute('SELECT desired_state FROM research_campaigns WHERE campaign_id=?', (source['campaign_id'],)).fetchone()[0] != 'RUNNING':
                raise ValueError('storage preparation campaign is not running')
            active = connection.execute("SELECT * FROM research_campaign_storage_operations WHERE state='PREPARING'").fetchall()
            cutoff = (datetime.fromisoformat(now) - timedelta(seconds=RESEARCH_STORAGE_PREPARATION_SECONDS)).isoformat()
            for operation in active:
                if operation['started_at'] > cutoff and _campaign_worker_owned(connection, dict(operation), now):
                    return None
            for operation in active:
                connection.execute("UPDATE research_campaign_storage_operations SET state='ABANDONED',finished_at=?,reason='preparation ownership expired' WHERE operation_id=?", (now, operation['operation_id']))
            operation_id = str(uuid.uuid4())
            connection.execute('INSERT INTO research_campaign_storage_operations(operation_id,source_id,campaign_id,root,storage_cap_bytes,owner_token,generation,state,started_at) VALUES(?,?,?,?,?,?,?,?,?)',
                               (operation_id, source_id, source['campaign_id'], source['root'], source['storage_cap_bytes'], worker_claim['owner_token'], worker_claim['generation'], 'PREPARING', now))
            return operation_id

    def require_campaign_storage_preparation(self, operation_id, worker_claim):
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            _require_campaign_storage_owned(connection, operation_id, worker_claim, now)

    def record_campaign_storage_staging(self, operation_id, worker_claim, path):
        path = Path(path).resolve()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            _require_campaign_storage_owned(connection, operation_id, worker_claim, datetime.now(UTC).isoformat())
            root = connection.execute('SELECT root FROM research_campaign_storage_operations WHERE operation_id=?', (operation_id,)).fetchone()[0]
            if path.parent != Path(root) or not path.name.startswith('.nas-preparing-'):
                raise ValueError('storage staging path is outside source root')
            connection.execute('UPDATE research_campaign_storage_operations SET staging_path=? WHERE operation_id=?', (str(path), operation_id))

    @contextmanager
    def campaign_storage_publication(self, operation_id, worker_claim):
        """Hold the short database fence over publication, never over download/validation."""
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            _require_campaign_storage_owned(connection, operation_id, worker_claim, datetime.now(UTC).isoformat())
            yield

    def finish_campaign_storage_preparation(self, operation_id, worker_claim, *, outcome, input_path=''):
        if outcome not in ('PUBLISHED', 'UNCHANGED', 'BLOCKED', 'FAILED', 'CANCELLED'):
            raise ValueError('invalid storage operation outcome')
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("UPDATE research_campaign_storage_operations SET state=?,finished_at=?,input_path=?,reason=? WHERE operation_id=? AND campaign_id=? AND owner_token=? AND generation=? AND state='PREPARING'",
                                        (outcome, datetime.now(UTC).isoformat(), str(input_path or ''), outcome.lower(), operation_id, worker_claim['campaign_id'], worker_claim['owner_token'], worker_claim['generation']))
            return cursor.rowcount == 1

    def load_campaign_storage_operations(self, source_id, *, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError('invalid storage ledger limit')
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            return tuple(dict(row) for row in connection.execute('SELECT * FROM research_campaign_storage_operations WHERE source_id=? ORDER BY started_at DESC,operation_id DESC LIMIT ?', (source_id, limit)))

    def record_campaign_storage_wait(self, source_id, *, capacity=False, now=None):
        moment = _campaign_clock(now)
        reason = '연구 자료 폴더 용량 상한 도달 · 새 자료 준비 대기' if capacity else '다른 연구 자료 준비 완료 대기'
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE research_campaign_input_sources SET state='WAITING_STORAGE',failure_count=0,next_scan_at=?,reason=? WHERE source_id=?", ((moment + timedelta(seconds=60)).isoformat(), reason, source_id))

    def load_campaign_staging_cleanups(self, source_id, *, limit=100):
        if not 1 <= limit <= 1000:
            raise ValueError('invalid cleanup ledger limit')
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            return tuple(dict(row) for row in connection.execute('SELECT * FROM research_campaign_staging_cleanups WHERE source_id=? ORDER BY updated_at DESC,operation_id LIMIT ?', (source_id, limit)))

    def cleanup_campaign_incomplete_staging(self, source_id, worker_claim, *, checkpoint=lambda: None, now=None):
        """Remove old known partial files only; complete inputs remain unconditionally protected."""
        from kiwoom_monitor.infrastructure.research_storage import (
            inspect_incomplete_research_staging, delete_incomplete_research_staging, ResearchStagingCleanupYield,
        )
        moment = _campaign_clock(now)
        instant = moment.isoformat()
        cutoff = moment - timedelta(hours=24)
        result = {'deleted': 0, 'missing': 0, 'protected': 0, 'yielded': 0, 'errors': []}

        def authorized(connection, operation_id):
            connection.row_factory = sqlite3.Row
            source = connection.execute('SELECT * FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            operation = connection.execute('SELECT * FROM research_campaign_storage_operations WHERE operation_id=? AND source_id=?', (operation_id, source_id)).fetchone()
            if source is None or operation is None or not source['enabled'] or not source['nas_auto_prepare'] or source['root'] != operation['root'] or source['campaign_id'] != worker_claim['campaign_id']:
                return None
            if not _campaign_worker_owned(connection, worker_claim, datetime.now(UTC).isoformat()) or _campaign_policy(connection, source['campaign_id'])[0]['desired_state'] != 'RUNNING':
                return None
            if operation['state'] in ('PREPARING', 'ABANDONED') and _campaign_worker_owned(connection, dict(operation), datetime.now(UTC).isoformat()):
                return None
            return dict(operation)

        def record(operation, inspection, state, reason=''):
            with closing(self._connect()) as connection, connection:
                connection.execute('BEGIN IMMEDIATE')
                if authorized(connection, operation['operation_id']) is None:
                    return False
                current = connection.execute('SELECT root,staging_path FROM research_campaign_storage_operations WHERE operation_id=?', (operation['operation_id'],)).fetchone()
                if tuple(current) != (operation['root'], operation['staging_path']):
                    return False
                previous = connection.execute('SELECT * FROM research_campaign_staging_cleanups WHERE operation_id=?', (operation['operation_id'],)).fetchone()
                if previous is not None and previous['state'] in ('DELETED', 'MISSING', 'PROTECTED'):
                    return False
                failures = (previous['failure_count'] if previous else 0) + int(state == 'FAILED')
                retry = (moment + timedelta(seconds=min(3600, 60 * 2 ** min(6, max(0, failures - 1))))).isoformat() if state == 'FAILED' else ''
                marker_hash = (previous['marker_hash'] if previous else '') or inspection.get('marker_hash', '')
                connection.execute('INSERT INTO research_campaign_staging_cleanups(operation_id,source_id,path,marker_hash,state,failure_count,next_retry_at,bytes_expected,reason,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operation_id) DO UPDATE SET state=excluded.state,failure_count=excluded.failure_count,next_retry_at=excluded.next_retry_at,reason=excluded.reason,updated_at=excluded.updated_at',
                                   (operation['operation_id'], source_id, operation['staging_path'], marker_hash, state, failures, retry, inspection.get('bytes', 0), reason, instant))
                return True

        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            source = connection.execute('SELECT root FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            if source is None:
                raise ValueError('unknown cleanup source')
            operations = tuple(dict(row) for row in connection.execute("SELECT o.* FROM research_campaign_storage_operations o LEFT JOIN research_campaign_staging_cleanups c ON c.operation_id=o.operation_id WHERE o.source_id=? AND o.root=? AND o.staging_path!='' AND (c.operation_id IS NULL OR (c.state IN ('READY','FAILED') AND c.next_retry_at<=?)) ORDER BY o.started_at,o.operation_id LIMIT 20", (source_id, source['root'], instant)))

        for operation in operations:
            checkpoint()
            try:
                times = [datetime.fromisoformat(operation[key]) for key in ('started_at', 'finished_at') if operation[key]]
            except ValueError:
                record(operation, {}, 'PROTECTED', 'operation_time_unverified')
                result['protected'] += 1
                continue
            if any(value.tzinfo is None for value in times) or max(times) > cutoff:
                continue
            with closing(self._connect()) as connection:
                if authorized(connection, operation['operation_id']) is None:
                    result['protected'] += 1
                    continue
                prior = connection.execute('SELECT marker_hash FROM research_campaign_staging_cleanups WHERE operation_id=?', (operation['operation_id'],)).fetchone()
            inspection = {}
            try:
                inspection = inspect_incomplete_research_staging(operation['root'], operation['staging_path'], source_id, operation['operation_id'], marker_hash=prior[0] if prior else '', checkpoint=checkpoint)
                if inspection.get('created_at', cutoff) > cutoff:
                    continue
                if inspection['missing']:
                    record(operation, inspection, 'MISSING', 'path absent; no deletion inferred')
                    result['missing'] += 1
                    continue
                if not record(operation, inspection, 'READY'):
                    continue
                checkpoint()  # May heartbeat/cancel, always outside the DB write fence.
                deadline = time.monotonic() + .25
                def bounded_check():
                    if time.monotonic() >= deadline:
                        raise ResearchStagingCleanupYield()
                with closing(self._connect()) as connection, connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if authorized(connection, operation['operation_id']) is None:
                        result['protected'] += 1
                        continue
                    current = connection.execute('SELECT root,staging_path FROM research_campaign_storage_operations WHERE operation_id=?', (operation['operation_id'],)).fetchone()
                    if tuple(current) != (operation['root'], operation['staging_path']):
                        result['protected'] += 1
                        continue
                    bounded_check()
                    target = Path(operation['staging_path']).resolve()
                    for row in connection.execute("SELECT input_path FROM research_campaign_jobs UNION SELECT input_path FROM research_campaign_input_acceptances UNION SELECT input_path FROM research_campaign_storage_operations WHERE input_path!='' UNION SELECT staging_path FROM research_campaign_storage_operations WHERE operation_id!=? AND state='PREPARING' AND staging_path!=''", (operation['operation_id'],)):
                        bounded_check()
                        reference = Path(row[0]).resolve()
                        if target == reference or target in reference.parents or reference in target.parents:
                            raise ValueError('research_reference_preserved')
                    inspection = inspect_incomplete_research_staging(operation['root'], operation['staging_path'], source_id, operation['operation_id'], marker_hash=inspection['marker_hash'], checkpoint=bounded_check)
                    if inspection['missing']:
                        raise ValueError('staging_changed_before_delete')
                    delete_incomplete_research_staging(operation['root'], operation['staging_path'], inspection, checkpoint=bounded_check)
                    connection.execute("UPDATE research_campaign_staging_cleanups SET state='DELETED',next_retry_at='',reason='known partial files removed',updated_at=? WHERE operation_id=?", (instant, operation['operation_id']))
                result['deleted'] += 1
            except ResearchStagingCleanupYield:
                result['yielded'] += 1
            except ValueError as exc:
                record(operation, inspection, 'PROTECTED', str(exc))
                result['protected'] += 1
            except InterruptedError:
                raise
            except OSError as exc:
                record(operation, inspection, 'FAILED', type(exc).__name__)
                result['errors'].append(f'임시 자료 정리 대기 · {type(exc).__name__}')
            checkpoint()
        return result

    def record_campaign_prepared_input(self, source_id, signature, worker_claim):
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.row_factory = sqlite3.Row
            source = connection.execute('SELECT * FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            now = datetime.now(UTC).isoformat()
            if source is None or not source['enabled'] or not source['nas_auto_prepare'] or worker_claim['campaign_id'] != source['campaign_id'] or not _campaign_worker_owned(connection, worker_claim, now) or _campaign_policy(connection, source['campaign_id'])[0]['desired_state'] != 'RUNNING':
                raise ValueError('input source or worker is no longer active')
            connection.execute('UPDATE research_campaign_input_sources SET remote_signature=? WHERE source_id=?', (signature, source_id))

    def load_campaign_input_sources(self, campaign_id):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            return tuple(dict(row) for row in connection.execute('SELECT * FROM research_campaign_input_sources WHERE campaign_id=? ORDER BY source_id', (campaign_id,)))

    def load_campaign_input_acceptances(self, source_id):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            return tuple(dict(row) for row in connection.execute('SELECT * FROM research_campaign_input_acceptances WHERE source_id=?', (source_id,)))

    def initialize_campaign_input_source(self, source_id, scope, fingerprint, input_path, job_id, manifest_hash, worker_claim):
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT scope_json,campaign_id,enabled FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            now = datetime.now(UTC).isoformat()
            if old is None or not old[2] or worker_claim['campaign_id'] != old[1] or not _campaign_worker_owned(connection, worker_claim, now) or _campaign_policy(connection, old[1])[0]['desired_state'] != 'RUNNING':
                raise ValueError('input source or worker is no longer active')
            document = _canonical_json(scope)
            if old is None or (old[0] and old[0] != document):
                raise ValueError('source scope is immutable')
            connection.execute('UPDATE research_campaign_input_sources SET scope_json=? WHERE source_id=?', (document, source_id))
            connection.execute('INSERT OR IGNORE INTO research_campaign_input_acceptances VALUES(?,?,?,?,?)', (source_id, fingerprint, str(Path(input_path).resolve()), job_id, manifest_hash))

    def inspect_campaign_input_storage(self, source_id, *, checkpoint=lambda: None, max_entries=10000):
        """All campaign states remain references, including completed evidence.

        This read snapshot cannot authorize deletion or exclude external journals.
        """
        from kiwoom_monitor.infrastructure.research_storage import inventory_research_storage
        with closing(self._connect()) as connection:
            source = connection.execute('SELECT root FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            if source is None:
                raise ValueError('unknown campaign input source')
            references = tuple(row[0] for row in connection.execute(
                'SELECT input_path FROM research_campaign_jobs UNION SELECT input_path FROM research_campaign_input_acceptances'))
        return inventory_research_storage(Path(source[0]), source_id, references, checkpoint=checkpoint, max_entries=max_entries)

    def record_campaign_input_scan(self, source_id, *, error='', now=None):
        moment = _campaign_clock(now)
        with closing(self._connect()) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute('BEGIN IMMEDIATE')
            source = connection.execute('SELECT * FROM research_campaign_input_sources WHERE source_id=?', (source_id,)).fetchone()
            _, policy = _campaign_policy(connection, source['campaign_id'])
            failures = source['failure_count'] + 1 if error else 0
            state = 'NEEDS_ATTENTION' if error and failures >= policy.max_attempts else 'BACKOFF' if error else 'READY'
            next_scan = (moment + timedelta(seconds=policy.retry_seconds(failures) if error else 60)).isoformat() if state != 'NEEDS_ATTENTION' else ''
            connection.execute('UPDATE research_campaign_input_sources SET state=?,failure_count=?,next_scan_at=?,reason=? WHERE source_id=?', (state, failures, next_scan, error, source_id))

    def revise_campaign_job_budget(self, campaign_id, job_id, spec: ExperimentSpec, *, expected_revision: int) -> int:
        """Explicit operating edit; frozen scientific spec and completed trials are untouched."""
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, policy = _campaign_policy(connection, campaign_id)
            job = connection.execute('SELECT * FROM research_campaign_jobs WHERE campaign_id=? AND job_id=?',
                                     (campaign_id, job_id)).fetchone()
            if job is None:
                raise ValueError('unknown campaign job')
            current = _campaign_job_spec(connection, job)
            if campaign['desired_state'] == 'RUNNING' or job['budget_revision'] != expected_revision:
                raise ValueError('pause campaign and refresh budget revision before editing')
            if (job['state'] == 'RUNNING' and job['lease_expires_at'] > now) or connection.execute(
                "SELECT 1 FROM research_search_jobs WHERE job_id=? AND status='running' AND lease_expires_at>?", (job_id, now)
            ).fetchone():
                raise ValueError('wait for the active worker to finish before editing budget')
            if _canonical_json(current.evidence_dict()) != _canonical_json(spec.evidence_dict()):
                raise ValueError('budget revision must not change scientific evidence')
            allowed = {'memory_mb', 'cpu_duty_percent', 'max_retained_jobs'}
            if {k: v for k, v in current.resource_budget.items() if k not in allowed} != {
                k: v for k, v in spec.resource_budget.items() if k not in allowed
            } or spec.max_trials < current.max_trials:
                raise ValueError('budget revision must preserve generated candidates and must not reduce trial limit')
            if _operating_budget(current) == _operating_budget(spec) and job['reason'] != 'budget_expansion_required':
                return expected_revision
            used = connection.execute('SELECT COUNT(*) FROM research_search_trials WHERE experiment_id=?', (spec.experiment_id,)).fetchone()[0]
            remaining = used < min(spec.max_trials, len(generate_trials(spec)))
            search_completed = connection.execute('SELECT status FROM research_search_jobs WHERE job_id=?', (job_id,)).fetchone()[0] == 'completed'
            if remaining and job['state'] == 'COMPLETED':
                count = connection.execute("SELECT COUNT(*) FROM research_campaign_jobs WHERE campaign_id=? AND state!='COMPLETED'", (campaign_id,)).fetchone()[0]
                if count >= policy.max_active_jobs:
                    raise ValueError('campaign active backlog limit reached')
            if remaining and search_completed:
                active = connection.execute("SELECT COUNT(*) FROM research_search_jobs WHERE status IN ('queued','running')").fetchone()[0]
                if active >= min(policy.max_active_jobs, int(spec.resource_budget.get('max_retained_jobs', 100))):
                    raise ValueError('research active backlog limit reached')
            revision = expected_revision + 1
            connection.execute('INSERT INTO research_campaign_job_budgets VALUES(?,?,?,?,?)',
                               (campaign_id, job_id, revision, _canonical_json(_operating_budget(spec)), now))
            state = 'COMPLETED' if search_completed and not remaining else 'PENDING'
            reason = _campaign_completion_reason(connection, job_id, _canonical_json(spec.to_dict()), verify_trial_rows=True) if state == 'COMPLETED' else ''
            connection.execute("UPDATE research_campaign_jobs SET budget_revision=?,state=?,reason=?,next_attempt_at='',"
                               "failure_count=0,attempt_count=0,owner_token='',lease_expires_at='' WHERE campaign_id=? AND job_id=?",
                               (revision, state, reason, campaign_id, job_id))
            connection.execute("UPDATE research_campaign_cycles SET state='INTERRUPTED',finished_at=?,reason='operating_budget_revised' "
                               "WHERE campaign_id=? AND job_id=? AND state='RUNNING'", (now, campaign_id, job_id))
            connection.execute('UPDATE research_campaigns SET updated_at=? WHERE campaign_id=?', (now, campaign_id))
            _refresh_campaign_state(connection, campaign_id, now)
            return revision

    def load_campaign_job_budget_revisions(self, campaign_id, job_id):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT * FROM research_campaign_job_budgets WHERE campaign_id=? AND job_id=? ORDER BY revision',
                                      (campaign_id, job_id)).fetchall()
            return tuple({**dict(row), 'budget': json.loads(row['budget_json'])} for row in rows)

    def claim_campaign_cycle(self, campaign_id, *, owner_token: str, lease_seconds=30, now: datetime | None = None):
        moment = _campaign_clock(now)
        timestamp = moment.isoformat()
        if not owner_token.strip() or not 1 <= lease_seconds <= 3600:
            raise ValueError('campaign owner/lease is invalid')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, policy = _campaign_policy(connection, campaign_id)
            if campaign['desired_state'] != 'RUNNING':
                return None
            recovered = connection.execute("SELECT * FROM research_campaign_jobs WHERE campaign_id=? "
                                           "AND (state='PENDING' OR (state='RUNNING' AND lease_expires_at<=?)) "
                                           "AND job_id IN (SELECT job_id FROM research_search_jobs WHERE status='completed')",
                                           (campaign_id, timestamp)).fetchall()
            for completed in recovered:
                completion_reason = _campaign_completion_reason(connection, completed['job_id'], _canonical_json(_campaign_job_spec(connection, completed).to_dict()),
                                                                 verify_trial_rows=completed['budget_revision'] > 1)
                if completion_reason == 'budget_expansion_required' and completed['budget_revision'] > 1:
                    continue
                recovered_state = 'NEEDS_ATTENTION' if completion_reason == 'budget_expansion_required' else 'COMPLETED'
                connection.execute("UPDATE research_campaign_jobs SET state=?,reason=?,owner_token='',lease_expires_at='' WHERE campaign_id=? AND job_id=?",
                                   (recovered_state, completion_reason, campaign_id, completed['job_id']))
                connection.execute("UPDATE research_campaign_cycles SET state=?,finished_at=?,reason=? "
                                   "WHERE campaign_id=? AND job_id=? AND state='RUNNING'",
                                   ('FAILED' if recovered_state == 'NEEDS_ATTENTION' else 'COMPLETED', timestamp,
                                    'completed_search_job_recovered' if completion_reason == 'registered_jobs_completed' else completion_reason,
                                    campaign_id, completed['job_id']))
            # One active cycle per campaign; search jobs have their own existing owner fence.
            busy = connection.execute("SELECT 1 FROM research_campaign_jobs WHERE campaign_id=? AND state='RUNNING' AND lease_expires_at>?",
                                      (campaign_id, timestamp)).fetchone()
            if busy:
                return None
            job = connection.execute("SELECT * FROM research_campaign_jobs WHERE campaign_id=? AND "
                                     "((state='RUNNING' AND lease_expires_at<=?) OR (state='PENDING' AND next_attempt_at<=?)) "
                                     "AND job_id NOT IN (SELECT job_id FROM research_search_jobs WHERE status='running' AND lease_expires_at>?) "
                                     "ORDER BY CASE WHEN attempt_count>0 THEN 0 WHEN source_kind='hypothesis' THEN 1 ELSE 2 END, accepted_sequence LIMIT 1",
                                     (campaign_id, timestamp, timestamp, timestamp)).fetchone()
            if job is None:
                _refresh_campaign_state(connection, campaign_id, timestamp)
                return None
            job = dict(job)
            if job['state'] == 'RUNNING':
                connection.execute("UPDATE research_campaign_cycles SET state='INTERRUPTED',finished_at=?,reason='lease_expired' "
                                   "WHERE campaign_id=? AND job_id=? AND state='RUNNING'", (timestamp, campaign_id, job['job_id']))
            generation, sequence = job['generation'] + 1, campaign['cycle_sequence'] + 1
            expires = (moment + timedelta(seconds=lease_seconds)).isoformat()
            connection.execute("UPDATE research_campaign_jobs SET state='RUNNING',attempt_count=attempt_count+1,generation=?,owner_token=?,"
                               "lease_expires_at=?,reason='' WHERE campaign_id=? AND job_id=?",
                               (generation, owner_token, expires, campaign_id, job['job_id']))
            connection.execute("UPDATE research_campaigns SET cycle_sequence=?,operational_state='RUNNING',reason='',updated_at=? WHERE campaign_id=?",
                               (sequence, timestamp, campaign_id))
            connection.execute('INSERT INTO research_campaign_cycles(campaign_id,sequence,campaign_revision,job_id,generation,owner_token,state,started_at) '
                               "VALUES(?,?,?,?,?,?,'RUNNING',?)", (campaign_id, sequence, campaign['revision'], job['job_id'], generation, owner_token, timestamp))
            connection.execute('UPDATE research_campaign_cycles SET budget_revision=? WHERE campaign_id=? AND sequence=?',
                               (job['budget_revision'], campaign_id, sequence))
            return dict(campaign_id=campaign_id, sequence=sequence, campaign_revision=campaign['revision'], job_id=job['job_id'],
                        budget_revision=job['budget_revision'], generation=generation, owner_token=owner_token,
                        input_path=job['input_path'], request=_campaign_job_spec(connection, job).to_dict(),
                        source_request=_campaign_source_spec(connection, job).to_dict())

    def renew_campaign_cycle(self, campaign_id, sequence, *, owner_token, generation, lease_seconds=30, now=None) -> bool:
        moment = _campaign_clock(now)
        if not 1 <= lease_seconds <= 3600:
            raise ValueError('campaign lease is invalid')
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            campaign, _ = _campaign_policy(connection, campaign_id)
            if campaign['desired_state'] != 'RUNNING':
                return False
            job_id = _campaign_owned_job(connection, campaign_id, sequence, owner_token, generation, moment.isoformat())
            if job_id is None:
                return False
            connection.execute('UPDATE research_campaign_jobs SET lease_expires_at=? WHERE campaign_id=? AND job_id=?',
                               ((moment + timedelta(seconds=lease_seconds)).isoformat(), campaign_id, job_id))
            return True

    def finish_campaign_cycle(self, campaign_id, sequence, *, owner_token, generation, outcome: str, reason='', now=None) -> bool:
        if outcome not in ('COMPLETED', 'INTERRUPTED', 'FAILED', 'RESOURCE_BLOCKED'):
            raise ValueError('campaign cycle outcome is invalid')
        moment = _campaign_clock(now)
        timestamp = moment.isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            _campaign_policy(connection, campaign_id)
            job_id = _campaign_owned_job(connection, campaign_id, sequence, owner_token, generation, timestamp)
            if job_id is None:
                return False
            revision = connection.execute('SELECT campaign_revision FROM research_campaign_cycles WHERE campaign_id=? AND sequence=?',
                                          (campaign_id, sequence)).fetchone()[0]
            _, policy = _campaign_policy(connection, campaign_id, revision=revision)
            job = connection.execute('SELECT failure_count FROM research_campaign_jobs WHERE campaign_id=? AND job_id=?', (campaign_id, job_id)).fetchone()
            state, next_attempt = 'PENDING', ''
            if outcome == 'COMPLETED':
                if connection.execute('SELECT status FROM research_search_jobs WHERE job_id=?', (job_id,)).fetchone()[0] != 'completed':
                    raise ValueError('campaign completion requires a completed search job')
                work = connection.execute('SELECT * FROM research_campaign_jobs WHERE campaign_id=? AND job_id=?',
                                          (campaign_id, job_id)).fetchone()
                request_json = _canonical_json(_campaign_job_spec(connection, work).to_dict())
                reason = _campaign_completion_reason(connection, job_id, request_json, verify_trial_rows=work['budget_revision'] > 1)
                state = 'NEEDS_ATTENTION' if reason == 'budget_expansion_required' else 'COMPLETED'
                if state == 'NEEDS_ATTENTION':
                    outcome = 'FAILED'
            elif outcome == 'RESOURCE_BLOCKED':
                state = 'RESOURCE_BLOCKED'
            elif outcome == 'FAILED':
                failure_count = job[0] + 1
                connection.execute('UPDATE research_campaign_jobs SET failure_count=? WHERE campaign_id=? AND job_id=?', (failure_count, campaign_id, job_id))
                state = 'NEEDS_ATTENTION' if failure_count >= policy.max_attempts else 'PENDING'
                next_attempt = (moment + timedelta(seconds=policy.retry_seconds(failure_count))).isoformat()
            connection.execute('UPDATE research_campaign_jobs SET state=?,next_attempt_at=?,reason=?,owner_token=\'\',lease_expires_at=\'\' '
                               'WHERE campaign_id=? AND job_id=?', (state, next_attempt, reason, campaign_id, job_id))
            connection.execute('UPDATE research_campaign_cycles SET state=?,reason=?,finished_at=? WHERE campaign_id=? AND sequence=?',
                               (outcome, reason, timestamp, campaign_id, sequence))
            _refresh_campaign_state(connection, campaign_id, timestamp)
            return True

    def retry_campaign_job(self, campaign_id, job_id):
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            _campaign_policy(connection, campaign_id)
            changed = connection.execute("UPDATE research_campaign_jobs SET state='PENDING',attempt_count=0,failure_count=0,next_attempt_at='',reason='' "
                                         "WHERE campaign_id=? AND job_id=? AND state IN ('RESOURCE_BLOCKED','NEEDS_ATTENTION')", (campaign_id, job_id)).rowcount
            if not changed:
                raise ValueError('only a blocked campaign job can be explicitly retried')
            _refresh_campaign_state(connection, campaign_id, datetime.now(UTC).isoformat())

    def load_campaign(self, campaign_id):
        with closing(self._connect()) as connection:
            campaign, policy = _campaign_policy(connection, campaign_id)
            return {**dict(campaign), 'policy': policy.to_dict()}

    def load_campaign_job(self, campaign_id, job_id):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute('SELECT * FROM research_campaign_jobs WHERE campaign_id=? AND job_id=?', (campaign_id, job_id)).fetchone()
            if row is None:
                return None
            return {**dict(row), 'request': _campaign_job_spec(connection, row).to_dict(),
                    'source_request': _campaign_source_spec(connection, row).to_dict()}

    def load_campaign_jobs(self, campaign_id):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT * FROM research_campaign_jobs WHERE campaign_id=? ORDER BY accepted_sequence', (campaign_id,)).fetchall()
            return tuple({**dict(row), 'request': _campaign_job_spec(connection, row).to_dict(),
                          'source_request': _campaign_source_spec(connection, row).to_dict()} for row in rows)

    def load_campaign_cycles(self, campaign_id):
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            return tuple(dict(row) for row in connection.execute('SELECT * FROM research_campaign_cycles WHERE campaign_id=? ORDER BY sequence', (campaign_id,)))

    def append_search_result(
        self,
        trial: Mapping[str, Any],
        outcome: Mapping[str, Any],
        card: Mapping[str, Any],
    ) -> bool:
        with closing(self._connect()) as connection, connection:
            return _insert_search_result(connection, trial, outcome, card)

    def commit_trial_result(
        self,
        attempt_id: str,
        trial: Mapping[str, Any],
        outcome: Mapping[str, Any],
        card: Mapping[str, Any],
        *,
        owner_token: str,
        generation: int,
        campaign_cycle: Mapping[str, Any] | None = None,
    ) -> bool:
        """fencing 확인과 결과·attempt 완료를 한 트랜잭션으로 확정한다."""
        if str(outcome.get("status", "")) not in {"COMPLETED", "FAILED", "INELIGIBLE"}:
            raise ValueError("interrupted trial attempts cannot be committed as results")
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            attempt = connection.execute(
                "SELECT job_id,experiment_id,trial_id,status FROM research_trial_attempts "
                "WHERE attempt_id=? AND owner_token=? AND generation=?",
                (attempt_id, str(owner_token), int(generation)),
            ).fetchone()
            if attempt is None or attempt[3] != "RUNNING":
                return False
            if campaign_cycle is not None:
                campaign, _ = _campaign_policy(connection, campaign_cycle['campaign_id'])
                if campaign_cycle.get('worker_claim') is not None and not _campaign_worker_owned(connection, campaign_cycle['worker_claim'], now):
                    return False
                owned_job = _campaign_owned_job(connection, campaign_cycle['campaign_id'], campaign_cycle['sequence'],
                                                campaign_cycle['owner_token'], campaign_cycle['generation'], now)
                if campaign['desired_state'] != 'RUNNING' or owned_job != attempt[0]:
                    return False
            fence = connection.execute(
                "SELECT 1 FROM research_search_jobs WHERE job_id=? AND status='running' "
                "AND owner_token=? AND generation=?",
                (attempt[0], str(owner_token), int(generation)),
            ).fetchone()
            if fence is None:
                return False
            if campaign_cycle is not None and not connection.execute(
                'SELECT 1 FROM research_search_jobs WHERE job_id=? AND lease_expires_at>?', (attempt[0], now)
            ).fetchone():
                return False
            if (str(attempt[1]), str(attempt[2])) != (
                str(trial["experiment_id"]), str(trial["trial_id"]),
            ):
                raise ValueError("trial result does not match its execution attempt")
            inserted = _insert_search_result(connection, trial, outcome, card)
            updated = connection.execute(
                "UPDATE research_trial_attempts SET status=?,finished_at=?,heartbeat_at=? "
                "WHERE attempt_id=? AND status='RUNNING' AND owner_token=? AND generation=?",
                (
                    str(outcome["status"]), now, now, attempt_id,
                    str(owner_token), int(generation),
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("trial attempt changed before result commit")
            return inserted

    def load_search_trials(self, experiment_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_search_trials WHERE experiment_id=? "
                "ORDER BY ordinal", (experiment_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def load_candidate_cards(self, experiment_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_candidate_cards WHERE experiment_id=? "
                "ORDER BY rowid", (experiment_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def enqueue_search_job(
        self,
        job_id: str,
        experiment_id: str,
        dataset_id: str,
        dataset_hash: str,
        *,
        max_retained_jobs: int,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            return _enqueue_search_job(connection, job_id, experiment_id, dataset_id, dataset_hash,
                                       max_retained_jobs=max_retained_jobs, now=now)

    def start_search_job(
        self, job_id: str, *, owner_token: str, lease_seconds: int,
        campaign_cycle: Mapping[str, Any] | None = None,
    ) -> int | None:
        normalized_owner = str(owner_token).strip()
        if not normalized_owner:
            raise ValueError("research search job owner token is required")
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if campaign_cycle is not None:
                campaign, policy = _campaign_policy(connection, campaign_cycle['campaign_id'])
                if campaign_cycle.get('worker_claim') is not None and not _campaign_worker_owned(connection, campaign_cycle['worker_claim'], now):
                    return None
                owned = _campaign_owned_job(connection, campaign_cycle['campaign_id'], campaign_cycle['sequence'],
                                            campaign_cycle['owner_token'], campaign_cycle['generation'], now)
                if campaign['desired_state'] != 'RUNNING' or owned != job_id:
                    return None
                work = connection.execute('SELECT * FROM research_campaign_jobs WHERE campaign_id=? AND job_id=?',
                                          (campaign_cycle['campaign_id'], job_id)).fetchone()
                if work['budget_revision'] != campaign_cycle['budget_revision']:
                    return None
                spec = _campaign_job_spec(connection, work)
                if _canonical_json(campaign_cycle['request']) != _canonical_json(spec.to_dict()):
                    raise ValueError('campaign captured request does not match its stored budget revision')
                search = connection.execute('SELECT status,lease_expires_at FROM research_search_jobs WHERE job_id=?', (job_id,)).fetchone()
                if search[0] in ('cancelled', 'failed') or (search[0] == 'running' and search[1] <= now):
                    if search[0] in ('cancelled', 'failed'):
                        active = connection.execute("SELECT COUNT(*) FROM research_search_jobs WHERE status IN ('queued','running')").fetchone()[0]
                        if active >= min(policy.max_active_jobs, int(spec.resource_budget.get('max_retained_jobs', 100))):
                            raise ValueError('research active backlog limit reached')
                    _enqueue_search_job(connection, job_id, spec.experiment_id, spec.dataset_id, spec.dataset_hash,
                                        max_retained_jobs=policy.max_active_jobs, now=now)
                if search[0] == 'completed':
                    used = connection.execute('SELECT COUNT(*) FROM research_search_trials WHERE experiment_id=?', (spec.experiment_id,)).fetchone()[0]
                    if used < min(spec.max_trials, len(generate_trials(spec))) and work['budget_revision'] > 1:
                        count = connection.execute("SELECT COUNT(*) FROM research_search_jobs WHERE status IN ('queued','running')").fetchone()[0]
                        cap = min(policy.max_active_jobs, int(spec.resource_budget.get('max_retained_jobs', 100)))
                        if count >= cap:
                            raise ValueError('research active backlog limit reached')
                        connection.execute("UPDATE research_search_jobs SET status='queued',updated_at=?,owner_token='',lease_expires_at='',error='' WHERE job_id=?",
                                           (now, job_id))
                        _append_search_job_event(connection, job_id, 'queued', now,
                                                 {'reason': 'campaign_budget_expanded', 'budget_revision': work['budget_revision']})
            row = connection.execute(
                "SELECT status FROM research_search_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown research search job")
            if row[0] == "completed":
                return None
            if row[0] == "running":
                return None
            if row[0] != "queued":
                raise ValueError(f"cannot start research search job in {row[0]} state")
            lease_expires_at = (datetime.now(UTC) + timedelta(
                seconds=max(60, int(lease_seconds)),
            )).isoformat()
            updated = connection.execute(
                "UPDATE research_search_jobs SET status='running',"
                "attempt_count=attempt_count+1,generation=generation+1,updated_at=?,"
                "owner_token=?,heartbeat_at=?,lease_expires_at=? "
                "WHERE job_id=? AND status='queued'",
                (now, normalized_owner, now, lease_expires_at, job_id),
            )
            if updated.rowcount != 1:
                return None
            generation, attempt_count = connection.execute(
                "SELECT generation,attempt_count FROM research_search_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            connection.execute(
                "UPDATE research_trial_attempts SET status='INTERRUPTED',finished_at=?,"
                "heartbeat_at=?,error='execution lease replaced' "
                "WHERE job_id=? AND status='RUNNING'",
                (now, now, job_id),
            )
            _append_search_job_event(
                connection, job_id, "running", now,
                {"attempt_count": int(attempt_count), "generation": int(generation)},
            )
            return int(generation)

    def renew_search_job(
        self,
        job_id: str,
        *,
        owner_token: str,
        generation: int,
        lease_seconds: int,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        lease_expires_at = (datetime.now(UTC) + timedelta(
            seconds=max(60, int(lease_seconds)),
        )).isoformat()
        with closing(self._connect()) as connection, connection:
            updated = connection.execute(
                "UPDATE research_search_jobs SET heartbeat_at=?,updated_at=?,lease_expires_at=? "
                "WHERE job_id=? AND status='running' AND owner_token=? AND generation=?",
                (now, now, lease_expires_at, job_id, str(owner_token), int(generation)),
            )
            if updated.rowcount != 1:
                return False
            connection.execute(
                "UPDATE research_trial_attempts SET heartbeat_at=? "
                "WHERE job_id=? AND owner_token=? AND generation=? AND status='RUNNING'",
                (now, job_id, str(owner_token), int(generation)),
            )
            return True

    def finish_search_job(
        self,
        job_id: str,
        status: str,
        result: Mapping[str, Any],
        error: str = "",
        *,
        owner_token: str,
        generation: int,
    ) -> bool:
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("search job terminal status is invalid")
        now = datetime.now(UTC).isoformat()
        result_json = _canonical_json(result)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT status,result_json,error FROM research_search_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown research search job")
            fence = connection.execute(
                "SELECT 1 FROM research_search_jobs WHERE job_id=? AND owner_token=? "
                "AND generation=?",
                (job_id, str(owner_token), int(generation)),
            ).fetchone()
            if fence is None:
                return False
            if row[0] == "completed":
                if status != "completed" or row[1] != result_json or row[2] != str(error):
                    raise ValueError("completed research search job is immutable")
                return False
            if row[0] != "running":
                raise ValueError(f"cannot finish research search job in {row[0]} state")
            connection.execute(
                "UPDATE research_search_jobs SET status=?,updated_at=?,result_json=?,error=?,"
                "lease_expires_at='',heartbeat_at=? WHERE job_id=? AND owner_token=? "
                "AND generation=?",
                (
                    status, now, result_json, str(error), now, job_id,
                    str(owner_token), int(generation),
                ),
            )
            _append_search_job_event(
                connection, job_id, status, now,
                {"result": dict(result), "error": str(error)},
            )
            return True

    def start_trial_attempt(
        self,
        job_id: str,
        experiment_id: str,
        trial_id: str,
        *,
        owner_token: str,
        generation: int,
    ) -> str:
        now = datetime.now(UTC).isoformat()
        attempt_id = "trial_attempt_" + uuid.uuid4().hex
        with closing(self._connect()) as connection, connection:
            fence = connection.execute(
                "SELECT experiment_id FROM research_search_jobs WHERE job_id=? "
                "AND status='running' AND owner_token=? AND generation=?",
                (job_id, str(owner_token), int(generation)),
            ).fetchone()
            if fence is None:
                raise RuntimeError("research search job execution lease was lost")
            if str(fence[0]) != str(experiment_id):
                raise ValueError("trial attempt experiment does not match its job")
            if connection.execute(
                "SELECT 1 FROM research_search_trials WHERE trial_id=?", (trial_id,),
            ).fetchone() is not None:
                raise ValueError("completed search trial cannot start another attempt")
            connection.execute(
                "INSERT INTO research_trial_attempts("
                "attempt_id,job_id,experiment_id,trial_id,owner_token,generation,status,"
                "started_at,heartbeat_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id, job_id, experiment_id, trial_id, str(owner_token),
                    int(generation), "RUNNING", now, now,
                ),
            )
        return attempt_id

    def interrupt_trial_attempt(
        self,
        attempt_id: str,
        error: str,
        *,
        owner_token: str,
        generation: int,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection, connection:
            updated = connection.execute(
                "UPDATE research_trial_attempts SET status='INTERRUPTED',finished_at=?,"
                "heartbeat_at=?,error=? WHERE attempt_id=? AND status='RUNNING' "
                "AND owner_token=? AND generation=?",
                (now, now, str(error), attempt_id, str(owner_token), int(generation)),
            )
            return updated.rowcount == 1

    def load_trial_attempts(
        self, experiment_id: str, trial_id: str = "",
    ) -> tuple[dict[str, Any], ...]:
        query = (
            "SELECT * FROM research_trial_attempts WHERE experiment_id=?"
            + (" AND trial_id=?" if trial_id else "")
            + " ORDER BY started_at,attempt_id"
        )
        parameters = (experiment_id, trial_id) if trial_id else (experiment_id,)
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query, parameters).fetchall()
        return tuple(dict(row) for row in rows)

    def load_search_job(self, job_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM research_search_jobs WHERE job_id=?", (job_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json"))
        return result

    def load_search_job_events(self, job_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_search_job_events WHERE job_id=? "
                "ORDER BY accepted_sequence", (job_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def append_evaluation(self, evaluation: StrategyEvaluation) -> bool:
        return self.append_evaluations((evaluation,))[0]

    def append_evaluations(self, evaluations: tuple[StrategyEvaluation, ...]) -> tuple[bool, ...]:
        if not evaluations:
            return ()
        documents = []
        for evaluation in evaluations:
            snapshot = evaluation.snapshot.to_dict()
            decision = evaluation.decision.to_dict()
            candidate = evaluation.candidate_event.to_dict() if evaluation.candidate_event else None
            if snapshot["run_id"] != decision["run_id"]:
                raise ValueError("snapshot and decision run_id must match")
            if decision["snapshot_id"] != snapshot["snapshot_id"]:
                raise ValueError("decision must reference the appended snapshot")
            documents.append((snapshot, decision, candidate))
        run_id = documents[0][0]["run_id"]
        if any(snapshot["run_id"] != run_id for snapshot, _, _ in documents):
            raise ValueError("evaluation batch must belong to one run")
        with closing(self._connect()) as connection, connection:
            run = connection.execute(
                "SELECT status FROM research_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError("research run must be started before appending results")
            if run[0] == "completed":
                for snapshot, decision, candidate in documents:
                    _verify_existing_evaluation(connection, snapshot, decision, candidate)
                return tuple(False for _ in documents)
            if run[0] != "running":
                raise ValueError(f"cannot append to research run in {run[0]} state")
            return tuple(
                self._append_evaluation_on_connection(connection, snapshot, decision, candidate)
                for snapshot, decision, candidate in documents
            )

    @staticmethod
    def _append_evaluation_on_connection(connection, snapshot, decision, candidate) -> bool:
        inserted = _insert_immutable(
            connection,
            table="research_feature_snapshots",
            id_column="snapshot_id",
            identifier=snapshot["snapshot_id"],
            document_json=_canonical_json(snapshot),
            insert_sql=(
                "INSERT INTO research_feature_snapshots("
                "snapshot_id,run_id,decision_time,input_cutoff,symbol,document_json"
                ") VALUES(?,?,?,?,?,?)"
            ),
            insert_values=(
                snapshot["snapshot_id"], snapshot["run_id"], snapshot["decision_time"],
                snapshot["input_cutoff"], snapshot["symbol"], _canonical_json(snapshot),
            ),
        )
        _insert_immutable(
            connection,
            table="research_decisions",
            id_column="decision_id",
            identifier=decision["decision_id"],
            document_json=_canonical_json(decision),
            insert_sql=(
                "INSERT INTO research_decisions("
                "decision_id,run_id,snapshot_id,decided_at,symbol,proposal,final_action,document_json"
                ") VALUES(?,?,?,?,?,?,?,?)"
            ),
            insert_values=(
                decision["decision_id"], decision["run_id"], decision["snapshot_id"],
                decision["decided_at"], decision["symbol"], decision["proposal"],
                decision["final_action"], _canonical_json(decision),
            ),
        )
        if candidate is not None:
            _insert_candidate(connection, candidate)
        return inserted

    def finish_run(self, run_id: str, logical_result_hash: str) -> None:
        if not str(logical_result_hash).strip():
            raise ValueError("logical_result_hash is required")
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT status,logical_result_hash FROM research_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown research run")
            if row[0] == "completed":
                if row[1] != logical_result_hash:
                    raise ValueError("completed run result hash is immutable")
                return
            if row[0] != "running":
                raise ValueError(f"cannot complete research run in {row[0]} state")
            connection.execute(
                "UPDATE research_runs SET status='completed',finished_at=?,logical_result_hash=? "
                "WHERE run_id=?",
                (datetime.now(UTC).isoformat(), logical_result_hash, run_id),
            )

    def append_execution_events(self, events: tuple[ExecutionEvent, ...]) -> int:
        if not events:
            return 0
        run_id = events[0].run_id
        if any(event.run_id != run_id for event in events):
            raise ValueError("execution event batch must belong to one run")
        inserted = 0
        with closing(self._connect()) as connection, connection:
            status = _run_status(connection, run_id)
            for event in events:
                document = event.to_dict()
                if status == "completed":
                    _verify_immutable_row(
                        connection, "research_execution_events", "event_id",
                        event.event_id, document,
                    )
                    continue
                if status != "running":
                    raise ValueError(f"cannot append execution event in {status} state")
                inserted += int(_insert_immutable(
                    connection,
                    table="research_execution_events",
                    id_column="event_id",
                    identifier=event.event_id,
                    document_json=_canonical_json(document),
                    insert_sql=(
                        "INSERT INTO research_execution_events("
                        "event_id,run_id,intent_id,decision_id,event_type,occurred_at,received_at,"
                        "symbol,document_json) VALUES(?,?,?,?,?,?,?,?,?)"
                    ),
                    insert_values=(
                        event.event_id, event.run_id, event.intent_id, event.decision_id,
                        event.event_type, event.occurred_at, event.received_at, event.symbol,
                        _canonical_json(document),
                    ),
                ))
        return inserted

    def append_outcome_labels(self, labels: tuple[OutcomeLabel, ...]) -> int:
        if not labels:
            return 0
        run_id = labels[0].run_id
        if any(label.run_id != run_id for label in labels):
            raise ValueError("outcome label batch must belong to one run")
        inserted = 0
        with closing(self._connect()) as connection, connection:
            status = _run_status(connection, run_id)
            for label in labels:
                document = label.to_dict()
                if status == "completed":
                    _verify_immutable_row(
                        connection, "research_outcome_labels", "label_id",
                        label.label_id, document,
                    )
                    continue
                if status != "running":
                    raise ValueError(f"cannot append outcome label in {status} state")
                row = connection.execute(
                    "SELECT label_id,document_json FROM research_outcome_labels "
                    "WHERE run_id=? AND candidate_event_id=? AND horizon_seconds=?",
                    (run_id, label.candidate_event_id, label.horizon_seconds),
                ).fetchone()
                if row is not None:
                    if row[0] != label.label_id or row[1] != _canonical_json(document):
                        raise ValueError("outcome horizon already has different immutable content")
                    continue
                connection.execute(
                    "INSERT INTO research_outcome_labels("
                    "label_id,run_id,candidate_event_id,horizon_seconds,status,available_at,document_json"
                    ") VALUES(?,?,?,?,?,?,?)",
                    (
                        label.label_id, label.run_id, label.candidate_event_id,
                        label.horizon_seconds, label.status, label.available_at,
                        _canonical_json(document),
                    ),
                )
                inserted += 1
        return inserted

    def save_run_evaluation(self, summary: PerformanceSummary) -> bool:
        document = summary.to_dict()
        with closing(self._connect()) as connection, connection:
            status = _run_status(connection, summary.run_id)
            row = connection.execute(
                "SELECT evaluation_id,document_json FROM research_run_evaluations WHERE run_id=?",
                (summary.run_id,),
            ).fetchone()
            if row is not None:
                if row[0] != summary.evaluation_id or row[1] != _canonical_json(document):
                    raise ValueError("run evaluation is immutable")
                return False
            if status != "running":
                raise ValueError(f"cannot save run evaluation in {status} state")
            connection.execute(
                "INSERT INTO research_run_evaluations(evaluation_id,run_id,status,document_json) "
                "VALUES(?,?,?,?)",
                (
                    summary.evaluation_id, summary.run_id, summary.status,
                    _canonical_json(document),
                ),
            )
            return True

    def save_research_report(self, report: ResearchReport) -> bool:
        document = report.to_dict()
        with closing(self._connect()) as connection, connection:
            status = _run_status(connection, report.run_id)
            row = connection.execute(
                "SELECT report_id,document_json FROM research_reports WHERE run_id=?",
                (report.run_id,),
            ).fetchone()
            if row is not None:
                if row[0] != report.report_id or row[1] != _canonical_json(document):
                    raise ValueError("research report is immutable")
                return False
            if status != "running":
                raise ValueError(f"cannot save research report in {status} state")
            connection.execute(
                "INSERT INTO research_reports(report_id,run_id,status,document_json) "
                "VALUES(?,?,?,?)",
                (report.report_id, report.run_id, report.status, _canonical_json(document)),
            )
            return True

    def save_research_comparison(self, comparison: ResearchComparison) -> bool:
        document = comparison.to_dict()
        with closing(self._connect()) as connection, connection:
            for run_id in (comparison.baseline_run_id, comparison.variant_run_id):
                if _run_status(connection, run_id) != "completed":
                    raise ValueError("comparison runs must be completed")
            row = connection.execute(
                "SELECT comparison_id,document_json FROM research_comparisons "
                "WHERE baseline_run_id=? AND variant_run_id=? AND changed_condition=?",
                (
                    comparison.baseline_run_id, comparison.variant_run_id,
                    comparison.changed_condition,
                ),
            ).fetchone()
            if row is not None:
                if row[0] != comparison.comparison_id or row[1] != _canonical_json(document):
                    raise ValueError("research comparison is immutable")
                return False
            connection.execute(
                "INSERT INTO research_comparisons("
                "comparison_id,baseline_run_id,variant_run_id,changed_condition,status,document_json"
                ") VALUES(?,?,?,?,?,?)",
                (
                    comparison.comparison_id, comparison.baseline_run_id,
                    comparison.variant_run_id, comparison.changed_condition,
                    comparison.status, _canonical_json(document),
                ),
            )
            return True

    def append_context_hypothesis(self, hypothesis: ContextHypothesis) -> bool:
        """C1 가설의 최초 상태와 반응별 개정본을 불변 이력으로 저장한다."""
        document = hypothesis.to_dict()
        revision_available_at = hypothesis.last_evaluated_at or hypothesis.available_at
        normalized_time = _normalized_timestamp(revision_available_at)
        document_json = _canonical_json(document)
        with closing(self._connect()) as connection, connection:
            return _insert_immutable(
                connection,
                table="research_context_hypothesis_revisions",
                id_column="revision_id",
                identifier=hypothesis.revision_id,
                document_json=document_json,
                insert_sql=(
                    "INSERT INTO research_context_hypothesis_revisions("
                    "revision_id,hypothesis_id,target_id,status,revision_available_at,document_json"
                    ") VALUES(?,?,?,?,?,?)"
                ),
                insert_values=(
                    hypothesis.revision_id, hypothesis.hypothesis_id,
                    hypothesis.target_id, hypothesis.status, normalized_time, document_json,
                ),
            )

    def load_context_hypothesis_revisions(
        self,
        *,
        hypothesis_id: str = "",
        target_id: str = "",
        as_of: str = "",
    ) -> tuple[dict[str, Any], ...]:
        clauses: list[str] = []
        parameters: list[str] = []
        if hypothesis_id:
            clauses.append("hypothesis_id=?")
            parameters.append(str(hypothesis_id))
        if target_id:
            clauses.append("target_id=?")
            parameters.append(str(target_id).strip())
        if as_of:
            clauses.append("revision_available_at<=?")
            parameters.append(_normalized_timestamp(as_of))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_context_hypothesis_revisions"
                f"{where} ORDER BY revision_available_at,accepted_sequence",
                tuple(parameters),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def load_latest_context_hypotheses(
        self, *, target_id: str = "", as_of: str = "",
    ) -> tuple[dict[str, Any], ...]:
        revisions = self.load_context_hypothesis_revisions(
            target_id=target_id, as_of=as_of,
        )
        latest: dict[str, dict[str, Any]] = {}
        for document in revisions:
            latest[str(document["hypothesis_id"])] = document
        return tuple(latest[key] for key in sorted(latest))

    def append_theme_leadership(self, evaluation: ThemeLeadershipEvaluation) -> bool:
        document = evaluation.to_dict()
        document_json = _canonical_json(document)
        with closing(self._connect()) as connection, connection:
            return _insert_immutable(
                connection,
                table="research_theme_leadership_revisions",
                id_column="revision_id",
                identifier=evaluation.revision_id,
                document_json=document_json,
                insert_sql=(
                    "INSERT INTO research_theme_leadership_revisions("
                    "revision_id,theme_id,available_at,document_json) VALUES(?,?,?,?)"
                ),
                insert_values=(
                    evaluation.revision_id, evaluation.theme_id,
                    _normalized_timestamp(evaluation.as_of), document_json,
                ),
            )

    def append_entry_thesis(self, thesis: EntryThesis) -> bool:
        document = thesis.to_dict()
        document_json = _canonical_json(document)
        with closing(self._connect()) as connection, connection:
            status = _run_status(connection, thesis.run_id)
            if status == "completed":
                _verify_immutable_row(
                    connection, "research_entry_theses", "thesis_id",
                    thesis.thesis_id, document,
                )
                return False
            if status != "running":
                raise ValueError(f"cannot append entry thesis in {status} state")
            return _insert_immutable(
                connection,
                table="research_entry_theses",
                id_column="thesis_id",
                identifier=thesis.thesis_id,
                document_json=document_json,
                insert_sql=(
                    "INSERT INTO research_entry_theses("
                    "thesis_id,run_id,leadership_revision_id,symbol,created_at,document_json"
                    ") VALUES(?,?,?,?,?,?)"
                ),
                insert_values=(
                    thesis.thesis_id, thesis.run_id, thesis.leadership_revision_id,
                    thesis.symbol, _normalized_timestamp(thesis.created_at), document_json,
                ),
            )

    def append_thesis_decision(self, decision: ThesisPolicyDecision) -> bool:
        document = decision.to_dict()
        document_json = _canonical_json(document)
        with closing(self._connect()) as connection, connection:
            status = _run_status(connection, decision.run_id)
            thesis = connection.execute(
                "SELECT run_id FROM research_entry_theses WHERE thesis_id=?",
                (decision.thesis_id,),
            ).fetchone()
            if thesis is None or str(thesis[0]) != decision.run_id:
                raise ValueError("thesis decision must reference an entry thesis in the same run")
            if status == "completed":
                _verify_immutable_row(
                    connection, "research_thesis_decisions", "decision_id",
                    decision.decision_id, document,
                )
                return False
            if status != "running":
                raise ValueError(f"cannot append thesis decision in {status} state")
            return _insert_immutable(
                connection,
                table="research_thesis_decisions",
                id_column="decision_id",
                identifier=decision.decision_id,
                document_json=document_json,
                insert_sql=(
                    "INSERT INTO research_thesis_decisions("
                    "decision_id,run_id,thesis_id,leadership_revision_id,policy_version,"
                    "final_action,available_at,document_json) VALUES(?,?,?,?,?,?,?,?)"
                ),
                insert_values=(
                    decision.decision_id, decision.run_id, decision.thesis_id,
                    decision.leadership_revision_id, decision.policy_version,
                    decision.final_action, _normalized_timestamp(decision.available_at),
                    document_json,
                ),
            )

    def load_theme_leadership_revisions(
        self, *, theme_id: str = "", as_of: str = "",
    ) -> tuple[dict[str, Any], ...]:
        clauses: list[str] = []
        parameters: list[str] = []
        if theme_id:
            clauses.append("theme_id=?")
            parameters.append(str(theme_id))
        if as_of:
            clauses.append("available_at<=?")
            parameters.append(_normalized_timestamp(as_of))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_theme_leadership_revisions"
                f"{where} ORDER BY available_at,accepted_sequence",
                tuple(parameters),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def load_entry_theses(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_entry_theses WHERE run_id=? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def load_thesis_decisions(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_thesis_decisions WHERE run_id=? "
                "ORDER BY available_at,accepted_sequence",
                (run_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def fail_run(self, run_id: str, error: str) -> None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT status FROM research_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown research run")
            if row[0] == "completed":
                raise ValueError("completed research run is immutable")
            connection.execute(
                "UPDATE research_runs SET status='failed',finished_at=?,error=? WHERE run_id=?",
                (datetime.now(UTC).isoformat(), str(error), run_id),
            )

    def cancel_run(self, run_id: str, reason: str = "user_requested") -> None:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT status FROM research_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError("unknown research run")
            if row[0] == "completed":
                raise ValueError("completed research run is immutable")
            connection.execute(
                "UPDATE research_runs SET status='cancelled',finished_at=?,error=? WHERE run_id=?",
                (datetime.now(UTC).isoformat(), str(reason), run_id),
            )

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM research_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["spec"] = json.loads(result.pop("spec_json"))
            result["input_manifest"] = json.loads(result.pop("input_manifest_json"))
            return result

    def load_evaluations(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_decisions WHERE run_id=? "
                "ORDER BY accepted_sequence", (run_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def load_candidate_events(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_candidate_events WHERE run_id=? "
                "ORDER BY accepted_sequence", (run_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def load_execution_events(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_execution_events WHERE run_id=? "
                "ORDER BY accepted_sequence", (run_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def load_outcome_labels(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT document_json FROM research_outcome_labels WHERE run_id=? "
                "ORDER BY accepted_sequence", (run_id,),
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def load_run_evaluation(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT document_json FROM research_run_evaluations WHERE run_id=?", (run_id,),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def load_research_report(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT document_json FROM research_reports WHERE run_id=?", (run_id,),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def load_independent_development_comparison(
        self, run_ids: tuple[str, ...],
    ) -> IndependentDevelopmentComparison:
        """Bounded explicit references, one SQLite snapshot, no trial/raw-data load or write."""
        if not run_ids or len(run_ids) > 200 or any(not isinstance(value, str) or not value.strip() for value in run_ids):
            raise ValueError('comparison requires 1 to 200 nonempty run references')
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                'SELECT r.run_id,r.status,r.error,r.logical_result_hash,r.spec_json,r.input_manifest_json,'
                'p.document_json AS report_json FROM research_runs r '
                'LEFT JOIN research_reports p ON p.run_id=r.run_id '
                f"WHERE r.run_id IN ({','.join('?' for _ in run_ids)})", run_ids,
            ).fetchall()
        indexed = {row['run_id']: row for row in rows}
        records = []
        for run_id in run_ids:
            row = indexed.get(run_id)
            if row is None:
                records.append({'run_id': run_id, 'status': 'missing'})
                continue
            record = {key: row[key] for key in ('run_id', 'status', 'error', 'logical_result_hash')}
            try:
                record.update(spec=json.loads(row['spec_json']), input_manifest=json.loads(row['input_manifest_json']),
                              report=json.loads(row['report_json']) if row['report_json'] else None)
            except (TypeError, ValueError):
                record.update(spec={}, input_manifest={}, report=None)
            records.append(record)
        return build_independent_development_comparison(records)

    def load_research_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT document_json FROM research_comparisons WHERE comparison_id=?",
                (comparison_id,),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def schema_version(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT MAX(version) FROM research_schema_migrations",
            ).fetchone()
        return int(row[0] or 0)

    def _connect(self) -> sqlite3.Connection:
        connection = (sqlite3.connect(self._path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1.0)
                      if self._read_only else sqlite3.connect(self._path))
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _insert_immutable(
    connection: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    identifier: str,
    document_json: str,
    insert_sql: str,
    insert_values: tuple[Any, ...],
) -> bool:
    row = connection.execute(
        f"SELECT document_json FROM {table} WHERE {id_column}=?", (identifier,),
    ).fetchone()
    if row is not None:
        if row[0] != document_json:
            raise ValueError(f"immutable {table} row changed: {identifier}")
        return False
    connection.execute(insert_sql, insert_values)
    return True


def _campaign_clock(now):
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError('campaign clock must be timezone-aware')
    return moment.astimezone(UTC)


def _operating_budget(spec):
    return {key: spec.to_dict()[key] for key in ('max_trials', 'max_seconds', 'resource_budget')}


def _validate_campaign_hypothesis_spec(spec, hypothesis):
    if (
        spec.hypothesis_refs != (hypothesis.hypothesis_id,)
        or spec.family_allowlist != (hypothesis.family_id,)
        or tuple(spec.factor_allowlist) != tuple(hypothesis.factor_allowlist)
        or spec.parameter_space
        or spec.generation_mode != 'manual'
        or not spec.include_no_trade_baseline
        or spec.cost_stress_multipliers_ppm
        or spec.ablations
        or spec.final_holdout_accessed_at
        or spec.final_holdout_access_reason
        or spec.research_context.get('family') != hypothesis.family_id
        or spec.research_context.get('baseline_strategy') != dict(hypothesis.parameters)
    ):
        raise ValueError('automatic campaign experiment does not match its immutable hypothesis')


def _bind_campaign_hypothesis(connection, campaign_id, hypothesis_id, job_id, now):
    sequence = connection.execute(
        'SELECT COALESCE(MAX(enqueued_sequence),0)+1 FROM research_campaign_hypotheses '
        'WHERE campaign_id=?', (campaign_id,),
    ).fetchone()[0]
    changed = connection.execute(
        "UPDATE research_campaign_hypotheses SET state='ENQUEUED',job_id=?,"
        "enqueued_sequence=?,enqueued_at=? WHERE campaign_id=? AND hypothesis_id=? AND state='AVAILABLE'",
        (job_id, sequence, now, campaign_id, hypothesis_id),
    ).rowcount
    if changed != 1:
        row = connection.execute(
            'SELECT state,job_id FROM research_campaign_hypotheses '
            'WHERE campaign_id=? AND hypothesis_id=?',
            (campaign_id, hypothesis_id),
        ).fetchone()
        if row is None or row['state'] != 'ENQUEUED' or row['job_id'] != job_id:
            raise ValueError('campaign hypothesis was scheduled by another operation')


def _campaign_job_spec(connection, job):
    budget = json.loads(connection.execute('SELECT budget_json FROM research_campaign_job_budgets WHERE campaign_id=? AND job_id=? AND revision=?',
                                          (job['campaign_id'], job['job_id'], job['budget_revision'])).fetchone()[0])
    return replace(ExperimentSpec.from_dict(json.loads(job['request_json'])), **budget)


def _campaign_source_spec(connection, job):
    effective = _campaign_job_spec(connection, job)
    if not job['source_request_json']:
        return effective
    return replace(ExperimentSpec.from_dict(json.loads(job['source_request_json'])), **_operating_budget(effective))


def _campaign_policy(connection, campaign_id, *, revision=None):
    connection.row_factory = sqlite3.Row
    campaign = connection.execute('SELECT * FROM research_campaigns WHERE campaign_id=?', (campaign_id,)).fetchone()
    if campaign is None:
        raise ValueError('unknown research campaign')
    document = json.loads(connection.execute('SELECT policy_json FROM research_campaign_revisions WHERE campaign_id=? AND revision=?',
                                            (campaign_id, campaign['revision'] if revision is None else revision)).fetchone()[0])
    if document.pop('version') != 'research_campaign/v1':
        raise ValueError('unsupported research campaign policy')
    return campaign, ResearchCampaignPolicy(**document)


def _campaign_owned_job(connection, campaign_id, sequence, owner, generation, now):
    row = connection.execute("SELECT j.job_id FROM research_campaign_jobs j JOIN research_campaign_cycles c "
                             "ON j.campaign_id=c.campaign_id AND j.job_id=c.job_id "
                             "WHERE c.campaign_id=? AND c.sequence=? AND c.state='RUNNING' "
                             "AND j.state='RUNNING' AND j.generation=? AND c.generation=? "
                             "AND j.budget_revision=c.budget_revision "
                             "AND j.owner_token=? AND c.owner_token=? AND j.lease_expires_at>?",
                             (campaign_id, sequence, generation, generation, owner, owner, now)).fetchone()
    return row[0] if row is not None else None


def _refresh_campaign_state(connection, campaign_id, now):
    campaign, policy = _campaign_policy(connection, campaign_id)
    states = {row[0] for row in connection.execute('SELECT state FROM research_campaign_jobs WHERE campaign_id=?', (campaign_id,))}
    desired = campaign['desired_state']
    worker = connection.execute('SELECT state,next_retry_at FROM research_campaign_workers WHERE campaign_id=?', (campaign_id,)).fetchone()
    if desired != 'RUNNING':
        state, reason = desired, 'user_requested'
    elif worker is not None and worker[0] == 'NEEDS_ATTENTION':
        state, reason = 'NEEDS_ATTENTION', 'worker_retry_limit_reached'
    elif worker is not None and worker[0] == 'FAILED' and worker[1] > now:
        state, reason = 'RUNNING', 'worker_restart_backoff'
    elif states & {'RUNNING', 'PENDING'}:
        state, reason = 'RUNNING', ''
    elif 'RESOURCE_BLOCKED' in states:
        state, reason = 'RESOURCE_BLOCKED', 'registered_job_resource_blocked'
    elif 'NEEDS_ATTENTION' in states:
        needs_budget = connection.execute("SELECT 1 FROM research_campaign_jobs WHERE campaign_id=? AND reason='budget_expansion_required' LIMIT 1", (campaign_id,)).fetchone()
        state, reason = 'NEEDS_ATTENTION', 'budget_expansion_required' if needs_budget else 'retry_limit_reached'
    elif connection.execute("SELECT 1 FROM research_campaign_jobs WHERE campaign_id=? AND reason='trial_budget_exhausted' LIMIT 1", (campaign_id,)).fetchone():
        state, reason = 'NEEDS_ATTENTION', 'trial_budget_exhausted'
    elif policy.auto_hypotheses:
        available = connection.execute(
            "SELECT 1 FROM research_campaign_hypotheses WHERE campaign_id=? AND state='AVAILABLE' LIMIT 1",
            (campaign_id,),
        ).fetchone()
        registered = connection.execute(
            'SELECT 1 FROM research_campaign_hypotheses WHERE campaign_id=? LIMIT 1',
            (campaign_id,),
        ).fetchone()
        template = connection.execute(
            "SELECT 1 FROM research_campaign_jobs WHERE campaign_id=? AND source_kind!='auto_hypothesis' LIMIT 1",
            (campaign_id,),
        ).fetchone()
        blocked = connection.execute(
            "SELECT 1 FROM research_campaign_hypothesis_expansions "
            "WHERE campaign_id=? AND policy_revision=? AND state='BLOCKED' LIMIT 1",
            (campaign_id, campaign['revision']),
        ).fetchone()
        expandable_families = {
            row[0] for row in connection.execute(
                '''SELECT DISTINCT h.family_id
                   FROM research_campaign_hypotheses q
                   JOIN research_campaigns c ON c.campaign_id=q.campaign_id
                   JOIN research_hypotheses h ON h.hypothesis_id=q.hypothesis_id
                   JOIN research_campaign_jobs j
                     ON j.campaign_id=q.campaign_id AND j.job_id=q.job_id
                   LEFT JOIN research_campaign_hypothesis_expansions x
                     ON x.campaign_id=q.campaign_id
                    AND x.parent_hypothesis_id=q.hypothesis_id
                    AND x.policy_revision=c.revision
                   WHERE q.campaign_id=? AND q.state='ENQUEUED'
                     AND j.state='COMPLETED' AND x.parent_hypothesis_id IS NULL''',
                (campaign_id,),
            )
        }
        hypothesis_count = connection.execute(
            'SELECT COUNT(*) FROM research_campaign_hypotheses WHERE campaign_id=?',
            (campaign_id,),
        ).fetchone()[0]
        if available is not None and template is not None:
            state, reason = 'RUNNING', 'next_hypothesis_ready'
        elif available is not None:
            state, reason = 'WAITING_HYPOTHESIS', 'hypothesis_template_missing'
        elif blocked is not None:
            state, reason = 'NEEDS_ATTENTION', 'hypothesis_generation_policy_invalid'
        elif hypothesis_count >= policy.max_hypotheses:
            state, reason = 'WAITING_HYPOTHESIS', 'hypothesis_limit_reached'
        elif expandable_families & set(policy.hypothesis_parameter_values):
            state, reason = 'RUNNING', 'next_hypothesis_generation_ready'
        elif expandable_families:
            state, reason = 'WAITING_HYPOTHESIS', 'hypothesis_generation_policy_missing'
        elif registered is not None:
            state, reason = 'WAITING_HYPOTHESIS', 'hypothesis_space_exhausted'
        else:
            state, reason = 'WAITING_HYPOTHESIS', 'no_registered_hypotheses'
    elif states:
        state, reason = 'SEARCH_SPACE_EXHAUSTED', 'registered_jobs_completed'
    else:
        state, reason = 'WAITING_DATA', 'no_registered_jobs'
    connection.execute('UPDATE research_campaigns SET operational_state=?,reason=?,updated_at=? WHERE campaign_id=?',
                       (state, reason, now, campaign_id))


def _campaign_worker_owned(connection, claim, now):
    return connection.execute("SELECT 1 FROM research_campaign_workers WHERE campaign_id=? AND owner_token=? AND generation=? "
                              "AND state IN ('STARTING','RUNNING') AND lease_expires_at>?",
                              (claim['campaign_id'], claim['owner_token'], claim['generation'], now)).fetchone() is not None


def _require_campaign_storage_owned(connection, operation_id, claim, now):
    cutoff = (datetime.fromisoformat(now) - timedelta(seconds=RESEARCH_STORAGE_PREPARATION_SECONDS)).isoformat()
    operation = connection.execute("SELECT 1 FROM research_campaign_storage_operations WHERE operation_id=? AND campaign_id=? AND owner_token=? AND generation=? AND state='PREPARING' AND started_at>?", (operation_id, claim['campaign_id'], claim['owner_token'], claim['generation'], cutoff)).fetchone()
    intent = connection.execute('SELECT desired_state FROM research_campaigns WHERE campaign_id=?', (claim['campaign_id'],)).fetchone()
    if operation is None or intent is None or intent[0] != 'RUNNING' or not _campaign_worker_owned(connection, claim, now):
        raise ValueError('storage preparation worker is no longer active')


def _finish_campaign_worker(connection, worker, outcome, reason, exit_code, moment):
    campaign_id = worker['campaign_id']
    attempt = connection.execute('SELECT campaign_revision FROM research_campaign_worker_attempts WHERE campaign_id=? AND generation=?',
                                 (campaign_id, worker['generation'])).fetchone()
    _, policy = _campaign_policy(connection, campaign_id, revision=attempt[0])
    failures = worker['failure_count'] + int(outcome == 'FAILED')
    state = 'IDLE' if outcome == 'EXPECTED_EXIT' else 'NEEDS_ATTENTION' if failures >= policy.max_attempts else 'FAILED'
    retry = (moment + timedelta(seconds=policy.retry_seconds(failures))).isoformat() if state == 'FAILED' else ''
    connection.execute('UPDATE research_campaign_workers SET state=?,failure_count=?,next_retry_at=?,reason=?,owner_token=\'\',lease_expires_at=\'\',updated_at=? WHERE campaign_id=?',
                       (state, failures, retry, reason, moment.isoformat(), campaign_id))
    connection.execute('UPDATE research_campaign_worker_attempts SET state=?,finished_at=?,exit_code=?,reason=? WHERE campaign_id=? AND generation=?',
                       (outcome, moment.isoformat(), exit_code, reason, campaign_id, worker['generation']))
    _refresh_campaign_state(connection, campaign_id, moment.isoformat())


def _campaign_completion_reason(connection, job_id, request_json, *, verify_trial_rows=False):
    row = connection.execute('SELECT result_json FROM research_search_jobs WHERE job_id=?', (job_id,)).fetchone()
    budget = json.loads(row[0] or '{}').get('budget', {})
    if verify_trial_rows:
        experiment_id = ExperimentSpec.from_dict(json.loads(request_json)).experiment_id
        budget['used_trials'] = connection.execute('SELECT COUNT(*) FROM research_search_trials WHERE experiment_id=?', (experiment_id,)).fetchone()[0]
    if 'used_trials' not in budget:
        return 'registered_jobs_completed'
    spec = ExperimentSpec.from_dict(json.loads(request_json))
    total = len(generate_trials(spec))
    if int(budget['used_trials']) < min(spec.max_trials, total):
        return 'budget_expansion_required'
    return 'trial_budget_exhausted' if spec.max_trials < total else 'registered_jobs_completed'


def _save_search_experiment(connection, experiment_id, spec_json):
    row = connection.execute('SELECT spec_json FROM research_search_experiments WHERE experiment_id=?', (experiment_id,)).fetchone()
    if row is not None:
        if row[0] != spec_json:
            raise ValueError('experiment_id already belongs to a different immutable spec')
        return False
    connection.execute('INSERT INTO research_search_experiments(experiment_id,created_at,spec_json) VALUES(?,?,?)',
                       (experiment_id, datetime.now(UTC).isoformat(), spec_json))
    return True


def _enqueue_search_job(connection, job_id, experiment_id, dataset_id, dataset_hash, *, max_retained_jobs, now):
    row = connection.execute(
        'SELECT experiment_id,dataset_id,dataset_hash,status,lease_expires_at FROM research_search_jobs WHERE job_id=?',
        (job_id,),
    ).fetchone()
    if row is not None:
        if tuple(row[:3]) != (experiment_id, dataset_id, dataset_hash):
            raise ValueError('search job id belongs to different immutable inputs')
        if row[3] in ('completed', 'queued') or (row[3] == 'running' and row[4] and row[4] > now):
            return False
        connection.execute("UPDATE research_search_jobs SET status='queued',updated_at=?,error='',"
                           "lease_expires_at='',owner_token='',heartbeat_at='' WHERE job_id=?", (now, job_id))
        _append_search_job_event(connection, job_id, 'queued', now, {})
        return True
    count = connection.execute("SELECT COUNT(*) FROM research_search_jobs WHERE status IN ('queued','running')").fetchone()[0]
    if count >= int(max_retained_jobs):
        raise ValueError('research job retention limit reached')
    connection.execute('INSERT INTO research_search_jobs(job_id,experiment_id,dataset_id,dataset_hash,status,created_at,updated_at) '
                       'VALUES(?,?,?,?,?,?,?)', (job_id, experiment_id, dataset_id, dataset_hash, 'queued', now, now))
    _append_search_job_event(connection, job_id, 'queued', now, {})
    return True


def _append_search_job_event(
    connection: sqlite3.Connection,
    job_id: str,
    status: str,
    occurred_at: str,
    details: Mapping[str, Any],
) -> None:
    document = {
        "job_id": job_id, "status": status, "occurred_at": occurred_at,
        **dict(details),
    }
    document_json = _canonical_json(document)
    ordinal = int(connection.execute(
        "SELECT COUNT(*) FROM research_search_job_events WHERE job_id=?", (job_id,),
    ).fetchone()[0])
    event_id = "search_job_event_" + hashlib.sha256(
        f"{document_json}:{ordinal}".encode("utf-8")
    ).hexdigest()
    connection.execute(
        "INSERT INTO research_search_job_events("
        "event_id,job_id,status,occurred_at,document_json) VALUES(?,?,?,?,?)",
        (event_id, job_id, status, occurred_at, document_json),
    )


def _insert_search_result(
    connection: sqlite3.Connection,
    trial: Mapping[str, Any],
    outcome: Mapping[str, Any],
    card: Mapping[str, Any],
) -> bool:
    trial_document = dict(trial)
    trial_document["outcome"] = dict(outcome)
    trial_json = _canonical_json(trial_document)
    card_json = _canonical_json(card)
    trial_id = str(trial["trial_id"])
    experiment_id = str(trial["experiment_id"])
    experiment = connection.execute(
        "SELECT 1 FROM research_search_experiments WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()
    if experiment is None:
        raise ValueError("search experiment must be saved before trial results")
    row = connection.execute(
        "SELECT document_json FROM research_search_trials WHERE trial_id=?", (trial_id,),
    ).fetchone()
    if row is not None:
        if row[0] != trial_json:
            raise ValueError("search trial result is immutable")
        card_row = connection.execute(
            "SELECT document_json FROM research_candidate_cards WHERE trial_id=?", (trial_id,),
        ).fetchone()
        if card_row is None or card_row[0] != card_json:
            raise ValueError("search candidate card is immutable")
        return False
    now = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT INTO research_search_trials("
        "trial_id,experiment_id,ordinal,status,run_id,finished_at,document_json"
        ") VALUES(?,?,?,?,?,?,?)",
        (
            trial_id, experiment_id, int(trial["ordinal"]), str(outcome["status"]),
            str(outcome.get("run_id", "")), now, trial_json,
        ),
    )
    connection.execute(
        "INSERT INTO research_candidate_cards("
        "card_id,experiment_id,trial_id,status,document_json) VALUES(?,?,?,?,?)",
        (
            str(card["card_id"]), experiment_id, trial_id,
            str(card["status"]), card_json,
        ),
    )
    return True


def _normalized_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("research timestamps must be timezone-aware")
    return parsed.astimezone(UTC).isoformat()


def _insert_candidate(connection: sqlite3.Connection, candidate: Mapping[str, Any]) -> None:
    document_json = _canonical_json(candidate)
    row = connection.execute(
        "SELECT event_id,document_json FROM research_candidate_events "
        "WHERE run_id=? AND dedup_key=?",
        (candidate["run_id"], candidate["dedup_key"]),
    ).fetchone()
    if row is not None:
        if row[0] != candidate["event_id"] or row[1] != document_json:
            raise ValueError("candidate dedup key already has different immutable content")
        return
    connection.execute(
        "INSERT INTO research_candidate_events("
        "event_id,run_id,decision_id,dedup_key,symbol,available_at,document_json"
        ") VALUES(?,?,?,?,?,?,?)",
        (
            candidate["event_id"], candidate["run_id"], candidate["decision_id"],
            candidate["dedup_key"], candidate["symbol"], candidate["available_at"],
            document_json,
        ),
    )


def _verify_existing_evaluation(
    connection: sqlite3.Connection,
    snapshot: Mapping[str, Any],
    decision: Mapping[str, Any],
    candidate: Mapping[str, Any] | None,
) -> None:
    checks = (
        ("research_feature_snapshots", "snapshot_id", snapshot["snapshot_id"], snapshot),
        ("research_decisions", "decision_id", decision["decision_id"], decision),
    )
    for table, id_column, identifier, document in checks:
        row = connection.execute(
            f"SELECT document_json FROM {table} WHERE {id_column}=?", (identifier,),
        ).fetchone()
        if row is None or row[0] != _canonical_json(document):
            raise ValueError("cannot append a new or changed evaluation to a completed run")
    if candidate is not None:
        row = connection.execute(
            "SELECT document_json FROM research_candidate_events WHERE event_id=?",
            (candidate["event_id"],),
        ).fetchone()
        if row is None or row[0] != _canonical_json(candidate):
            raise ValueError("cannot append a new or changed candidate to a completed run")
    else:
        row = connection.execute(
            "SELECT 1 FROM research_candidate_events WHERE decision_id=?",
            (decision["decision_id"],),
        ).fetchone()
        if row is not None:
            raise ValueError("cannot remove a candidate from a completed run evaluation")


def _run_status(connection: sqlite3.Connection, run_id: str) -> str:
    row = connection.execute(
        "SELECT status FROM research_runs WHERE run_id=?", (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError("unknown research run")
    return str(row[0])


def _verify_immutable_row(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    identifier: str,
    document: Mapping[str, Any],
) -> None:
    row = connection.execute(
        f"SELECT document_json FROM {table} WHERE {id_column}=?", (identifier,),
    ).fetchone()
    if row is None or row[0] != _canonical_json(document):
        raise ValueError(f"completed run {table} row is missing or changed")


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
