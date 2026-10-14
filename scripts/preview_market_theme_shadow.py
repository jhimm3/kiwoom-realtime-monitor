"""Evaluate supplied H1/H2 input; optionally recover an explicit saved research thesis read-only."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiwoom_monitor.application.market_research_features import (
    MarketFeatureParameters, MarketMember, MarketResearchFrame, MatureBreakoutSummary,
)
from kiwoom_monitor.application.market_theme_shadow import (
    SHADOW_CONTEXT_VERSION, MarketThemeShadowInputs, MatureBreakoutEvidence,
    STOCK_THESIS_SHADOW_VERSION, StockThesisShadowRequest,
    evaluate_market_theme_shadow, evaluate_stock_thesis_shadow,
    stock_thesis_shadow_request_from_dict,
)
from kiwoom_monitor.application.theme_leadership import (
    EntryThesis, SecondTradePoint, ThemeLeadershipParameters, ThemeLeadershipState, ThemeMembership,
    UpperLimitReference,
)


def evaluate_document(document: dict[str, Any]) -> dict[str, Any]:
    schema = document.get("schema")
    from kiwoom_monitor.application.order_lifecycle import (
        UNFILLED_MANAGEMENT_SHADOW_VERSION,evaluate_unfilled_order_management_document,
    )
    if schema == UNFILLED_MANAGEMENT_SHADOW_VERSION:
        return evaluate_unfilled_order_management_document(document)
    from kiwoom_monitor.application.position_target import (
        POSITION_TARGET_SHADOW_VERSION, evaluate_position_target_document,
        POSITION_ACTION_COMPARISON_SHADOW_VERSION, evaluate_position_action_comparison_document,
        DYNAMIC_POSITION_ALLOCATION_SHADOW_VERSION, evaluate_dynamic_position_allocation_document,
    )
    if schema == POSITION_TARGET_SHADOW_VERSION:
        return evaluate_position_target_document(document)
    if schema == POSITION_ACTION_COMPARISON_SHADOW_VERSION:
        return evaluate_position_action_comparison_document(document)
    if schema == DYNAMIC_POSITION_ALLOCATION_SHADOW_VERSION:
        return evaluate_dynamic_position_allocation_document(document)
    if schema not in {SHADOW_CONTEXT_VERSION, STOCK_THESIS_SHADOW_VERSION}:
        raise ValueError("unsupported market/theme shadow input schema")
    allowed = {"schema", "inputs", "market_parameters", "leadership_parameters"}
    if schema == STOCK_THESIS_SHADOW_VERSION:
        allowed.add("stock_thesis")
    if set(document) - allowed:
        raise ValueError("unknown market/theme shadow document fields")
    raw = dict(document["inputs"])
    raw["market_frames"] = tuple(MarketResearchFrame(
        **{**frame, "members": tuple(MarketMember(**{**member, "themes": tuple(member.get("themes", ()))})
                                    for member in frame["members"])},
    ) for frame in raw["market_frames"])
    raw["second_points"] = tuple(SecondTradePoint(**point) for point in raw["second_points"])
    if raw["theme_membership"] is not None:
        raw["theme_membership"] = ThemeMembership(**{
            **raw["theme_membership"], "member_codes": tuple(raw["theme_membership"]["member_codes"]),
        })
    if raw.get("breakout_evidence") is not None:
        raw["breakout_evidence"] = MatureBreakoutEvidence(**{
            **raw["breakout_evidence"], "summary": MatureBreakoutSummary(**raw["breakout_evidence"]["summary"]),
        })
    if raw.get("previous_leadership_state") is not None:
        raw["previous_leadership_state"] = ThemeLeadershipState(**raw["previous_leadership_state"])
    raw["upper_limits"] = tuple(UpperLimitReference(**limit) for limit in raw.get("upper_limits", ()))
    inputs = MarketThemeShadowInputs(**raw)
    parameters = {
        "market_parameters": MarketFeatureParameters(**document.get("market_parameters", {})),
        "leadership_parameters": ThemeLeadershipParameters(**document.get("leadership_parameters", {})),
    }
    if schema == SHADOW_CONTEXT_VERSION:
        return evaluate_market_theme_shadow(inputs, **parameters)
    return evaluate_stock_thesis_shadow(
        inputs, stock_thesis_shadow_request_from_dict(document["stock_thesis"]), **parameters,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--research-db", type=Path)
    parser.add_argument("--research-run-id")
    parser.add_argument("--entry-thesis-id")
    parser.add_argument("--thesis-policy")
    parser.add_argument("--position-outcome-selection", type=Path)
    parser.add_argument("--position-outcome-role", choices=('TRAIN', 'VALIDATION'))
    args = parser.parse_args()
    try:
        document = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("shadow input must be a JSON object")
        source_receipt = None
        native_allocation_result = None
        if args.position_outcome_selection is not None:
            if (args.research_db is None or args.position_outcome_role is None
                    or any((args.research_run_id, args.entry_thesis_id, args.thesis_policy))
                    or document.get('schema') not in {'position-action-comparison-shadow/v1','dynamic-allocation-native-ev/v1','dynamic-allocation-native-exit/v1'}
                    or document.get('schema')=='position-action-comparison-shadow/v1' and document.get('outcomes') != []):
                raise ValueError('native outcomes require research DB, comparison input with empty outcomes, and no thesis selector')
            if document['schema']=='position-action-comparison-shadow/v1':
                evaluate_document(document)  # Validate the existing target contract before native evidence access.
            with args.position_outcome_selection.open('rb') as selection_file:
                selection_bytes = selection_file.read(256 * 1024 + 1)
            if len(selection_bytes) > 256 * 1024:
                raise ValueError('native position selection exceeds 256 KiB')
            from dataclasses import asdict
            from datetime import datetime
            from kiwoom_monitor.application.research_evaluation import read_position_action_outcomes
            from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
            if document['schema']=='dynamic-allocation-native-exit/v1':
                from kiwoom_monitor.application.research_evaluation import read_full_exit_stress_document
                native_allocation_result=read_full_exit_stress_document(document,
                    ResearchRepository(args.research_db,read_only=True).load_position_action_evidence,
                    json.loads(selection_bytes),development_role=args.position_outcome_role)
            elif document['schema']=='dynamic-allocation-native-ev/v1':
                from kiwoom_monitor.application.research_evaluation import read_dynamic_allocation_ev_document
                native_allocation_result=read_dynamic_allocation_ev_document(document,
                    ResearchRepository(args.research_db,read_only=True).load_position_action_evidence,
                    json.loads(selection_bytes),development_role=args.position_outcome_role)
            else:
                parameters, request = document['parameters'], document['requests'][0]
                outcomes, source_receipt = read_position_action_outcomes(
                    ResearchRepository(args.research_db, read_only=True).load_position_action_evidence,
                    json.loads(selection_bytes),
                    decision_time=datetime.fromisoformat(request['decision_time']),
                    horizon_seconds=parameters['horizon_seconds'], population_ref=parameters['population_ref'],
                    environment=request['environment'], symbol=request['symbol'], development_role=args.position_outcome_role)
                document = {**document, 'outcomes': [json.loads(json.dumps(asdict(outcome),
                    default=lambda value: value.isoformat())) for outcome in outcomes]}
        if args.position_outcome_role is not None and args.position_outcome_selection is None:
            raise ValueError('native position role requires its selection')
        selected = (args.research_db, args.research_run_id, args.entry_thesis_id, args.thesis_policy) if args.position_outcome_selection is None else ()
        if any(value is not None for value in selected):
            if not all(selected):
                raise ValueError("research DB, run ID, entry thesis ID and policy must all be explicit")
            if document.get("schema") != SHADOW_CONTEXT_VERSION or "stock_thesis" in document:
                raise ValueError("saved thesis selection requires a context document without an embedded thesis")
            from dataclasses import asdict
            from kiwoom_monitor.central_server.market_theme_shadow_inputs import read_persisted_stock_thesis_shadow_request
            from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
            request = read_persisted_stock_thesis_shadow_request(
                ResearchRepository(args.research_db, read_only=True), run_id=args.research_run_id,
                thesis_id=args.entry_thesis_id, thesis_policy_version=args.thesis_policy,
                leadership_venue=document["inputs"]["leadership_venue"], decision_time=document["inputs"]["decision_time"],
            )
            document = {**document, "schema": STOCK_THESIS_SHADOW_VERSION, "stock_thesis": asdict(request)}
        result = native_allocation_result if native_allocation_result is not None else evaluate_document(document)
        if source_receipt is not None:
            result = {**result, 'position_outcome_source': source_receipt}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        parser.exit(2, f"Invalid shadow input: {error}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
