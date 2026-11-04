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
from kiwoom_monitor.application.research_families import BREAKOUT_FAMILY_ID as BREAKOUT_FAMILY, PULLBACK_FAMILY_ID as PULLBACK_FAMILY
from kiwoom_monitor.infrastructure.research_data_source import load_daily_development_source


from kiwoom_monitor.research_candidate import (
    EXPECTED, inspect_development_baselines, _read_review_object,
    position_observation_summary, _daily_development_requests,
    assess_development_cost_reference, inspect_daily_development_inputs,
    daily_input_review_text, development_review_text,
)









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
