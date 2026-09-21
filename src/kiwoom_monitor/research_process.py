"""앱 이벤트 루프와 분리해 D7 연구 run 또는 쌍 비교를 실행한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from datetime import UTC, datetime
from typing import Any, Mapping, Callable

from kiwoom_monitor.application.research_families import (
    BREAKOUT_FAMILY_ID,
    FAMILY_REGISTRY,
    parse_strategy_config,
    family_for_config,
)
from kiwoom_monitor.application.market_session_schedule import (
    SUPPORTED_RESEARCH_SESSION_PROFILES,
    research_session_profile_document,
)
from kiwoom_monitor.application.research_execution import (
    SimulationCostModel,
    SimulationExecutionConfig,
)
from kiwoom_monitor.application.research_evaluation import (
    DevelopmentEvidence,
    build_development_evidence,
    build_research_report,
)
from kiwoom_monitor.application.research_hypotheses import (
    DevelopmentEvidenceRef,
    FollowupHypothesisGenerationRequest,
    build_development_evidence_snapshot,
    generate_followup_hypotheses,
)
from kiwoom_monitor.application.research_splits import (ResearchEvaluationSpec, DevelopmentPartitionSpec,
    DevelopmentSymbolPartitionSpec, FinalHoldoutBatchSpec)
from kiwoom_monitor.application.research_search import (
    ExperimentSpec,
    TrialAttemptInterrupted,
    TrialOutcome,
    apply_selection_constraints,
    outcome_from_report,
    generate_trials,
    run_limited_search,
)
from kiwoom_monitor.application.research_queue import (
    build_hypothesis_experiment_spec,
    build_research_job_identity,
    should_notify_research_result,
)
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, CentralContentHttpError, CentralContentUnavailableError
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from scripts.export_research_dataset import prepare_campaign_nas_input
from kiwoom_monitor.infrastructure.research_data_source import load_research_input, research_input_encoded_bytes, campaign_input_scope, campaign_input_fingerprint
from kiwoom_monitor.infrastructure.research_storage import validate_research_storage_root
from kiwoom_monitor.infrastructure.research_data_source import (prepare_development_partition,
    prepare_final_holdout_partition, final_holdout_partition_start, FrozenResearchDataset)
from kiwoom_monitor.application.research_resources import ResearchResourceGuard, ResearchResourceLimits, ResearchResourceBlocked
from scripts.run_research import (
    ResearchRunCancelled,
    execute_rank_comparison,
    execute_research,
    research_implementation_hash,
    research_run_identity,
)


@dataclass(frozen=True)
class ResearchProcessRequest:
    mode: str
    family: str
    dataset: Path
    database: Path
    runs_dir: Path
    strategy: Any
    execution: SimulationExecutionConfig
    evaluation: ResearchEvaluationSpec
    session_profile: str | None = None
    search: ExperimentSpec | None = None
    resource_limits: ResearchResourceLimits = ResearchResourceLimits()
    development_partition: DevelopmentPartitionSpec | None = None


def final_candidate_spec_document(
    request: ResearchProcessRequest, implementation_hash: str,
) -> dict[str, Any]:
    """Return the existing final_candidate/v1 identity document unchanged."""
    if (not isinstance(request, ResearchProcessRequest) or request.mode != 'single_run'
            or request.search is not None or request.development_partition is not None
            or request.session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES):
        raise ValueError('final candidate requires a fixed registered single-run strategy')
    if (not isinstance(implementation_hash, str) or len(implementation_hash) != 64
            or any(char not in '0123456789abcdef' for char in implementation_hash)):
        raise ValueError('final candidate requires a scientific implementation SHA256')
    family = family_for_config(request.strategy)
    if family.family_id != request.family:
        raise ValueError('final candidate family differs from its registered strategy')
    parameters = parse_strategy_config(request.family, request.strategy.to_dict()).to_dict()
    execution = request.execution
    if not isinstance(execution, SimulationExecutionConfig) or type(execution.initial_cash_won) is not int:
        raise ValueError('final candidate requires a typed execution model and integer capital')
    costs = execution.cost_model
    if (not isinstance(costs, SimulationCostModel) or costs.rate_basis == 'unspecified'
            or not costs.source.strip() or not costs.valid_from or not costs.valid_to
            or any(type(rate) is not int for rate in (costs.commission_bps, costs.sell_tax_bps, costs.slippage_bps))):
        raise ValueError('final candidate requires explicit cost provenance and validity')
    return {'version': 'final_candidate/v1', 'family': request.family, 'parameters': parameters,
            'execution_model': execution.to_dict(),
            'session_profile': research_session_profile_document(request.session_profile),
            'implementation_hash': implementation_hash}


def final_candidate_spec_hash(request: ResearchProcessRequest, implementation_hash: str) -> str:
    """Scientific identity excludes dates, source paths and operating budgets."""
    document = final_candidate_spec_document(request, implementation_hash)
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def prepare_mock_automation_candidate_publication(
    repository: ResearchRepository,
    request: ResearchProcessRequest,
    *,
    implementation_hash: str,
    strategy_ref: str,
    account_ref: str,
    final_batch_id: str,
    final_run_id: str,
    eligibility_policy: Any,
) -> dict[str, Any]:
    """Build the bounded publication documents; this performs no network or order call."""
    from kiwoom_monitor.application.mock_automation_candidate import (
        build_candidate_package,
        evaluate_candidate_eligibility,
        validate_publication_size,
    )

    candidate_spec = final_candidate_spec_document(request, implementation_hash)
    candidate_hash = final_candidate_spec_hash(request, implementation_hash)
    matches = [
        row for row in repository.load_final_holdout_executions(final_batch_id)
        if str(row.get("candidate_spec_hash", "")) == candidate_hash
    ]
    if len(matches) != 1:
        raise ValueError("candidate is not uniquely present in the final holdout ledger")
    run = repository.load_run(final_run_id)
    report = repository.load_research_report(final_run_id)
    if run is None or report is None:
        raise ValueError("final run or research report evidence is missing")
    package = build_candidate_package(
        strategy_ref=strategy_ref, candidate_spec=candidate_spec,
        batch_id=final_batch_id, run=run, execution=matches[0], report=report,
    )
    receipt = evaluate_candidate_eligibility(
        package, eligibility_policy, account_ref=account_ref,
    )
    documents = {
        "package": package.to_dict(),
        "eligibility_policy": eligibility_policy.to_dict(),
        "eligibility_receipt": receipt.to_dict(),
    }
    validate_publication_size(
        documents["package"], documents["eligibility_policy"],
        documents["eligibility_receipt"],
    )
    return documents


@dataclass(frozen=True)
class PreparedFinalHoldoutEvaluation:
    """Trusted preparation output, not a claim or permission to execute/retry."""
    batch: FinalHoldoutBatchSpec
    candidates: tuple[ResearchProcessRequest, ...]
    dataset: FrozenResearchDataset
    storage_paths: tuple[str, str, str]
    implementation_hash: str


@dataclass(frozen=True)
class FinalHoldoutExecutionRequest:
    """Bounded process request for one locked final batch and explicit recoveries."""
    batch: FinalHoldoutBatchSpec
    candidates: tuple[ResearchProcessRequest, ...]
    access_request_id: str
    accessed_at: str
    owner_token: str
    recoveries: tuple[tuple[str, str, str], ...] = ()

    def recovery_mapping(self) -> dict[str, dict[str, str]]:
        return {candidate_hash: {'request_id': request_id, 'reason': reason}
                for candidate_hash, request_id, reason in self.recoveries}

    def to_dict(self) -> dict[str, Any]:
        candidates = []
        for request in self.candidates:
            candidates.append({'mode': request.mode, 'family': request.family,
                'dataset': str(request.dataset.resolve()), 'database': str(request.database.resolve()),
                'runs_dir': str(request.runs_dir.resolve()), 'session_profile': request.session_profile,
                'strategy': request.strategy.to_dict(), 'execution': request.execution.to_dict(),
                'evaluation': request.evaluation.to_dict(),
                'resource_budget': {'memory_mb': request.resource_limits.memory_mb,
                                    'cpu_duty_percent': request.resource_limits.cpu_duty_percent}})
        return {'version': 'independent_final_holdout_request/v1', 'batch': self.batch.to_dict(),
                'candidates': candidates,
                'access': {'request_id': self.access_request_id, 'accessed_at': self.accessed_at},
                'owner_token': self.owner_token,
                'recoveries': {candidate_hash: {'request_id': request_id, 'reason': reason}
                               for candidate_hash, request_id, reason in self.recoveries}}


@dataclass(frozen=True)
class FinalHoldoutExposureRequest:
    """Bounded, auditable request to mark one locked final window as development evidence."""
    database: Path
    batch: FinalHoldoutBatchSpec
    request_id: str
    exposed_at: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {'version': 'final_holdout_exposure_request/v1',
                'database': str(self.database.resolve()), 'batch': self.batch.to_dict(),
                'exposure': {'request_id': self.request_id, 'exposed_at': self.exposed_at,
                             'reason': self.reason}}


def execute_final_holdout_evaluation(prepared: PreparedFinalHoldoutEvaluation, *, owner_token: str,
                                     cancel_requested=lambda: False,
                                     recoveries: Mapping[str, Mapping[str, str]] | None = None) -> dict[str, Any]:
    """Run only the locked candidate set; terminal failures never retry automatically."""
    if not isinstance(prepared, PreparedFinalHoldoutEvaluation):
        raise ValueError('prepared final holdout evaluation is required')
    if not callable(cancel_requested):
        raise ValueError('final cancellation check must be callable')
    if (not isinstance(owner_token, str) or not owner_token.strip() or len(owner_token) > 256
            or '\x00' in owner_token):
        raise ValueError('final execution owner token must be 1 to 256 characters')
    batch, dataset = prepared.batch, prepared.dataset
    if (not isinstance(batch, FinalHoldoutBatchSpec)
            or not isinstance(prepared.candidates, tuple) or not 1 <= len(prepared.candidates) <= 200
            or not isinstance(dataset, FrozenResearchDataset)):
        raise ValueError('prepared final execution requires 1 to 200 immutable candidates and one dataset')
    current_hash = research_implementation_hash(batch.session_profile)
    keyed = tuple((final_candidate_spec_hash(request, current_hash), request) for request in prepared.candidates)
    if (current_hash != prepared.implementation_hash
            or tuple(key for key, _ in keyed) != batch.candidate_spec_hashes):
        raise ValueError('prepared final candidates or implementation changed before execution')
    if any(FinalHoldoutBatchSpec(batch.version, batch.dataset_id, batch.dataset_hash,
               request.session_profile, batch.candidate_spec_hashes, request.evaluation).to_dict()['evaluation']
               != batch.to_dict()['evaluation'] for _, request in keyed):
        raise ValueError('prepared final candidate policy changed before execution')
    request = prepared.candidates[0]
    if any(not isinstance(path, Path) or not path.is_absolute()
           for candidate in prepared.candidates for path in (candidate.dataset, candidate.database, candidate.runs_dir)):
        raise ValueError('prepared final candidates must keep their shared absolute storage paths')
    paths = tuple(path.resolve() for path in (request.dataset, request.database, request.runs_dir))
    if (prepared.storage_paths != tuple(str(path) for path in paths)
            or any(tuple(value.resolve() for value in (candidate.dataset, candidate.database, candidate.runs_dir)) != paths
                   for candidate in prepared.candidates)):
        raise ValueError('prepared final candidates must keep their shared absolute storage paths')
    final_holdout_partition_start(dataset, batch.evaluation)
    repository = ResearchRepository(request.database)
    candidate_paths = {candidate_hash: candidate.runs_dir for candidate_hash, candidate in keyed}
    if recoveries is None:
        recoveries = {}
    if not isinstance(recoveries, Mapping):
        raise ValueError('final recoveries must be a candidate hash mapping')
    recovery_documents: dict[str, dict[str, str]] = {}
    existing_executions = {
        row['candidate_spec_hash']: row
        for row in repository.load_final_holdout_executions(batch.batch_id)
    }
    for candidate_hash, document in recoveries.items():
        if candidate_hash not in candidate_paths or not isinstance(document, Mapping) \
                or set(document) != {'request_id', 'reason'}:
            raise ValueError('final recovery must name one locked candidate and request ID/reason')
        request_id, reason = document.get('request_id'), document.get('reason')
        if (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256
                or '\x00' in request_id or not isinstance(reason, str) or not reason.strip()
                or len(reason) > 2000 or '\x00' in reason):
            raise ValueError('final recovery request ID/reason are invalid')
        execution = existing_executions.get(candidate_hash)
        if execution is None or execution['state'] not in ('FAILED', 'CANCELLED'):
            raise ValueError('only failed or cancelled final candidate can be recovered')
        manifest_path = candidate_paths[candidate_hash] / execution['run_id'] / 'manifest.json'
        if manifest_path.exists():
            raise ValueError('final recovery requires manual review because an output manifest exists')
        recovery_documents[candidate_hash] = {'request_id': request_id, 'reason': reason}
    rows = []
    for candidate_hash, candidate in keyed:
        row = {'candidate_spec_hash': candidate_hash, 'run_id': '', 'state': 'NOT_STARTED',
               'reason': '', 'logical_result_hash': '', 'performance_status': '', 'report_status': '',
               'recovery_request_id': ''}
        rows.append(row)
        if cancel_requested():
            row['state'], row['reason'] = 'CANCELLED', 'user_cancelled_before_claim'
            break
        if research_implementation_hash(batch.session_profile) != prepared.implementation_hash:
            raise ValueError('research implementation changed during final execution')
        guard = ResearchResourceGuard(candidate.resource_limits)
        run_id, spec = research_run_identity(dataset, candidate.strategy, candidate.execution, candidate.evaluation,
            session_profile=candidate.session_profile, execution_scope='independent_final_holdout/v1')
        row['run_id'] = run_id
        recovery = recovery_documents.get(candidate_hash)
        if recovery is not None:
            repository.request_final_holdout_recovery(batch, candidate_spec_hash=candidate_hash,
                request_id=recovery['request_id'], owner_token=owner_token, reason=recovery['reason'])
            row['recovery_request_id'] = recovery['request_id']
        claim = repository.claim_final_holdout_execution(batch, candidate_spec_hash=candidate_hash,
            run_id=run_id, spec=spec, input_manifest=dataset.manifest, owner_token=owner_token,
            recovery_request_id=recovery['request_id'] if recovery is not None else None)
        if claim == 'completed':
            run = repository.load_run(run_id)
            report = repository.load_research_report(run_id)
            manifest_path = candidate.runs_dir / run_id / 'manifest.json'
            try:
                output = json.loads(manifest_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                output = None
            if (run is None or report is None or not isinstance(output, dict)
                    or output.get('run_id') != run_id
                    or output.get('logical_result_hash') != run.get('logical_result_hash')
                    or output.get('input_dataset_id') != dataset.manifest.get('dataset_id')
                    or output.get('input_revision_ids_hash') != dataset.manifest.get('revision_ids_hash')
                    or output.get('spec') != run.get('spec')
                    or report.get('run_id') != run_id
                    or report.get('split_spec') != run.get('spec', {}).get('evaluation_spec')):
                row['state'], row['reason'] = 'CACHE_INVALID', 'completed_final_artifacts_are_incomplete'
            else:
                row.update(state='CACHED', logical_result_hash=run['logical_result_hash'],
                           performance_status=str(output.get('performance', {}).get('status', '')),
                           report_status=str(output.get('research_report', {}).get('status', '')))
            continue
        if claim != 'claimed':
            row['state'] = claim.upper()
            existing = repository.load_run(run_id)
            row['reason'] = str((existing or {}).get('error') or f'existing_final_execution_{claim}')
            continue
        ownership = {'batch_id': batch.batch_id, 'candidate_spec_hash': candidate_hash,
                     'owner_token': owner_token}
        try:
            result = execute_research(dataset, repository, candidate.runs_dir, candidate.strategy,
                candidate.execution, candidate.evaluation, cancel_requested, candidate.session_profile, guard,
                execution_scope='independent_final_holdout/v1', expected_code_hash=prepared.implementation_hash,
                final_execution=ownership)
            if result.run_id != run_id:
                raise RuntimeError('final scientific run identity mismatch')
            repository.finish_final_holdout_execution(batch.batch_id, candidate_hash, run_id=run_id,
                owner_token=owner_token, outcome='COMPLETED', logical_result_hash=result.logical_result_hash)
            row.update(state='COMPLETED', logical_result_hash=result.logical_result_hash,
                       performance_status=result.performance_status, report_status=result.report_status)
        except (ResearchRunCancelled, ResearchResourceBlocked) as exc:
            reason = f'{type(exc).__name__}: {exc}'[:2000]
            repository.finish_final_holdout_execution(batch.batch_id, candidate_hash, run_id=run_id,
                owner_token=owner_token, outcome='CANCELLED', reason=reason)
            row['state'], row['reason'] = 'CANCELLED', reason
            break
        except (OSError, TypeError, ValueError, RuntimeError, sqlite3.Error) as exc:
            reason = f'{type(exc).__name__}: {exc}'[:2000]
            repository.finish_final_holdout_execution(batch.batch_id, candidate_hash, run_id=run_id,
                owner_token=owner_token, outcome='FAILED', reason=reason)
            row['state'], row['reason'] = 'FAILED', reason
    while len(rows) < len(keyed):
        candidate_hash = keyed[len(rows)][0]
        rows.append({'candidate_spec_hash': candidate_hash, 'run_id': '', 'state': 'NOT_STARTED',
                     'reason': '', 'logical_result_hash': '', 'performance_status': '', 'report_status': '',
                     'recovery_request_id': ''})
    complete = all(row['state'] in ('COMPLETED', 'CACHED') for row in rows)
    return {'version': 'independent_final_holdout_result/v1', 'batch_id': batch.batch_id,
            'window_id': batch.window_id, 'implementation_hash': prepared.implementation_hash,
            'batch_status': 'COMPLETED' if complete else 'PARTIAL', 'candidates': rows,
            'limitations': ['fixed_candidates_only', 'no_cross_candidate_selection',
                            'failed_or_cancelled_candidates_require_explicit_review',
                            'recovery_requires_audited_request_and_absent_output_manifest']}


def prepare_final_holdout_evaluation(batch: FinalHoldoutBatchSpec, candidates: tuple[ResearchProcessRequest, ...],
                                    *, request_id: str, accessed_at: str, checkpoint=lambda: None) -> PreparedFinalHoldoutEvaluation:
    """Verify bytes privately, check history atomically, then hand off isolated input."""
    if not isinstance(batch, FinalHoldoutBatchSpec) or not isinstance(candidates, tuple) or not 1 <= len(candidates) <= 200:
        raise ValueError('final preparation requires a locked batch and 1 to 200 fixed candidates')
    implementation_hash = research_implementation_hash(batch.session_profile)
    keyed = [(final_candidate_spec_hash(request, implementation_hash), request) for request in candidates]
    if tuple(sorted(key for key, _ in keyed)) != batch.candidate_spec_hashes:
        raise ValueError('final candidates differ from the locked scientific hash set')
    first = candidates[0]
    if any(not isinstance(path, Path) or not path.is_absolute() for path in (first.dataset, first.database, first.runs_dir)):
        raise ValueError('final preparation requires absolute storage paths')
    paths = tuple(path.resolve() for path in (first.dataset, first.database, first.runs_dir))
    if paths[1] == paths[2] or any(path == paths[0] or paths[0] in path.parents for path in paths[1:]):
        raise ValueError('final state and run storage must be outside the frozen source')
    for request in candidates:
        checkpoint()
        if any(not isinstance(path, Path) or not path.is_absolute() for path in (request.dataset, request.database, request.runs_dir)):
            raise ValueError('final preparation requires absolute storage paths')
        if tuple(Path(path).resolve() for path in (request.dataset, request.database, request.runs_dir)) != paths:
            raise ValueError('final candidates must share source, ledger and run storage')
        policy = FinalHoldoutBatchSpec(batch.version, batch.dataset_id, batch.dataset_hash,
                                     request.session_profile, batch.candidate_spec_hashes, request.evaluation)
        if policy.to_dict() != batch.to_dict():
            raise ValueError('final candidate evaluation differs from the locked batch')
        costs = request.execution.cost_model
        if datetime.fromisoformat(costs.valid_from) > datetime.fromisoformat(batch.start) or datetime.fromisoformat(costs.valid_to) < datetime.fromisoformat(batch.end):
            raise ValueError('final candidate costs do not cover the evaluation window')
    guard = ResearchResourceGuard(first.resource_limits)
    def checked():
        checkpoint()
        guard.checkpoint()
    checked()
    source = load_research_input(paths[0], session_profile=batch.session_profile, checkpoint=checked)
    dataset = prepare_final_holdout_partition(source, batch, checkpoint=checked)
    prepared = PreparedFinalHoldoutEvaluation(batch,
        tuple(request for _, request in sorted(keyed, key=lambda pair: pair[0])), dataset,
        tuple(str(path) for path in paths), implementation_hash)
    if research_implementation_hash(batch.session_profile) != implementation_hash:
        raise ValueError('scientific implementation changed during final preparation')
    checked()
    repository = ResearchRepository(paths[1])
    repository.record_final_holdout_access(batch, request_id=request_id, accessed_at=accessed_at,
                                          check_development_history=True, checkpoint=checked)
    return prepared


def load_final_holdout_execution_request(path: Path) -> FinalHoldoutExecutionRequest:
    """Parse a self-contained final request without reading source bytes or creating its ledger."""
    path = Path(path).resolve()
    with path.open('rb') as stream:
        encoded = stream.read(4 * 1024 * 1024 + 1)
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError('final holdout request exceeds 4 MiB')
    document = json.loads(encoded.decode('utf-8-sig'))
    fields = {'version', 'batch', 'candidates', 'access', 'owner_token', 'recoveries'}
    if (not isinstance(document, Mapping) or set(document) != fields
            or document.get('version') != 'independent_final_holdout_request/v1'):
        raise ValueError('unsupported final holdout execution request contract')
    batch = FinalHoldoutBatchSpec.from_dict(_mapping(document['batch'], 'batch'))
    raw_candidates = document['candidates']
    candidate_fields = {'mode', 'family', 'dataset', 'database', 'runs_dir', 'session_profile',
                        'strategy', 'execution', 'evaluation', 'resource_budget'}
    if (not isinstance(raw_candidates, list) or not 1 <= len(raw_candidates) <= 200
            or any(not isinstance(value, Mapping) or set(value) != candidate_fields
                   for value in raw_candidates)):
        raise ValueError('final request requires 1 to 200 fixed candidate documents')
    candidates = tuple(_process_request_from_document(value, path.parent) for value in raw_candidates)
    if any(request.mode != 'single_run' or request.search is not None
           or request.development_partition is not None for request in candidates):
        raise ValueError('final request candidates must be fixed single_run documents')
    if any(_is_within(path, request.dataset) for request in candidates):
        raise ValueError('final request file must be outside the frozen dataset')
    implementation_hash = research_implementation_hash(batch.session_profile)
    hashes = tuple(sorted(final_candidate_spec_hash(request, implementation_hash) for request in candidates))
    if hashes != batch.candidate_spec_hashes:
        raise ValueError('final request candidates differ from the locked batch')
    access = _mapping(document['access'], 'access')
    if set(access) != {'request_id', 'accessed_at'}:
        raise ValueError('final access requires request_id and accessed_at only')
    access_request_id, accessed_at = access['request_id'], access['accessed_at']
    owner_token = document['owner_token']
    for value, name in ((access_request_id, 'access request ID'), (owner_token, 'owner token')):
        if not isinstance(value, str) or not value.strip() or len(value) > 256 or '\x00' in value:
            raise ValueError(f'final {name} must be 1 to 256 characters')
    if not isinstance(accessed_at, str):
        raise ValueError('final accessed_at must be timezone aware')
    try:
        moment = datetime.fromisoformat(accessed_at)
    except ValueError as exc:
        raise ValueError('final accessed_at must be timezone aware') from exc
    if moment.tzinfo is None or moment.utcoffset() is None or moment.astimezone(UTC) < datetime.fromisoformat(batch.end):
        raise ValueError('final accessed_at must be timezone aware and after the final window')
    raw_recoveries = document['recoveries']
    if not isinstance(raw_recoveries, Mapping):
        raise ValueError('final recoveries must be an object')
    recoveries = []
    for candidate_hash, recovery in raw_recoveries.items():
        if (candidate_hash not in batch.candidate_spec_hashes or not isinstance(recovery, Mapping)
                or set(recovery) != {'request_id', 'reason'}):
            raise ValueError('final recovery must identify one locked candidate and request ID/reason')
        request_id, reason = recovery['request_id'], recovery['reason']
        if (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256
                or '\x00' in request_id or not isinstance(reason, str) or not reason.strip()
                or len(reason) > 2000 or '\x00' in reason):
            raise ValueError('final recovery request ID/reason are invalid')
        recoveries.append((candidate_hash, request_id, reason))
    recoveries.sort(key=lambda row: row[0])
    return FinalHoldoutExecutionRequest(batch, candidates, access_request_id, accessed_at,
                                        owner_token, tuple(recoveries))


def execute_final_holdout_request(request: FinalHoldoutExecutionRequest, *,
                                  cancel_path: Path | None = None) -> dict[str, Any]:
    """Prepare and execute one immutable process request; the cancel file is advisory and bounded."""
    if not isinstance(request, FinalHoldoutExecutionRequest):
        raise ValueError('parsed final holdout execution request is required')
    cancelled = lambda: cancel_path is not None and cancel_path.is_file()
    def checkpoint():
        if cancelled():
            raise ResearchRunCancelled('final holdout cancellation requested')
    checkpoint()
    prepared = prepare_final_holdout_evaluation(request.batch, request.candidates,
        request_id=request.access_request_id, accessed_at=request.accessed_at, checkpoint=checkpoint)
    result = execute_final_holdout_evaluation(prepared, owner_token=request.owner_token,
        cancel_requested=cancelled, recoveries=request.recovery_mapping())
    resource_blocked = any(row['state'] == 'CANCELLED'
        and row['reason'].startswith('ResearchResourceBlocked:') for row in result['candidates'])
    user_cancelled = cancelled() and any(row['state'] in ('CANCELLED', 'NOT_STARTED')
                                         for row in result['candidates'])
    status = 'resource_blocked' if resource_blocked else 'cancelled' if user_cancelled else 'ok'
    return {'status': status, 'kind': 'independent_final_holdout', **result}


def load_final_holdout_exposure_request(path: Path) -> FinalHoldoutExposureRequest:
    """Parse an exposure command without opening its research database."""
    path = Path(path).resolve()
    with path.open('rb') as stream:
        encoded = stream.read(1024 * 1024 + 1)
    if len(encoded) > 1024 * 1024:
        raise ValueError('final exposure request exceeds 1 MiB')
    document = json.loads(encoded.decode('utf-8-sig'))
    if (not isinstance(document, Mapping)
            or set(document) != {'version', 'database', 'batch', 'exposure'}
            or document.get('version') != 'final_holdout_exposure_request/v1'):
        raise ValueError('unsupported final exposure request contract')
    database = _request_path(path.parent, document['database'], 'database')
    if not database.is_file():
        raise ValueError('final exposure requires an existing research database')
    batch = FinalHoldoutBatchSpec.from_dict(_mapping(document['batch'], 'batch'))
    exposure = _mapping(document['exposure'], 'exposure')
    if set(exposure) != {'request_id', 'exposed_at', 'reason'}:
        raise ValueError('final exposure requires request_id, exposed_at and reason only')
    request_id, exposed_at, reason = (exposure['request_id'], exposure['exposed_at'], exposure['reason'])
    if (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256
            or '\x00' in request_id):
        raise ValueError('final exposure request ID must be 1 to 256 characters')
    if (not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
            or '\x00' in reason):
        raise ValueError('final exposure reason must be 1 to 2000 characters')
    if not isinstance(exposed_at, str):
        raise ValueError('final exposed_at must be timezone aware')
    try:
        moment = datetime.fromisoformat(exposed_at)
    except ValueError as exc:
        raise ValueError('final exposed_at must be timezone aware') from exc
    if moment.tzinfo is None or moment.utcoffset() is None or moment.astimezone(UTC) < datetime.fromisoformat(batch.end):
        raise ValueError('final exposed_at must be timezone aware and after the final window')
    return FinalHoldoutExposureRequest(database, batch, request_id, exposed_at, reason)


def execute_final_holdout_exposure(request: FinalHoldoutExposureRequest, *,
                                   cancel_path: Path | None = None) -> dict[str, Any]:
    """Irreversibly expose one verified locked batch; cancellation is checked before mutation."""
    if not isinstance(request, FinalHoldoutExposureRequest):
        raise ValueError('parsed final exposure request is required')
    if cancel_path is not None and cancel_path.is_file():
        raise ResearchRunCancelled('final exposure cancellation requested')
    repository = ResearchRepository(request.database)
    window = repository.load_final_holdout_window(request.batch.window_id)
    if (window is None or window.get('batch_id') != request.batch.batch_id
            or window.get('spec') != request.batch.to_dict()):
        raise ValueError('final exposure batch differs from the locked window')
    if cancel_path is not None and cancel_path.is_file():
        raise ResearchRunCancelled('final exposure cancellation requested')
    recorded = repository.expose_final_holdout(request.batch.window_id,
        request_id=request.request_id, exposed_at=request.exposed_at, reason=request.reason)
    return {'status': 'ok', 'kind': 'final_holdout_exposure',
            'version': 'final_holdout_exposure_result/v1',
            'database': str(request.database.resolve()), 'window_id': request.batch.window_id,
            'batch_id': request.batch.batch_id, 'request_id': request.request_id,
            'state': 'EXPOSED_DEVELOPMENT', 'recorded': recorded}


def load_research_process_request(path: Path) -> ResearchProcessRequest:
    request_path = Path(path)
    try:
        document = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("research request cannot be read") from exc
    if not isinstance(document, Mapping):
        raise ValueError("research request must be a JSON object")
    return _process_request_from_document(document, request_path.resolve().parent)


def _process_request_from_document(document: Mapping[str, Any], root: Path) -> ResearchProcessRequest:
    mode = str(document.get("mode", ""))
    if mode not in {"single_run", "rank_comparison", "limited_search"}:
        raise ValueError(f"unsupported research request mode: {mode}")
    strategy_raw = _mapping(document.get("strategy"), "strategy")
    family = str(document.get("family", BREAKOUT_FAMILY_ID))
    execution_raw = _mapping(document.get("execution"), "execution")
    evaluation_raw = _mapping(document.get("evaluation"), "evaluation")
    session_profile_raw = document.get("session_profile")
    session_profile = (
        str(session_profile_raw).strip() if session_profile_raw is not None else None
    )
    if session_profile is not None and session_profile not in SUPPORTED_RESEARCH_SESSION_PROFILES:
        raise ValueError(f"unsupported research session profile: {session_profile}")
    cost_raw = _mapping(execution_raw.get("cost_model"), "execution.cost_model")
    dataset = _request_path(root, document.get("dataset"), "dataset")
    database = _request_path(root, document.get("database"), "database")
    runs_dir = _request_path(root, document.get("runs_dir"), "runs_dir")
    strategy = parse_strategy_config(family, strategy_raw)
    execution = SimulationExecutionConfig(
        version=str(execution_raw.get("version", "")),
        same_bar_path_version=str(execution_raw.get("same_bar_path_version", "")),
        initial_cash_won=int(execution_raw.get("initial_cash_won", 0)),
        cost_model=SimulationCostModel(**dict(cost_raw)),
    )
    evaluation = ResearchEvaluationSpec.from_dict(evaluation_raw)
    partition_raw = document.get('development_partition')
    partition = DevelopmentPartitionSpec.from_dict(_mapping(partition_raw, 'development_partition')) if partition_raw is not None else None
    if partition is not None:
        if session_profile is None:
            raise ValueError('development partition requires an explicit session profile')
        partition.evaluation_for(evaluation)
    search = None
    if mode == "limited_search":
        search_raw = _mapping(document.get("search"), "search")
        search_document = dict(search_raw)
        search_document.setdefault("dataset_id", "")
        search_document.setdefault("dataset_hash", "")
        search_document.setdefault("split_version", evaluation.version)
        search_document.setdefault("final_holdout_accessed_at", evaluation.final_holdout_accessed_at)
        search_document.setdefault("final_holdout_access_reason", evaluation.final_holdout_access_reason)
        expected_context = {
            "family": family,
            "baseline_strategy": strategy.to_dict(),
            "execution": execution.to_dict(),
            "evaluation": evaluation.to_dict(),
            "implementation_hash": research_implementation_hash(session_profile),
        }
        if session_profile is not None:
            expected_context["session_profile"] = research_session_profile_document(session_profile)
        if partition is not None:
            expected_context['development_partition'] = partition.to_dict()
        supplied_context = search_document.get("research_context")
        if supplied_context is not None and supplied_context != expected_context:
            raise ValueError("search research_context does not match the locked request")
        search_document["research_context"] = expected_context
        search = ExperimentSpec.from_dict(search_document)
    operating_budget = search.resource_budget if search is not None else _mapping(document.get('resource_budget', {}), 'resource_budget')
    request = ResearchProcessRequest(
        mode=mode,
        family=family,
        dataset=dataset,
        database=database,
        runs_dir=runs_dir,
        strategy=strategy,
        execution=execution,
        evaluation=evaluation,
        development_partition=partition,
        session_profile=session_profile,
        search=search,
        resource_limits=ResearchResourceLimits(
            int(operating_budget.get('memory_mb', 512)),
            int(operating_budget.get('cpu_duty_percent', 50)),
        ),
    )
    if not request.dataset.is_dir():
        raise ValueError("research dataset directory does not exist")
    if request.mode == "rank_comparison" and not request.strategy.rank_persistence_enabled:
        raise ValueError("rank comparison request must enable rank persistence")
    if _is_within(request.database, request.dataset) or _is_within(
        request.runs_dir, request.dataset,
    ):
        raise ValueError("research outputs must not overwrite the frozen dataset directory")
    return request


def _prepare_development_request(request, dataset, checkpoint):
    if request.development_partition is None:
        return request, dataset
    if request.session_profile is None:
        raise ValueError('development partition requires an explicit session profile')
    if request.mode == 'limited_search':
        if request.search is None:
            raise ValueError('development partition search spec is required')
        source_identity = (str(dataset.manifest.get('dataset_id', '')), str(dataset.manifest.get('revision_ids_hash', '')))
        if (request.search.dataset_id, request.search.dataset_hash) != source_identity:
            raise ValueError('search spec does not match the frozen source dataset identity')
        if request.search.research_context.get('development_partition') != request.development_partition.to_dict():
            raise ValueError('search development partition does not match the locked request')
    dataset = prepare_development_partition(dataset, request.development_partition, request.evaluation, checkpoint=checkpoint)
    selected_evaluation = request.development_partition.evaluation_for(request.evaluation)
    search = request.search
    if request.mode == 'limited_search':
        context = {**search.research_context, 'evaluation': selected_evaluation.to_dict()}
        search = replace(search, dataset_id=str(dataset.manifest['dataset_id']),
                         dataset_hash=str(dataset.manifest['revision_ids_hash']), research_context=context)
    return replace(request, evaluation=selected_evaluation, search=search), dataset


def _validate_search_input(request, dataset):
    if request.search is None:
        raise ValueError('search spec is required')
    if tuple(request.search.family_allowlist) != (request.family,):
        raise ValueError('search family allowlist does not match the selected strategy family')
    identity = (str(dataset.manifest.get('dataset_id', '')), str(dataset.manifest.get('revision_ids_hash', '')))
    if (request.search.dataset_id, request.search.dataset_hash) != identity:
        raise ValueError('search spec does not match the frozen dataset identity')
    if request.search.split_version != request.evaluation.version:
        raise ValueError('search split version does not match evaluation spec')
    if (request.search.final_holdout_accessed_at, request.search.final_holdout_access_reason) != (
            request.evaluation.final_holdout_accessed_at, request.evaluation.final_holdout_access_reason):
        raise ValueError('search holdout access history does not match evaluation spec')


def register_campaign_request(repository, campaign_id, request, *, cancel_requested=lambda: False):
    """Worker/CLI registration; never load a full partition input on the GUI thread."""
    if request.mode != 'limited_search' or request.search is None:
        raise ValueError('campaign registration requires a limited search request')
    if Path(repository.path).resolve() != request.database.resolve():
        raise ValueError('campaign registration database does not match request')
    guard = ResearchResourceGuard(request.resource_limits)
    guard.preflight(research_input_encoded_bytes(request.dataset))
    def checkpoint():
        if cancel_requested():
            raise ResearchRunCancelled('campaign registration interrupted')
        guard.checkpoint()
    dataset = load_research_input(request.dataset, session_profile=request.session_profile, checkpoint=checkpoint)
    source_spec = request.search
    if (source_spec.dataset_id, source_spec.dataset_hash) != (str(dataset.manifest.get('dataset_id', '')), str(dataset.manifest.get('revision_ids_hash', ''))):
        raise ValueError('campaign source dataset identity does not match request')
    effective, selected = _prepare_development_request(request, dataset, checkpoint)
    _validate_search_input(effective, selected)
    checkpoint()
    added = repository.enqueue_campaign_experiment(campaign_id, effective.search, request.dataset,
        source_spec=source_spec if request.development_partition is not None else None)
    identity = build_research_job_identity(effective.search)
    return {'status': 'ok', 'kind': 'campaign_registration', 'registered': added,
            'campaign_id': campaign_id, 'job_id': identity.job_id, 'experiment_id': identity.experiment_id}


def _campaign_request_from_spec(spec, input_path, database, runs_dir):
    context = spec.research_context
    profile = context.get('session_profile')
    return _process_request_from_document({
        'mode': 'limited_search', 'family': context.get('family'),
        'strategy': context.get('baseline_strategy'), 'execution': context.get('execution'),
        'evaluation': context.get('evaluation'),
        'session_profile': profile.get('profile') if isinstance(profile, Mapping) else None,
        'development_partition': context.get('development_partition'),
        'search': spec.to_dict(), 'dataset': str(input_path),
        'database': str(Path(database).resolve()), 'runs_dir': str(Path(runs_dir).resolve()),
    }, Path.cwd())


def execute_process_request(
    request: ResearchProcessRequest,
    *,
    cancel_path: Path | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    campaign_cycle: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cancelled = lambda: bool(
        (cancel_path is not None and cancel_path.is_file())
        or (cancel_requested is not None and cancel_requested())
    )
    resource_guard = ResearchResourceGuard(request.resource_limits)
    resource_guard.preflight(research_input_encoded_bytes(request.dataset))
    def loading_checkpoint():
        if cancelled is not None and cancelled():
            raise ResearchRunCancelled('research input loading cancellation requested')
        resource_guard.checkpoint()
    dataset = load_research_input(request.dataset, session_profile=request.session_profile, checkpoint=loading_checkpoint)
    request, dataset = _prepare_development_request(request, dataset, loading_checkpoint)
    if campaign_cycle is not None:
        if request.search is None or request.search.to_dict() != campaign_cycle.get('request') or build_research_job_identity(request.search).job_id != campaign_cycle.get('job_id'):
            raise ValueError('stored campaign identity does not match its execution request')
    repository = ResearchRepository(request.database)
    if request.mode == "limited_search":
        assert request.search is not None
        _validate_search_input(request, dataset)
        repository.save_search_experiment(
            request.search.experiment_id, request.search.evidence_dict(),
        )
        identity = build_research_job_identity(request.search)
        previous_cards = repository.load_candidate_cards(request.search.experiment_id)
        enqueued = repository.enqueue_search_job(
            identity.job_id, identity.experiment_id, identity.dataset_id,
            identity.dataset_hash,
            max_retained_jobs=int(request.search.resource_budget.get("max_retained_jobs", 100)),
        ) if campaign_cycle is None else False
        existing_job = repository.load_search_job(identity.job_id)
        assert existing_job is not None
        owner_token = secrets.token_hex(16)
        lease_seconds = max(120, request.search.max_seconds + 60)
        generation = None
        if campaign_cycle is not None:
            if campaign_cycle['request'] != request.search.to_dict():
                raise ValueError('campaign execution does not match the captured operating budget')
            generation = repository.start_search_job(
                identity.job_id, owner_token=owner_token, lease_seconds=lease_seconds,
                campaign_cycle=campaign_cycle,
            )
            existing_job = repository.load_search_job(identity.job_id)
        if existing_job["status"] == "completed":
            return {
                "status": "ok", "kind": "limited_search",
                "job_id": identity.job_id, "job_status": "completed",
                "experiment_id": request.search.experiment_id,
                "attempted_now": 0, "candidate_cards": previous_cards,
                "budget": existing_job["result"].get("budget", {}),
                "notification_proposed": False,
            }
        if existing_job["status"] == "running" and not enqueued and generation is None:
            return {
                "status": "ok", "kind": "limited_search",
                "job_id": identity.job_id, "job_status": "running",
                "experiment_id": request.search.experiment_id,
                "attempted_now": 0, "candidate_cards": previous_cards,
                "budget": {}, "notification_proposed": False,
            }
        if campaign_cycle is not None and generation is None:
            return {'status': 'ok', 'kind': 'limited_search', 'job_id': identity.job_id,
                    'job_status': 'cancelled', 'reason': 'campaign_execution_lease_lost',
                    'experiment_id': request.search.experiment_id, 'attempted_now': 0,
                    'candidate_cards': previous_cards, 'budget': {}, 'notification_proposed': False}
        if generation is None:
            generation = repository.start_search_job(
                identity.job_id, owner_token=owner_token, lease_seconds=lease_seconds,
            )
        if generation is None:
            raise RuntimeError("research search job could not acquire its execution lease")
        search_started = time.monotonic()
        last_lease_renewal = search_started
        lease_lost = False
        active_attempts: dict[str, str] = {}
        completed_now = 0

        def slice_expired() -> bool:
            return bool(
                request.search.max_seconds
                and time.monotonic() - search_started >= request.search.max_seconds
            )

        def execution_interrupted() -> bool:
            nonlocal last_lease_renewal, lease_lost
            if cancelled is not None and cancelled():
                return True
            now = time.monotonic()
            if now - last_lease_renewal >= 20:
                if not repository.renew_search_job(
                    identity.job_id, owner_token=owner_token, generation=generation,
                    lease_seconds=lease_seconds,
                ):
                    lease_lost = True
                    return True
                last_lease_renewal = now
            return lease_lost

        def stop_before_next_trial() -> bool:
            return slice_expired() or execution_interrupted()

        def evaluate(trial) -> TrialOutcome:
            if execution_interrupted():
                raise TrialAttemptInterrupted(_attempt_interrupt_reason(cancelled, lease_lost))
            attempt_id = repository.start_trial_attempt(
                identity.job_id, request.search.experiment_id, trial.trial_id,
                owner_token=owner_token, generation=generation,
            )
            active_attempts[trial.trial_id] = attempt_id
            try:
                resource_guard.checkpoint()
                if trial.variant == "no_trade_baseline":
                    run_id = _execute_no_trade_baseline(
                        dataset, repository, request.execution, request.evaluation,
                        request.search.experiment_id,
                        request.session_profile,
                    )
                    report = repository.load_research_report(run_id)
                    assert report is not None
                    resource_guard.checkpoint()
                    return outcome_from_report(run_id, report)
                strategy_changes = dict(trial.parameters)
                if trial.ablation == "rank_persistence":
                    strategy_changes.update({
                        "rank_persistence_enabled": False,
                        "rank_persistence_required": False,
                    })
                strategy = replace(request.strategy, **strategy_changes)
                execution = request.execution
                if trial.cost_multiplier_ppm != 1_000_000:
                    base_cost = request.execution.cost_model
                    if base_cost is None:
                        return TrialOutcome(
                            status="INELIGIBLE", error="cost stress requires a base cost model",
                        )
                    multiplier = trial.cost_multiplier_ppm
                    stressed_cost = replace(
                        base_cost,
                        commission_bps=base_cost.commission_bps * multiplier // 1_000_000,
                        sell_tax_bps=base_cost.sell_tax_bps * multiplier // 1_000_000,
                        slippage_bps=base_cost.slippage_bps * multiplier // 1_000_000,
                        rate_basis="model_estimate",
                        source=f"{base_cost.source} · stress {multiplier}ppm",
                    )
                    execution = replace(request.execution, cost_model=stressed_cost)
                run = execute_research(
                    dataset, repository, request.runs_dir, strategy,
                    execution, request.evaluation, execution_interrupted,
                    request.session_profile,
                    resource_guard,
                )
            except ResearchResourceBlocked as exc:
                reason = 'resource_blocked:' + str(exc)
                repository.interrupt_trial_attempt(attempt_id, reason, owner_token=owner_token, generation=generation)
                active_attempts.pop(trial.trial_id, None)
                raise TrialAttemptInterrupted(reason)
            except ResearchRunCancelled:
                reason = _attempt_interrupt_reason(cancelled, lease_lost)
                repository.interrupt_trial_attempt(
                    attempt_id, reason, owner_token=owner_token, generation=generation,
                )
                active_attempts.pop(trial.trial_id, None)
                raise TrialAttemptInterrupted(reason)
            report = repository.load_research_report(run.run_id)
            if report is None:
                return TrialOutcome(
                    status="FAILED", run_id=run.run_id, error="research report is missing",
                )
            return apply_selection_constraints(
                outcome_from_report(run.run_id, report), request.search.constraints,
            )

        def record(trial, outcome, card) -> None:
            nonlocal completed_now
            attempt_id = active_attempts.pop(trial.trial_id)
            if execution_interrupted():
                reason = _attempt_interrupt_reason(cancelled, lease_lost)
                repository.interrupt_trial_attempt(attempt_id, reason, owner_token=owner_token, generation=generation)
                raise TrialAttemptInterrupted(reason)
            if not repository.commit_trial_result(
                attempt_id, trial.to_dict(), asdict(outcome), card.to_dict(),
                owner_token=owner_token, generation=generation,
                campaign_cycle=campaign_cycle,
            ):
                repository.interrupt_trial_attempt(attempt_id, 'execution_lease_lost', owner_token=owner_token, generation=generation)
                raise TrialAttemptInterrupted("execution_lease_lost")
            completed_now += 1

        try:
            new_cards = run_limited_search(
                request.search,
                existing_trials=repository.load_search_trials(request.search.experiment_id),
                evaluate=evaluate,
                record=record,
                cancel_requested=stop_before_next_trial,
                batch_cpu_governed=True,
            )
            all_cards = repository.load_candidate_cards(request.search.experiment_id)
            used_trials = len(repository.load_search_trials(request.search.experiment_id))
            target_trials = min(request.search.max_trials, len(generate_trials(request.search)))
            cancelled_now = stop_before_next_trial() and used_trials < target_trials
            job_status = "cancelled" if cancelled_now else "completed"
            budget = {
                "max_trials": request.search.max_trials,
                "max_seconds": request.search.max_seconds,
                "used_trials": used_trials,
                "resource_budget": dict(request.search.resource_budget),
            }
            notification = should_notify_research_result(
                terminal_status=job_status,
                previous_cards=previous_cards,
                current_cards=all_cards,
            )
            if not repository.finish_search_job(
                identity.job_id, job_status,
                {"budget": budget, "notification_proposed": notification},
                owner_token=owner_token, generation=generation,
            ):
                raise RuntimeError("research search job execution lease was lost")
            return {
                "status": "ok", "kind": "limited_search",
                "job_id": identity.job_id, "job_status": job_status,
                "experiment_id": request.search.experiment_id,
                "attempted_now": len(new_cards), "candidate_cards": all_cards,
                "budget": budget, "notification_proposed": notification,
            }
        except TrialAttemptInterrupted as exc:
            all_cards = repository.load_candidate_cards(request.search.experiment_id)
            used_trials = len(repository.load_search_trials(request.search.experiment_id))
            budget = {
                "max_trials": request.search.max_trials,
                "max_seconds": request.search.max_seconds,
                "used_trials": used_trials,
                "resource_budget": dict(request.search.resource_budget),
            }
            if not repository.finish_search_job(
                identity.job_id, "cancelled", {"budget": budget}, str(exc),
                owner_token=owner_token, generation=generation,
            ):
                raise RuntimeError("research search job execution lease was lost") from exc
            return {
                "status": "ok", "kind": "limited_search",
                "job_id": identity.job_id, "job_status": "resource_blocked" if str(exc).startswith('resource_blocked:') else "cancelled",
                "reason": str(exc),
                "experiment_id": request.search.experiment_id,
                "attempted_now": completed_now, "candidate_cards": all_cards,
                "budget": budget, "notification_proposed": False,
            }
        except Exception as exc:
            repository.finish_search_job(
                identity.job_id, "failed", {}, f"{type(exc).__name__}: {exc}",
                owner_token=owner_token, generation=generation,
            )
            raise
    if request.mode == "rank_comparison":
        result = execute_rank_comparison(
            dataset, repository, request.runs_dir, request.strategy,
            request.execution, request.evaluation, cancelled,
            request.session_profile,
            resource_guard,
        )
        return {
            "status": "ok", "kind": "rank_comparison",
            "comparison_id": result.comparison_id,
            "comparison_status": result.status,
            "baseline_run_id": result.baseline_run_id,
            "variant_run_id": result.variant_run_id,
            "manifest": str(result.output_manifest),
            "comparison": repository.load_research_comparison(result.comparison_id),
            "market_regime": result.market_regime,
        }
    result = execute_research(
        dataset, repository, request.runs_dir, request.strategy,
        request.execution, request.evaluation, cancelled,
        request.session_profile,
        resource_guard,
    )
    return {
        "status": "ok", "kind": "single_run", "run_id": result.run_id,
        "report_status": result.report_status, "manifest": str(result.output_manifest),
        "report": repository.load_research_report(result.run_id),
        "market_regime": result.market_regime,
    }


def execute_campaign_cycle(
    repository: ResearchRepository, campaign_id: str, runs_dir: Path,
    *, cancel_path: Path | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    worker_claim: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One owned cycle; scientific settings come only from the durable ledger."""
    owner = secrets.token_hex(16)
    cycle = repository.claim_campaign_cycle(campaign_id, owner_token=owner, lease_seconds=30)
    if cycle is None:
        return {"status": "ok", "kind": "campaign", "campaign": repository.load_campaign(campaign_id)}
    if worker_claim is not None:
        cycle['worker_claim'] = dict(worker_claim)
    sequence, generation = cycle['sequence'], cycle['generation']
    last_check = 0.0
    interrupted = False

    def checkpoint_cancel() -> bool:
        nonlocal last_check, interrupted
        if cancel_path is not None and cancel_path.is_file():
            return True
        if cancel_requested is not None and cancel_requested():
            return True
        now = time.monotonic()
        if now - last_check >= 1:
            interrupted = not repository.renew_campaign_cycle(
                campaign_id, sequence, owner_token=owner, generation=generation, lease_seconds=30,
            )
            last_check = now
        return interrupted

    result: dict[str, Any] = {}
    try:
        spec = ExperimentSpec.from_dict(cycle['request'])
        if spec.final_holdout_accessed_at:
            raise ValueError('campaign automatic final evaluation is disabled')
        source_spec = ExperimentSpec.from_dict(cycle.get('source_request', cycle['request']))
        request = _campaign_request_from_spec(source_spec, cycle['input_path'], repository.path, runs_dir)
        if request.development_partition is None and build_research_job_identity(request.search).job_id != cycle['job_id']:
            raise ValueError('stored campaign identity does not match its execution request')
        if checkpoint_cancel():
            raise ResearchRunCancelled('campaign execution interrupted')
        result = execute_process_request(request, cancel_requested=checkpoint_cancel, campaign_cycle=cycle)
        status = result.get('job_status')
        if status not in ('completed', 'resource_blocked', 'cancelled', 'running'):
            raise ValueError('campaign executor returned an unsupported search status')
        outcome = 'COMPLETED' if status == 'completed' else 'RESOURCE_BLOCKED' if status == 'resource_blocked' else 'INTERRUPTED'
        reason = str(result.get('reason', '')) or ('time_slice_finished' if status == 'cancelled' else 'search_job_busy' if status == 'running' else '')
    except ResearchResourceBlocked as exc:
        outcome, reason = 'RESOURCE_BLOCKED', str(exc)
    except ResearchRunCancelled as exc:
        outcome, reason = 'INTERRUPTED', str(exc)
    except Exception as exc:
        outcome, reason = 'FAILED', f'{type(exc).__name__}: {exc}'
    accepted = repository.finish_campaign_cycle(
        campaign_id, sequence, owner_token=owner, generation=generation, outcome=outcome, reason=reason,
    )
    return {'status': 'ok', 'kind': 'campaign', 'cycle_sequence': sequence,
            'cycle_outcome': outcome, 'cycle_accepted': accepted, 'cycle_reason': reason,
            'campaign': repository.load_campaign(campaign_id), 'last_result': result}


