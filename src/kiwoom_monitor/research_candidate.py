"""Local frozen candidate artifact workflow shared by the GUI and CLI."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping
from collections import Counter
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from kiwoom_monitor.application.mock_automation_candidate import (
    MAX_CANDIDATE_PUBLICATION_BYTES,
    MockAutomationEligibilityPolicy,
    candidate_package_from_dict,
    eligibility_receipt_from_dict,
    eligibility_policy_from_dict,
    evaluate_candidate_eligibility,
    validate_publication_size,
)
from kiwoom_monitor.application.research_implementation import research_implementation_hash
from kiwoom_monitor.domain.time_basis import as_kst, kst_now
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.research_process import (
    final_candidate_spec_hash, final_candidate_spec_document,
    development_validation_request_from_document,
    prepare_mock_automation_candidate_publication,
    research_process_request_from_document,
)


from kiwoom_monitor.application.market_session_schedule import (
    KST, krx_regular_session_hours, research_session_profile_contract_matches,
)
from kiwoom_monitor.application.research_families import (
    BREAKOUT_FAMILY_ID as BREAKOUT_FAMILY, PULLBACK_FAMILY_ID as PULLBACK_FAMILY,
)
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec, DevelopmentPartitionSpec
from kiwoom_monitor.infrastructure.research_data_source import load_daily_development_source, prepare_development_partition
from kiwoom_monitor.infrastructure.historical_research_readiness import (
    assess_historical_development_readiness, audit_development_gap_provenance,
)

CRITERIA_NAMES = ("minimum_closed_trades", "minimum_active_days",
                  "minimum_net_realized_pnl_won", "maximum_drawdown_ppm")
EXPECTED = (
    ("TRAIN", "breakout", "train-breakout.json"),
    ("TRAIN", "pullback", "train-pullback.json"),
    ("VALIDATION", "breakout", "validation-breakout.json"),
    ("VALIDATION", "pullback", "validation-pullback.json"),
)


def _read_object(path: Path, *, maximum_bytes: int) -> dict[str, Any]:
    with Path(path).open("rb") as stream:
        encoded = stream.read(maximum_bytes + 1)
    if len(encoded) > maximum_bytes:
        raise ValueError("입력 파일 크기 상한을 초과했습니다.")
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError("JSON 객체가 필요합니다.")
    return value


def _request(path: Path):
    # Keep the original bounded JSON-object precheck, but consume its bytes once.
    with Path(path).open("rb") as stream:
        encoded = stream.read(4 * 1024 * 1024 + 1)
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("입력 파일 크기 상한을 초과했습니다.")
    document = json.loads(encoded)
    if not isinstance(document, dict):
        raise ValueError("JSON 객체가 필요합니다.")
    # Match the former strict UTF8 text read after its JSON-object precheck.
    # json.loads(bytes) also accepts raw surrogate bytes via surrogatepass.
    encoded.decode("utf-8")
    # Preserve the former second JSON parse's BOM/UTF16/UTF32 rejection.
    if json.detect_encoding(encoded) != "utf-8":
        raise ValueError("research request cannot be read")
    return research_process_request_from_document(document, Path(path).resolve().parent)


def _output_path(output: Path, request) -> Path:
    path = Path(output).resolve()
    if path.suffix.lower() != ".json":
        raise ValueError("출력은 JSON 파일이어야 합니다.")
    if path.is_relative_to(request.dataset.resolve()):
        raise ValueError("동결 연구 입력 안에는 출력할 수 없습니다.")
    return path


def _write_immutable(path: Path, document: dict[str, Any]) -> None:
    from kiwoom_monitor.infrastructure.persistence.backup_file import ImmutableContentConflict, write_immutable_bytes

    encoded = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        write_immutable_bytes(path, encoded)
    except ImmutableContentConflict:
        raise ValueError("다른 내용의 동결 파일이 이미 있습니다.") from None


def freeze_policy(
    request_path: Path, output: Path, *, strategy_ref: str,
    minimum_closed_trades: int | None = None,
    minimum_active_days: int | None = None,
    minimum_net_realized_pnl_won: int | None = None,
    maximum_drawdown_ppm: int | None = None,
    now_provider: Callable[[], datetime] = kst_now,
) -> MockAutomationEligibilityPolicy:
    """Freeze local artifacts; offline CLI/tests use KST fallback, UI supplies server time."""
    request = _request(request_path)
    output = _output_path(output, request)
    candidate_hash = final_candidate_spec_hash(request, research_implementation_hash(request.session_profile))
    values = {
        "strategy_ref": strategy_ref, "candidate_spec_hash": candidate_hash,
        "minimum_closed_trades": minimum_closed_trades,
        "minimum_active_days": minimum_active_days,
        "minimum_net_realized_pnl_won": minimum_net_realized_pnl_won,
        "maximum_drawdown_ppm": maximum_drawdown_ppm,
    }
    if output.exists():
        policy = eligibility_policy_from_dict(_read_object(output, maximum_bytes=MAX_CANDIDATE_PUBLICATION_BYTES))
        if any(getattr(policy, name) != value for name, value in values.items()):
            raise ValueError("기존 동결 기준이나 후보 설정을 변경할 수 없습니다.")
        return policy
    policy = MockAutomationEligibilityPolicy(frozen_at=as_kst(now_provider()), **values)
    _write_immutable(output, policy.to_dict())
    return policy


def prepare_publication(
    request_path: Path, policy_path: Path, output: Path, *,
    account_ref: str, final_batch_id: str, final_run_id: str,
) -> dict[str, Any]:
    request = _request(request_path)
    output = _output_path(output, request)
    policy = eligibility_policy_from_dict(_read_object(policy_path, maximum_bytes=MAX_CANDIDATE_PUBLICATION_BYTES))
    implementation_hash = research_implementation_hash(request.session_profile)
    if policy.candidate_spec_hash != final_candidate_spec_hash(request, implementation_hash):
        raise ValueError("동결 기준과 현재 전략·비용·구현 설정이 다릅니다.")
    if not account_ref.strip() or not final_batch_id.strip() or not final_run_id.strip():
        raise ValueError("계좌 식별자와 final batch/run 참조가 필요합니다.")
    repository = ResearchRepository(request.database, read_only=True)
    documents = prepare_mock_automation_candidate_publication(
        repository, request, implementation_hash=implementation_hash,
        strategy_ref=policy.strategy_ref, account_ref=account_ref,
        final_batch_id=final_batch_id, final_run_id=final_run_id,
        eligibility_policy=policy,
    )
    _write_immutable(output, documents)
    return documents


def load_publication(path: Path, *, account_ref: str) -> dict[str, Any]:
    """검토·게시 때 같은 bounded 파일과 재계산한 판정을 확인한다."""
    document = _read_object(path, maximum_bytes=4 * 1024 * 1024)
    if set(document) != {"package", "eligibility_policy", "eligibility_receipt"}:
        raise ValueError("후보 게시 파일의 문서 구성이 올바르지 않습니다.")
    if any(not isinstance(value, dict) for value in document.values()):
        raise ValueError("후보 게시 문서는 JSON 객체여야 합니다.")
    validate_publication_size(document["package"], document["eligibility_policy"], document["eligibility_receipt"])
    package = candidate_package_from_dict(document["package"])
    policy = eligibility_policy_from_dict(document["eligibility_policy"])
    receipt = eligibility_receipt_from_dict(document["eligibility_receipt"])
    if receipt.account_ref != account_ref:
        raise ValueError("선택한 모의계좌와 후보 파일의 계좌가 다릅니다.")
    expected = evaluate_candidate_eligibility(package, policy, account_ref=account_ref)
    if receipt.to_dict() != expected.to_dict():
        raise ValueError("후보 파일의 적격 판정이 재계산 결과와 다릅니다.")
    session = package.candidate_spec["session_profile"]["profile"]
    if package.scientific_implementation_hash != research_implementation_hash(session):
        raise ValueError("후보 파일과 현재 전략 구현이 다릅니다.")
    return document


def inspect_development_baselines(root: Path) -> dict[str, Any]:
    """고정 네 개발 실행만 조회한다. 입력 재생·final 접근·후보 승격은 없다."""
    root = Path(root).resolve()
    references = []
    # 요청의 분할부터 검사한다. OOS 요청이면 결과와 원장을 조회하지 않는다.
    for role, family, filename in EXPECTED:
        request = _read_review_object(root / "requests" / filename)
        context = request.get("historical_baseline_context", {})
        evaluation = request.get("evaluation", {})
        if not isinstance(context, dict) or not isinstance(evaluation, dict):
            raise ValueError("개발 요청의 분할 메타데이터가 잘못됐습니다.")
        folds = evaluation.get("folds", [])
        expected_family = BREAKOUT_FAMILY if family == "breakout" else PULLBACK_FAMILY
        if (request.get("mode") != "single_run"
                or not isinstance(request.get("strategy"), dict)
                or not isinstance(request.get("execution"), dict)
                or request.get("family") != expected_family
                or request.get("session_profile") != "krx-regular/v1"
                or context.get("version") != "historical_baseline_context/v1"
                or context.get("oos_included") is not False
                or context.get("partition_role") != role
                or not isinstance(folds, list) or len(folds) != 1
                or not isinstance(folds[0], dict) or folds[0].get("role") != role
                or evaluation.get("final_holdout_accessed_at")
                or evaluation.get("final_holdout_access_reason")):
            raise ValueError("TRAIN·VALIDATION 고정 기준 요청만 확인할 수 있습니다.")
        references.append((role, family, filename, request))
    repository = ResearchRepository(root / "research.sqlite3", read_only=True)
    current_hash = research_implementation_hash("krx-regular/v1")
    records = []
    for role, family, filename, request in references:
        result = _read_review_object(root / "results" / filename)
        run_id = result.get("run_id")
        if (result.get("status") != "ok" or result.get("kind") != "single_run"
                or not isinstance(run_id, str) or not run_id.strip()):
            raise ValueError(f"완료 실행 참조가 없습니다: {filename}")
        run = repository.load_run(run_id)
        if run is None or run["status"] != "completed":
            raise ValueError(f"원장에 완료 실행이 없습니다: {filename}")
        spec, manifest = run["spec"], run["input_manifest"]
        if not isinstance(spec, dict) or not isinstance(manifest, dict):
            raise ValueError("개발 원장 메타데이터가 잘못됐습니다.")
        evaluation = spec.get("evaluation_spec", {})
        if not isinstance(evaluation, dict):
            raise ValueError("개발 원장 분할이 잘못됐습니다.")
        folds = evaluation.get("folds", [])
        if (manifest.get("runtime_input_version") != "independent_development_input/v1"
                or spec.get("family") != request["family"]
                or evaluation != request["evaluation"]
                or spec.get("parameters") != request.get("strategy")
                or spec.get("execution_model") != request.get("execution")
                or not research_session_profile_contract_matches(spec.get("session_profile"), "krx-regular/v1")
                or not isinstance(folds, list) or len(folds) != 1
                or not isinstance(folds[0], dict) or folds[0].get("role") != role
                or evaluation.get("final_holdout_accessed_at")
                or evaluation.get("final_holdout_access_reason")):
            raise ValueError(f"요청과 원장의 개발 조건이 다릅니다: {filename}")
        records.append({"role": role, "family": family, "run_id": run_id,
                        "spec": spec, "manifest": manifest})
    comparisons, rows = {}, []
    for family in ("breakout", "pullback"):
        selected = [row for row in records if row["family"] == family]
        comparison = repository.load_independent_development_comparison(
            tuple(row["run_id"] for row in selected),
        ).to_dict()
        comparisons[family] = comparison
        for record, partition in zip(selected, comparison["partitions"], strict=True):
            spec = record["spec"]
            report = repository.load_research_report(record["run_id"]) or {}
            if not isinstance(report, dict):
                raise ValueError("개발 보고서가 JSON 객체가 아닙니다.")
            folds = report.get("fold_reports", [])
            fold = folds[0] if isinstance(folds, list) and len(folds) == 1 and isinstance(folds[0], dict) else {}
            rows.append({**partition, "family": family,
                         "code_hash": spec.get("code_hash", ""),
                         "current_code_matches": spec.get("code_hash") == current_hash,
                         "parameters": spec["parameters"],
                         "cost_model": spec.get("cost_model"),
                         "data_quality": report.get("data_quality"),
                         "limitations": report.get("limitations", []),
                         "censored_order_count": fold.get("censored_order_count"),
                         "no_trade_reasons": fold.get("no_trade_reasons", {}),
                         "position_observations": position_observation_summary(
                             repository.load_execution_events(record["run_id"]),
                         )})
    pair_reasons = []
    for role in ("TRAIN", "VALIDATION"):
        a, b = [row for row in records if row["role"] == role]
        if any(a["manifest"].get(key) != b["manifest"].get(key)
               for key in ("dataset_id", "revision_ids_hash")):
            pair_reasons.append(f"{role}:input_mismatch")
        if any(a["spec"].get(key) != b["spec"].get(key)
               for key in ("evaluation_spec", "execution_model", "session_profile", "code_hash")):
            pair_reasons.append(f"{role}:conditions_mismatch")
    return {
        "version": "historical_baseline_review/v1", "root": str(root),
        "development_only": True, "oos_queried": False,
        "candidate_eligibility_evaluated": False,
        "current_code_hash": current_hash, "runs": rows,
        "comparisons": comparisons, "paired_input_reasons": pair_reasons,
        "parameter_status": "provisional_not_user_approved",
        "cost_status": "development_model_not_broker_verified",
    }


def _read_review_object(path: Path, *, include_bytes: bool = False):
    with path.open("rb") as stream:
        encoded = stream.read(4 * 1024 * 1024 + 1)
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("개발 결과 파일 크기 상한을 초과했습니다.")
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError("개발 결과는 JSON 객체여야 합니다.")
    return (encoded, value) if include_bytes else value


def position_observation_summary(events) -> dict[str, Any]:
    """원장의 종료 미청산 포지션만 추적한다. 원본 봉의 존재·거래 가능성을 추정하지 않는다."""
    terminal = next((event for event in reversed(events)
                     if event.get("event_type") == "POSITION_CENSORED"), None)
    if terminal is None:
        return {"status": "NO_TERMINAL_POSITION_CENSOR", "source": "execution_events"}
    symbol, intent = terminal["symbol"], terminal["intent_id"]
    entry = next((event for event in events if event.get("event_type") == "FILL"
                  and event.get("side") == "BUY" and event.get("intent_id") == intent
                  and event.get("symbol") == symbol), None)
    if entry is None:
        return {"status": "ENTRY_REFERENCE_MISSING", "source": "execution_events", "symbol": symbol}
    def timestamp(value):
        at = datetime.fromisoformat(value)
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("포지션 원장 시각에는 시간대가 필요합니다.")
        return at.astimezone(timezone.utc)
    opened, ended = timestamp(entry["occurred_at"]), timestamp(terminal["occurred_at"])
    if ended < opened:
        raise ValueError("포지션 원장의 종료 시각이 진입보다 이릅니다.")
    relevant = [event for event in events if event.get("symbol") == symbol
                and opened <= timestamp(event["occurred_at"]) <= ended]
    observations = [event for event in relevant
                    if event.get("event_type") in {"MARK", "RISK_UNSUPPORTED"}
                    and event.get("intent_id") == intent and event.get("source_revision_id")]
    last_observation = max((timestamp(event["occurred_at"]) for event in observations), default=None)
    censor_reasons = Counter(event["reason"] for event in relevant
                             if event.get("side") == "SELL"
                             and event.get("event_type") in {"ORDER_CENSORED", "ORDER_UNSUPPORTED"})
    return {
        "status": "OPEN_POSITION_CENSORED", "source": "execution_events",
        "symbol": symbol, "quantity": terminal["quantity"],
        "entry_at": opened.isoformat(), "end_at": ended.isoformat(),
        "last_source_observation_at": last_observation.isoformat() if last_observation else None,
        "exit_request_count": sum(event.get("event_type") == "ORDER_SUBMITTED"
                                  and event.get("side") == "SELL" for event in relevant),
        "exit_censored_reasons": dict(sorted(censor_reasons.items())),
        "position_censor_reason": terminal["reason"],
    }


def _daily_development_requests(root: Path, *, include_raw: bool = False):
    """자료·원장을 열기 전에 고정 일별 계획과 두 요청의 개발 경계를 확인한다."""
    root = Path(root).resolve()
    plan = _read_review_object(root / "daily-plan.json")
    selection = plan.get("selection", {})
    evaluation = plan.get("evaluation", {})
    if not isinstance(selection, dict) or not isinstance(evaluation, dict) or not isinstance(selection.get("oos"), dict):
        raise ValueError("일별 개발 계획의 분할 메타데이터가 잘못됐습니다.")
    folds = evaluation.get("folds", [])
    cases = selection.get("cases", [])
    if (plan.get("version") != "historical_daily_development_plan/v1" or plan.get("oos_included") is not False
            or selection.get("oos", {}).get("status") != "SEALED"
            or not isinstance(folds, list) or not 2 <= len(folds) <= 20
            or not isinstance(cases, list) or len(cases) != len(folds)
            or any(not isinstance(f, dict) or f.get("role") not in {"TRAIN", "VALIDATION"} for f in folds)
            or any(not isinstance(c, dict) for c in cases)
            or plan["evaluation"].get("final_holdout_accessed_at")
            or plan["evaluation"].get("final_holdout_access_reason")):
        raise ValueError("봉인 OOS를 제외한 고정 일별 개발 계획만 조회할 수 있습니다.")
    ResearchEvaluationSpec.from_dict(plan["evaluation"])
    boundary = selection["oos"]["selection_date"]
    for case, fold in zip(cases, folds, strict=True):
        if not (case["selection_date"] < case["outcome_date"] < boundary and case["role"] == fold["role"]
                and datetime.fromisoformat(fold["start"]).astimezone(KST).date().isoformat() == case["outcome_date"]
                and datetime.fromisoformat(fold["end"]).astimezone(KST).date().isoformat() == case["outcome_date"]):
            raise ValueError("일별 계획의 결과일·역할·OOS 경계가 일치하지 않습니다.")
    references = []
    for family, family_id in (("breakout", BREAKOUT_FAMILY), ("pullback", PULLBACK_FAMILY)):
        encoded, request = _read_review_object(root / "requests" / f"daily-{family}.json", include_bytes=True)
        locked = request.get("request", {})
        strategy = next((s for s in plan["strategies"] if s["name"] == family), {})
        if (request.get("version") != "independent_development_validation/v1"
                or locked.get("mode") != "single_run" or locked.get("family") != family_id
                or locked.get("session_profile") != "krx-regular/v1"
                or locked.get("strategy") != strategy.get("strategy")
                or locked.get("execution") != plan["execution"] or locked.get("evaluation") != plan["evaluation"]
                or request.get("fold_names") != [f["name"] for f in folds]
                or Path(locked["database"]).resolve() != root / "research.sqlite3"):
            raise ValueError("고정 일별 개발 요청과 계획이 일치하지 않습니다.")
        references.append((family, request, encoded) if include_raw else (family, request))
    if any(references[0][1].get(k) != references[1][1].get(k) for k in ("dataset_id", "dataset_hash")):
        raise ValueError("두 전략의 개발 입력 식별자가 다릅니다.")
    return plan, references


def assess_development_cost_reference(plan, manifest, costs, reference_path: Path) -> dict[str, Any]:
    """자료 바이트에 묶인 날짜별 비용 참고값만 대조한다. 계좌 비용을 확정하지 않는다."""
    reference = _read_review_object(Path(reference_path))
    if (reference.get("version") != "historical_stock_cost_reference/v1"
            or reference.get("asset_scope") != "kospi_kosdaq_individual_stocks/v1"
            or manifest.get("individual_stocks_only") is not True
            or any(reference.get(key) != manifest.get(key) or not manifest.get(key)
                   for key in ("dataset_id", "revision_ids_hash", "observations_file_hash"))):
        raise ValueError("비용 참고 자료의 대상·원본 바이트 식별자가 다릅니다.")
    checked = datetime.fromisoformat(str(reference.get("checked_at", "")))
    if checked.tzinfo is None or checked.utcoffset() is None:
        raise ValueError("비용 참고 자료 확인 시각에는 시간대가 필요합니다.")
    def rate(value):
        if not isinstance(value, str):
            raise ValueError("참고 비용은 정확한 bp 문자열이어야 합니다.")
        try:
            result = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("참고 비용 bp가 잘못됐습니다.") from error
        if not result.is_finite() or not 0 <= result <= 10000:
            raise ValueError("참고 비용 bp 범위가 잘못됐습니다.")
        return result
    def sources(value):
        if not isinstance(value, list) or not value or not all(
                isinstance(url, str) and urlparse(url).scheme == "https" and urlparse(url).hostname
                and not urlparse(url).username and not urlparse(url).password for url in value):
            raise ValueError("참고 비용의 출처 URL이 필요합니다.")
        return value
    commission = rate(reference.get("commission_bps"))
    commission_sources = sources(reference.get("commission_source_urls"))
    dates = reference.get("dates")
    expected = {case["outcome_date"] for case in plan["selection"]["cases"]}
    if (not isinstance(dates, list) or len(dates) != len(expected)
            or any(not isinstance(row, dict) or not isinstance(row.get("outcome_date"), str) for row in dates)
            or {row.get("outcome_date") for row in dates} != expected):
        raise ValueError("참고 비용의 날짜는 고정 개발 결과일 전체와 일치해야 합니다.")
    rows = []
    for row in sorted(dates, key=lambda row: row["outcome_date"]):
        tax = rate(row.get("sell_tax_bps"))
        urls = sources(row.get("source_urls"))
        rows.append({"outcome_date": row["outcome_date"], "reference_sell_tax_bps": str(tax),
                     "model_sell_tax_bps": str(costs.sell_tax_bps),
                     "matches": tax == Decimal(str(costs.sell_tax_bps)), "source_urls": urls})
    commission_matches = commission == Decimal(str(costs.commission_bps))
    return {"status": "MATCH" if commission_matches and all(row["matches"] for row in rows) else "MISMATCH",
            "reference_document_sha256": hashlib.sha256(json.dumps(reference, sort_keys=True,
                ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
            "reference_checked_at": checked.isoformat(), "reference_commission_bps": str(commission),
            "commission_source_urls": commission_sources, "commission_matches": commission_matches,
            "tax_mismatch_day_count": sum(not row["matches"] for row in rows), "dates": rows,
            "account_costs_verified": False, "slippage_verified": False,
            "limitations": ["reference_urls_not_account_execution_costs", "existing_results_not_recosted_or_replayed"]}


def inspect_daily_development_inputs(root: Path, *, cost_reference: Path | None = None,
                                     gap_provenance: Path | None = None,
                                     checkpoint: Callable[[], None] = lambda: None) -> dict[str, Any]:
    """Read one frozen operation and inspect it without replay or native writes."""
    root = Path(root).resolve()
    checkpoint()
    plan, references = _daily_development_requests(root, include_raw=True)
    batches = [development_validation_request_from_document(document, root / "requests", encoded=encoded)
               for _, document, encoded in references]
    return _inspect_daily_development_inputs(plan, batches, cost_reference=cost_reference,
        gap_provenance=gap_provenance, checkpoint=checkpoint)


def _inspect_daily_development_inputs(plan, batches, *, cost_reference=None,
                                      gap_provenance=None, checkpoint=lambda: None):
    if batches[0].request.dataset.resolve() != batches[1].request.dataset.resolve():
        raise ValueError("두 전략의 개발 입력 경로가 다릅니다.")
    dataset = load_daily_development_source(plan, batches[0].request.dataset, checkpoint=checkpoint)
    if (dataset.manifest["dataset_id"], dataset.manifest["revision_ids_hash"]) != (
            batches[0].dataset_id, batches[0].dataset_hash):
        raise ValueError("동결 요청과 실제 입력 식별자가 다릅니다.")
    evaluation = ResearchEvaluationSpec.from_dict(plan["evaluation"])
    costs = batches[0].request.execution.cost_model
    valid_from, valid_to = (datetime.fromisoformat(value) for value in (costs.valid_from, costs.valid_to))
    cost_coverage = all(valid_from <= datetime.fromisoformat(fold.start)
                        and valid_to >= datetime.fromisoformat(fold.end) for fold in evaluation.folds)
    rows, partitions = [], []
    for fold, case in zip(evaluation.folds, plan["selection"]["cases"], strict=True):
        checkpoint()
        projected = prepare_development_partition(dataset, DevelopmentPartitionSpec("development_partition/v2", fold.name), evaluation, checkpoint=checkpoint)
        readiness = assess_historical_development_readiness(projected)
        if readiness.candidate_code_count != case["candidate_codes_after_exclusion"]:
            raise ValueError("실제 분할 후보 수와 고정 계획이 다릅니다.")
        partitions.append({"fold_name": fold.name, "readiness": readiness.to_dict()})
        starts_by_code = {}
        for row in projected.observations:
            checkpoint()
            payload = row["payload"]
            if row["kind"] == "historical_candidate_population" and payload.get("selection_date") == case["selection_date"]:
                starts_by_code.update({code: set() for code in payload["codes"]})
        day = datetime.fromisoformat(fold.start).astimezone(KST).date()
        opened, closed = krx_regular_session_hours(day)
        start = datetime.combine(day, opened, KST)
        end = datetime.combine(day, closed, KST) - timedelta(minutes=10)
        expected = int((end - start).total_seconds()) // 60
        outside = Counter()
        for row in projected.observations:
            checkpoint()
            if row["kind"] != "minute_bar" or row.get("venue") != "KRX":
                continue
            payload = row["payload"]
            if payload.get("historical_case_date") != case["selection_date"] or payload.get("code") not in starts_by_code:
                continue
            stamp = datetime.fromisoformat(payload["bar_start"]).astimezone(KST)
            if stamp.second or stamp.microsecond:
                raise ValueError("분봉 시작 시각이 분 경계와 다릅니다.")
            if start <= stamp < end:
                starts_by_code[payload["code"]].add(stamp)
            else:
                outside[payload["code"]] += 1
        for code, stamps in sorted(starts_by_code.items()):
            ordered = sorted(stamps)
            gaps = [{"previous_bar_start": previous.isoformat(), "next_bar_start": current.isoformat(),
                     "unobserved_minutes": int((current - previous).total_seconds()) // 60 - 1}
                    for previous, current in zip(ordered, ordered[1:]) if current - previous > timedelta(minutes=1)]
            rows.append({"fold_name": fold.name, "role": fold.role, "code": code,
                         "selection_date": case["selection_date"], "outcome_date": case["outcome_date"],
                         "continuous_session_start": start.isoformat(), "continuous_session_end": end.isoformat(),
                         "expected_minute_slots": expected, "observed_unique_minutes": len(stamps),
                         "unobserved_minute_slots": expected - len(stamps),
                         "first_bar_start": ordered[0].isoformat() if ordered else None,
                         "last_bar_start": ordered[-1].isoformat() if ordered else None,
                         "leading_unobserved_minutes": int((ordered[0] - start).total_seconds()) // 60 if ordered else expected,
                         "trailing_unobserved_minutes": int((end - ordered[-1]).total_seconds()) // 60 - 1 if ordered else 0,
                         "internal_gaps": gaps, "outside_continuous_session_observations": outside[code]})
    reasons = ["broker_costs_unverified", "full_session_completeness_unverified"]
    if not cost_coverage:
        reasons.append("cost_validity_does_not_cover_development_windows")
    if any(partition["readiness"]["status"] != "READY" for partition in partitions):
        reasons.append("minimum_continuous_pair_not_ready")
    result = {"version": "historical_daily_input_review/v1", "status": "REVIEW_REQUIRED",
            "development_only": True, "oos_queried": False, "candidate_eligibility_evaluated": False,
            "source_dataset_id": dataset.manifest["dataset_id"],
            "source_revision_ids_hash": dataset.manifest["revision_ids_hash"],
            "source_selection_sha256": plan["source_selection_sha256"],
            "cost_model": costs.to_dict(), "cost_validity_covers_windows": cost_coverage,
            "broker_costs_verified": False, "full_session_completeness_verified": False,
            "reasons": reasons, "partitions": partitions, "stock_days": rows,
            "summary": {"stock_day_count": len(rows),
                        "stock_days_with_unobserved_minutes": sum(row["unobserved_minute_slots"] > 0 for row in rows),
                        "internal_gap_count": sum(len(row["internal_gaps"]) for row in rows)},
            "limitations": ["missing_minutes_may_be_no_trades_or_source_omission",
                            "duplicate_revisions_count_as_one_minute", "auction_bars_excluded_from_continuous_slots",
                            "cost_source_text_is_not_broker_cost_evidence", "eligibility_final_and_shadow_not_assessed"]}
    if cost_reference is not None:
        result["cost_reference"] = assess_development_cost_reference(plan, dataset.manifest, costs, cost_reference)
        if result["cost_reference"]["status"] == "MISMATCH":
            result["reasons"].append("model_costs_differ_from_date_reference")
    if gap_provenance is not None:
        result["gap_provenance"] = audit_development_gap_provenance(dataset, result, gap_provenance, checkpoint=checkpoint)
    return result


def daily_input_review_text(review: Mapping[str, Any]) -> str:
    costs, summary = review["cost_model"], review["summary"]
    lines = ["일별 개발 비용·입력 점검 · final 준비 검토 필요",
             f"최소 연속 봉 충족: {sum(p['readiness']['status'] == 'READY' for p in review['partitions'])}/{len(review['partitions'])}일",
             f"수수료(매수·매도 각각): {costs['commission_bps']}bp / 매도세: {costs['sell_tax_bps']}bp / 슬리피지: {costs['slippage_bps']}bp",
             f"비용 출처: {costs['source']}", f"비용 근거 종류: {costs['rate_basis']}",
             f"비용 유효기간: {costs['valid_from']} ~ {costs['valid_to']}",
             "개발 기간 포함: " + ("예" if review["cost_validity_covers_windows"] else "아니오"),
             "실제 브로커 비용: 미검증 / 종일 입력 완전성: 미검증",
             f"종목·일 {summary['stock_day_count']}건 / 연속장 미관측 분이 있는 종목·일 {summary['stock_days_with_unobserved_minutes']}건 / 내부 공백 {summary['internal_gap_count']}건",
             "공백은 무거래 또는 공급자 누락 여부 미확인입니다. 종목을 자동 제외하지 않습니다.",
             "적격 기준·final·동일 설정 Shadow는 별도 확인이 필요합니다.", ""]
    if "cost_reference" in review:
        reference = review["cost_reference"]
        lines.extend(["당시 비용 참고값 대조: " + ("일치" if reference["status"] == "MATCH" else "불일치"),
                      f"매도세 불일치 {reference['tax_mismatch_day_count']}일 / 수수료 참고값 일치: {'예' if reference['commission_matches'] else '아니오'}",
                      "참고값 일치는 실제 계좌 비용 확인이나 기존 성과 재계산이 아닙니다."])
        for row in reference["dates"]:
            if not row["matches"]:
                lines.append(f"{row['outcome_date']}: 모델 매도세 {row['model_sell_tax_bps']}bp / 당시 참고값 {row['reference_sell_tax_bps']}bp")
        lines.append("")
    if "gap_provenance" in review:
        counts = review["gap_provenance"]["counts"]
        mismatches = sum(row["status"] in {"RAW_HASH_MISMATCH", "CURRENT_RAW_INPUT_MISMATCH"}
                         for row in review["gap_provenance"]["stock_days"])
        lines.extend([f"긴 내부 공백 {counts['long_gap_count']}구간 / 과거 감사 구간 일치 {counts['archived_gap_match_count']}구간 / 현재 원응답 바이트 대조 {counts['current_raw_input_match_count']}종목·일",
                      f"원응답 미확인 {counts['raw_unavailable_stock_days']}종목·일 / 불일치 {mismatches}종목·일 · 과거 기록의 일치는 현재 원본 검증을 대신하지 않습니다.", ""])
    for row in review["stock_days"]:
        if row["unobserved_minute_slots"]:
            lines.append(f"{row['outcome_date']} {row['code']}: 관측 {row['observed_unique_minutes']}/{row['expected_minute_slots']}분, 내부 공백 {len(row['internal_gaps'])}건")
    return "\n".join(lines)


def development_review_text(review: Mapping[str, Any]) -> str:
    lines = ["개발 비교 · 자동매매 적격 판정은 수행하지 않음",
             "잠정 전략 조건·개발용 비용이며 최종 후보 선택 근거로 승인되지 않았습니다.",
             "거래일 수는 완료 거래가 있는 날이며 입력의 전체 거래일 수가 아닙니다.",
             "현재 구현 식별자: " + review["current_code_hash"][:12], ""]
    labels = {"breakout": "돌파", "pullback": "눌림"}
    states = {"COMPLETE": "완료", "INCOMPARABLE": "조건이 달라 통합 비교 불가",
              "ELIGIBLE": "개발 기준 충족", "INVALID": "근거 유효성 확인 필요",
              "INELIGIBLE": "개발 기준 미충족", "NO_CLOSED_TRADE": "완료 거래 없음",
              "INSUFFICIENT_SAMPLE": "표본 부족"}
    for family, comparison in review["comparisons"].items():
        lines.append(f"{labels[family]} 개발 분할 대조: {states.get(comparison['status'], comparison['status'])}")
    censored_runs = sum(row["position_observations"]["status"] == "OPEN_POSITION_CENSORED" for row in review["runs"])
    if censored_runs:
        lines.append(f"종료 미청산 포지션이 있는 실행 {censored_runs}개 · 보유종목 관측·청산 검열을 아래에서 확인하세요.")
    for reason in review["paired_input_reasons"]:
        lines.append("같은 입력·조건 대조 불일치: " + reason)
    for row in review["runs"]:
        parameters = row["parameters"]
        costs = row["cost_model"] or {}
        parameter_labels = {"lookback_bars": "기준 봉 수", "buffer_bps": "돌파 여유(bp)",
                            "minimum_pullback_bps": "최소 눌림(bp)", "minimum_reacceleration_bps": "최소 재가속(bp)",
                            "stop_loss_bps": "손절(bp)", "target_bps": "익절(bp)", "max_hold_minutes": "최대 보유(분)",
                            "quantity": "주문 수량(주)", "capital_won": "전략 자금(원)",
                            "signal_valid_seconds": "신호 유효(초)", "cooldown_seconds": "재진입 대기(초)"}
        conditions = " · ".join(f"{label} {parameters[key]}" for key, label in parameter_labels.items() if key in parameters)
        quality = row["data_quality"] or {}
        lines.extend(["", f"{row['role']} · {labels[row['family']]} · {states.get(row['status'], row['status'])}",
                      f"기간: {row['start']} ~ {row['end']} (끝 시각 제외)",
                      f"완료 거래 {row['closed_trade_count']}건 · 거래일 {row['active_day_count']}일 · 순손익 {row['net_pnl_won']}원",
                      f"최대 낙폭 {row['max_drawdown_won']}원 · 현재 코드 {'일치' if row['current_code_matches'] else '불일치: 현 구현 재검증 필요'}",
                      "조건: " + conditions,
                      f"순위 지속성: {'사용' if parameters.get('rank_persistence_enabled') else '미사용'}",
                      f"비용: 양쪽 수수료 {costs.get('commission_bps', '미확인')}bp · 매도세 {costs.get('sell_tax_bps', '미확인')}bp · 슬리피지 {costs.get('slippage_bps', '미확인')}bp",
                      f"비용 유효기간: {costs.get('valid_from', '미확인')} ~ {costs.get('valid_to', '미확인')} · 개발용, 브로커 미검증",
                      f"자료 품질 검사: {'통과' if quality.get('status') == 'PASS' else quality.get('status', '미확인')} · 종일 완전성·대표성은 별도 확인",
                      f"평가 기간 안의 검열 주문: {row['censored_order_count']}",
                      "미거래 사유: " + json.dumps(row["no_trade_reasons"], ensure_ascii=False, sort_keys=True),
                      "실행 참조: " + row["run_id"]])
        position = row["position_observations"]
        if position["status"] == "OPEN_POSITION_CENSORED":
            reason_labels = {"research_interval_ended_before_fill": "평가 종료까지 체결 근거 없음",
                             "session_boundary_before_fill": "체결 전 세션 변경",
                             "next_tradable_bar_gap": "필요한 다음 연속 봉 없음",
                             "minute_bar_phase_cannot_prove_next_open_fill": "해당 장 단계에서 체결 순서 확인 불가"}
            reasons = " · ".join(f"{reason_labels.get(key, key)} {count}건"
                                  for key, count in position["exit_censored_reasons"].items()) or "없음"
            lines.extend([f"종료 미청산: {position['symbol']} {position['quantity']}주 · 진입 {position['entry_at']}",
                          f"보유 종목 마지막 관측(실행 원장): {position['last_source_observation_at'] or '미확인'}",
                          f"청산 요청 {position['exit_request_count']}건 · 청산 검열: {reasons}",
                          "계속 보유·단일 포지션 평가의 제약입니다. 원본 후속 봉·독립 평가기간을 확인해야 합니다."])
        elif position["status"] == "ENTRY_REFERENCE_MISSING":
            lines.append("종료 미청산의 진입 참조 누락 · 포지션 근거 확인 필요")
        limitation_labels = {"partial_fill_model_unsupported": "부분체결 재현 미지원",
                             "vi_orderability_not_exported": "VI 주문 가능성 자료 없음",
                             "posthoc_candidate_population_not_contemporaneous_top20": "사후 선정 후보군이며 당시 TOP20 아님",
                             "historical_bar_close_replay_clock_with_source_availability_preserved": "복원 봉 종료시각 사용·원 수집시각 보존"}
        lines.append("보고서 제약: " + ", ".join(limitation_labels.get(value, value) for value in row["limitations"]))
        for dimension, key, count, pnl in row["strata"]:
            if dimension == "by_date":
                lines.append(f"  {key}: 완료 {count}건 · 순손익 {pnl}원")
        for reason in (*row["reasons"], row["excluded_reason"]):
            if reason:
                lines.append("개발 검증·제외 근거: " + reason)
    lines.extend(["", "다음 확인: 자료 대표성·공백, 현 코드 재검증, 실제 비용 근거, 전략 조건 및 적격 기준 4개 확정.",
                  "그 뒤 final 후보·기간을 동결합니다. 이 조회는 기준 동결·OOS 접근·NAS 게시·주문을 실행하지 않습니다."])
    return "\n".join(lines)


def inspect_final_preparation(
    development_root: Path, *, family: str = "breakout", strategy_ref: str = "",
    minimum_closed_trades: int | None = None, minimum_active_days: int | None = None,
    minimum_net_realized_pnl_won: int | None = None, maximum_drawdown_ppm: int | None = None,
    final_start: str | None = None, final_end: str | None = None,
    request_path: Path | None = None,
    cost_reference: Path | None = None, gap_provenance: Path | None = None,
    checkpoint: Callable[[], None] = lambda: None,
    now_provider: Callable[[], datetime] = kst_now,
) -> dict[str, Any]:
    """동결 전 선택 사항을 점검한다. OOS·DB·최종 실행/기준 파일에는 접근하지 않는다."""
    checkpoint()
    if family not in {"breakout", "pullback"}:
        raise ValueError("지원하지 않는 개발 전략입니다.")
    root = Path(development_root).resolve()
    plan, references = _daily_development_requests(root, include_raw=True)
    _, document, encoded = next(row for row in references if row[0] == family)
    batch = development_validation_request_from_document(document, root / "requests", encoded=encoded)
    request = batch.request
    implementation_hash = research_implementation_hash(request.session_profile)
    candidate = final_candidate_spec_document(request, implementation_hash)
    candidate_hash = final_candidate_spec_hash(request, implementation_hash)
    if request_path is not None:
        fixed = _request(request_path)
        if final_candidate_spec_hash(fixed, implementation_hash) != candidate_hash:
            raise ValueError("고정 연구 요청과 개발 후보의 전략·비용·세션 설정이 다릅니다.")
    # 기존 계약으로 값만 검사한다. 이 임시 객체의 시각·ID를 동결 근거로 반환하지 않는다.
    criteria = dict(minimum_closed_trades=minimum_closed_trades, minimum_active_days=minimum_active_days,
                    minimum_net_realized_pnl_won=minimum_net_realized_pnl_won, maximum_drawdown_ppm=maximum_drawdown_ppm)
    validation = MockAutomationEligibilityPolicy(strategy_ref="preparation-validation-only",
        candidate_spec_hash=candidate_hash, frozen_at=as_kst(now_provider()), **criteria)
    if not isinstance(strategy_ref, str):
        raise ValueError("전략 이름은 문자열이어야 합니다.")
    def moment(value):
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError("final 기간은 시간대가 있는 ISO 시각이어야 합니다.")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("final 기간에는 시간대가 필요합니다.")
        return parsed.astimezone(UTC)
    start, end = moment(final_start), moment(final_end)
    if start is not None and end is not None and start >= end:
        raise ValueError("final 시작은 끝 시각보다 빨라야 합니다.")
    folds = plan["evaluation"]["folds"]
    development_end = max(datetime.fromisoformat(fold["end"]).astimezone(UTC) for fold in folds)
    # 현 작업은 개발 뒤의 시간순 final 준비다. 재사용·겹침은 자료 읽기 전에 거절한다.
    if ((start is not None and start < development_end)
            or (end is not None and end <= development_end)):
        raise ValueError("final 기간은 모든 고정 개발 기간 이후여야 합니다.")
    checkpoint()
    checkpoint()
    batches = [batch if name == family else development_validation_request_from_document(
        document, root / "requests", encoded=encoded) for name, document, encoded in references]
    inputs = _inspect_daily_development_inputs(plan, batches, cost_reference=cost_reference,
        gap_provenance=gap_provenance, checkpoint=checkpoint)
    reasons = [f"TBD:{name}" for name in validation.tbd_fields]
    if not strategy_ref.strip():
        reasons.append("TBD:strategy_ref")
    for name, value in (("final_start", start), ("final_end", end)):
        if value is None:
            reasons.append(f"TBD:{name}")
    costs = request.execution.cost_model
    if start is not None and end is not None:
        if not (datetime.fromisoformat(costs.valid_from) <= start < end <= datetime.fromisoformat(costs.valid_to)):
            reasons.append("COST_VALIDITY_OUTSIDE_FINAL_WINDOW")
    if family != "breakout":
        reasons.append("MOCK_SHADOW_FAMILY_NOT_SUPPORTED")
    reasons.extend(["ACTUAL_BROKER_COSTS_UNVERIFIED", "FULL_SESSION_COMPLETENESS_UNVERIFIED",
                    "FINAL_INPUT_AND_UNUSED_WINDOW_UNCHECKED", "DEVELOPMENT_PERFORMANCE_UNACCEPTED",
                    "SAME_SETTINGS_SHADOW_UNCHECKED"])
    report = {"version": "mock_candidate_final_preparation/v1", "status": "REVIEW_REQUIRED",
            "strategy_ref": strategy_ref.strip() or None, "family": family,
            "candidate_spec": candidate, "candidate_spec_hash": candidate_hash,
            "criteria": criteria, "criteria_frozen": False,
            "final_window": {"start": start.isoformat() if start else None,
                             "end": end.isoformat() if end else None, "end_exclusive": True,
                             "after_development_checked": start is not None and end is not None},
            "development_end": development_end.isoformat(), "input_review": inputs,
            "reasons": reasons, "final_request_created": False, "oos_queried": False,
            "candidate_eligibility_evaluated": False, "orders_started": False,
            "limitations": ["preview_is_not_a_frozen_policy_or_final_admission",
                            "final_candidate_identity_excludes_dates_and_source_paths",
                            "final_batch_must_separately_bind_input_hash_window_and_unused_ledger"]}
    if request_path is not None:
        report["fixed_request"] = {"path": str(Path(request_path).resolve()), "candidate_spec_matches": True,
                                   "source_and_period_admitted": False}
    return report


def final_preparation_text(report: dict[str, Any]) -> str:
    labels = {"minimum_closed_trades": "완료 거래 최소(건)", "minimum_active_days": "운용일 최소(일)",
              "minimum_net_realized_pnl_won": "순실현손익 최소(원)", "maximum_drawdown_ppm": "낙폭 최대(ppm)"}
    window, inputs = report["final_window"], report["input_review"]
    parameters = report["candidate_spec"]["parameters"]
    costs = report["candidate_spec"]["execution_model"]["cost_model"]
    leader = report["family"] == "leader_breakout/v1"
    contract = parameters.get("execution_contract") or {}
    lines = ["후보·final 동결 준비 · 확인 필요", f"전략: {report['strategy_ref'] or '미정'} / {report['family']}",
             f"후보 설정 hash: {report['candidate_spec_hash']}",
             ("실시간 0B / 확정·보완 분봉 연구 · 명시 구조/고정 하드 경계" if leader else
              f"보유 {parameters['max_hold_minutes']}분 / 손절 {parameters['stop_loss_bps']}bp / 익절 {parameters['target_bps']}bp"),
             (f"명시 수량 {contract.get('quantity')}주 / 전략 자금 {contract.get('capital_won')}원" if leader else
              f"수량 {parameters['quantity']}주 / 전략 자금 {parameters['capital_won']}원"),
             (f"시간 정책: {contract.get('session_exit_policy')} · 현재 근거 별도 검증" if leader else
              "시간 정책: " + (f"정규장 단일가 이전 청산 / 여유 {parameters['session_exit_buffer_seconds']}초"
                if parameters.get("session_exit_policy") == "krx_regular_before_auction/v1" else "기본 정책")),
             f"비용: 수수료 각 {costs['commission_bps']}bp / 매도세 {costs['sell_tax_bps']}bp / 슬리피지 {costs['slippage_bps']}bp",
             "", "적격 기준 · 아직 동결하지 않음"]
    lines.insert(3, "고정 연구 요청 설정: " + ("후보와 일치 · 자료·기간 승인은 별도" if "fixed_request" in report else "별도 대조 필요"))
    lines.extend(f"{labels[name]}: {report['criteria'][name] if report['criteria'][name] is not None else '미정'}"
                 for name in CRITERIA_NAMES)
    lines.extend(["", f"개발 기간 끝: {report['development_end']}",
                  f"final 시작: {window['start'] or '미정'} / 끝(제외): {window['end'] or '미정'}",
                  "개발 이후 범위: " + ("시각 대조 완료" if window['after_development_checked'] else "미확정"),
                  "final 자료·미사용 원장·개발 성과 승인·같은 설정 Shadow는 별도 확인이 필요합니다.",
                  "실제 계좌 비용·종일 입력 완전성: 미검증"])
    if "cost_reference" in inputs:
        lines.append(f"개발 기간 매도세 참고값 불일치: {inputs['cost_reference']['tax_mismatch_day_count']}일")
    if "gap_provenance" in inputs:
        counts = inputs["gap_provenance"]["counts"]
        lines.append(f"긴 공백 {counts['long_gap_count']}구간 / 과거 감사 일치 {counts['archived_gap_match_count']}구간 / 현재 원응답 대조 {counts['current_raw_input_match_count']}종목·일")
    reason_labels = {"COST_VALIDITY_OUTSIDE_FINAL_WINDOW": "비용 유효기간이 final 범위를 포함하지 않음",
                     "MOCK_SHADOW_FAMILY_NOT_SUPPORTED": "현재 모의 Shadow는 돌파 전략만 지원"}
    lines.extend(reason_labels[reason] for reason in report["reasons"] if reason in reason_labels)
    lines.extend(["", "준비 점검은 기준 동결·final 실행·후보 적격 판정이 아닙니다."])
    return "\n".join(lines)
