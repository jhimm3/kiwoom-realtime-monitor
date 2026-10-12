"""고정 역사 개발 결과를 요약하고 native 원장의 손실·미청산 근거를 조회한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository
from kiwoom_monitor.application.research_implementation import research_implementation_hash
from kiwoom_monitor.application.market_session_schedule import research_session_profile_contract_matches
from kiwoom_monitor.application.market_session_schedule import KST, krx_regular_session_hours
from kiwoom_monitor.application.research_evaluation import _closed_trades
from kiwoom_monitor.application.research_execution import _slipped_price
from kiwoom_monitor.application.research_factors import compute_rolling_high_breakout, RollingHighBreakoutParameters
from kiwoom_monitor.application.research_replay import replay_krx_minute_bars
from kiwoom_monitor.application.research_splits import ResearchEvaluationSpec
from kiwoom_monitor.application.research_splits import DevelopmentPartitionSpec
from kiwoom_monitor.infrastructure.research_data_source import prepare_development_partition
from kiwoom_monitor.infrastructure.historical_research_readiness import assess_historical_development_readiness
from kiwoom_monitor.research_process import load_development_validation_request
from scripts.prepare_historical_baseline_requests import BREAKOUT_FAMILY, PULLBACK_FAMILY, load_daily_development_source


EXPECTED = (
    ("TRAIN", "breakout", "train-breakout.json"),
    ("TRAIN", "pullback", "train-pullback.json"),
    ("VALIDATION", "breakout", "validation-breakout.json"),
    ("VALIDATION", "pullback", "validation-pullback.json"),
)


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


def _read_review_object(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        encoded = stream.read(4 * 1024 * 1024 + 1)
    if len(encoded) > 4 * 1024 * 1024:
        raise ValueError("개발 결과 파일 크기 상한을 초과했습니다.")
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise ValueError("개발 결과는 JSON 객체여야 합니다.")
    return value


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


def execution_loss_summary(events, decisions, *, attribute_prices: bool = False) -> dict[str, Any]:
    """닫힌 거래의 기존 FIFO 계산을 재사용한다. 미청산·비용 없는 재실행을 만들지 않는다."""
    def at(value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("손실 원장 시각에는 시간대가 필요합니다.")
        return parsed.astimezone(timezone.utc)
    decision_index = {}
    for decision in decisions:
        key = decision["decision_id"]
        if key in decision_index:
            raise ValueError("판단 참조가 중복됩니다.")
        decision_index[key] = decision
    fills = {}
    for event in events:
        if event["event_type"] not in {"FILL", "RISK_FILL", "PARTIAL_FILL"}:
            continue
        key = event["symbol"], at(event["occurred_at"]), event["side"]
        if key in fills:
            raise ValueError("같은 시각의 체결 참조가 모호합니다.")
        fills[key] = event
    rows = []
    for trade in _closed_trades(tuple(events)):
        buy = fills[trade["symbol"], at(trade["opened_at"]), "BUY"]
        sell = fills[trade["symbol"], at(trade["closed_at"]), "SELL"]
        if buy["quantity"] != trade["quantity"] or sell["quantity"] != trade["quantity"]:
            raise ValueError("부분 체결 비용 배분은 이 조회에서 지원하지 않습니다.")
        entry = decision_index.get(buy["decision_id"])
        exit_decision = decision_index.get(sell["decision_id"])
        if (entry is None or entry["symbol"] != trade["symbol"] or entry["final_action"] != "ENTER"
                or sell["event_type"] != "RISK_FILL" and (exit_decision is None
                    or exit_decision["symbol"] != trade["symbol"] or exit_decision["final_action"] != "EXIT")):
            raise ValueError("진입·청산 판단 참조를 확인할 수 없습니다.")
        commission = buy["commission_won"] + sell["commission_won"]
        tax = buy["tax_won"] + sell["tax_won"]
        gross = sell["notional_won"] - buy["notional_won"]
        if (any(event[key] < 0 for event in (buy, sell) for key in ("commission_won", "tax_won"))
                or any(event["commission_won"] + event["tax_won"] != event["total_cost_won"] for event in (buy, sell))
                or commission + tax != trade["total_cost_won"]
                or gross - commission - tax != trade["net_pnl_won"]):
            raise ValueError("체결 손익과 기존 비용 계산이 일치하지 않습니다.")
        reasons = [sell["reason"]] if sell["event_type"] == "RISK_FILL" else exit_decision["reasons"]
        if (not isinstance(reasons, (list, tuple)) or not reasons
                or not all(isinstance(reason, str) and reason for reason in reasons)
                or not isinstance(entry["reasons"], (list, tuple))):
            raise ValueError("청산 사유를 확인할 수 없습니다.")
        rows.append({**trade, "gross_at_fill_prices_won": gross, "commission_won": commission,
                     "tax_won": tax, "entry_reasons": list(entry["reasons"]), "exit_reasons": list(reasons),
                     "entry_decision_id": buy["decision_id"], "exit_decision_id": sell["decision_id"],
                     "buy_event_id": buy["event_id"], "sell_event_id": sell["event_id"]})
        if attribute_prices:
            def signal_notional(decision, fill):
                price, quantity = decision.get("signal_reference_price"), decision.get("quantity")
                if (type(price) is not int or price <= 0 or type(quantity) is not int
                        or quantity != trade["quantity"] or at(decision["decided_at"]) > at(fill["occurred_at"])):
                    raise ValueError("판단 가격·수량·시각을 체결과 대조할 수 없습니다.")
                return price * quantity
            for fill in (buy, sell):
                if (type(fill.get("price")) is not int or fill["price"] <= 0
                        or fill["price"] * fill["quantity"] != fill["notional_won"]):
                    raise ValueError("체결 가격·수량·금액이 일치하지 않습니다.")
            entry_notional = signal_notional(entry, buy)
            entry_effect = entry_notional - buy["notional_won"]
            # 위험 청산에는 EXIT 판단이 없다. 청산 신호 가격을 만들어내지 않는다.
            exit_notional = None if sell["event_type"] == "RISK_FILL" else signal_notional(exit_decision, sell)
            attribution = {
                "status": "EXIT_SIGNAL_UNAVAILABLE" if exit_notional is None else "COMPLETE",
                "entry_signal_notional_won": entry_notional,
                "exit_signal_notional_won": exit_notional,
                "entry_signal_to_fill_won": entry_effect,
                "signal_price_move_won": None if exit_notional is None else exit_notional - entry_notional,
                "exit_signal_to_fill_won": None if exit_notional is None else sell["notional_won"] - exit_notional,
                "unattributed_after_entry_won": gross - entry_effect if exit_notional is None else 0,
            }
            if sum(attribution[key] or 0 for key in ("entry_signal_to_fill_won", "signal_price_move_won",
                    "exit_signal_to_fill_won", "unattributed_after_entry_won")) != gross:
                raise ValueError("판단·체결 가격 분해가 체결 손익과 일치하지 않습니다.")
            rows[-1]["price_attribution"] = attribution
    return {"closed_trade_count": len(rows), "net_pnl_won": sum(r["net_pnl_won"] for r in rows),
            "gross_at_fill_prices_won": sum(r["gross_at_fill_prices_won"] for r in rows),
            "commission_won": sum(r["commission_won"] for r in rows), "tax_won": sum(r["tax_won"] for r in rows),
            "slippage_already_in_fill_prices": True, "trades": rows}


def _daily_development_requests(root: Path):
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
        request = _read_review_object(root / "requests" / f"daily-{family}.json")
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
        references.append((family, request))
    if any(references[0][1].get(k) != references[1][1].get(k) for k in ("dataset_id", "dataset_hash")):
        raise ValueError("두 전략의 개발 입력 식별자가 다릅니다.")
    return plan, references


def inspect_daily_development_losses(root: Path, *, attribute_prices: bool = False,
                                     training_breakout_only: bool = False) -> dict[str, Any]:
    """고정 일별 계획·완료 개발 원장만 읽는다. 요청 검사 후에만 DB를 연다."""
    root = Path(root).resolve()
    plan, requests = _daily_development_requests(root)
    folds = plan["evaluation"]["folds"]
    if training_breakout_only and not any(fold["role"] == "TRAIN" for fold in folds):
        raise ValueError("TRAIN 돌파 개발 분할이 없습니다.")
    references = []
    for family, request in requests:
        result = _read_review_object(root / "results" / f"daily-{family}.json")
        steps = result.get("steps", [])
        if (result.get("kind") != "independent_development_validation" or result.get("batch_status") != "COMPLETED"
                or len(steps) != len(folds)
                or any(s["state"] not in {"COMPLETED", "CACHED"} or (s["fold_name"], s["role"]) != (f["name"], f["role"])
                       for s, f in zip(steps, folds, strict=True))
                or result.get("run_ids") != [s["run_id"] for s in steps]
                or len(set(result["run_ids"])) != len(steps)):
            raise ValueError("일별 개발 완료 참조를 확인할 수 없습니다.")
        references.append((family, request, steps))
    repository = ResearchRepository(root / "research.sqlite3", read_only=True)
    current_hash = research_implementation_hash("krx-regular/v1")
    records = []
    for family, request, steps in references:
        if training_breakout_only and family != "breakout":
            continue
        selected = [(step, fold) for step, fold in zip(steps, folds, strict=True)
                    if not training_breakout_only or fold["role"] == "TRAIN"]
        steps, selected_folds = zip(*selected, strict=True)
        comparison = repository.load_independent_development_comparison(tuple(s["run_id"] for s in steps)).to_dict()
        for step, partition, fold in zip(steps, comparison["partitions"], selected_folds, strict=True):
            run = repository.load_run(step["run_id"])
            spec = run["spec"] if run else {}
            if (not run or run["status"] != "completed" or partition["status"] not in {
                    "ELIGIBLE", "INELIGIBLE", "INSUFFICIENT_SAMPLE", "NO_CLOSED_TRADE"}
                    or spec.get("execution_scope") != "independent_development_validation/v1"
                    or spec.get("parameters") != request["request"]["strategy"]
                    or spec.get("execution_model") != request["request"]["execution"]
                    or spec.get("family") != request["request"]["family"]
                    or spec.get("evaluation_spec") != {**plan["evaluation"], "folds": [fold]}
                    or not research_session_profile_contract_matches(spec.get("session_profile"), "krx-regular/v1")):
                raise ValueError("일별 native 원장과 요청의 조건이 일치하지 않습니다.")
            events = repository.load_execution_events(step["run_id"])
            decisions = repository.load_evaluations(step["run_id"])
            loss = execution_loss_summary(events, decisions, attribute_prices=attribute_prices)
            if (loss["closed_trade_count"] != partition["closed_trade_count"]
                    or loss["closed_trade_count"] and loss["net_pnl_won"] != partition["net_pnl_won"]):
                raise ValueError("손실 분해와 native 개발 비교의 거래·손익이 일치하지 않습니다.")
            position = position_observation_summary(events)
            if position["status"] == "OPEN_POSITION_CENSORED":
                opened = datetime.fromisoformat(position["entry_at"]).astimezone(KST)
                _, close = krx_regular_session_hours(opened.date())
                auction_start = datetime.combine(opened.date(), close, KST) - timedelta(minutes=10)
                due = opened + timedelta(minutes=spec["parameters"]["max_hold_minutes"])
                position.update(max_hold_due_at=due.isoformat(), continuous_session_end=auction_start.isoformat(),
                                max_hold_due_in_or_after_auction=due >= auction_start)
            records.append({"family": family, "fold_name": step["fold_name"], "role": step["role"],
                            "run_id": step["run_id"], "code_hash": spec["code_hash"],
                            "current_code_matches": spec["code_hash"] == current_hash,
                            "partition": partition, "loss": loss, "position_observations": position})
    for index in range(0 if training_breakout_only else len(folds)):
        a, b = records[index], records[index + len(folds)]
        if any(a["partition"].get(key) != b["partition"].get(key)
               for key in ("input_dataset_id", "input_revision_ids_hash", "start", "end")):
            raise ValueError("두 전략의 실제 일별 입력·기간이 다릅니다.")
    return {"version": "historical_daily_loss_review/v1", "development_only": True, "oos_queried": False,
            "candidate_eligibility_evaluated": False, "current_code_hash": current_hash, "runs": records,
            "cost_status": "development_model_not_broker_verified",
            "limitations": ["closed_trade_amount_sums_not_continuous_account_returns",
                            "slippage_in_fill_prices_not_a_cost_free_rerun", "full_session_completeness_unverified"]}


def _loss_attribution_totals(trades) -> dict[str, Any]:
    rows = tuple(trades)
    amount_keys = ("gross_at_fill_prices_won", "commission_won", "tax_won", "net_pnl_won")
    components = ("entry_signal_to_fill_won", "signal_price_move_won",
                  "exit_signal_to_fill_won", "unattributed_after_entry_won")
    return {
        "closed_trade_count": len(rows),
        **{key: sum(row[key] for row in rows) for key in amount_keys},
        **{key: sum(row["price_attribution"][key] or 0 for row in rows) for key in components},
        "complete_price_attribution_count": sum(row["price_attribution"]["status"] == "COMPLETE" for row in rows),
        "exit_signal_unavailable_count": sum(row["price_attribution"]["status"] == "EXIT_SIGNAL_UNAVAILABLE" for row in rows),
        "gross_positive_trade_count": sum(row["gross_at_fill_prices_won"] > 0 for row in rows),
        "net_positive_trade_count": sum(row["net_pnl_won"] > 0 for row in rows),
        "gross_profit_erased_by_fees_count": sum(row["gross_at_fill_prices_won"] > 0 and row["net_pnl_won"] <= 0 for row in rows),
        "flat_gross_net_loss_count": sum(row["gross_at_fill_prices_won"] == 0 and row["net_pnl_won"] < 0 for row in rows),
    }


def _entry_time_bucket(trade) -> str:
    at = datetime.fromisoformat(trade["opened_at"]).astimezone(KST)
    minute = at.hour * 60 + at.minute
    for start, end, name in ((540, 600, "09:00-10:00"), (600, 720, "10:00-12:00"),
                             (720, 840, "12:00-14:00"), (840, 920, "14:00-15:20")):
        if start <= minute < end:
            return name
    return "outside_continuous_session"


def inspect_breakout_loss_attribution(root: Path) -> dict[str, Any]:
    """개발 원장의 가격·비용 항등식과 사전 고정 구간을 집계한다. 전략을 선택하지 않는다."""
    report = inspect_daily_development_losses(root, attribute_prices=True)
    runs = [run for run in report["runs"] if run["family"] == "breakout"]
    if any(not run["current_code_matches"] for run in runs):
        raise ValueError("현재 구현과 다른 돌파 원장은 현재 후보 진단에 사용할 수 없습니다.")
    roles = []
    for role in ("TRAIN", "VALIDATION"):
        selected = [run for run in runs if run["role"] == role]
        trades = [trade for run in selected for trade in run["loss"]["trades"]]
        groups = []
        for dimension, key_fn in (("exit_reasons", lambda trade: tuple(sorted(trade["exit_reasons"]))),
                                  ("entry_time", _entry_time_bucket)):
            buckets = {}
            for trade in trades:
                buckets.setdefault(key_fn(trade), []).append(trade)
            for key, rows in sorted(buckets.items()):
                groups.append({"dimension": dimension, "key": list(key) if isinstance(key, tuple) else key,
                               **_loss_attribution_totals(rows)})
        roles.append({"role": role, "run_ids": [run["run_id"] for run in selected],
                      "observed_day_count": len(selected),
                      "net_positive_day_count": sum(run["loss"]["net_pnl_won"] > 0 for run in selected),
                      "terminal_censored_day_count": sum(run["position_observations"]["status"] == "OPEN_POSITION_CENSORED" for run in selected),
                      "totals": _loss_attribution_totals(trades), "groups": groups})
    return {"version": "historical_breakout_loss_attribution/v1", "status": "DIAGNOSTIC_ONLY",
            "development_only": True, "oos_queried": False, "candidate_eligibility_evaluated": False,
            "strategy_changed": False, "current_code_hash": report["current_code_hash"],
            "cost_status": report["cost_status"], "roles": roles, "runs": runs,
            "limitations": [*report["limitations"], "signal_to_fill_includes_price_gap_and_modeled_slippage",
                            "risk_exit_signal_unavailable_not_zero_price_move",
                            "descriptive_groups_not_causal_or_out_of_sample_evidence",
                            "fixed_share_quantities_not_equal_notional_allocations"]}


def _training_trade_path(trade, events, decisions, bars, strategy, cost) -> dict[str, Any]:
    """실제 보유 안의 완료봉만 진단한다. 경로의 가격을 가상 청산 손익으로 쓰지 않는다."""
    def at(value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("경로 진단 시각에는 시간대가 필요합니다.")
        return parsed.astimezone(timezone.utc)
    buy, sell = (events[trade[key]] for key in ("buy_event_id", "sell_event_id"))
    entry = decisions[trade["entry_decision_id"]]
    opened, closed, decided = at(trade["opened_at"]), at(trade["closed_at"]), at(entry["decided_at"])
    same_symbol = [bar for bar in bars if bar.code == trade["symbol"]]
    signals = [bar for bar in same_symbol if at(bar.available_at) == decided]
    if len(signals) != 1 or signals[0].close != entry["signal_reference_price"]:
        raise ValueError("동결 분봉과 진입 판단 가격 참조가 일치하지 않습니다.")
    signal = signals[0]
    factor = compute_rolling_high_breakout(same_symbol, signal,
                RollingHighBreakoutParameters(strategy.lookback_bars, strategy.buffer_bps))
    if factor.status != "valid" or not factor.value["breakout"]:
        raise ValueError("기존 돌파 Factor로 진입 근거를 재현할 수 없습니다.")
    refs = {bar.revision_id: bar for bar in same_symbol}
    if len(refs) != len(same_symbol):
        raise ValueError("동결 분봉 참조가 중복됩니다.")
    entry_bar, exit_bar = (refs.get(event["source_revision_id"]) for event in (buy, sell))
    risk = sell["event_type"] == "RISK_FILL"
    if (entry_bar is None or exit_bar is None or at(entry_bar.bar_start) != opened
            or at(exit_bar.bar_end if risk else exit_bar.bar_start) != closed
            or _slipped_price(entry_bar.open, "BUY", cost.slippage_bps) != buy["price"]
            or not risk and _slipped_price(exit_bar.open, "SELL", cost.slippage_bps) != sell["price"]):
        raise ValueError("동결 분봉·비용 모델과 체결 참조가 일치하지 않습니다.")
    observed = [bar for bar in same_symbol if at(bar.bar_start) >= opened
                and at(bar.bar_end) <= closed and at(bar.available_at) <= closed
                and (not risk or at(bar.bar_end) < closed)]
    observed.sort(key=lambda bar: at(bar.bar_end))
    if any(at(bar.bar_end) - at(bar.bar_start) != timedelta(minutes=1) for bar in observed):
        raise ValueError("보유 경로에는 완료 1분봉만 사용할 수 있습니다.")
    by_end = {at(bar.bar_end): bar for bar in observed}
    if len(by_end) != len(observed):
        raise ValueError("보유 경로의 같은 시각 분봉이 모호합니다.")
    expected = int((closed - opened).total_seconds() // 60) - int(risk)
    expected = max(0, expected)
    def bps(price):
        return (price - buy["price"]) * 10000 // buy["price"]
    horizons = []
    for minute in (1, 3, 5, 10):
        target = opened + timedelta(minutes=minute)
        bar = by_end.get(target)
        status = ("NOT_HELD_THAT_LONG" if target > closed else
                  "RISK_EXIT_BAR_EXCLUDED" if risk and target == closed else
                  "OBSERVED" if bar else "BAR_UNAVAILABLE")
        horizons.append({"minutes": minute, "status": status,
                         "close_delta_bps": bps(bar.close) if status == "OBSERVED" else None,
                         "revision_id": bar.revision_id if status == "OBSERVED" else None})
    peak = max((bar.close for bar in observed), default=None)
    last = observed[-1].close if observed else None
    quantity = trade["quantity"]
    exit_decision = decisions.get(trade["exit_decision_id"])
    raw_effects = {
        "entry_next_bar_gap_won": (entry["signal_reference_price"] - entry_bar.open) * quantity,
        "entry_modeled_slippage_won": (entry_bar.open - buy["price"]) * quantity,
        "exit_next_bar_gap_won": None if risk else (exit_bar.open - exit_decision["signal_reference_price"]) * quantity,
        "exit_modeled_slippage_won": None if risk else (sell["price"] - exit_bar.open) * quantity,
    }
    attribution = trade["price_attribution"]
    if (raw_effects["entry_next_bar_gap_won"] + raw_effects["entry_modeled_slippage_won"] != attribution["entry_signal_to_fill_won"]
            or not risk and raw_effects["exit_next_bar_gap_won"] + raw_effects["exit_modeled_slippage_won"] != attribution["exit_signal_to_fill_won"]):
        raise ValueError("다음 봉 이동·모델 슬리피지 분해가 원장과 일치하지 않습니다.")
    return {"symbol": trade["symbol"], "buy_event_id": trade["buy_event_id"], "sell_event_id": trade["sell_event_id"],
            "opened_at": trade["opened_at"], "closed_at": trade["closed_at"], "net_pnl_won": trade["net_pnl_won"],
            "exit_reasons": trade["exit_reasons"], "entry_decision_id": trade["entry_decision_id"],
            "entry_factor": factor.to_dict(), "raw_fill_effects": raw_effects,
            "path_status": "NO_FULL_BAR_OBSERVABLE" if expected == 0 else
                           "COMPLETE_OBSERVED_WINDOW" if len(observed) == expected else "GAPS_OR_LATE_AVAILABILITY",
            "expected_full_bar_count": expected, "observed_full_bar_count": len(observed),
            "risk_exit_bar_excluded": risk, "horizons": horizons,
            "best_observed_close_bps": bps(peak) if peak is not None else None,
            "last_observed_close_bps": bps(last) if last is not None else None,
            "peak_to_last_close_bps": (peak - last) * 10000 // buy["price"] if peak is not None else None,
            "positive_close_seen_net_loss": None if peak is None else peak > buy["price"] and trade["net_pnl_won"] < 0}


def inspect_training_breakout_paths(root: Path, *, checkpoint: Callable[[], None] = lambda: None) -> dict[str, Any]:
    """개발 전체 바이트 검증 후 TRAIN 돌파 native 원장·보유 경로만 분석한다."""
    root = Path(root).resolve()
    plan, requests = _daily_development_requests(root)
    batch = load_development_validation_request(root / "requests/daily-breakout.json")
    if batch.request.execution.cost_model is None:
        raise ValueError("TRAIN 경로 진단에는 기존 비용 모델이 필요합니다.")
    report = inspect_daily_development_losses(root, attribute_prices=True, training_breakout_only=True)
    if any(not run["current_code_matches"] for run in report["runs"]):
        raise ValueError("현재 구현과 다른 원장은 TRAIN 경로 진단에 사용할 수 없습니다.")
    dataset = load_daily_development_source(plan, Path(requests[0][1]["request"]["dataset"]), checkpoint=checkpoint)
    if (dataset.manifest["dataset_id"], dataset.manifest["revision_ids_hash"]) != (batch.dataset_id, batch.dataset_hash):
        raise ValueError("동결 개발 입력과 요청 식별자가 다릅니다.")
    evaluation = ResearchEvaluationSpec.from_dict(plan["evaluation"])
    repository = ResearchRepository(root / "research.sqlite3", read_only=True)
    rows = []
    for run in report["runs"]:
        checkpoint()
        projected = prepare_development_partition(dataset,
            DevelopmentPartitionSpec("development_partition/v2", run["fold_name"]), evaluation, checkpoint=checkpoint)
        if (projected.manifest["dataset_id"], projected.manifest["revision_ids_hash"]) != (
                run["partition"]["input_dataset_id"], run["partition"]["input_revision_ids_hash"]):
            raise ValueError("동결 TRAIN 입력과 native 분할 식별자가 다릅니다.")
        bars = replay_krx_minute_bars(projected.observations, session_profile="krx-regular/v1")
        events = {event["event_id"]: event for event in repository.load_execution_events(run["run_id"])}
        decisions = {decision["decision_id"]: decision for decision in repository.load_evaluations(run["run_id"])}
        for trade in run["loss"]["trades"]:
            checkpoint()
            rows.append({"run_id": run["run_id"], "fold_name": run["fold_name"],
                         **_training_trade_path(trade, events, decisions, bars, batch.request.strategy, batch.request.execution.cost_model)})
    horizon_summary = []
    for minute in (1, 3, 5, 10):
        samples = [next(point for point in row["horizons"] if point["minutes"] == minute) for row in rows]
        observed = [point["close_delta_bps"] for point in samples if point["status"] == "OBSERVED"]
        horizon_summary.append({"minutes": minute, "status_counts": dict(sorted(Counter(point["status"] for point in samples).items())),
                                "observed_median_close_delta_bps": median(observed) if observed else None,
                                "observed_positive_count": sum(value > 0 for value in observed)})
    strengths = []
    for lower, upper, label in ((0, 10, "0-10bp"), (10, 25, "10-25bp"), (25, 50, "25-50bp"), (50, None, "50bp+")):
        selected = [row for row in rows if row["entry_factor"]["value"]["distance_bps"] >= lower
                    and (upper is None or row["entry_factor"]["value"]["distance_bps"] < upper)]
        strengths.append({"bucket": label, "closed_trade_count": len(selected), "net_pnl_won": sum(row["net_pnl_won"] for row in selected)})
    return {"version": "historical_training_breakout_paths/v1", "status": "DIAGNOSTIC_ONLY", "training_only": True,
            "development_only": True, "oos_queried": False, "validation_performance_queried": False,
            "development_source_bytes_verified": True, "candidate_eligibility_evaluated": False, "strategy_changed": False,
            "current_code_hash": report["current_code_hash"], "source_dataset_id": dataset.manifest["dataset_id"],
            "source_revision_ids_hash": dataset.manifest["revision_ids_hash"],
            "observations_file_hash": dataset.manifest["observations_file_hash"],
            "strategy": batch.request.strategy.to_dict(), "cost_model": batch.request.execution.cost_model.to_dict(),
            "run_ids": [run["run_id"] for run in report["runs"]], "observed_day_count": len(report["runs"]),
            "closed_trade_count": len(rows), "net_pnl_won": sum(row["net_pnl_won"] for row in rows),
            "complete_observed_window_count": sum(row["path_status"] == "COMPLETE_OBSERVED_WINDOW" for row in rows),
            "positive_close_seen_net_loss_count": sum(row["positive_close_seen_net_loss"] is True for row in rows),
            "raw_fill_effect_totals": {key: sum(row["raw_fill_effects"][key] or 0 for row in rows) for key in (
                "entry_next_bar_gap_won", "entry_modeled_slippage_won", "exit_next_bar_gap_won", "exit_modeled_slippage_won")},
            "exit_raw_decomposition_count": sum(not row["risk_exit_bar_excluded"] for row in rows),
            "horizon_summary": horizon_summary, "strength_groups": strengths, "trades": rows,
            "limitations": [*report["limitations"], "source_integrity_reads_all_declared_development_bytes",
                "closed_trades_only_open_positions_excluded", "risk_exit_bar_intrabar_order_unknown_and_excluded",
                "horizon_samples_conditioned_on_actual_holding", "observed_close_not_an_executable_counterfactual_exit",
                "fixed_diagnostic_strength_buckets_not_selected_parameters"]}


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
    """동결 개발 자료의 비용 출처·연속장 봉 공백을 점검한다. 재생·DB 조회는 없다."""
    root = Path(root).resolve()
    checkpoint()
    plan, references = _daily_development_requests(root)
    batches = [load_development_validation_request(root / "requests" / f"daily-{family}.json")
               for family, _ in references]
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
        from scripts.audit_historical_monthly_gap_raw import audit_development_gap_provenance
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


def summarize(root: Path) -> Mapping[str, Any]:
    root = root.resolve()
    repository = ResearchRepository(root / "research.sqlite3")
    runs: list[dict[str, Any]] = []
    for role, family, filename in EXPECTED:
        result = _read_mapping(root / "results" / filename)
        if result.get("status") != "ok" or result.get("kind") != "single_run":
            raise ValueError(f"baseline result is incomplete: {filename}")
        report = result.get("report")
        if not isinstance(report, Mapping):
            raise ValueError(f"baseline report is missing: {filename}")
        folds = report.get("fold_reports")
        if not isinstance(folds, list) or len(folds) != 1 or folds[0].get("role") != role:
            raise ValueError(f"baseline result role mismatch: {filename}")
        fold = folds[0]
        run_id = str(result["run_id"])
        manifest = _read_mapping(Path(str(result["manifest"])))
        censored = Counter(
            str(event.get("reason", ""))
            for event in repository.load_execution_events(run_id)
            if event.get("state") == "CENSORED"
        )
        runs.append({
            "partition_role": role,
            "family": family,
            "run_id": run_id,
            "report_status": str(result["report_status"]),
            "data_quality": report.get("data_quality"),
            "decision_count": int(manifest.get("decision_count", 0)),
            "candidate_count": int(manifest.get("candidate_count", 0)),
            "submitted_order_count": int(fold.get("submitted_order_count", 0)),
            "filled_order_count": int(fold.get("filled_order_count", 0)),
            "censored_order_count": int(fold.get("censored_order_count", 0)),
            "run_censored_event_count": sum(censored.values()),
            "censored_reasons": dict(sorted(censored.items())),
            "closed_trade_count": int(fold.get("closed_trade_count", 0)),
            "active_day_count": int(fold.get("active_day_count", 0)),
            "net_realized_pnl_won": fold.get("net_realized_pnl_won"),
            "event_outcomes": fold.get("event_outcomes", []),
            "limitations": report.get("limitations", []),
        })
    return {
        "version": "historical_baseline_comparison/v1",
        "scope": "provisional structural TRAIN/VALIDATION baseline",
        "oos_included": False,
        "rank_persistence_enabled": False,
        "parameter_status": "provisional_not_user_approved",
        "cost_status": "development_model_not_broker_verified",
        "conclusion_policy": (
            "Do not select or reject a strategy from this sparse collection-in-progress sample."
        ),
        "runs": runs,
    }


def _read_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON cannot be read: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON must be an object: {path}")
    return value


def _markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Historical development baseline comparison",
        "",
        "This is a structural TRAIN/VALIDATION check on a sparse collection-in-progress sample. "
        "Parameters are provisional, costs are not broker verified, and OOS remains sealed.",
        "",
        "| Partition | Family | Status | Decisions | Candidates | Submitted | Filled | In-fold censored | Closed | Net PnL |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in summary["runs"]:
        lines.append(
            f"| {run['partition_role']} | {run['family']} | {run['report_status']} | "
            f"{run['decision_count']} | {run['candidate_count']} | "
            f"{run['submitted_order_count']} | {run['filled_order_count']} | "
            f"{run['censored_order_count']} | {run['closed_trade_count']} | "
            f"{run['net_realized_pnl_won']} |"
        )
    lines.extend([
        "", "The in-fold count uses the evaluation window. The reason counts below cover the whole run,",
        "including orders censored when the research interval ended.",
        "", "## Run-wide censored event reasons", "",
    ])
    for run in summary["runs"]:
        reasons = ", ".join(
            f"{reason or '(empty)'}={count}"
            for reason, count in run["censored_reasons"].items()
        ) or "none"
        lines.append(f"- {run['partition_role']} {run['family']}: {reasons}")
    lines.extend([
        "",
        "Do not use this sample to select or reject either strategy. Rebuild the split after "
        "historical collection coverage is representative, then rerun TRAIN and VALIDATION.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inspect-development", action="store_true", help="개발 원장만 읽기 전용 조회하고 JSON을 출력")
    mode.add_argument("--inspect-daily-losses", action="store_true", help="고정 일별 개발 원장의 진입·비용·청산·미청산을 읽기 전용 조회")
    mode.add_argument("--inspect-breakout-losses", action="store_true", help="고정 개발 돌파의 판단/체결 가격·비용과 청산/진입시간별 손실 기여를 읽기 전용 조회")
    mode.add_argument("--inspect-training-paths", action="store_true", help="TRAIN 돌파 원장과 동결 분봉의 보유 경로·돌파 강도·체결 차이를 읽기 전용 조회")
    mode.add_argument("--inspect-daily-inputs", action="store_true", help="동결 개발 자료의 비용 출처·연속장 봉 공백을 읽기 전용 점검")
    parser.add_argument("--cost-reference", type=Path, help="점검할 개발 입력 바이트에 묶인 날짜별 비용 참고 파일")
    parser.add_argument("--gap-provenance", type=Path, help="과거 원응답 감사 파일; 현재 원본 재검증과 구분")
    args = parser.parse_args()
    if (args.cost_reference is not None or args.gap_provenance is not None) and not args.inspect_daily_inputs:
        parser.error("비용·공백 참고 파일은 --inspect-daily-inputs에서만 사용합니다.")
    if args.inspect_daily_inputs:
        print(json.dumps(inspect_daily_development_inputs(args.root, cost_reference=args.cost_reference,
                         gap_provenance=args.gap_provenance), ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.inspect_daily_losses:
        print(json.dumps(inspect_daily_development_losses(args.root), ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.inspect_breakout_losses:
        print(json.dumps(inspect_breakout_loss_attribution(args.root), ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.inspect_training_paths:
        print(json.dumps(inspect_training_breakout_paths(args.root), ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.inspect_development:
        print(json.dumps(inspect_development_baselines(args.root), ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    summary = summarize(args.root)
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.root / "SUMMARY.md").write_text(_markdown(summary), encoding="utf-8")
    print(json.dumps({"status": "ok", "summary": str((args.root / 'summary.json').resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