def schedule_next_campaign_hypothesis(
    repository: ResearchRepository,
    campaign_id: str,
    worker_claim: Mapping[str, Any],
) -> dict[str, Any]:
    """Register at most one owned READY node, rotating registered Families."""
    campaign = repository.load_campaign(campaign_id)
    if not campaign['policy'].get('auto_hypotheses', False):
        return {'status': 'disabled', 'reason': 'automatic_hypotheses_disabled'}
    jobs = repository.load_campaign_jobs(campaign_id)
    if sum(job['state'] != 'COMPLETED' for job in jobs) >= int(campaign['policy']['max_active_jobs']):
        return {'status': 'waiting', 'reason': 'campaign_active_backlog_limit'}
    templates = tuple(job for job in jobs if job['source_kind'] != 'auto_hypothesis')
    if not templates:
        return {'status': 'waiting', 'reason': 'hypothesis_template_missing'}
    available = repository.load_next_campaign_hypotheses(campaign_id)
    if not available:
        bindings = repository.load_campaign_hypothesis_bindings(campaign_id)
        return {'status': 'waiting', 'reason': (
            'hypothesis_space_exhausted' if bindings else 'no_registered_hypotheses'
        )}
    last = repository.load_last_enqueued_campaign_hypothesis(campaign_id)
    family_order = tuple(FAMILY_REGISTRY)
    start = (family_order.index(last.family_id) + 1) % len(family_order) if last is not None else 0
    by_family = {item.family_id: item for item in available}
    hypothesis = next(
        by_family[family_order[(start + offset) % len(family_order)]]
        for offset in range(len(family_order))
        if family_order[(start + offset) % len(family_order)] in by_family
    )
    template = templates[0]
    effective = build_hypothesis_experiment_spec(
        ExperimentSpec.from_dict(template['request']), hypothesis,
    )
    source = build_hypothesis_experiment_spec(
        ExperimentSpec.from_dict(template['source_request']), hypothesis,
    )
    needs_source = 'development_partition' in effective.research_context
    added = repository.enqueue_campaign_experiment(
        campaign_id,
        effective,
        Path(template['input_path']),
        source_kind='auto_hypothesis',
        source_spec=source if needs_source else None,
        hypothesis_id=hypothesis.hypothesis_id,
        worker_claim=worker_claim,
    )
    identity = build_research_job_identity(effective)
    return {
        'status': 'scheduled' if added else 'already_scheduled',
        'reason': '',
        'hypothesis_id': hypothesis.hypothesis_id,
        'family_id': hypothesis.family_id,
        'job_id': identity.job_id,
    }


