"""D3c 후보 시계열 label과 모의 실행 성과 계산."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from kiwoom_monitor.application.breakout_strategy import CandidateEvent
from kiwoom_monitor.application.research_execution import ExecutionEvent
from kiwoom_monitor.application.research_replay import KrxMinuteBarFrame
from kiwoom_monitor.application.research_splits import (
    ResearchEvaluationSpec,
    DevelopmentPartitionSpec,
    assign_interval_to_fold,
    warmup_start,
)


OUTCOME_HORIZONS_SECONDS = (1, 60, 180, 300, 600)


@dataclass(frozen=True)
class DevelopmentPartitionResult:
    run_id: str
    status: str
    reasons: tuple[str, ...] = ()
    condition_key: str = ''
    input_dataset_id: str = ''
    input_revision_ids_hash: str = ''
    role: str = ''
    start: str = ''
    end: str = ''
    closed_trade_count: int = 0
    active_day_count: int = 0
    net_pnl_won: int | None = None
    max_drawdown_won: int | None = None
    # dimension, bucket key, closed trades, net realized PnL; no mutable raw report.
    strata: tuple[tuple[str, str, int, int], ...] = ()
    excluded_reason: str = ''


@dataclass(frozen=True)
class IndependentDevelopmentComparison:
    version: str
    status: str
    requested_count: int
    unique_run_count: int
    condition_keys: tuple[str, ...]
    partitions: tuple[DevelopmentPartitionResult, ...]
    eligible_partition_count: int
    positive_partition_count: int
    minimum_partition_pnl_won: int | None
    maximum_partition_pnl_won: int | None
    median_partition_pnl_won: float | None
    worst_partition_drawdown_won: int | None
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _development_partition_result(record: Mapping[str, Any]) -> DevelopmentPartitionResult:
    run_id = str(record.get('run_id', ''))
    state = record.get('status')
    if state != 'completed':
        status = 'MISSING' if state == 'missing' else 'FAILED' if state == 'failed' else 'INCOMPLETE'
        result = DevelopmentPartitionResult(run_id, status, (str(record.get('error') or state),))
        # Failed runs can still identify their intended window; absent metadata is not invented.
        try:
            manifest = record['input_manifest']
            descriptor = manifest['development_partition']
            policy = DevelopmentPartitionSpec.from_dict(descriptor['spec'])
            evaluation = ResearchEvaluationSpec.from_dict(descriptor['evaluation'])
            fold = policy.evaluation_for(evaluation).folds[0]
            if manifest.get('runtime_input_version') == 'independent_development_input/v1':
                result = replace(result, role=fold.role,
                    start=_aware_datetime(fold.start).astimezone(timezone.utc).isoformat(),
                    end=_aware_datetime(fold.end).astimezone(timezone.utc).isoformat(),
                    input_dataset_id=str(manifest['dataset_id']),
                    input_revision_ids_hash=str(manifest['revision_ids_hash']))
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        return result
    try:
        spec, manifest, report = record['spec'], record['input_manifest'], record['report']
        descriptor = manifest['development_partition']
        if manifest.get('runtime_input_version') != 'independent_development_input/v1':
            raise ValueError('independent development input required')
        partition = DevelopmentPartitionSpec.from_dict(descriptor['spec'])
        evaluation = ResearchEvaluationSpec.from_dict(spec['evaluation_spec'])
        selected = partition.evaluation_for(evaluation)
        if len(evaluation.folds) != 1 or selected.to_dict() != evaluation.to_dict():
            raise ValueError('one development fold required')
        fold = selected.folds[0]
        start, end = (_aware_datetime(value).astimezone(timezone.utc).isoformat()
                      for value in (fold.start, fold.end))
        if (descriptor['evaluation'] != evaluation.to_dict()
                or _aware_datetime(descriptor['warmup_start']).astimezone(timezone.utc) != warmup_start(evaluation)
                or _aware_datetime(descriptor['active_start']).astimezone(timezone.utc).isoformat() != start
                or _aware_datetime(descriptor['end']).astimezone(timezone.utc).isoformat() != end):
            raise ValueError('partition boundary mismatch')
        if (not spec.get('code_hash') or not spec.get('session_profile')
                or spec['session_profile'] != manifest.get('research_session_profile')
                or not spec.get('execution_model') or not record.get('logical_result_hash')
                or not str(manifest['dataset_id']).startswith('development-')
                or not manifest['revision_ids_hash']):
            raise ValueError('verified run identity required')
        rows = report['fold_reports']
        if (report['run_id'] != run_id or report['split_spec'] != evaluation.to_dict()
                or len(rows) != 1 or (rows[0]['name'], rows[0]['role']) != (fold.name, fold.role)):
            raise ValueError('report partition mismatch')
        row = rows[0]
        if row['status'] not in ('ELIGIBLE', 'INELIGIBLE', 'NOT_APPLICABLE'):
            raise ValueError('development report status invalid')
        # Preserve every run policy, excluding only per-input identity and active fold bounds.
        conditions = {key: value for key, value in spec.items()
                      if key not in ('data_manifest_hash', 'evaluation_spec')}
        conditions['evaluation_policy'] = {key: value for key, value in evaluation.to_dict().items()
                                           if key != 'folds'}
        conditions['partition_policy'] = {'version': partition.version, 'state_policy': partition.state_policy}
        if partition.symbol_partition is not None:
            conditions['partition_policy']['symbol_partition'] = partition.symbol_partition.to_dict()
        conditions['input_contract'] = {key: manifest.get(key) for key in (
            'schema_version', 'research_session_profile', 'subject', 'universe_rule', 'order_policy_version')}
        key = hashlib.sha256(json.dumps(conditions, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        reasons = tuple(str(reason) for reason in row.get('reasons', ()))
        sample_reasons = {'minimum_closed_trades_not_met', 'minimum_active_days_not_met'}
        status = ('INSUFFICIENT_SAMPLE' if row['status'] == 'INELIGIBLE' and reasons
                  and set(reasons) <= sample_reasons else
                  'NO_CLOSED_TRADE' if row['status'] == 'NOT_APPLICABLE' else row['status'])
        closed, days = int(row['closed_trade_count']), int(row['active_day_count'])
        net = row.get('net_realized_pnl_won')
        if min(closed, days) < 0 or (status == 'ELIGIBLE' and (closed == 0 or net is None)):
            raise ValueError('development metrics invalid')
        if status == 'ELIGIBLE' and (closed < evaluation.minimum_closed_trades or days < evaluation.minimum_active_days):
            raise ValueError('eligible report does not meet locked sample policy')
        strata = tuple((dimension, str(bucket['key']), int(bucket['closed_trade_count']),
                        int(bucket['net_realized_pnl_won']))
                       for dimension in ('by_symbol', 'by_date', 'by_time_bucket')
                       for bucket in row.get(dimension, ()))
        if partition.symbol_partition is not None:
            symbols = tuple(value for value in strata if value[0] == 'by_symbol')
            if (sum(value[2] for value in symbols) != closed
                    or any(value[2] < 0 or value[2] > 0 and not partition.symbol_partition.allows(value[1]) for value in symbols)):
                raise ValueError('closed trades do not match the declared stock partition')
        return DevelopmentPartitionResult(
            run_id, status, reasons, key, str(manifest['dataset_id']), str(manifest['revision_ids_hash']),
            fold.role, start, end, closed, days, int(net) if net is not None else None,
            int(row['max_drawdown_won']) if row.get('max_drawdown_won') is not None else None, strata)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return DevelopmentPartitionResult(run_id, 'INVALID', (str(exc),))


def build_independent_development_comparison(
    records: Iterable[Mapping[str, Any]],
) -> IndependentDevelopmentComparison:
    """Read-only development projection, never a combined account equity curve.

    Explicit run references define the requested scope; this is not a search-attempt census.
    Overlapping windows and revised inputs for the same window remain visible but cannot
    supply multiple independent observations. Missing/failed results are never silently dropped.
    """
    records = tuple(records)
    if not records or len(records) > 200:
        raise ValueError('comparison requires 1 to 200 explicit run references')
    results = [_development_partition_result(record) for record in records]
    seen: set[str] = set()
    first_reference: dict[str, int] = {}
    for index, result in enumerate(results):
        if result.run_id in seen:
            first = first_reference[result.run_id]
            if records[first] != records[index]:
                results[first] = replace(results[first], excluded_reason='conflicting_run_reference')
                results[index] = replace(result, excluded_reason='conflicting_run_reference')
            else:
                results[index] = replace(result, excluded_reason='duplicate_run_reference')
        else:
            first_reference[result.run_id] = index
        seen.add(result.run_id)
    keys = tuple(sorted({row.condition_key for row in results if row.condition_key}))
    unique = [(index, row) for index, row in enumerate(results)
              if row.condition_key and not row.excluded_reason]
    evidence_seen: dict[tuple[str, ...], int] = {}
    for index, row in unique:
        identity = (row.condition_key, row.start, row.end, row.role,
                    row.input_dataset_id, row.input_revision_ids_hash,
                    str(records[index].get('logical_result_hash', '')))
        if identity in evidence_seen:
            results[index] = replace(row, excluded_reason='duplicate_evidence')
        else:
            evidence_seen[identity] = index
    unique = [(index, results[index]) for index, _ in unique if not results[index].excluded_reason]
    for offset, (index, row) in enumerate(unique):
        for other_index, other in unique[offset + 1:]:
            if row.condition_key != other.condition_key:
                continue
            if max(row.start, other.start) < min(row.end, other.end):
                reason = ('same_window_input_revision' if (row.start, row.end) == (other.start, other.end)
                          else 'overlapping_active_windows')
                results[index] = replace(results[index], excluded_reason=reason)
                results[other_index] = replace(results[other_index], excluded_reason=reason)
    eligible = tuple(row for row in results if row.status == 'ELIGIBLE' and not row.excluded_reason) if len(keys) == 1 else ()
    pnl = tuple(row.net_pnl_won for row in eligible if row.net_pnl_won is not None)
    mdd = tuple(row.max_drawdown_won for row in eligible if row.max_drawdown_won is not None)
    considered = tuple(row for row in results if row.excluded_reason not in ('duplicate_run_reference', 'duplicate_evidence'))
    status = ('INCOMPARABLE' if len(keys) > 1 else
              'COMPLETE' if considered and all(row.status == 'ELIGIBLE' and not row.excluded_reason for row in considered)
              else 'INCOMPLETE')
    return IndependentDevelopmentComparison(
        'independent_development_comparison/v1', status, len(records), len(seen), keys, tuple(results),
        len(eligible), sum(value > 0 for value in pnl), min(pnl) if pnl else None,
        max(pnl) if pnl else None, float(median(pnl)) if pnl else None, max(mdd) if mdd else None,
        ('independent_initial_cash_not_continuous_portfolio', 'no_combined_return_or_drawdown',
         'active_days_are_per_partition_not_unique_days', 'shared_warmup_is_not_independent_data',
         'strata_describe_closed_trades_not_all_market_coverage', 'explicit_run_scope_not_search_attempt_census',
         'no_final_access_or_automatic_promotion'))


@dataclass(frozen=True)
class DevelopmentEvidence:
    """Selection input copied from development folds, with no raw/final report."""

    status: str
    reasons: tuple[str, ...]
    fold_refs: tuple[tuple[str, str], ...]
    net_pnl_won: int | None
    max_drawdown_won: int | None
    closed_trade_count: int
    active_day_count: int


def build_development_evidence(report: Mapping[str, Any]) -> DevelopmentEvidence:
    """Global report status/reasons and OOS fields are never selection inputs."""
    folds = tuple(
        row for row in report.get('fold_reports', ())
        if isinstance(row, Mapping) and row.get('role') in ('TRAIN', 'VALIDATION')
    )
    reasons = list(dict.fromkeys(str(reason) for row in folds for reason in row.get('reasons', ())))
    statuses = tuple(str(row.get('status', '')) for row in folds)
    if not folds:
        status = 'NOT_APPLICABLE'
        reasons.append('search_development_folds_missing')
    elif any(value not in ('ELIGIBLE', 'INELIGIBLE', 'NOT_APPLICABLE') for value in statuses):
        status = 'INELIGIBLE'
        reasons.append('search_development_fold_status_invalid')
    elif 'INELIGIBLE' in statuses:
        status = 'INELIGIBLE'
    elif 'ELIGIBLE' in statuses:
        status = 'ELIGIBLE'
    else:
        status = 'NOT_APPLICABLE'
    # These preserve the legacy fold-summary arithmetic, not independent portfolio statistics.
    visible = tuple(row for row in folds if row.get('status') != 'SEALED')
    net = tuple(int(row['net_realized_pnl_won']) for row in visible if row.get('net_realized_pnl_won') is not None)
    drawdowns = tuple(int(row['max_drawdown_won']) for row in visible if row.get('max_drawdown_won') is not None)
    return DevelopmentEvidence(
        status=status, reasons=tuple(dict.fromkeys(reasons)),
        fold_refs=tuple((str(row.get('name', '')), str(row['role'])) for row in folds),
        net_pnl_won=sum(net) if net else None,
        max_drawdown_won=max(drawdowns) if drawdowns else None,
        closed_trade_count=sum(int(row.get('closed_trade_count', 0)) for row in visible),
        active_day_count=sum(int(row.get('active_day_count', 0)) for row in visible),
    )


@dataclass(frozen=True)
class OutcomeLabel:
    label_id: str
    run_id: str
    candidate_event_id: str
    symbol: str
    horizon_seconds: int
    horizon_end: str
    matured_at: str
    computed_at: str
    available_at: str
    status: str
    values: Mapping[str, Any] | None
    input_refs: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceSummary:
    evaluation_id: str
    run_id: str
    status: str
    reason: str
    initial_cash_won: int
    closed_trade_count: int
    winning_trade_count: int | None
    gross_realized_pnl_won: int | None
    total_cost_won: int | None
    net_realized_pnl_won: int | None
    return_ppm: int | None
    optimistic_additional_pnl_won: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchFoldReport:
    name: str
    role: str
    status: str
    reasons: tuple[str, ...]
    closed_trade_count: int
    active_day_count: int
    winning_trade_count: int | None
    losing_trade_count: int | None
    gross_realized_pnl_won: int | None
    total_cost_won: int | None
    net_realized_pnl_won: int | None
    expectancy_won: int | None
    payoff_ratio_ppm: int | None
    profit_factor_ppm: int | None
    turnover_won: int
    exposure_seconds: int
    max_drawdown_won: int | None
    max_drawdown_ppm: int | None
    submitted_order_count: int
    filled_order_count: int
    fill_rate_ppm: int | None
    rejected_order_count: int
    censored_order_count: int
    unsupported_order_count: int
    partial_fill_count: int
    purged_trade_count: int
    purged_outcome_count: int
    no_trade_reasons: Mapping[str, int]
    event_outcomes: tuple[Mapping[str, Any], ...]
    by_symbol: tuple[Mapping[str, Any], ...]
    by_date: tuple[Mapping[str, Any], ...]
    by_time_bucket: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchReport:
    report_id: str
    run_id: str
    status: str
    reasons: tuple[str, ...]
    split_spec: Mapping[str, Any]
    data_quality: Mapping[str, Any]
    cost_model: Mapping[str, Any] | None
    limitations: tuple[str, ...]
    fold_reports: tuple[ResearchFoldReport, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchFoldComparison:
    name: str
    role: str
    status: str
    reasons: tuple[str, ...]
    baseline_closed_trade_count: int | None
    variant_closed_trade_count: int | None
    baseline_net_pnl_won: int | None
    variant_net_pnl_won: int | None
    net_pnl_delta_won: int | None
    shared_candidate_count: int | None
    baseline_only_candidate_count: int | None
    variant_only_candidate_count: int | None
    avoided_loss_won: int | None
    missed_profit_won: int | None
    variant_only_net_pnl_won: int | None
    event_outcome_deltas: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchComparison:
    comparison_id: str
    baseline_run_id: str
    variant_run_id: str
    changed_condition: str
    status: str
    reasons: tuple[str, ...]
    input_dataset_id: str
    input_revision_ids_hash: str
    fold_comparisons: tuple[ResearchFoldComparison, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_outcome_labels(
    candidate_events: Iterable[CandidateEvent],
    bars: Iterable[KrxMinuteBarFrame],
    *,
    run_ended: bool,
    session_profile: str | None = None,
) -> tuple[OutcomeLabel, ...]:
    ordered_bars = tuple(sorted(bars, key=lambda bar: (
        _aware_datetime(bar.bar_start).astimezone(timezone.utc),
        bar.code,
        bar.revision_id,
    )))
    labels: list[OutcomeLabel] = []
    for candidate in candidate_events:
        event_time = _aware_datetime(candidate.available_at).astimezone(timezone.utc)
        reference_bar = next(
            (bar for bar in ordered_bars if bar.revision_id == candidate.reference_revision_id),
            None,
        )
        event_session = reference_bar.research_session if reference_bar is not None else ""
        for horizon_seconds in OUTCOME_HORIZONS_SECONDS:
            horizon_end = event_time + timedelta(seconds=horizon_seconds)
            if horizon_seconds == 1:
                labels.append(_label(
                    candidate, horizon_seconds, horizon_end,
                    matured_at="", computed_at=candidate.available_at,
                    available_at=candidate.available_at, status="UNSUPPORTED", values=None,
                    input_refs=(), reason="second_level_input_not_exported",
                ))
                continue
            future = [
                bar for bar in ordered_bars
                if bar.code == candidate.symbol
                and _aware_datetime(bar.bar_start).astimezone(timezone.utc) >= event_time
                and (
                    session_profile is None
                    or not event_session
                    or bar.research_session == event_session
                )
            ]
            target = next((
                bar for bar in future
                if _aware_datetime(bar.bar_end).astimezone(timezone.utc) >= horizon_end
            ), None)
            if target is None:
                status = "CENSORED" if run_ended else "PENDING"
                labels.append(_label(
                    candidate, horizon_seconds, horizon_end,
                    matured_at="", computed_at=candidate.available_at,
                    available_at=candidate.available_at, status=status, values=None,
                    input_refs=tuple(bar.revision_id for bar in future),
                    reason=(
                        "research_interval_ended_before_horizon"
                        if run_ended else "horizon_not_matured"
                    ),
                ))
                continue
            through_target = [
                bar for bar in future
                if _aware_datetime(bar.bar_start) <= _aware_datetime(target.bar_start)
            ]
            continuous = (
                _continuous_minutes(through_target)
                if session_profile is None
                else _continuous_minutes_from(event_time, through_target)
            )
            if not continuous:
                labels.append(_label(
                    candidate, horizon_seconds, horizon_end,
                    matured_at=target.bar_end, computed_at=target.available_at,
                    available_at=target.available_at, status="CENSORED", values=None,
                    input_refs=tuple(bar.revision_id for bar in through_target),
                    reason="minute_bar_coverage_gap",
                ))
                continue
            if session_profile is not None and any(
                bar.market_phase != "CONTINUOUS" for bar in through_target
            ):
                labels.append(_label(
                    candidate, horizon_seconds, horizon_end,
                    matured_at=target.bar_end, computed_at=target.available_at,
                    available_at=target.available_at, status="UNSUPPORTED", values=None,
                    input_refs=tuple(bar.revision_id for bar in through_target),
                    reason="minute_bar_phase_cannot_prove_intrabar_path",
                ))
                continue
            reference = candidate.signal_reference_price
            values = {
                "reference_price": reference,
                "close": target.close,
                "return_bps": (target.close - reference) * 10_000 // reference,
                "max_up_bps": (max(bar.high for bar in through_target) - reference) * 10_000 // reference,
                "max_down_bps": (min(bar.low for bar in through_target) - reference) * 10_000 // reference,
            }
            labels.append(_label(
                candidate, horizon_seconds, horizon_end,
                matured_at=target.bar_end, computed_at=target.available_at,
                available_at=target.available_at, status="COMPLETE", values=values,
                input_refs=tuple(bar.revision_id for bar in through_target), reason="",
            ))
    return tuple(labels)


def evaluate_performance(
    run_id: str,
    execution_events: Iterable[ExecutionEvent],
    *,
    initial_cash_won: int,
    cost_model_present: bool,
) -> PerformanceSummary:
    events = tuple(execution_events)
    if not cost_model_present:
        return _summary(
            run_id, "INELIGIBLE", "cost_model_missing", initial_cash_won,
            0, None, None, None, None, None, None,
        )
    fills = tuple(event for event in events if event.event_type in {"FILL", "RISK_FILL"})
    sells = tuple(event for event in fills if event.side == "SELL")
    if not sells:
        return _summary(
            run_id, "NOT_APPLICABLE", "no_closed_simulated_trade", initial_cash_won,
            0, None, None, None, None, None, None,
        )
    net = sum(event.realized_pnl_won for event in sells)
    total_cost = sum(event.total_cost_won for event in fills)
    gross = net + total_cost
    optimistic_additional = sum(
        max(0, int(event.optimistic_exit_price or 0) - event.price) * event.quantity
        for event in sells
    )
    return _summary(
        run_id, "ELIGIBLE", "", initial_cash_won,
        len(sells), sum(event.realized_pnl_won > 0 for event in sells),
        gross, total_cost, net, net * 1_000_000 // initial_cash_won,
        optimistic_additional,
    )


def build_research_report(
    run_id: str,
    *,
    input_manifest: Mapping[str, Any],
    observations: Iterable[Mapping[str, Any]],
    candidate_events: Iterable[CandidateEvent | Mapping[str, Any]],
    decisions: Iterable[Mapping[str, Any]],
    execution_events: Iterable[ExecutionEvent | Mapping[str, Any]],
    outcome_labels: Iterable[OutcomeLabel | Mapping[str, Any]],
    initial_cash_won: int,
    cost_model: Mapping[str, Any] | None,
    spec: ResearchEvaluationSpec,
    logical_result_hash: str,
) -> ResearchReport:
    """한 불변 run을 시간순 fold별로 평가하며 경계 표본을 명시적으로 제외한다."""
    observation_rows = tuple(dict(row) for row in observations)
    candidate_rows = tuple(_document(row) for row in candidate_events)
    decision_rows = tuple(dict(row) for row in decisions)
    event_rows = tuple(_document(row) for row in execution_events)
    label_rows = tuple(_document(row) for row in outcome_labels)
    quality = _data_quality(input_manifest, observation_rows, spec, logical_result_hash)
    cost_reasons = _cost_model_reasons(cost_model, spec)
    historical_reconstruction = _historical_reconstruction_input(input_manifest)
    limitations = (
        "partial_fill_model_unsupported",
        "vi_orderability_not_exported",
    ) + ((
        "posthoc_candidate_population_not_contemporaneous_top20",
        "historical_bar_close_replay_clock_with_source_availability_preserved",
    ) if historical_reconstruction else ())
    trades = _closed_trades(event_rows)
    candidate_by_id = {
        str(row.get("event_id", "")): row for row in candidate_rows
    }
    fold_reports = tuple(
        _build_fold_report(
            fold=fold, spec=spec, trades=trades, decisions=decision_rows,
            events=event_rows, labels=label_rows, candidate_by_id=candidate_by_id,
            initial_cash_won=initial_cash_won,
            common_reasons=tuple(quality["reasons"]) + tuple(cost_reasons),
        )
        for fold in spec.folds
    )
    reasons = list(dict.fromkeys(tuple(quality["reasons"]) + tuple(cost_reasons)))
    if any(report.status == "INELIGIBLE" for report in fold_reports):
        status = "INELIGIBLE"
    elif all(report.status in {"NOT_APPLICABLE", "SEALED"} for report in fold_reports):
        status = "NOT_APPLICABLE"
        if not reasons:
            reasons.append("no_closed_simulated_trade")
    elif any(report.status == "SEALED" for report in fold_reports):
        status = "ELIGIBLE_WITH_SEALED_HOLDOUT"
    else:
        status = "ELIGIBLE"
    identity_body = {
        "run_id": run_id,
        "status": status,
        "reasons": tuple(reasons),
        "split_spec": spec.to_dict(),
        "data_quality": quality,
        "cost_model": dict(cost_model) if cost_model is not None else None,
        "limitations": limitations,
        "fold_reports": tuple(report.to_dict() for report in fold_reports),
    }
    return ResearchReport(
        report_id=_content_id("research_report", identity_body), run_id=run_id,
        status=status, reasons=tuple(reasons), split_spec=spec.to_dict(),
        data_quality=quality,
        cost_model=dict(cost_model) if cost_model is not None else None,
        limitations=limitations,
        fold_reports=fold_reports,
    )


def build_research_comparison(
    *,
    baseline_run_id: str,
    variant_run_id: str,
    changed_condition: str,
    input_dataset_id: str,
    input_revision_ids_hash: str,
    baseline_report: Mapping[str, Any],
    variant_report: Mapping[str, Any],
    baseline_candidates: Iterable[Mapping[str, Any]],
    variant_candidates: Iterable[Mapping[str, Any]],
    baseline_events: Iterable[Mapping[str, Any]],
    variant_events: Iterable[Mapping[str, Any]],
    spec: ResearchEvaluationSpec,
) -> ResearchComparison:
    """한 조건만 다른 두 run을 candidate identity와 fold 경계로 비교한다."""
    if not baseline_run_id or not variant_run_id or baseline_run_id == variant_run_id:
        raise ValueError("comparison requires two distinct research runs")
    if changed_condition not in {
        "rank_persistence_filter", "market_type_filter", "market_raw_feature_filter",
    }:
        raise ValueError(f"unsupported research comparison condition: {changed_condition}")
    baseline_folds = _fold_report_map(baseline_report)
    variant_folds = _fold_report_map(variant_report)
    expected_names = tuple(fold.name for fold in spec.folds)
    if tuple(baseline_folds) != expected_names or tuple(variant_folds) != expected_names:
        raise ValueError("comparison reports must use the same ordered fold spec")
    baseline_keys = {
        str(row.get("decision_id", "")): str(row.get("dedup_key", ""))
        for row in baseline_candidates if row.get("decision_id") and row.get("dedup_key")
    }
    variant_keys = {
        str(row.get("decision_id", "")): str(row.get("dedup_key", ""))
        for row in variant_candidates if row.get("decision_id") and row.get("dedup_key")
    }
    baseline_trades = _closed_trades(tuple(baseline_events), baseline_keys)
    variant_trades = _closed_trades(tuple(variant_events), variant_keys)
    comparisons = tuple(_compare_fold(
        fold, spec, baseline_folds[fold.name], variant_folds[fold.name],
        baseline_trades, variant_trades,
    ) for fold in spec.folds)
    reasons = tuple(dict.fromkeys(
        reason for comparison in comparisons for reason in comparison.reasons
    ))
    if any(comparison.status == "INELIGIBLE" for comparison in comparisons):
        status = "INELIGIBLE"
    elif all(comparison.status in {"NOT_APPLICABLE", "SEALED"} for comparison in comparisons):
        status = "NOT_APPLICABLE"
    elif any(comparison.status == "SEALED" for comparison in comparisons):
        status = "ELIGIBLE_WITH_SEALED_HOLDOUT"
    else:
        status = "ELIGIBLE"
    body = {
        "baseline_run_id": baseline_run_id, "variant_run_id": variant_run_id,
        "changed_condition": changed_condition, "status": status, "reasons": reasons,
        "input_dataset_id": input_dataset_id,
        "input_revision_ids_hash": input_revision_ids_hash,
        "fold_comparisons": tuple(value.to_dict() for value in comparisons),
    }
    return ResearchComparison(
        comparison_id=_content_id("research_comparison", body),
        baseline_run_id=baseline_run_id, variant_run_id=variant_run_id,
        changed_condition=changed_condition, status=status, reasons=reasons,
        input_dataset_id=input_dataset_id,
        input_revision_ids_hash=input_revision_ids_hash,
        fold_comparisons=comparisons,
    )


def _compare_fold(
    fold: Any,
    spec: ResearchEvaluationSpec,
    baseline_report: Mapping[str, Any],
    variant_report: Mapping[str, Any],
    baseline_trades: tuple[Mapping[str, Any], ...],
    variant_trades: tuple[Mapping[str, Any], ...],
) -> ResearchFoldComparison:
    statuses = (str(baseline_report.get("status", "")), str(variant_report.get("status", "")))
    if "SEALED" in statuses:
        return _empty_fold_comparison(fold.name, fold.role, "SEALED", ("final_holdout_not_accessed",))
    reasons = tuple(dict.fromkeys(
        str(reason) for report in (baseline_report, variant_report)
        for reason in report.get("reasons", ()) if str(reason)
    ))
    baseline_included = _fold_trade_map(baseline_trades, fold.name, spec)
    variant_included = _fold_trade_map(variant_trades, fold.name, spec)
    baseline_set = set(baseline_included)
    variant_set = set(variant_included)
    shared = baseline_set & variant_set
    baseline_only = baseline_set - variant_set
    variant_only = variant_set - baseline_set
    baseline_pnl = sum(int(row["net_pnl_won"]) for row in baseline_included.values())
    variant_pnl = sum(int(row["net_pnl_won"]) for row in variant_included.values())
    if "INELIGIBLE" in statuses:
        status = "INELIGIBLE"
    elif not baseline_included and not variant_included:
        status = "NOT_APPLICABLE"
        reasons = tuple(dict.fromkeys((*reasons, "no_closed_simulated_trade")))
    else:
        status = "ELIGIBLE"
    return ResearchFoldComparison(
        name=fold.name, role=fold.role, status=status, reasons=reasons,
        baseline_closed_trade_count=len(baseline_included),
        variant_closed_trade_count=len(variant_included),
        baseline_net_pnl_won=baseline_pnl, variant_net_pnl_won=variant_pnl,
        net_pnl_delta_won=variant_pnl - baseline_pnl,
        shared_candidate_count=len(shared),
        baseline_only_candidate_count=len(baseline_only),
        variant_only_candidate_count=len(variant_only),
        avoided_loss_won=sum(
            max(0, -int(baseline_included[key]["net_pnl_won"])) for key in baseline_only
        ),
        missed_profit_won=sum(
            max(0, int(baseline_included[key]["net_pnl_won"])) for key in baseline_only
        ),
        variant_only_net_pnl_won=sum(
            int(variant_included[key]["net_pnl_won"]) for key in variant_only
        ),
        event_outcome_deltas=_compare_event_outcomes(baseline_report, variant_report),
    )


def _empty_fold_comparison(
    name: str, role: str, status: str, reasons: tuple[str, ...],
) -> ResearchFoldComparison:
    return ResearchFoldComparison(
        name, role, status, reasons, None, None, None, None, None, None, None,
        None, None, None, None, (),
    )


def _fold_report_map(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = report.get("fold_reports")
    if not isinstance(rows, (list, tuple)):
        raise ValueError("research report fold rows are missing")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not str(row.get("name", "")):
            raise ValueError("research report contains an invalid fold row")
        result[str(row["name"])] = row
    return result


def _fold_trade_map(
    trades: tuple[Mapping[str, Any], ...], fold_name: str, spec: ResearchEvaluationSpec,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for trade in trades:
        candidate_key = str(trade.get("candidate_key", ""))
        if not candidate_key:
            continue
        assignment = assign_interval_to_fold(
            spec, str(trade["opened_at"]), str(trade["closed_at"]),
        )
        if assignment.fold_name == fold_name and assignment.status == "INCLUDED":
            if candidate_key in result:
                raise ValueError("comparison candidate key closed more than once in one run")
            result[candidate_key] = trade
    return result


def _compare_event_outcomes(
    baseline: Mapping[str, Any], variant: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    def rows(value: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
        raw = value.get("event_outcomes", ())
        return {
            int(row["horizon_seconds"]): row
            for row in raw if isinstance(row, Mapping) and row.get("horizon_seconds") is not None
        }
    base = rows(baseline)
    changed = rows(variant)
    return tuple({
        "horizon_seconds": horizon,
        "baseline_sample_count": int(base.get(horizon, {}).get("sample_count", 0)),
        "variant_sample_count": int(changed.get(horizon, {}).get("sample_count", 0)),
        "average_return_bps_delta": (
            int(changed[horizon]["average_return_bps"])
            - int(base[horizon]["average_return_bps"])
            if horizon in base and horizon in changed else None
        ),
    } for horizon in sorted(set(base) | set(changed)))


def _build_fold_report(
    *,
    fold: Any,
    spec: ResearchEvaluationSpec,
    trades: tuple[Mapping[str, Any], ...],
    decisions: tuple[Mapping[str, Any], ...],
    events: tuple[Mapping[str, Any], ...],
    labels: tuple[Mapping[str, Any], ...],
    candidate_by_id: Mapping[str, Mapping[str, Any]],
    initial_cash_won: int,
    common_reasons: tuple[str, ...],
) -> ResearchFoldReport:
    if fold.role == "OOS" and not spec.final_holdout_accessed_at:
        return _empty_fold_report(fold.name, fold.role, "SEALED", ("final_holdout_not_accessed",))
    included: list[Mapping[str, Any]] = []
    purged_trade_count = 0
    for trade in trades:
        assignment = assign_interval_to_fold(
            spec, str(trade["opened_at"]), str(trade["closed_at"]),
        )
        if assignment.fold_name != fold.name:
            continue
        if assignment.status == "INCLUDED":
            included.append(trade)
        elif assignment.status == "PURGED":
            purged_trade_count += 1
    included_labels: list[Mapping[str, Any]] = []
    purged_outcome_count = 0
    for label in labels:
        candidate = candidate_by_id.get(str(label.get("candidate_event_id", "")))
        if candidate is None:
            continue
        assignment = assign_interval_to_fold(
            spec, str(candidate.get("available_at", "")), str(label.get("horizon_end", "")),
        )
        if assignment.fold_name != fold.name:
            continue
        if assignment.status == "PURGED":
            purged_outcome_count += 1
        elif assignment.status == "INCLUDED" and label.get("status") == "COMPLETE":
            included_labels.append(label)
    fold_events = tuple(row for row in events if _point_in_fold(row.get("occurred_at"), fold))
    fold_decisions = tuple(
        row for row in decisions if _point_in_fold(row.get("decided_at"), fold)
    )
    no_trade_reasons: dict[str, int] = {}
    for decision in fold_decisions:
        if decision.get("final_action") != "NO_TRADE":
            continue
        raw_reasons = decision.get("reasons")
        for reason in raw_reasons if isinstance(raw_reasons, (list, tuple)) else ():
            key = str(reason)
            no_trade_reasons[key] = no_trade_reasons.get(key, 0) + 1
    reasons = list(common_reasons)
    closed = len(included)
    active_days = len({_kst_date(row["closed_at"]) for row in included})
    if closed < spec.minimum_closed_trades:
        reasons.append("minimum_closed_trades_not_met")
    if active_days < spec.minimum_active_days:
        reasons.append("minimum_active_days_not_met")
    if common_reasons or closed < spec.minimum_closed_trades or active_days < spec.minimum_active_days:
        status = "INELIGIBLE"
    elif not included:
        status = "NOT_APPLICABLE"
        reasons.append("no_closed_simulated_trade")
    else:
        status = "ELIGIBLE"
    pnl = [int(row["net_pnl_won"]) for row in included]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    total_cost = sum(int(row["total_cost_won"]) for row in included)
    net = sum(pnl)
    gross = net + total_cost
    mdd_won, mdd_ppm = _maximum_drawdown(fold, events, initial_cash_won)
    submitted_intents = {
        str(row.get("intent_id", "")) for row in fold_events
        if row.get("event_type") == "ORDER_SUBMITTED" and row.get("intent_id")
    }
    filled_intents = {
        str(row.get("intent_id", "")) for row in fold_events
        if row.get("event_type") in {"FILL", "PARTIAL_FILL"} and row.get("intent_id")
    }
    submitted = len(submitted_intents)
    filled = len(submitted_intents & filled_intents)
    return ResearchFoldReport(
        name=fold.name, role=fold.role, status=status,
        reasons=tuple(dict.fromkeys(reasons)), closed_trade_count=closed,
        active_day_count=active_days,
        winning_trade_count=len(wins) if included else None,
        losing_trade_count=len(losses) if included else None,
        gross_realized_pnl_won=gross if included else None,
        total_cost_won=total_cost if included else None,
        net_realized_pnl_won=net if included else None,
        expectancy_won=net // closed if included else None,
        payoff_ratio_ppm=(
            (sum(wins) * len(losses) * 1_000_000) // (abs(sum(losses)) * len(wins))
            if wins and losses else None
        ),
        profit_factor_ppm=(sum(wins) * 1_000_000 // abs(sum(losses))) if losses else None,
        turnover_won=sum(int(row["turnover_won"]) for row in included),
        exposure_seconds=sum(int(row["exposure_seconds"]) for row in included),
        max_drawdown_won=mdd_won, max_drawdown_ppm=mdd_ppm,
        submitted_order_count=submitted,
        filled_order_count=filled,
        fill_rate_ppm=filled * 1_000_000 // submitted if submitted else None,
        rejected_order_count=sum(
            row.get("event_type") in {"ORDER_REJECTED", "ORDER_CANCELLED"} for row in fold_events
        ),
        censored_order_count=sum(
            row.get("event_type") in {"ORDER_CENSORED", "POSITION_CENSORED"} for row in fold_events
        ),
        unsupported_order_count=sum(
            row.get("event_type") == "ORDER_UNSUPPORTED" for row in fold_events
        ),
        partial_fill_count=sum(
            row.get("event_type") == "PARTIAL_FILL" or row.get("state") == "PARTIAL"
            for row in fold_events
        ),
        purged_trade_count=purged_trade_count,
        purged_outcome_count=purged_outcome_count,
        no_trade_reasons=dict(sorted(no_trade_reasons.items())),
        event_outcomes=_event_outcome_rows(included_labels),
        by_symbol=_group_trade_rows(included, lambda row: str(row["symbol"])),
        by_date=_group_trade_rows(included, lambda row: _kst_date(row["opened_at"])),
        by_time_bucket=_group_trade_rows(included, lambda row: _time_bucket(row["opened_at"])),
    )


def _empty_fold_report(
    name: str, role: str, status: str, reasons: tuple[str, ...],
) -> ResearchFoldReport:
    return ResearchFoldReport(
        name, role, status, reasons, 0, 0, None, None, None, None, None, None,
        None, None, 0, 0, None, None, 0, 0, None, 0, 0, 0, 0, 0, 0,
        {}, (), (), (), (),
    )


def _closed_trades(
    events: tuple[Mapping[str, Any], ...],
    candidate_keys_by_decision: Mapping[str, str] | None = None,
) -> tuple[Mapping[str, Any], ...]:
    ordered = sorted(events, key=lambda row: (
        _aware_datetime(row.get("occurred_at")).astimezone(timezone.utc),
        str(row.get("event_id", "")),
    ))
    entries: dict[str, list[dict[str, Any]]] = {}
    result: list[dict[str, Any]] = []
    for event in ordered:
        if event.get("event_type") not in {"FILL", "RISK_FILL", "PARTIAL_FILL"}:
            continue
        symbol = str(event.get("symbol", ""))
        quantity = int(event.get("quantity", 0))
        if event.get("side") == "BUY" and quantity > 0:
            entries.setdefault(symbol, []).append({
                "opened_at": str(event.get("occurred_at", "")),
                "quantity": quantity,
                "remaining": quantity,
                "notional_won": int(event.get("notional_won", 0)),
                "entry_cost_won": int(event.get("total_cost_won", 0)),
                "decision_id": str(event.get("decision_id", "")),
            })
            continue
        if event.get("side") != "SELL" or quantity <= 0 or not entries.get(symbol):
            continue
        remaining_sell = quantity
        while remaining_sell > 0 and entries[symbol]:
            entry = entries[symbol][0]
            matched = min(remaining_sell, int(entry["remaining"]))
            entry_quantity = int(entry["quantity"])
            allocated_notional = int(entry["notional_won"]) * matched // entry_quantity
            allocated_cost = int(entry["entry_cost_won"]) * matched // entry_quantity
            sell_cost = int(event.get("total_cost_won", 0)) * matched // quantity
            sell_notional = int(event.get("notional_won", 0)) * matched // quantity
            realized = int(event.get("realized_pnl_won", 0)) * matched // quantity
            opened_at = str(entry["opened_at"])
            closed_at = str(event.get("occurred_at", ""))
            result.append({
                "symbol": symbol, "opened_at": opened_at, "closed_at": closed_at,
                "candidate_key": (
                    candidate_keys_by_decision.get(str(entry["decision_id"]), "")
                    if candidate_keys_by_decision is not None else ""
                ),
                "quantity": matched, "net_pnl_won": realized,
                "total_cost_won": allocated_cost + sell_cost,
                "turnover_won": allocated_notional + sell_notional,
                "exposure_seconds": max(0, int(
                    (_aware_datetime(closed_at) - _aware_datetime(opened_at)).total_seconds()
                )),
            })
            entry["remaining"] = int(entry["remaining"]) - matched
            remaining_sell -= matched
            if entry["remaining"] == 0:
                entries[symbol].pop(0)
    return tuple(result)


def _event_outcome_rows(labels: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    grouped: dict[int, list[int]] = {}
    for label in labels:
        values = label.get("values")
        if not isinstance(values, Mapping) or values.get("return_bps") is None:
            continue
        grouped.setdefault(int(label.get("horizon_seconds", 0)), []).append(
            int(values["return_bps"])
        )
    return tuple({
        "horizon_seconds": horizon, "sample_count": len(values),
        "average_return_bps": sum(values) // len(values),
        "median_return_bps": int(median(values)),
        "positive_count": sum(value > 0 for value in values),
    } for horizon, values in sorted(grouped.items()))


def _group_trade_rows(
    trades: list[Mapping[str, Any]], key_fn: Any,
) -> tuple[Mapping[str, Any], ...]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(str(key_fn(trade)), []).append(trade)
    return tuple({
        "key": key, "closed_trade_count": len(values),
        "net_realized_pnl_won": sum(int(row["net_pnl_won"]) for row in values),
        "winning_trade_count": sum(int(row["net_pnl_won"]) > 0 for row in values),
    } for key, values in sorted(grouped.items()))


def _maximum_drawdown(
    fold: Any, events: tuple[Mapping[str, Any], ...], initial_cash_won: int,
) -> tuple[int | None, int | None]:
    ordered = sorted(events, key=lambda row: (
        _aware_datetime(row.get("occurred_at")).astimezone(timezone.utc),
        str(row.get("event_id", "")),
    ))
    last_equity = initial_cash_won
    fold_start = _aware_datetime(fold.start).astimezone(timezone.utc)
    for event in ordered:
        occurred = _aware_datetime(event.get("occurred_at")).astimezone(timezone.utc)
        if occurred >= fold_start:
            break
        price = int(event.get("price", 0))
        if price > 0 and event.get("event_type") in {"FILL", "RISK_FILL", "MARK", "PARTIAL_FILL"}:
            last_equity = int(event.get("cash_after_won", 0)) + (
                int(event.get("position_quantity_after", 0)) * price
            )
    points: list[int] = [last_equity]
    for event in ordered:
        occurred = _aware_datetime(event.get("occurred_at")).astimezone(timezone.utc)
        if occurred < fold_start:
            continue
        price = int(event.get("price", 0))
        if price > 0 and event.get("event_type") in {"FILL", "RISK_FILL", "MARK", "PARTIAL_FILL"}:
            last_equity = int(event.get("cash_after_won", 0)) + (
                int(event.get("position_quantity_after", 0)) * price
            )
        if _point_in_fold(occurred.isoformat(), fold):
            points.append(last_equity)
    if len(points) == 1:
        return None, None
    peak = points[0]
    maximum = 0
    for value in points:
        peak = max(peak, value)
        maximum = max(maximum, peak - value)
    return maximum, maximum * 1_000_000 // peak if peak > 0 else None


def _data_quality(
    manifest: Mapping[str, Any], observations: tuple[Mapping[str, Any], ...],
    spec: ResearchEvaluationSpec, logical_result_hash: str,
) -> Mapping[str, Any]:
    minute = tuple(row for row in observations if row.get("kind") == "minute_bar")
    historical_reconstruction = _historical_reconstruction_input(manifest)
    universe_kind = (
        "historical_candidate_population" if historical_reconstruction else "top20_membership"
    )
    universe = tuple(row for row in observations if row.get("kind") == universe_kind)
    strict = tuple(row for row in minute if (
        row.get("venue") == "KRX" and row.get("completeness") == "complete"
        and row.get("value_kind") == "actual" and isinstance(row.get("payload"), Mapping)
        and row["payload"].get("window_closed") is True
        and row["payload"].get("capture_quality") == "complete"
    ))
    reasons: list[str] = []
    if not strict:
        reasons.append("strict_krx_minute_bars_missing")
    if not universe:
        reasons.append("candidate_universe_missing")
    if historical_reconstruction and manifest.get("not_contemporaneous_top20") is not True:
        reasons.append("historical_population_boundary_missing")
    revision_ids = [str(row.get("revision_id", "")) for row in observations]
    actual_hash = hashlib.sha256("\n".join(revision_ids).encode("utf-8")).hexdigest()
    reproducible = (
        bool(logical_result_hash)
        and len(observations) == int(manifest.get("revision_count", -1))
        and actual_hash == manifest.get("revision_ids_hash")
    )
    if not reproducible:
        reasons.append("input_manifest_or_result_hash_unverified")
    available_times = [
        _aware_datetime(row.get("available_at")).astimezone(timezone.utc)
        for row in observations if row.get("available_at")
    ]
    warmup_covered = bool(available_times) and min(available_times) <= warmup_start(spec)
    if manifest.get('development_partition') is not None:
        # Universe seeds and backfills received after active start cannot prove bar warmup.
        lower = warmup_start(spec)
        active = _aware_datetime(spec.folds[0].start).astimezone(timezone.utc)
        warmup_covered = spec.warmup_seconds == 0 or any(
            _aware_datetime(row['payload']['bar_start']).astimezone(timezone.utc) <= lower
            < _aware_datetime(row['payload']['bar_end']).astimezone(timezone.utc) <= active
            and _aware_datetime(row['available_at']).astimezone(timezone.utc) <= active
            for row in strict
        )
    if not warmup_covered:
        reasons.append("configured_warmup_not_covered")
    return {
        "status": "INELIGIBLE" if reasons else "PASS",
        "reasons": tuple(reasons),
        "revision_count": len(observations),
        "strict_krx_minute_bar_revision_count": len(strict),
        "excluded_minute_bar_revision_count": len(minute) - len(strict),
        "candidate_universe_revision_count": len(universe),
        "reproducibility": "VERIFIED" if reproducible else "UNVERIFIED",
    }


def _historical_reconstruction_input(manifest: Mapping[str, Any]) -> bool:
    return (
        manifest.get("runtime_input_version") == "historical_reconstruction_strategy/v1"
        or manifest.get("source_runtime_input_version") == "historical_reconstruction_strategy/v1"
    )


def _cost_model_reasons(
    cost_model: Mapping[str, Any] | None, spec: ResearchEvaluationSpec,
) -> tuple[str, ...]:
    if cost_model is None:
        return ("cost_model_missing",)
    reasons: list[str] = []
    if cost_model.get("rate_basis") == "unspecified" or not str(cost_model.get("source", "")).strip():
        reasons.append("cost_model_provenance_missing")
    valid_from = str(cost_model.get("valid_from", ""))
    valid_to = str(cost_model.get("valid_to", ""))
    if not valid_from or not valid_to:
        reasons.append("cost_model_validity_missing")
    else:
        start = _aware_datetime(valid_from).astimezone(timezone.utc)
        end = _aware_datetime(valid_to).astimezone(timezone.utc)
        first = _aware_datetime(spec.folds[0].start).astimezone(timezone.utc)
        last = _aware_datetime(spec.folds[-1].end).astimezone(timezone.utc)
        if start > first or end < last:
            reasons.append("cost_model_validity_does_not_cover_folds")
    return tuple(reasons)


def _point_in_fold(value: object, fold: Any) -> bool:
    point = _aware_datetime(value).astimezone(timezone.utc)
    return (
        _aware_datetime(fold.start).astimezone(timezone.utc)
        <= point
        < _aware_datetime(fold.end).astimezone(timezone.utc)
    )


def _kst_date(value: object) -> str:
    return _aware_datetime(value).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()


def _time_bucket(value: object) -> str:
    hour = _aware_datetime(value).astimezone(ZoneInfo("Asia/Seoul")).hour
    if 9 <= hour < 10:
        return "09:00-10:00"
    if 10 <= hour < 12:
        return "10:00-12:00"
    return "other"


def _document(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return value.to_dict()


def _label(
    candidate: CandidateEvent,
    horizon_seconds: int,
    horizon_end: datetime,
    *,
    matured_at: str,
    computed_at: str,
    available_at: str,
    status: str,
    values: Mapping[str, Any] | None,
    input_refs: tuple[str, ...],
    reason: str,
) -> OutcomeLabel:
    body = {
        "run_id": candidate.run_id,
        "candidate_event_id": candidate.event_id,
        "symbol": candidate.symbol,
        "horizon_seconds": horizon_seconds,
        "horizon_end": horizon_end.isoformat(),
        "matured_at": matured_at,
        "computed_at": computed_at,
        "available_at": available_at,
        "status": status,
        "values": values,
        "input_refs": input_refs,
        "reason": reason,
    }
    return OutcomeLabel(label_id=_content_id("outcome", body), **body)


def _summary(
    run_id: str,
    status: str,
    reason: str,
    initial_cash_won: int,
    closed_trade_count: int,
    winning_trade_count: int | None,
    gross: int | None,
    cost: int | None,
    net: int | None,
    return_ppm: int | None,
    optimistic: int | None,
) -> PerformanceSummary:
    body = {
        "run_id": run_id, "status": status, "reason": reason,
        "initial_cash_won": initial_cash_won,
        "closed_trade_count": closed_trade_count,
        "winning_trade_count": winning_trade_count,
        "gross_realized_pnl_won": gross, "total_cost_won": cost,
        "net_realized_pnl_won": net, "return_ppm": return_ppm,
        "optimistic_additional_pnl_won": optimistic,
    }
    return PerformanceSummary(evaluation_id=_content_id("performance", body), **body)


def _continuous_minutes(bars: list[KrxMinuteBarFrame]) -> bool:
    if not bars:
        return False
    for previous, current in zip(bars, bars[1:]):
        if _aware_datetime(previous.bar_end) != _aware_datetime(current.bar_start):
            return False
    return all(
        _aware_datetime(bar.bar_end) - _aware_datetime(bar.bar_start) == timedelta(minutes=1)
        for bar in bars
    )


def _continuous_minutes_from(event_time: datetime, bars: list[KrxMinuteBarFrame]) -> bool:
    if not bars:
        return False
    normalized = event_time.astimezone(timezone.utc)
    expected = normalized.replace(second=0, microsecond=0)
    if normalized != expected:
        expected += timedelta(minutes=1)
    first = _aware_datetime(bars[0].bar_start).astimezone(timezone.utc)
    return first == expected and _continuous_minutes(bars)


def _content_id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _aware_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("outcome timestamps must be timezone-aware")
    return parsed
