"""Prepare fixed TRAIN/VALIDATION requests for historical strategy baselines.

The generated requests never include an OOS partition.  They are intended to
confirm that the existing registered families can consume the independently
frozen historical development inputs before any parameter search is attempted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.infrastructure.historical_research_readiness import (
    assess_historical_development_readiness,
)
from kiwoom_monitor.infrastructure.research_data_source import FrozenResearchDataset, load_research_input
from kiwoom_monitor.application.market_session_schedule import (
    KST, krx_regular_session_hours, research_session_profile_contract_matches,
)
from kiwoom_monitor.application.breakout_strategy import REGULAR_SESSION_EXIT_POLICY, validate_session_exit_policy
from kiwoom_monitor.application.research_splits import (
    ResearchEvaluationSpec, DevelopmentPartitionSpec, PARTITION_STATE_POLICY,
)
from kiwoom_monitor.infrastructure.research_data_source import prepare_development_partition


BREAKOUT_FAMILY = "krx_bar_close_breakout/v1"
PULLBACK_FAMILY = "krx_pullback_reacceleration/v1"


def prepare_training_batch_request(expansion: Path, projection: Path, prior_selection: Path,
                                  baseline_request: Path, selected_dates: tuple[str, ...], source: Path, output: Path, *,
                                  memory_mb: int = 1024, max_seconds: int = 3600) -> Path:
    """확대 원장의 연속 2~20일을 기존 독립 일별 TRAIN runner에 연결한다."""
    from scripts.plan_historical_monthly_case_selection import expand_train
    from kiwoom_monitor.research_process import load_development_validation_request
    from kiwoom_monitor.application.research_implementation import research_implementation_hash

    if type(memory_mb) is not int or not 128 <= memory_mb <= 4096 or type(max_seconds) is not int or not 1 <= max_seconds <= 3600:
        raise ValueError("invalid training case resource budget")
    expansion_bytes, projection_bytes, prior_bytes = [Path(path).read_bytes() for path in (expansion, projection, prior_selection)]
    document = json.loads(expansion_bytes)
    expected = expand_train(json.loads(projection_bytes), hashlib.sha256(projection_bytes).hexdigest(),
                            json.loads(prior_bytes), hashlib.sha256(prior_bytes).hexdigest())
    if not isinstance(document, Mapping) or {key: value for key, value in document.items() if key != "generated_at"} != {
            key: value for key, value in expected.items() if key != "generated_at"}:
        raise ValueError("TRAIN expansion does not match its frozen source bindings")
    if (not isinstance(selected_dates, tuple) or not 2 <= len(selected_dates) <= 20
            or any(not isinstance(day, str) for day in selected_dates)):
        raise ValueError("training batch requires 2 to 20 included TRAIN dates")
    all_dates = [case["selection_date"] for case in document["cases"]]
    if selected_dates[0] not in all_dates:
        raise ValueError("selected dates are not included TRAIN expansion cases")
    offset = all_dates.index(selected_dates[0])
    if tuple(all_dates[offset:offset + len(selected_dates)]) != selected_dates:
        raise ValueError("selected dates must be contiguous included TRAIN expansion cases")
    cases = document["cases"][offset:offset + len(selected_dates)]
    baseline_bytes = Path(baseline_request).read_bytes()
    baseline = load_development_validation_request(baseline_request)
    canonical = baseline.to_dict()
    base = canonical["request"]
    if (baseline.version != "independent_development_validation/v1" or base["mode"] != "single_run"
            or base["family"] != BREAKOUT_FAMILY
            or base["session_profile"] != "krx-regular/v1"
            or base["strategy"].get("session_exit_policy") != REGULAR_SESSION_EXIT_POLICY
            or base["evaluation"].get("final_holdout_accessed_at")
            or base["evaluation"].get("final_holdout_access_reason")
            or any(fold["role"] == "OOS" for fold in base["evaluation"]["folds"])):
        raise ValueError("baseline must be an OOS-free fixed breakout session-exit development request")
    source, output = Path(source).resolve(), Path(output).resolve()
    protected = [source, Path(expansion).resolve().parent, Path(base["database"]).resolve().parent,
                 Path(base["dataset"]).resolve()]
    if any(output.is_relative_to(path) for path in protected):
        raise ValueError("training case results must be outside frozen inputs and existing baseline results")
    folds = []
    for case in cases:
        outcome = date.fromisoformat(case["outcome_date"])
        opened, closed = krx_regular_session_hours(outcome)
        folds.append({"name": f"train-{outcome.isoformat()}", "role": "TRAIN",
                      "start": datetime.combine(outcome, opened, KST).isoformat(),
                      "end": (datetime.combine(outcome, closed, KST) + timedelta(microseconds=1)).isoformat()})
    policy = {**base["evaluation"], "folds": folds}
    evaluation = ResearchEvaluationSpec.from_dict(policy)
    # The existing source guard also checks native bytes, actual dates and populations.
    dataset = load_daily_development_source({"selection": {"cases": cases}}, source)
    readiness_rows = []
    for case, fold in zip(cases, folds, strict=True):
        projected = prepare_development_partition(dataset, DevelopmentPartitionSpec("development_partition/v2", fold["name"]), evaluation)
        readiness = assess_historical_development_readiness(projected)
        if readiness.status != "READY" or readiness.candidate_code_count != case["candidate_codes_after_exclusion"]:
            raise ValueError("training case has incomplete minimum candidate coverage")
        readiness_rows.append({"fold_name": fold["name"], "readiness": readiness.to_dict()})
    request = {"version": "independent_development_validation/v1",
               "dataset_id": dataset.manifest["dataset_id"], "dataset_hash": dataset.manifest["revision_ids_hash"],
               "fold_names": [fold["name"] for fold in folds], "max_seconds": max_seconds,
               "request": {**base, "dataset": str(source), "database": str(output / "research.sqlite3"),
                           "runs_dir": str(output / "runs"), "evaluation": evaluation.to_dict(),
                           "resource_budget": {"memory_mb": memory_mb, "cpu_duty_percent": 50}}}
    bound = {"version": "historical_training_batch_preparation/v1",
             "expansion_plan_id": document["plan_id"], "expansion_sha256": hashlib.sha256(expansion_bytes).hexdigest(),
             "baseline_request_sha256": hashlib.sha256(baseline_bytes).hexdigest(), "cases": cases,
             "source_dataset_id": dataset.manifest["dataset_id"], "source_revision_ids_hash": dataset.manifest["revision_ids_hash"],
             "scientific_hash": research_implementation_hash("krx-regular/v1"),
             "readiness": readiness_rows, "training_only": True, "oos_included": False,
             "full_session_completeness_verified": False, "actual_account_cost_verified": False,
             "candidate_eligibility_evaluated": False, "research_executed": False,
             "request_sha256": hashlib.sha256((json.dumps(request, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()).hexdigest()}
    output.mkdir(parents=True, exist_ok=True)
    path = output / "train-breakout.json"
    for target, value in ((path, request), (output / "preparation.json", bound)):
        if target.exists() and _read_mapping(target, "training batch preparation") != json.loads(json.dumps(value)):
            raise ValueError("training case preparation already exists with different content")
    _write_immutable_json(output / "preparation.json", bound)
    _write_immutable_json(path, request)
    return path


def prepare_daily_plan(selection: Path, output: Path, *, regular_exit_buffer_seconds: int | None = None) -> Path:
    """사전에 정한 월별 결과일을 독립 평가 구간으로 고정한다. 입력/성과는 읽지 않는다."""
    if regular_exit_buffer_seconds is not None:
        validate_session_exit_policy(REGULAR_SESSION_EXIT_POLICY, regular_exit_buffer_seconds)
    document = _read_mapping(selection, "monthly selection")
    oos = document.get("oos")
    if (not isinstance(oos, Mapping)
            or document.get("version") != "historical_monthly_case_selection/v1"
            or document.get("selection_policy") != "first_candidate_date_per_calendar_month_before_outcomes"
            or oos.get("status") != "SEALED"
            or oos.get("results_included") is not False):
        raise ValueError("daily development requires the sealed calendar-first selection")
    cases = document.get("cases")
    if not isinstance(cases, list) or not 2 <= len(cases) <= 20:
        raise ValueError("daily development requires 2 to 20 declared cases")
    boundary = date.fromisoformat(document["oos"]["selection_date"])
    folds, previous, months = [], None, set()
    for case in cases:
        if not isinstance(case, Mapping) or case.get("role") not in {"TRAIN", "VALIDATION"}:
            raise ValueError("daily development permits TRAIN and VALIDATION only")
        selected, outcome = (date.fromisoformat(case[key]) for key in ("selection_date", "outcome_date"))
        count = case.get("candidate_codes_after_exclusion")
        if (selected.isoformat()[:7] in months
                or not selected < outcome < boundary or previous is not None and outcome <= previous
                or not isinstance(count, int) or isinstance(count, bool) or count <= 0):
            raise ValueError("daily cases must retain chronological pre-OOS dates and candidates")
        opened, closed = krx_regular_session_hours(outcome)
        folds.append({"name": f"{case['role'].lower()}-{outcome}", "role": case["role"],
                      "start": datetime.combine(outcome, opened, KST).isoformat(),
                      "end": (datetime.combine(outcome, closed, KST) + timedelta(microseconds=1)).isoformat()})
        previous = outcome
        months.add(selected.isoformat()[:7])
    evaluation = ResearchEvaluationSpec.from_dict({
        "version": "chronological_holdout/v1", "folds": folds,
        "warmup_seconds": 0, "gap_seconds": 0, "purge_seconds": 0,
        "minimum_closed_trades": 0, "minimum_active_days": 1,
    }).to_dict()
    body = {"version": "historical_daily_development_plan/v1",
            "source_selection_sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
            "selection": dict(document), "evaluation": evaluation,
            "state_policy": PARTITION_STATE_POLICY,
            "strategies": [{"family": family, "name": name, "strategy": strategy}
                           for family, name, strategy in _strategies(regular_exit_buffer_seconds)],
            "execution": _execution_model(),
            "oos_included": False, "candidate_eligibility_evaluated": False,
            "parameter_status": "provisional_not_user_approved",
            "cost_status": "development_model_not_broker_verified"}
    # The executable requests are published separately only after source validation.
    output.mkdir(parents=True, exist_ok=True)
    path = output / "daily-plan.json"
    _write_immutable_json(path, body)
    return path


def prepare_daily_requests(selection: Path, source: Path, output: Path, *,
                           memory_mb: int = 1024, max_seconds: int = 3600,
                           regular_exit_buffer_seconds: int | None = None) -> tuple[Path, ...]:
    """자료를 확인한 뒤 기존 순차 독립 개발 계약의 두 Family 요청을 만든다."""
    if not 128 <= memory_mb <= 4096 or not 1 <= max_seconds <= 3600:
        raise ValueError("invalid daily validation resource budget")
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source):
        raise ValueError("daily results must be outside the frozen source directory")
    plan_path = prepare_daily_plan(selection, output, regular_exit_buffer_seconds=regular_exit_buffer_seconds)
    plan = _read_mapping(plan_path, "daily development plan")
    dataset = load_daily_development_source(plan, source)
    manifest = dataset.manifest
    cases = plan["selection"]["cases"]
    evaluation = ResearchEvaluationSpec.from_dict(plan["evaluation"])
    readiness_rows = []
    for fold, case in zip(evaluation.folds, cases):
        projected = prepare_development_partition(dataset, DevelopmentPartitionSpec("development_partition/v2", fold.name), evaluation)
        readiness = assess_historical_development_readiness(projected)
        if readiness.status != "READY" or readiness.candidate_code_count != case["candidate_codes_after_exclusion"]:
            raise ValueError(f"daily partition is not ready: {fold.name}")
        readiness_rows.append({"fold_name": fold.name, "readiness": readiness.to_dict()})
    requests_dir = output / "requests"
    requests_dir.mkdir(exist_ok=True)
    documents = []
    for configuration in plan["strategies"]:
        family, name, strategy = (configuration[key] for key in ("family", "name", "strategy"))
        document = {"version": "independent_development_validation/v1",
                    "dataset_id": manifest["dataset_id"], "dataset_hash": manifest["revision_ids_hash"],
                    "fold_names": [fold.name for fold in evaluation.folds], "max_seconds": max_seconds,
                    "request": {"mode": "single_run", "family": family,
                                "dataset": str(source.resolve()), "database": str((output / "research.sqlite3").resolve()),
                                "runs_dir": str((output / "runs").resolve()), "session_profile": "krx-regular/v1",
                                "strategy": strategy, "execution": plan["execution"], "evaluation": evaluation.to_dict(),
                                "resource_budget": {"memory_mb": memory_mb, "cpu_duty_percent": 50}}}
        documents.append((requests_dir / f"daily-{name}.json", document))
    # Check every existing target before publishing any changed request.
    for path, document in documents:
        if path.exists() and _read_mapping(path, "daily request") != document:
            raise ValueError(f"request already exists with different content: {path}")
    _write_immutable_json(output / "daily-readiness.json", {"version": "historical_daily_readiness/v1",
                          "source_dataset_id": manifest["dataset_id"], "source_revision_ids_hash": manifest["revision_ids_hash"],
                          "source_selection_sha256": plan["source_selection_sha256"], "partitions": readiness_rows,
                          "full_session_completeness_verified": False, "oos_included": False})
    for path, document in documents:
        _write_immutable_json(path, document)
    return tuple(path for path, _ in documents)


def load_daily_development_source(plan: Mapping[str, Any], source: Path, *,
                                  checkpoint: Callable[[], None] = lambda: None) -> FrozenResearchDataset:
    """요청 생성과 읽기 전용 점검이 같은 개발 자료 범위 검사를 사용한다."""
    source = Path(source).resolve()
    checkpoint()
    cases = plan["selection"]["cases"]
    expected_dates = [case["selection_date"] for case in cases]
    manifest = _read_mapping(source / "manifest.json", "development-only source manifest")
    if (manifest.get("runtime_input_version") != "historical_reconstruction_strategy/v1"
            or any(key in manifest for key in ("bundle_version", "development_partition", "final_holdout_partition"))
            or manifest.get("selected_dates") != expected_dates
            or manifest.get("excluded_cases") != []
            or manifest.get("theme_snapshots_file")
            or not research_session_profile_contract_matches(manifest.get("research_session_profile"), "krx-regular/v1")
            or not manifest.get("dataset_id") or not manifest.get("revision_ids_hash")):
        raise ValueError("source must contain exactly the declared development cases; OOS/full/partition inputs are forbidden")
    dataset = load_research_input(source, session_profile="krx-regular/v1", checkpoint=checkpoint)
    if dataset.manifest != manifest:
        raise ValueError("development source manifest changed while loading")
    # Inspect actual case/date scope as well as the pre-load declaration.
    case_by_date = {case["selection_date"]: case for case in cases}
    for row in dataset.observations:
        checkpoint()
        payload = row.get("payload", {})
        if row.get("kind") == "historical_candidate_population":
            selected = payload.get("selection_date")
            if (selected not in case_by_date
                    or len(set(payload.get("codes", []))) != case_by_date[selected]["candidate_codes_after_exclusion"]
                    or set(payload.get("codes", [])) & set(case_by_date[selected].get("excluded_codes", []))):
                raise ValueError("source candidate population differs from the frozen daily plan")
        elif row.get("kind") == "minute_bar":
            selected = payload.get("historical_case_date")
            observed = datetime.fromisoformat(payload["bar_end"])
            if (observed.tzinfo is None or selected not in case_by_date
                    or observed.astimezone(KST).date().isoformat() != case_by_date[selected]["outcome_date"]):
                raise ValueError("source bar falls outside the declared development result day")
        else:
            raise ValueError("daily historical source contains an unsupported observation kind")
    return dataset


def prepare_requests(
    package: Path, output: Path, *, allow_partial: bool = False,
    memory_mb: int = 512,
) -> tuple[Path, ...]:
    if not 128 <= memory_mb <= 4096:
        raise ValueError("research memory budget must be between 128MB and 4096MB")
    package = package.resolve()
    output = output.resolve()
    manifest = _read_mapping(package / "manifest.json", "development package manifest")
    if manifest.get("version") != "historical_development_inputs/v1":
        raise ValueError("unsupported historical development package")
    if manifest.get("oos_included") is not False:
        raise ValueError("historical baseline requests require an OOS-free package")

    partitions = manifest.get("partitions")
    if not isinstance(partitions, list) or not partitions:
        raise ValueError("historical development package has no partitions")
    roles = {str(item.get("role", "")) for item in partitions if isinstance(item, Mapping)}
    if roles != {"TRAIN", "VALIDATION"}:
        raise ValueError("historical baseline requests allow TRAIN and VALIDATION only")

    requests_dir = output / "requests"
    requests_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for partition in partitions:
        if not isinstance(partition, Mapping):
            raise ValueError("historical development partition must be an object")
        role = str(partition["role"]).lower()
        dataset = package / str(partition["path"])
        evaluation = partition.get("evaluation")
        if not dataset.is_dir() or not isinstance(evaluation, Mapping):
            raise ValueError(f"historical {role} partition is incomplete")
        readiness = assess_historical_development_readiness(load_research_input(
            dataset, session_profile="krx-regular/v1",
        ))
        if readiness.status != "READY" and not allow_partial:
            raise ValueError(
                f"historical {role} partition is not ready: "
                f"bar coverage {readiness.codes_with_bars}/{readiness.candidate_code_count}, "
                f"continuous pairs {readiness.codes_with_continuous_minute_pair}/"
                f"{readiness.candidate_code_count}; use --allow-partial only for a structural check"
            )
        for family, name, strategy in _strategies():
            request = {
                "mode": "single_run",
                "family": family,
                "dataset": str(dataset),
                "database": str(output / "research.sqlite3"),
                "runs_dir": str(output / "runs"),
                "session_profile": "krx-regular/v1",
                "strategy": strategy,
                "execution": _execution_model(),
                "evaluation": dict(evaluation),
                "resource_budget": {"memory_mb": memory_mb, "cpu_duty_percent": 50},
                "historical_baseline_context": {
                    "version": "historical_baseline_context/v1",
                    "purpose": "structural_train_validation_comparison",
                    "partition_role": str(partition["role"]),
                    "source_package_id": str(manifest["package_id"]),
                    "split_plan_id": str(manifest["split_plan_id"]),
                    "oos_included": False,
                    "parameter_status": "provisional_not_user_approved",
                    "cost_status": "development_model_not_broker_verified",
                },
            }
            path = requests_dir / f"{role}-{name}.json"
            _write_immutable_json(path, request)
            written.append(path)
    return tuple(written)


def _strategies(regular_exit_buffer_seconds: int | None = None) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    common = {
        "strategy_version": "v1",
        "rank_factor_version": "v1",
        "rank_persistence_enabled": False,
        "rank_persistence_required": False,
        "rank_top_k": None,
        "rank_window_seconds": None,
        "rank_max_gap_seconds": None,
        "rank_min_residency_seconds": None,
        "stop_loss_bps": 300,
        "target_bps": 500,
        "max_hold_minutes": 10,
        "quantity": 1,
        "capital_won": 1_000_000,
        "signal_valid_seconds": 60,
        "cooldown_seconds": 30,
    }
    if regular_exit_buffer_seconds is not None:
        validate_session_exit_policy(REGULAR_SESSION_EXIT_POLICY, regular_exit_buffer_seconds)
        common.update(session_exit_policy=REGULAR_SESSION_EXIT_POLICY,
                      session_exit_buffer_seconds=regular_exit_buffer_seconds)
    return (
        (BREAKOUT_FAMILY, "breakout", {
            **common,
            "rolling_factor_version": "v1",
            "lookback_bars": 5,
            "buffer_bps": 0,
        }),
        (PULLBACK_FAMILY, "pullback", {
            **common,
            "pullback_factor_version": "v1",
            "lookback_bars": 3,
            "minimum_pullback_bps": 500,
            "minimum_reacceleration_bps": 100,
        }),
    )


def _execution_model() -> dict[str, Any]:
    return {
        "version": "next_tradable_bar_open/v1",
        "same_bar_path_version": "conservative_with_optimistic_bound/v1",
        "initial_cash_won": 1_000_000,
        "cost_model": {
            "version": "fixed_bps/v2",
            "commission_bps": "1.5",
            "sell_tax_bps": "20",
            "slippage_bps": "5",
            "rate_basis": "model_estimate",
            "source": "user-agreed fee 0.015% each side and sell tax 0.200%; slippage model estimate",
            "valid_from": "2024-01-01T00:00:00+09:00",
            "valid_to": "2027-01-01T00:00:00+09:00",
        },
    }


def _read_mapping(path: Path, label: str) -> Mapping[str, Any]:
    try:
        with path.open("rb") as stream:
            encoded = stream.read(4 * 1024 * 1024 + 1)
        if len(encoded) > 4 * 1024 * 1024:
            raise ValueError(f"{label} exceeds 4 MiB")
        value = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} cannot be read") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError(f"request already exists with different content: {path}")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != encoded:
                raise ValueError(f"request already exists with different content: {path}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare fixed baseline requests from an OOS-free historical package."
    )
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--package", type=Path)
    inputs.add_argument("--daily-selection", type=Path, help="기존 월별 선정 원장의 결과일을 독립 평가 구간으로 고정합니다.")
    inputs.add_argument("--train-expansion", type=Path, help="고정 TRAIN 확대 원장의 연속 2~20일을 기존 runner 요청으로 준비합니다.")
    parser.add_argument("--projection", type=Path)
    parser.add_argument("--prior-selection", type=Path)
    parser.add_argument("--baseline-request", type=Path)
    parser.add_argument("--selection-dates", help="쉼표로 구분한 연속 TRAIN 선택일 2~20개")
    parser.add_argument("--development-source", type=Path, help="선정된 개발 날짜만 포함한 원본 어댑터 입력. 생략하면 계획만 저장합니다.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--regular-exit-buffer-seconds", type=int,
                        help="명시적 정규장 단타 시간 정책: 청산 여유 60~3600초. 일별 개발 전용이며 생략 시 기존 정책 유지.")
    parser.add_argument(
        "--allow-partial", action="store_true",
        help="Allow an explicitly labelled structural run on incomplete candidate coverage.",
    )
    parser.add_argument("--memory-mb", type=int, default=512,
                        help="Per-process RSS guard for the structural run (128-4096 MB).")
    args = parser.parse_args()
    if args.train_expansion is not None:
        if (not all((args.projection, args.prior_selection, args.baseline_request, args.selection_dates, args.development_source))
                or args.allow_partial or args.regular_exit_buffer_seconds is not None):
            parser.error("TRAIN expansion requires projection, prior-selection, baseline-request, selection-dates and development-source; partial or changed exit policy is forbidden")
        dates = tuple(day.strip() for day in args.selection_dates.split(","))
        path = prepare_training_batch_request(args.train_expansion, args.projection, args.prior_selection,
                                             args.baseline_request, dates, args.development_source,
                                             args.output, memory_mb=args.memory_mb)
        print(json.dumps({"status": "ok", "request": str(path), "training_only": True, "oos_included": False}, ensure_ascii=False))
        return 0
    if any((args.projection, args.prior_selection, args.baseline_request, args.selection_dates)):
        parser.error("TRAIN expansion options require --train-expansion")
    if args.daily_selection is not None:
        if args.allow_partial:
            parser.error("daily validation does not allow partial candidate coverage")
        if args.development_source is None:
            path = prepare_daily_plan(args.daily_selection, args.output,
                                      regular_exit_buffer_seconds=args.regular_exit_buffer_seconds)
            print(json.dumps({"status": "source_required", "plan": str(path), "requests": [], "oos_included": False}, ensure_ascii=False))
        else:
            paths = prepare_daily_requests(args.daily_selection, args.development_source, args.output,
                                          memory_mb=args.memory_mb, regular_exit_buffer_seconds=args.regular_exit_buffer_seconds)
            print(json.dumps({"status": "ok", "requests": [str(path) for path in paths], "oos_included": False}, ensure_ascii=False))
        return 0
    if args.development_source is not None:
        parser.error("--development-source requires --daily-selection")
    if args.regular_exit_buffer_seconds is not None:
        parser.error("--regular-exit-buffer-seconds requires --daily-selection")
    paths = prepare_requests(args.package, args.output,
                             allow_partial=args.allow_partial, memory_mb=args.memory_mb)
    print(json.dumps({"status": "ok", "requests": [str(path) for path in paths]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