def generate_next_campaign_hypotheses(
    repository: ResearchRepository,
    campaign_id: str,
    worker_claim: Mapping[str, Any],
) -> dict[str, Any]:
    """Expand at most one completed parent from development-only evidence."""
    campaign = repository.load_campaign(campaign_id)
    policy = campaign['policy']
    if not policy.get('auto_hypotheses', False):
        return {'status': 'disabled', 'reason': 'automatic_hypotheses_disabled'}
    candidate = repository.load_next_expandable_campaign_hypothesis(campaign_id)
    if candidate is None:
        return {'status': 'waiting', 'reason': 'no_completed_hypothesis_to_expand'}
    parent = candidate['hypothesis']
    allowed = policy.get('hypothesis_parameter_values', {}).get(parent.family_id)
    if not allowed:
        return {
            'status': 'waiting',
            'reason': 'hypothesis_generation_policy_missing',
            'hypothesis_id': parent.hypothesis_id,
            'family_id': parent.family_id,
        }

    spec = ExperimentSpec.from_dict(candidate['request'])
    baseline_trials = tuple(
        row for row in repository.load_search_trials(spec.experiment_id)
        if row.get('variant') == 'baseline'
    )
    run_id = str(
        baseline_trials[0].get('outcome', {}).get('run_id', '')
    ) if len(baseline_trials) == 1 else ''
    report = repository.load_research_report(run_id) if run_id else None
    if report is None:
        # A terminal failed/no-report trial is still consumed, but cannot become evidence.
        unavailable = DevelopmentEvidence(
            status='NOT_APPLICABLE',
            reasons=('baseline_development_evidence_unavailable',),
            fold_refs=(), net_pnl_won=None, max_drawdown_won=None,
            closed_trade_count=0, active_day_count=0,
        )
        snapshot = build_development_evidence_snapshot(
            run_id or f'job:{candidate["job_id"]}', unavailable,
        )
        repository.record_campaign_hypothesis_expansion(
            campaign_id, parent.hypothesis_id, snapshot,
            state='EXHAUSTED', generated_count=0,
            reason='baseline_development_evidence_unavailable',
            worker_claim=worker_claim,
        )
        return {
            'status': 'exhausted',
            'reason': 'baseline_development_evidence_unavailable',
            'hypothesis_id': parent.hypothesis_id,
            'family_id': parent.family_id,
        }

    evidence = build_development_evidence(report)
    snapshot = build_development_evidence_snapshot(run_id, evidence)
    try:
        generated = generate_followup_hypotheses(FollowupHypothesisGenerationRequest(
            parent=parent,
            allowed_parameter_values={
                key: tuple(values) for key, values in allowed.items()
            },
            development_evidence_refs=(DevelopmentEvidenceRef(snapshot.evidence_id),),
            seed=int(policy['hypothesis_seed']),
            max_variants=int(policy['max_generated_hypotheses_per_cycle']),
        ))
    except (KeyError, TypeError, ValueError) as exc:
        repository.record_campaign_hypothesis_expansion(
            campaign_id, parent.hypothesis_id, snapshot,
            state='BLOCKED', generated_count=0,
            reason=f'{type(exc).__name__}: {exc}', worker_claim=worker_claim,
        )
        return {
            'status': 'blocked',
            'reason': 'hypothesis_generation_policy_invalid',
            'detail': f'{type(exc).__name__}: {exc}',
            'hypothesis_id': parent.hypothesis_id,
            'family_id': parent.family_id,
        }

    existing = repository.load_campaign_registered_hypotheses(campaign_id)
    registered_ids = {item.hypothesis_id for item in existing}
    configurations = {
        (item.family_id, json.dumps(
            item.parameters, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        ))
        for item in existing
    }
    remaining = max(0, int(policy['max_hypotheses']) - len(existing))
    unique = tuple(
        item for item in generated
        if item.hypothesis_id not in registered_ids
        and (item.family_id, json.dumps(
            item.parameters, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        )) not in configurations
    )[:remaining]
    if unique:
        repository.save_research_hypotheses(unique)
        repository.register_campaign_hypotheses(
            campaign_id,
            tuple(item.hypothesis_id for item in unique),
            worker_claim=worker_claim,
        )
    accepted = tuple(item for item in generated if item.hypothesis_id in registered_ids) + unique
    state = 'GENERATED' if accepted else 'EXHAUSTED'
    reason = '' if accepted else (
        'hypothesis_limit_reached' if remaining == 0
        else 'registered_configuration_space_exhausted'
    )
    repository.record_campaign_hypothesis_expansion(
        campaign_id, parent.hypothesis_id, snapshot,
        state=state, generated_count=len(accepted), reason=reason,
        worker_claim=worker_claim,
    )
    return {
        'status': 'generated' if accepted else 'exhausted',
        'reason': reason,
        'hypothesis_id': parent.hypothesis_id,
        'family_id': parent.family_id,
        'evidence_id': snapshot.evidence_id,
        'generated_count': len(accepted),
        'generated_hypothesis_ids': [item.hypothesis_id for item in accepted],
    }


