"""Local CLI for pattern discovery and a separately authorized research holdout."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from kiwoom_monitor.application.research_discovery import DiscoveryRisk, DiscoveryStrategy, FEATURE_BINS, scan_patterns
from kiwoom_monitor.application.research_implementation import research_implementation_hash
from kiwoom_monitor.application.research_resources import ResearchResourceGuard, ResearchResourceLimits
from kiwoom_monitor.application.research_splits import DevelopmentPartitionSpec, FinalHoldoutBatchSpec, ResearchEvaluationSpec
from kiwoom_monitor.domain.research_contract import DISCOVERY_FAMILY
from kiwoom_monitor.domain.simulation_contract import SimulationCostModel, SimulationExecutionConfig, default_research_cost_model
from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.infrastructure.research_data_source import (
    load_research_input, prepare_development_partition, research_input_encoded_bytes, research_observation_order,
)
from kiwoom_monitor.research_candidate import _write_immutable
from kiwoom_monitor.research_process import (
    DevelopmentValidationRequest, FinalHoldoutExecutionRequest, ResearchProcessRequest,
    execute_development_validation, execute_final_holdout_request, final_candidate_spec_hash, _write_result,
)
from kiwoom_monitor.research_run import ResearchRunCancelled

PROFILE = "krx-regular/v1"
VERSION = "strategy_discovery/v1"


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _execution(document):
    cost = document["cost_model"]
    model = (default_research_cost_model(**cost) if set(cost) == {"valid_from", "valid_to"}
             else SimulationCostModel(**cost))
    return SimulationExecutionConfig(**{**document, "cost_model": model})


def _request(output, dataset, strategy, execution, evaluation, limits):
    return ResearchProcessRequest("single_run", DISCOVERY_FAMILY, dataset, output / "research.sqlite3", output / "runs",
                                  strategy, execution, evaluation, PROFILE, resource_limits=limits)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def discover(document, root: Path, *, cancel_path=None):
    dataset_path = (root / document["dataset"]).resolve()
    output = (root / document["output"]).resolve()
    if output == dataset_path or dataset_path in output.parents or output in dataset_path.parents:
        raise ValueError("discovery output and frozen source must be separate")
    evaluation = ResearchEvaluationSpec.from_dict(document["evaluation"])
    if ([fold.role for fold in evaluation.folds] != ["TRAIN", "VALIDATION"]
            or evaluation.final_holdout_accessed_at or evaluation.source_event_links):
        raise ValueError("discovery requires one TRAIN and one VALIDATION fold without final access")
    train, validation = evaluation.folds
    risk = DiscoveryRisk(**document["risk"])
    execution = _execution(document["execution"])
    cost = execution.cost_model
    if (datetime.fromisoformat(cost.valid_from) > datetime.fromisoformat(train.start)
            or datetime.fromisoformat(cost.valid_to) < datetime.fromisoformat(validation.end)
            or cost.rate_basis == "unspecified"):
        raise ValueError("explicit costs must cover both development folds")
    maximum, samples, seconds = (document[key] for key in ("max_candidates", "minimum_samples", "max_seconds"))
    if (any(type(value) is not int for value in (maximum, samples, seconds))
            or not 1 <= maximum <= 200 or samples < 1 or seconds < 1):
        raise ValueError("discovery requires explicit candidate, sample and time budgets")
    relative_shortlist = document.get("relative_shortlist", False)
    if type(relative_shortlist) is not bool:
        raise ValueError("relative_shortlist must be boolean")
    limits = ResearchResourceLimits(**document.get("resource_budget", {}))
    guard = ResearchResourceGuard(limits)
    deadline = time.monotonic() + seconds

    def checkpoint():
        if (cancel_path is not None and cancel_path.is_file()) or time.monotonic() >= deadline:
            raise ResearchRunCancelled("discovery stopped before completing development")
        if guard.checkpoint_due():
            guard.checkpoint()

    guard.preflight(research_input_encoded_bytes(dataset_path, development_only=True))
    dataset = load_research_input(dataset_path, session_profile=PROFILE, checkpoint=checkpoint, development_only=True)
    dev_end = datetime.fromisoformat(validation.end)
    if (datetime.fromisoformat(dataset.manifest["captured_range"]["end"]) > dev_end
            or any(research_observation_order(row)[0] >= dev_end for row in dataset.observations)):
        raise ValueError("discovery source contains observations beyond the declared development window")
    partition = DevelopmentPartitionSpec("development_partition/v2", train.name)
    train_input = prepare_development_partition(dataset, partition, evaluation, checkpoint=checkpoint)
    implementation = research_implementation_hash(PROFILE)
    scan_spec = {"family": DISCOVERY_FAMILY, "mode": "discovery_screen/v1", "code_hash": implementation,
                 "execution_scope": "independent_development_validation/v1", "execution_model": execution.to_dict(),
                 "risk": document["risk"], "max_candidates": maximum, "minimum_samples": samples,
                 "relative_shortlist": relative_shortlist,
                 "evaluation_spec": partition.evaluation_for(evaluation).to_dict()}
    scan_id = "run_" + _digest({"dataset_id": train_input.manifest["dataset_id"],
                               "revision_ids_hash": train_input.manifest["revision_ids_hash"], "spec": scan_spec})
    repo = ResearchRepository(output / "research.sqlite3")
    owner = uuid.uuid4().hex
    claim = repo.start_run(scan_id, scan_spec, train_input.manifest, claim_independent=True, owner_token=owner)
    screen_path = output / "screening.json"
    if claim == "completed":
        screen = _read(screen_path)
        if _digest(screen) != repo.load_run(scan_id)["logical_result_hash"]:
            raise ValueError("saved discovery screen differs from its research receipt")
    elif claim == "claimed":
        try:
            stats = scan_patterns(train_input.observations, fold=train, purge_seconds=evaluation.purge_seconds,
                risk=risk, cost=cost, minimum_samples=samples, max_candidates=maximum, checkpoint=checkpoint,
                relative_shortlist=relative_shortlist)
            screen = {"version": VERSION, "scan_id": scan_id, "feature_bins": FEATURE_BINS,
                      "label": "one-minute net proxy, TRAIN only", "relative_shortlist": relative_shortlist, **stats}
            _write_immutable(screen_path, screen)
            repo.finish_run(scan_id, _digest(screen))
        except (Exception, KeyboardInterrupt) as exc:
            if repo.load_run(scan_id)["status"] == "running":
                repo.cancel_run(scan_id, str(exc))
            raise
    else:
        raise ValueError(f"discovery screen is {claim}; inspect the existing local run")
    del train_input

    reviewed = []
    for candidate in screen["candidates"]:
        checkpoint()
        strategy = DiscoveryStrategy.from_dict(candidate["strategy"])
        request = _request(output, dataset_path, strategy, execution, evaluation, limits)
        batch = DevelopmentValidationRequest(request, dataset.manifest["dataset_id"],
            dataset.manifest["revision_ids_hash"], (train.name, validation.name), max(1, int(deadline - time.monotonic())))
        result = execute_development_validation(batch, cancel_path=cancel_path, owner_token=owner, loaded_dataset=dataset)
        if result["batch_status"] != "COMPLETED":
            _write_result(output / "development-progress.json", {"reviewed": reviewed, "pending": result})
            raise ValueError("precise backtests incomplete; review development-progress.json")
        folds = result["comparison"]["partitions"]
        selected = len(folds) == 2 and all(row["status"] == "ELIGIBLE" and row["net_pnl_won"] > 0 for row in folds)
        reviewed.append({**candidate, "candidate_id": _digest(candidate["strategy"]), "folds": folds,
                         "selected_for_final": selected})
    report = {"version": VERSION, "scan_id": scan_id, "implementation_hash": implementation,
              "development_end": dataset.manifest["captured_range"]["end"], "evaluation": evaluation.to_dict(),
              "execution": execution.to_dict(), "resource_budget": document.get("resource_budget", {}),
              "candidates": reviewed, "order_authorized": False}
    _write_immutable(output / "development.json", report)
    repo.save_search_experiment("discovery_result_" + scan_id, report)
    return {"screened": len(screen["candidates"]), "selected_for_final": sum(row["selected_for_final"] for row in reviewed),
            "report": str(output / "development.json")}


def evaluate_final(document, root: Path, *, cancel_path=None):
    output = (root / document["discovery_output"]).resolve()
    report = _read(output / "development.json")
    implementation = research_implementation_hash(PROFILE)
    if report["version"] != VERSION or report["implementation_hash"] != implementation:
        raise ValueError("discovery result requires its original scientific implementation")
    repo = ResearchRepository(output / "research.sqlite3", read_only=True)
    # An existing immutable repository record must match the review artifact.
    if repo.load_search_experiment("discovery_result_" + report["scan_id"]) != report:
        raise ValueError("discovery result does not match a saved research receipt")
    evaluation = ResearchEvaluationSpec.from_dict(document["evaluation"])
    dev = ResearchEvaluationSpec.from_dict(report["evaluation"])
    if len(evaluation.folds) != 1 or evaluation.folds[0].role != "OOS":
        raise ValueError("final evaluation requires one independent OOS fold")
    start = datetime.fromisoformat(evaluation.folds[0].start)
    if start < max(datetime.fromisoformat(report["development_end"]), datetime.fromisoformat(dev.folds[-1].end)) + timedelta(seconds=dev.gap_seconds):
        raise ValueError("final window must follow development and its configured gap")
    if (evaluation.minimum_closed_trades, evaluation.minimum_active_days) != (dev.minimum_closed_trades, dev.minimum_active_days):
        raise ValueError("final evaluation must preserve the declared minimum sample policy")
    dataset = (root / document["dataset"]).resolve()
    execution = _execution(report["execution"])
    limits = ResearchResourceLimits(**report["resource_budget"])
    candidates = tuple(_request(output, dataset, DiscoveryStrategy.from_dict(row["strategy"]), execution, evaluation, limits)
                       for row in report["candidates"] if row["selected_for_final"])
    if not candidates:
        raise ValueError("no discovery candidate met both development folds")
    hashes = tuple(sorted(final_candidate_spec_hash(request, implementation) for request in candidates))
    # Only the source identity is read before locking the candidates. The existing evaluator verifies the bytes.
    manifest = _read(dataset / "manifest.json")
    dataset_id = document.get("dataset_id", manifest.get("dataset_id", manifest.get("bundle_id")))
    dataset_hash = document.get("dataset_hash", manifest.get("revision_ids_hash"))
    batch = FinalHoldoutBatchSpec("final_holdout_batch/v1", dataset_id, dataset_hash, PROFILE, hashes, evaluation)
    request = FinalHoldoutExecutionRequest(batch, candidates, "discovery-" + batch.batch_id,
        document["accessed_at"], "discovery-" + report["scan_id"])
    _write_immutable(output / "final-request.json", request.to_dict())
    result = execute_final_holdout_request(request, cancel_path=cancel_path)
    _write_result(output / "final-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Discover KRX minute patterns from local frozen inputs")
    parser.add_argument("action", choices=("discover", "final"))
    parser.add_argument("request", type=Path)
    parser.add_argument("--cancel", type=Path)
    args = parser.parse_args(argv)
    try:
        result = (discover if args.action == "discover" else evaluate_final)(
            _read(args.request), args.request.resolve().parent, cancel_path=args.cancel)
    except (OSError, KeyError, TypeError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, ensure_ascii=False))
    return int(result.get("status", "ok") != "ok" or any(
        row["state"] not in ("COMPLETED", "CACHED") for row in result.get("candidates", ())))


if __name__ == "__main__":
    raise SystemExit(main())