def discover_campaign_inputs(repository, campaign_id, worker_claim, *, cancel_requested=lambda: False, now=None):
    """Inspect explicitly configured shallow folders inside the existing worker."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError('input discovery clock must be timezone-aware')
    moment = moment.astimezone(UTC)
    summary = {'registered': 0, 'unchanged': 0, 'out_of_scope': 0, 'waiting_backlog': 0, 'errors': []}
    all_sources = repository.load_campaign_input_sources(campaign_id)
    sources = []
    for source in all_sources:
        if not source['enabled']:
            continue
        if source['state'] != 'NEEDS_ATTENTION' and source['next_scan_at'] <= moment.isoformat():
            sources.append(source)
        elif source['reason']:
            summary['errors'].append(source['reason'])
    if not sources:
        return summary
    jobs = {job['job_id']: job for job in repository.load_campaign_jobs(campaign_id)}
    active_jobs = sum(job['state'] != 'COMPLETED' for job in jobs.values())
    active_limit = repository.load_campaign(campaign_id)['policy']['max_active_jobs']
    for source in sources:
        if source['nas_auto_prepare'] and active_jobs >= active_limit:
            repository.record_campaign_input_scan(source['source_id'], now=moment)
            summary['waiting_backlog'] += 1
            continue
        try:
            template = jobs[source['template_job_id']]
            spec = ExperimentSpec.from_dict(template['source_request'])
            profile = (spec.research_context.get('session_profile') or {}).get('profile')
            if spec.research_context.get('implementation_hash') != research_implementation_hash(profile):
                raise ValueError('input source template implementation hash is outdated; register a new explicit request')
            guard = ResearchResourceGuard(ResearchResourceLimits(int(spec.resource_budget.get('memory_mb', 512)), int(spec.resource_budget.get('cpu_duty_percent', 50))))
            deadline = time.monotonic() + min(30, spec.max_seconds)
            def checkpoint():
                if cancel_requested():
                    raise ResearchRunCancelled('input discovery interrupted')
                guard.checkpoint()
                if time.monotonic() >= deadline:
                    raise ResearchResourceBlocked('input_discovery_time_budget_exceeded')
            def load(path):
                checkpoint()
                guard.preflight(research_input_encoded_bytes(path))
                return load_research_input(path, session_profile=profile, checkpoint=checkpoint)
            scope = json.loads(source['scope_json']) if source['scope_json'] else None
            if scope is None:
                baseline = load(Path(template['input_path']))
                if baseline.manifest.get('dataset_id') != spec.dataset_id or baseline.manifest.get('revision_ids_hash') != spec.dataset_hash:
                    raise ValueError('source template dataset identity does not match frozen job')
                scope = campaign_input_scope(baseline)
                fingerprint = campaign_input_fingerprint(baseline, checkpoint=checkpoint)
                baseline_manifest_hash = hashlib.sha256((Path(template['input_path']) / 'manifest.json').read_bytes()).hexdigest()
                repository.initialize_campaign_input_source(source['source_id'], scope, fingerprint, Path(template['input_path']), template['job_id'], baseline_manifest_hash, worker_claim)
                del baseline
            root = Path(source['root'])
            if source['nas_auto_prepare']:
                validate_research_storage_root(root)
                baseline_path = Path(template['input_path'])
                baseline_record = next(row for row in repository.load_campaign_input_acceptances(source['source_id']) if row['input_path'] == str(baseline_path.resolve()))
                if hashlib.sha256((baseline_path / 'manifest.json').read_bytes()).hexdigest() != baseline_record['manifest_hash']:
                    raise ValueError('source template manifest was modified')
            def register_paths(paths):
                nonlocal active_jobs
                accepted = {row['input_path']: row for row in repository.load_campaign_input_acceptances(source['source_id'])}
                for path in sorted(paths):
                    checkpoint()
                    if (path / 'manifest.json').stat().st_size > 1024 * 1024:
                        raise ResearchResourceBlocked('input_manifest_too_large')
                    manifest_hash = hashlib.sha256((path / 'manifest.json').read_bytes()).hexdigest()
                    previous = accepted.get(str(path.resolve()))
                    if previous is not None:
                        if previous['manifest_hash'] != manifest_hash:
                            raise ValueError('accepted frozen input manifest was modified')
                        summary['unchanged'] += 1
                        continue
                    dataset = load(path)
                    if campaign_input_scope(dataset) != scope:
                        summary['out_of_scope'] += 1
                        del dataset
                        continue
                    fingerprint = campaign_input_fingerprint(dataset, checkpoint=checkpoint)
                    candidate = replace(spec, dataset_id=str(dataset.manifest['dataset_id']), dataset_hash=str(dataset.manifest['revision_ids_hash']))
                    source_spec = None
                    if 'development_partition' in candidate.research_context:
                        source_spec = candidate
                        request = _campaign_request_from_spec(candidate, path, repository.path, repository.path.parent / 'runs')
                        effective, selected = _prepare_development_request(request, dataset, checkpoint)
                        candidate = effective.search
                        del selected
                    added = repository.enqueue_campaign_experiment(campaign_id, candidate, path, source_kind='new_data',
                        input_source=(source['source_id'], fingerprint, worker_claim, manifest_hash),
                        **({'source_spec': source_spec} if source_spec is not None else {}))
                    summary['registered' if added else 'unchanged'] += 1
                    active_jobs += int(added)
                    del dataset
            # Recover complete inputs before network access or the download capacity gate.
            directories = []
            entries = () if source['nas_auto_prepare'] and not root.exists() else root.iterdir()
            for entry_count, entry in enumerate(entries, start=1):
                checkpoint()
                if entry_count > 10000:
                    raise ResearchResourceBlocked('input_source_directory_entry_limit_exceeded')
                if entry.is_symlink():
                    continue
                if entry.is_dir() and (entry / 'manifest.json').is_file():
                    directories.append(entry)
                    if len(directories) > 1000:
                        raise ValueError('input source exceeds 1000 prepared directories; narrow the source folder')
            register_paths(directories)
            if source['nas_auto_prepare'] and active_jobs >= active_limit:
                repository.record_campaign_input_scan(source['source_id'], now=moment)
                summary['waiting_backlog'] += 1
                continue
            if source['nas_auto_prepare']:
                settings = DataSourceConfig(Path(source['nas_config_path'])).load()
                if settings.mode not in ('personal_server', 'local_server'):
                    raise ValueError('NAS input preparation requires an existing NAS connection setting; direct Kiwoom fallback is not used')
                cleanup = repository.cleanup_campaign_incomplete_staging(source['source_id'], worker_claim, checkpoint=checkpoint, now=moment)
                summary.setdefault('staging_cleanup', []).append(cleanup)
                summary['errors'].extend(cleanup['errors'])
                acceptances = repository.load_campaign_input_acceptances(source['source_id'])
                client = CentralContentClient(settings.server_url, settings.access_token, timeout_seconds=10)
                storage_operation = None
                storage_outcome, storage_path = 'FAILED', ''
                def begin_storage():
                    nonlocal storage_operation
                    storage_operation = repository.begin_campaign_storage_preparation(source['source_id'], worker_claim)
                    if storage_operation is None:
                        raise ResearchResourceBlocked('research_storage_preparation_busy')
                    return storage_operation
                def storage_publication():
                    return repository.campaign_storage_publication(storage_operation, worker_claim)
                def record_storage_staging(path):
                    repository.record_campaign_storage_staging(storage_operation, worker_claim, path)
                try:
                    prepared = prepare_campaign_nas_input(client, source, baseline_path, scope, {row['fingerprint'] for row in acceptances}, checkpoint=checkpoint, max_encoded_bytes=guard.limits.memory_mb * 1024 * 1024 // 16, begin_preparation=begin_storage, publication_context=storage_publication, record_staging=record_storage_staging)
                    storage_outcome = 'PUBLISHED' if prepared['status'] == 'prepared' else 'UNCHANGED'
                    storage_path = prepared['path'] or ''
                except ResearchRunCancelled:
                    storage_outcome = 'CANCELLED'
                    raise
                except ResearchResourceBlocked:
                    storage_outcome = 'BLOCKED'
                    raise
                except CentralContentHttpError as exc:
                    raise ValueError(f'NAS preparation request failed: HTTP {exc.status_code}') from None
                except CentralContentUnavailableError:
                    raise ValueError('NAS preparation server is unavailable') from None
                finally:
                    if storage_operation is not None:
                        repository.finish_campaign_storage_preparation(storage_operation, worker_claim, outcome=storage_outcome, input_path=storage_path)
                checkpoint()
                summary['nas_prepared'] = summary.get('nas_prepared', 0) + int(prepared['status'] == 'prepared')
                if prepared['status'] == 'prepared':
                    register_paths([Path(prepared['path'])])
            if source['nas_auto_prepare']:
                repository.record_campaign_prepared_input(source['source_id'], prepared['signature'], worker_claim)
                # Diagnostic only: no archive/deletion policy or filesystem mutation.
                summary.setdefault('storage_inventory', []).append(
                    repository.inspect_campaign_input_storage(source['source_id'], checkpoint=checkpoint, max_entries=1000))
            repository.record_campaign_input_scan(source['source_id'], now=moment)
        except ResearchRunCancelled:
            return summary
        except (OSError, ValueError, KeyError, StopIteration, sqlite3.Error, ResearchResourceBlocked) as exc:
            reason = f'{type(exc).__name__}: {exc}'
            if isinstance(exc, ResearchResourceBlocked) and str(exc) in ('research_storage_preparation_busy', 'research_storage_capacity_reached'):
                repository.record_campaign_storage_wait(source['source_id'], capacity=str(exc) == 'research_storage_capacity_reached', now=moment)
                summary['waiting_storage'] = summary.get('waiting_storage', 0) + 1
                continue
            if isinstance(exc, ValueError) and 'backlog limit reached' in str(exc):
                repository.record_campaign_input_scan(source['source_id'], now=moment)
                summary['waiting_backlog'] += 1
                continue
            repository.record_campaign_input_scan(source['source_id'], error=reason, now=moment)
            summary['errors'].append(reason)
    return summary


def run_campaign_worker(database: Path, campaign_id: str, runs_dir: Path, result_path: Path,
                        *, cancel_path: Path | None = None, worker_claim: Mapping[str, Any] | None = None) -> dict[str, Any]:
    repository = ResearchRepository(database)
    result = {'status': 'ok', 'kind': 'campaign', 'campaign': repository.load_campaign(campaign_id)}
    if cancel_path is not None and cancel_path.is_file() and worker_claim is None:
        return result
    claim = worker_claim or repository.claim_campaign_worker(campaign_id, owner_token=secrets.token_hex(16))
    if claim is None:
        return {**result, 'worker_status': 'busy_or_blocked'}
    last_check = 0.0
    lease_lost = False

    def worker_cancel() -> bool:
        nonlocal last_check, lease_lost
        if cancel_path is not None and cancel_path.is_file():
            return True
        now = time.monotonic()
        if now - last_check >= 1:
            lease_lost = not repository.renew_campaign_worker(campaign_id, owner_token=claim['owner_token'], generation=claim['generation'])
            last_check = now
        return lease_lost

    try:
        while repository.load_campaign(campaign_id)['desired_state'] == 'RUNNING' and not worker_cancel():
            discovery = discover_campaign_inputs(repository, campaign_id, claim, cancel_requested=worker_cancel)
            if worker_cancel():
                break
            hypothesis_generation = generate_next_campaign_hypotheses(
                repository, campaign_id, claim,
            )
            hypothesis_scheduling = schedule_next_campaign_hypothesis(
                repository, campaign_id, claim,
            )
            update = execute_campaign_cycle(repository, campaign_id, runs_dir, cancel_path=cancel_path, cancel_requested=worker_cancel, worker_claim=claim)
            # Waiting must not erase the last completed trial table.
            result = {**result, **update, 'last_result': update.get('last_result') or result.get('last_result', {})}
            result['input_discovery'] = discovery
            result['hypothesis_generation'] = hypothesis_generation
            result['hypothesis_scheduling'] = hypothesis_scheduling
            _write_result(result_path, result)
            for _ in range(10):
                if worker_cancel():
                    break
                time.sleep(.1)
        expected = repository.load_campaign(campaign_id)['desired_state'] != 'RUNNING' or (cancel_path is not None and cancel_path.is_file())
        repository.finish_campaign_worker(campaign_id, owner_token=claim['owner_token'], generation=claim['generation'],
                                          outcome='EXPECTED_EXIT' if expected else 'FAILED', reason='cancel_or_pause' if expected else 'worker_lease_lost', exit_code=0)
    except BaseException as exc:
        expected = repository.load_campaign(campaign_id)['desired_state'] != 'RUNNING' or (cancel_path is not None and cancel_path.is_file())
        repository.finish_campaign_worker(campaign_id, owner_token=claim['owner_token'], generation=claim['generation'],
                                          outcome='EXPECTED_EXIT' if expected else 'FAILED', reason=f'{type(exc).__name__}: {exc}', exit_code=1)
        raise
    result['worker_status'] = 'exited'
    result['worker_generation'] = claim['generation']
    result['campaign'] = repository.load_campaign(campaign_id)
    return result


@dataclass(frozen=True)
class DevelopmentValidationRequest:
    request: ResearchProcessRequest
    dataset_id: str
    dataset_hash: str
    fold_names: tuple[str, ...]
    max_seconds: int
    symbol_partitions: tuple[DevelopmentSymbolPartitionSpec, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.symbol_partitions, tuple):
            raise ValueError('validation symbol partitions must be immutable')
        if not self.symbol_partitions:
            return
        if (not 2 <= len(self.symbol_partitions) <= 20
                or any(not isinstance(policy, DevelopmentSymbolPartitionSpec) for policy in self.symbol_partitions)):
            raise ValueError('validation requires 2 to 20 stock buckets')
        first = self.symbol_partitions[0]
        if any((policy.version, policy.salt, policy.bucket_count) != (first.version, first.salt, first.bucket_count)
               for policy in self.symbol_partitions):
            raise ValueError('validation stock buckets must share one frozen hash policy')
        buckets = tuple(policy.bucket for policy in self.symbol_partitions)
        if buckets != tuple(sorted(set(buckets))):
            raise ValueError('validation stock buckets must be unique and ascending')
        if not 1 <= len(self.fold_names) <= 20 or len(self.fold_names) * len(buckets) > 200:
            raise ValueError('validation requires 1 to 20 development folds and at most 200 group-window steps')

    @property
    def version(self) -> str:
        return 'independent_development_validation/v2' if self.symbol_partitions else 'independent_development_validation/v1'

    def symbol_partition_document(self) -> dict[str, Any] | None:
        if not self.symbol_partitions:
            return None
        first = self.symbol_partitions[0]
        return {'version': first.version, 'salt': first.salt, 'bucket_count': first.bucket_count,
                'buckets': [policy.bucket for policy in self.symbol_partitions]}

    def to_dict(self) -> dict[str, Any]:
        """Normalized snapshot of the parsed fixed request; no input/DB reads."""
        request = self.request
        document = {'version': self.version,
                'request': {'mode': request.mode, 'family': request.family,
                    'dataset': str(request.dataset.resolve()), 'database': str(request.database.resolve()),
                    'runs_dir': str(request.runs_dir.resolve()), 'session_profile': request.session_profile,
                    'strategy': request.strategy.to_dict(), 'execution': request.execution.to_dict(),
                    'evaluation': request.evaluation.to_dict(),
                    'resource_budget': {'memory_mb': request.resource_limits.memory_mb,
                                        'cpu_duty_percent': request.resource_limits.cpu_duty_percent}},
                'dataset_id': self.dataset_id, 'dataset_hash': self.dataset_hash,
                'fold_names': list(self.fold_names), 'max_seconds': self.max_seconds}
        if self.symbol_partitions:
            document['symbol_partition'] = self.symbol_partition_document()
        return document


def load_development_validation_request(path: Path) -> DevelopmentValidationRequest:
    path = Path(path).resolve()
    with path.open('rb') as stream:
        encoded = stream.read(1024 * 1024 + 1)
    if len(encoded) > 1024 * 1024:
        raise ValueError('development validation request exceeds 1 MiB')
    document = json.loads(encoded.decode('utf-8-sig'))
    required = {'version', 'request', 'dataset_id', 'dataset_hash', 'fold_names', 'max_seconds'}
    if not isinstance(document, dict) or document.get('version') not in (
            'independent_development_validation/v1', 'independent_development_validation/v2'):
        raise ValueError('unsupported development validation request contract')
    grouped = document['version'] == 'independent_development_validation/v2'
    if set(document) != required | ({'symbol_partition'} if grouped else set()):
        raise ValueError('unsupported development validation request contract')
    symbols = ()
    if grouped:
        policy = document['symbol_partition']
        if not isinstance(policy, dict) or set(policy) != {'version', 'salt', 'bucket_count', 'buckets'}:
            raise ValueError('unsupported validation stock hash policy')
        buckets = policy['buckets']
        if not isinstance(buckets, list) or not 2 <= len(buckets) <= 20:
            raise ValueError('validation requires 2 to 20 stock buckets')
        symbols = tuple(DevelopmentSymbolPartitionSpec(policy['version'], policy['salt'], policy['bucket_count'], bucket)
                        for bucket in buckets)
    request = _process_request_from_document(_mapping(document['request'], 'request'), path.parent)
    if request.mode != 'single_run' or request.development_partition is not None or request.session_profile is None:
        raise ValueError('validation requires one fixed single_run strategy, full evaluation and explicit session profile')
    names = document['fold_names']
    if (not isinstance(names, list) or not (1 if grouped else 2) <= len(names) <= 20
            or any(not isinstance(name, str) for name in names) or len(set(names)) != len(names)):
        raise ValueError('validation requires unique development fold names within the version limit')
    selected = [DevelopmentPartitionSpec('development_partition/v2', name).evaluation_for(request.evaluation).folds[0] for name in names]
    if selected != sorted(selected, key=lambda fold: datetime.fromisoformat(fold.start)):
        raise ValueError('development validation folds must be in chronological order')
    seconds = document['max_seconds']
    if type(seconds) is not int or not 1 <= seconds <= 3600:
        raise ValueError('validation max_seconds must be an integer from 1 to 3600')
    for key in ('dataset_id', 'dataset_hash'):
        if not isinstance(document[key], str) or not document[key].strip():
            raise ValueError('validation requires locked source dataset ID/hash')
    return DevelopmentValidationRequest(request, document['dataset_id'], document['dataset_hash'], tuple(names), seconds, symbols)


class _ValidationTimeBudgetExpired(ResearchRunCancelled):
    pass


def execute_development_validation(batch: DevelopmentValidationRequest, *, cancel_path: Path | None = None,
                                   result_path: Path | None = None) -> dict[str, Any]:
    """One frozen strategy, sequential fresh engines, scoped atomic claims and completed cache."""
    request = batch.request
    implementation_hash = research_implementation_hash(request.session_profile)
    deadline = time.monotonic() + batch.max_seconds
    guard = ResearchResourceGuard(request.resource_limits)
    cancelled = lambda: cancel_path is not None and cancel_path.is_file()
    stopped = lambda: cancelled() or time.monotonic() >= deadline
    rows = []
    for name in batch.fold_names:
        fold = next(fold for fold in request.evaluation.folds if fold.name == name)
        for policy in batch.symbol_partitions or (None,):
            row = {'fold_name': name, 'role': fold.role, 'start': fold.start, 'end': fold.end,
                   'run_id': '', 'state': 'NOT_STARTED', 'reason': ''}
            if policy is not None:
                row.update(symbol_bucket=policy.bucket, step_key=f'{name}/bucket-{policy.bucket}')
            rows.append(row)
    repository = None
    status, reason, attempted_now = 'ok', '', 0
    def document():
        ids = tuple(row['run_id'] for row in rows if row['run_id'])
        complete = all(row['state'] in ('COMPLETED', 'CACHED') for row in rows)
        terminal_reason = reason or ('development_validation_failures_require_review'
            if any(row['state'] in ('FAILED', 'CACHE_INVALID') for row in rows) else
            'existing_runs_require_owner_or_recovery_check' if any(row['state'] == 'BUSY' for row in rows) else '')
        result = {'status': status, 'kind': 'independent_development_validation',
                'batch_status': 'CANCELLED' if status == 'cancelled' else 'RESOURCE_BLOCKED' if status == 'resource_blocked'
                                else 'COMPLETED' if complete else 'PARTIAL',
                'reason': terminal_reason, 'database': str(request.database.resolve()), 'fold_names': list(batch.fold_names),
                'implementation_hash': implementation_hash,
                'steps': [dict(row) for row in rows], 'run_ids': list(ids), 'attempted_now': attempted_now,
                'cached_count': sum(row['state'] == 'CACHED' for row in rows),
                'not_started_count': sum(row['state'] == 'NOT_STARTED' for row in rows),
                'comparison_scope': 'identified_runs_only/v1',
                'comparison': repository.load_independent_development_comparison(ids).to_dict()
                              if not batch.symbol_partitions and repository is not None and ids else None}
        if batch.symbol_partitions:
            result.update(version=batch.version, symbol_partition=batch.symbol_partition_document(),
                          requested_step_count=len(rows), comparison_scope='per_symbol_bucket_identified_runs/v1', group_comparisons=[])
            for policy in batch.symbol_partitions:
                group_rows = [row for row in rows if row['symbol_bucket'] == policy.bucket]
                group_ids = tuple(row['run_id'] for row in group_rows if row['run_id'])
                result['group_comparisons'].append({'symbol_bucket': policy.bucket,
                    'requested_step_count': len(group_rows),
                    'completed_step_count': sum(row['state'] in ('COMPLETED', 'CACHED') for row in group_rows),
                    'not_started_count': sum(row['state'] == 'NOT_STARTED' for row in group_rows),
                    'run_ids': list(group_ids), 'comparison_scope': 'identified_runs_only/v1',
                    'comparison': repository.load_independent_development_comparison(group_ids).to_dict()
                                  if repository is not None and group_ids else None})
        return result
    def finish():
        result = document()
        if result_path is not None:
            _write_result(result_path, result)
        return result
    def checkpoint():
        if cancelled():
            raise ResearchRunCancelled('development validation cancellation requested')
        if time.monotonic() >= deadline:
            raise _ValidationTimeBudgetExpired('validation_time_budget_exhausted')
        guard.checkpoint()
    try:
        checkpoint()
        guard.preflight(research_input_encoded_bytes(request.dataset))
        dataset = load_research_input(request.dataset, session_profile=request.session_profile, checkpoint=checkpoint)
        if (dataset.manifest.get('dataset_id'), dataset.manifest.get('revision_ids_hash')) != (batch.dataset_id, batch.dataset_hash):
            raise ValueError('development validation does not match locked source dataset identity')
        checkpoint()
    except _ValidationTimeBudgetExpired as exc:
        reason = str(exc)
        return finish()
    except ResearchRunCancelled as exc:
        status, reason = 'cancelled', str(exc)
        return finish()
    except ResearchResourceBlocked as exc:
        status, reason = 'resource_blocked', str(exc)
        return finish()
    repository = ResearchRepository(request.database)
    for row in rows:
        if stopped():
            status, reason = ('cancelled', 'user_cancelled') if cancelled() else ('ok', 'validation_time_budget_exhausted')
            break
        claimed = False
        try:
            policy = next((policy for policy in batch.symbol_partitions if policy.bucket == row.get('symbol_bucket')), None)
            partition = (DevelopmentPartitionSpec('development_partition/v3', row['fold_name'], symbol_partition=policy)
                         if policy is not None else DevelopmentPartitionSpec('development_partition/v2', row['fold_name']))
            selected = prepare_development_partition(dataset, partition, request.evaluation, checkpoint=checkpoint)
            evaluation = partition.evaluation_for(request.evaluation)
            run_id, spec = research_run_identity(selected, request.strategy, request.execution, evaluation,
                session_profile=request.session_profile, execution_scope='independent_development_validation/v1')
            if spec['code_hash'] != implementation_hash:
                raise ValueError('research implementation changed during locked validation')
            row['run_id'] = run_id
            checkpoint()
            if stopped():
                status, reason = ('cancelled', 'user_cancelled') if cancelled() else ('ok', 'validation_time_budget_exhausted')
                break
            claim = repository.start_run(run_id, spec, selected.manifest, claim_independent=True)
            if claim == 'completed':
                projection = repository.load_independent_development_comparison((run_id,))
                row['state'] = 'CACHE_INVALID' if projection.partitions[0].status == 'INVALID' else 'CACHED'
                row['reason'] = '; '.join(projection.partitions[0].reasons) if row['state'] == 'CACHE_INVALID' else ''
            elif claim in ('busy', 'failed'):
                row['state'] = 'BUSY' if claim == 'busy' else 'FAILED'
                row['reason'] = 'existing_run_requires_owner_or_recovery_check' if claim == 'busy' else str(repository.load_run(run_id).get('error', 'existing_run_failed'))
            else:
                claimed = True
                row['state'] = 'RUNNING'
                if result_path is not None:
                    _write_result(result_path, document())
                attempted_now += 1
                result = execute_research(selected, repository, request.runs_dir, request.strategy, request.execution,
                    evaluation, stopped, request.session_profile, guard, execution_scope='independent_development_validation/v1',
                    expected_code_hash=implementation_hash)
                if result.run_id != run_id:
                    raise RuntimeError('validation scientific run identity mismatch')
                row['state'] = 'COMPLETED'
        except (ResearchRunCancelled, ResearchResourceBlocked) as exc:
            if claimed and repository.load_run(row['run_id'])['status'] == 'running':
                repository.cancel_run(row['run_id'])
            status, reason = ('resource_blocked', str(exc)) if isinstance(exc, ResearchResourceBlocked) else (
                ('cancelled', 'user_cancelled') if cancelled() else ('ok', 'validation_time_budget_exhausted'))
            row['state'] = 'RESOURCE_BLOCKED' if status == 'resource_blocked' else 'CANCELLED' if status == 'cancelled' else 'BUDGET_EXHAUSTED'
            row['reason'] = reason
            break
        except (OSError, TypeError, ValueError, RuntimeError, sqlite3.Error) as exc:
            row['state'], row['reason'] = 'FAILED', f'{type(exc).__name__}: {exc}'
            if claimed and repository.load_run(row['run_id'])['status'] == 'running':
                repository.fail_run(row['run_id'], row['reason'])
        finally:
            if result_path is not None:
                _write_result(result_path, document())
        # Release the copied partition before preparing the next one.
        selected = None
    return finish()


@dataclass(frozen=True)
class IndependentComparisonRequest:
    database: Path
    run_ids: tuple[str, ...]


def load_independent_comparison_request(path: Path) -> IndependentComparisonRequest:
    path = Path(path).resolve()
    with path.open('rb') as stream:
        encoded = stream.read(65537)
    if len(encoded) > 65536:
        raise ValueError('comparison request exceeds 64 KiB')
    document = json.loads(encoded.decode('utf-8-sig'))
    if not isinstance(document, dict) or set(document) != {'version', 'database', 'run_ids'}:
        raise ValueError('comparison request requires version, database and run_ids only')
    if document['version'] != 'independent_development_comparison_request/v1':
        raise ValueError('unsupported independent comparison request version')
    ids = document['run_ids']
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 200
            or any(not isinstance(value, str) or not value.strip() or len(value) > 256 for value in ids)):
        raise ValueError('comparison requires 1 to 200 nonempty run IDs, at most 256 characters each')
    return IndependentComparisonRequest(_request_path(path.parent, document['database'], 'database'), tuple(ids))


def execute_independent_comparison(request: IndependentComparisonRequest, *, cancel_path: Path | None = None) -> dict[str, Any]:
    def checkpoint():
        if cancel_path is not None and cancel_path.is_file():
            raise ResearchRunCancelled('independent comparison cancelled')
    checkpoint()
    repository = ResearchRepository(request.database, read_only=True)
    checkpoint()
    comparison = repository.load_independent_development_comparison(request.run_ids)
    checkpoint()
    return {'status': 'ok', 'kind': 'independent_development_comparison',
            'database': str(request.database.resolve()), 'run_ids': list(request.run_ids),
            'comparison': comparison.to_dict()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="별도 프로세스에서 고정 연구 요청을 실행합니다.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--request", type=Path)
    source.add_argument("--campaign")
    source.add_argument('--compare-runs', type=Path, help='Read an explicit independent development comparison request')
    source.add_argument('--validate-partitions', type=Path, help='Sequentially validate a fixed strategy on locked development folds')
    source.add_argument('--evaluate-final', type=Path, help='Execute one locked final holdout request')
    source.add_argument('--expose-final', type=Path, help='Mark one locked final window as exposed development evidence')
    parser.add_argument('--database', type=Path)
    parser.add_argument('--runs-dir', type=Path)
    parser.add_argument('--worker-token')
    parser.add_argument('--worker-generation', type=int)
    parser.add_argument('--register-campaign', help='Validate and register --request in an existing campaign')
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--cancel", type=Path)
    args = parser.parse_args(argv)
    if args.expose_final:
        if args.register_campaign or args.database or args.runs_dir or args.worker_token or args.worker_generation is not None:
            parser.error('--expose-final cannot be combined with campaign options')
        try:
            exposure_request = load_final_holdout_exposure_request(args.expose_final)
        except (OSError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        protected = {args.expose_final.resolve(), exposure_request.database.resolve()}
        outputs = tuple(path.resolve() for path in (args.result, args.cancel) if path is not None)
        if len(set(outputs)) != len(outputs) or any(path in protected for path in outputs):
            parser.error('final exposure result/cancel must not overwrite request or database')
    if args.evaluate_final:
        if args.register_campaign or args.database or args.runs_dir or args.worker_token or args.worker_generation is not None:
            parser.error('--evaluate-final cannot be combined with campaign options')
        try:
            final_request = load_final_holdout_execution_request(args.evaluate_final)
        except (OSError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        first = final_request.candidates[0]
        protected = {args.evaluate_final.resolve(), first.database.resolve()}
        outputs = tuple(path.resolve() for path in (args.result, args.cancel) if path is not None)
        if (len(set(outputs)) != len(outputs)
                or any(path in protected or _is_within(path, first.dataset) or _is_within(path, first.runs_dir)
                       for path in outputs)):
            parser.error('final result/cancel must not overwrite request, database, frozen dataset or run artifacts')
    if args.validate_partitions:
        if args.register_campaign or args.database or args.runs_dir or args.worker_token or args.worker_generation is not None:
            parser.error('--validate-partitions cannot be combined with campaign options')
        try:
            validation_request = load_development_validation_request(args.validate_partitions)
        except (OSError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        protected = {args.validate_partitions.resolve(), validation_request.request.database.resolve()}
        for output in (args.result, args.cancel):
            if output is not None and (output.resolve() in protected or _is_within(output.resolve(), validation_request.request.dataset)):
                parser.error('validation result/cancel must not overwrite request, database or frozen dataset')
    # A query must never overwrite its source/DB, including when writing an error envelope.
    if args.compare_runs:
        if args.register_campaign or args.database or args.runs_dir or args.worker_token or args.worker_generation is not None:
            parser.error('--compare-runs cannot be combined with campaign options')
        try:
            comparison_request = load_independent_comparison_request(args.compare_runs)
        except (OSError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        protected = {args.compare_runs.resolve(), comparison_request.database.resolve()}
        if args.result.resolve() in protected or (args.cancel is not None and args.cancel.resolve() in protected):
            parser.error('comparison result/cancel must not overwrite request or database')
    try:
        if args.register_campaign and args.request is None:
            raise ValueError('--register-campaign requires --request')
        if args.expose_final:
            result = execute_final_holdout_exposure(exposure_request, cancel_path=args.cancel)
        elif args.evaluate_final:
            result = execute_final_holdout_request(final_request, cancel_path=args.cancel)
        elif args.validate_partitions:
            result = execute_development_validation(validation_request, cancel_path=args.cancel, result_path=args.result)
        elif args.compare_runs:
            result = execute_independent_comparison(comparison_request, cancel_path=args.cancel)
        elif args.campaign:
            if args.database is None or args.runs_dir is None:
                raise ValueError('campaign requires --database and --runs-dir')
            if bool(args.worker_token) != (args.worker_generation is not None):
                raise ValueError('worker token/generation must be supplied together')
            claim = {'campaign_id': args.campaign, 'owner_token': args.worker_token, 'generation': args.worker_generation} if args.worker_token else None
            result = run_campaign_worker(args.database, args.campaign, args.runs_dir, args.result, cancel_path=args.cancel, worker_claim=claim)
        else:
            request = load_research_process_request(args.request)
            if args.register_campaign:
                result = register_campaign_request(ResearchRepository(request.database), args.register_campaign, request,
                    cancel_requested=lambda: args.cancel is not None and args.cancel.is_file())
            else:
                result = execute_process_request(request, cancel_path=args.cancel)
        exit_code = 2 if result.get('status') == 'cancelled' else 3 if result.get('status') == 'resource_blocked' else 0
    except ResearchRunCancelled as exc:
        result = {"status": "cancelled", "reason": str(exc)}
        exit_code = 2
    except ResearchResourceBlocked as exc:
        result = {"status": "resource_blocked", "reason": str(exc)}
        exit_code = 3
    except (OSError, TypeError, ValueError, RuntimeError, sqlite3.Error) as exc:
        result = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
        exit_code = 1
    _write_result(args.result, result)
    print(json.dumps(result, ensure_ascii=False))
    return exit_code


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"research request {name} must be an object")
    return value


def _attempt_interrupt_reason(cancelled: object, lease_lost: bool) -> str:
    if lease_lost:
        return "execution_lease_lost"
    return "user_requested" if callable(cancelled) and cancelled() else "process_interrupted"


def _execute_no_trade_baseline(
    dataset: Any,
    repository: ResearchRepository,
    execution: SimulationExecutionConfig,
    evaluation: ResearchEvaluationSpec,
    experiment_id: str,
    session_profile: str | None,
) -> str:
    """같은 시간순 report builder로 무거래 기준선을 평가한다."""
    identity = json.dumps(
        {
            "experiment_id": experiment_id,
            "dataset_id": dataset.manifest.get("dataset_id", ""),
            "evaluation": evaluation.to_dict(),
            "kind": "no_trade_baseline",
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    run_id = f"run_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
    spec = {
        "mode": "no_trade_baseline", "experiment_id": experiment_id,
        "evaluation_spec": evaluation.to_dict(),
    }
    if session_profile is not None:
        spec["session_profile"] = research_session_profile_document(session_profile)
    repository.start_run(run_id, spec, dataset.manifest)
    report = build_research_report(
        run_id, input_manifest=dataset.manifest, observations=dataset.observations,
        candidate_events=(), decisions=(), execution_events=(), outcome_labels=(),
        initial_cash_won=execution.initial_cash_won,
        cost_model=execution.cost_model.to_dict() if execution.cost_model else None,
        spec=evaluation, logical_result_hash=hashlib.sha256(b"no_trade").hexdigest(),
    )
    repository.save_research_report(report)
    repository.finish_run(run_id, hashlib.sha256(
        json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest())
    return run_id


def _request_path(root: Path, value: object, name: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"research request {name} path is required")
    path = Path(text)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _write_result(path: Path, document: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + '.' + secrets.token_hex(8) + ".tmp")
    try:
        temporary.write_text(
            json.dumps(dict(document), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        # Windows readers briefly deny deletion while polling this snapshot.
        # Keep publication atomic; retry sharing/access conflicts only, with a bound.
        for attempt in range(10):
            try:
                temporary.replace(target)
                break
            except PermissionError as exc:
                if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 9:
                    raise
                time.sleep(0.025)
    finally:
        temporary.unlink(missing_ok=True)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main())
