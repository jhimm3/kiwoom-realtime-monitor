"""Bind explicit Section 12 minute hypotheses to the existing paper run contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

from kiwoom_monitor.application.breakout_strategy import (
    CandidateEvent, FeatureSnapshot, StrategyEvaluation,
)
from kiwoom_monitor.application.market_session_schedule import KST
from kiwoom_monitor.domain.market_data_contract import ListingPhase
from kiwoom_monitor.domain.strategy_contract import StrategyDecision


CLOSE_FAMILY = "close_betting/v1"
IPO_CLOSE_FAMILY = "ipo_close/v1"
IPO_BREAKOUT_FAMILY = "ipo_breakout/v1"
PANIC_FAMILY = "panic_rebound/v1"
BREAKOUT_STUDY_FAMILY = "breakout_study/v1"
THEME_STUDY_FAMILY = "theme_leader_study/v1"
BREAKOUT_STUDY_FAMILIES = frozenset({BREAKOUT_STUDY_FAMILY, THEME_STUDY_FAMILY})
MINUTE_EXPERIMENT_LABELS = {
    CLOSE_FAMILY: "종가베팅 · 격리 연구",
    IPO_CLOSE_FAMILY: "신규주 종가베팅 · 격리 연구",
    IPO_BREAKOUT_FAMILY: "신규주 돌파 · 격리 연구",
    PANIC_FAMILY: "낙주 · 격리 연구",
    BREAKOUT_STUDY_FAMILY: "완료봉 돌파 기준선 · 격리 연구",
    THEME_STUDY_FAMILY: "테마 주도권 · 격리 연구",
}


@dataclass(frozen=True)
class MinuteExperiment:
    family: str
    capital_won: int
    quantity: int
    policy: Any
    news_proofs: tuple = ()
    theme_proofs: tuple = ()

    session_exit_policy = ""
    cooldown_seconds = 0
    rank_persistence_enabled = False

    @classmethod
    def from_document(cls, family: str, document: Mapping[str, Any]) -> MinuteExperiment:
        fields = {"policy", "capital_won"}
        if family == PANIC_FAMILY:
            fields |= {"quantity", "news_proofs"}
        if family == THEME_STUDY_FAMILY:
            fields.add("theme_proofs")
        if set(document) != fields or not isinstance(document["policy"], Mapping):
            raise ValueError("invalid minute experiment fields")
        capital = document["capital_won"]
        if type(capital) is not int or capital <= 0:
            raise ValueError("minute experiment capital_won must be a positive integer")
        policy_document = dict(document["policy"])
        if family == PANIC_FAMILY:
            from kiwoom_monitor.application.panic_rebound import PanicNewsProof, PanicReboundRules

            quantity = document["quantity"]
            if type(quantity) is not int or quantity <= 0 or not isinstance(document["news_proofs"], list):
                raise ValueError("panic research requires explicit quantity and news proofs")
            proofs = tuple(PanicNewsProof(**{
                **row,
                "effective_at": datetime.fromisoformat(row["effective_at"]) if row["effective_at"] is not None else None,
                "available_at": datetime.fromisoformat(row["available_at"]) if row["available_at"] is not None else None,
                "evidence_refs": tuple(row["evidence_refs"]),
            }) for row in document["news_proofs"])
            return cls(family, capital, quantity, PanicReboundRules(**policy_document), proofs)
        if family in BREAKOUT_STUDY_FAMILIES:
            from kiwoom_monitor.application.breakout_theme_research import BreakoutThemePolicy, ThemeRoleProof

            policy = BreakoutThemePolicy.from_document(policy_document)
            if policy.variant != ("TH" if family == THEME_STUDY_FAMILY else "B0"):
                raise ValueError("breakout study policy differs from the selected family")
            proofs = document.get("theme_proofs", [])
            if not isinstance(proofs, list):
                raise ValueError("theme_proofs must be an array")
            return cls(family, capital, policy.quantity, policy, theme_proofs=tuple(
                ThemeRoleProof.from_document(row) for row in proofs
            ))
        from kiwoom_monitor.application.close_and_ipo import ClosePolicy, IpoBreakoutPolicy

        policy_document["listing_phases"] = tuple(ListingPhase(value) for value in policy_document["listing_phases"])
        policy_type = IpoBreakoutPolicy if family == IPO_BREAKOUT_FAMILY else ClosePolicy
        policy = policy_type(**policy_document)
        variants = {CLOSE_FAMILY: "CLOSE", IPO_CLOSE_FAMILY: "IPO_CLOSE", IPO_BREAKOUT_FAMILY: "IPO_BREAKOUT"}
        if policy.variant != variants[family]:
            raise ValueError("minute experiment policy differs from the selected family")
        return cls(family, capital, policy.quantity, policy)

    def to_dict(self) -> dict[str, Any]:
        document = {"capital_won": self.capital_won, "policy": _wire_dataclass(self.policy)}
        if self.family == PANIC_FAMILY:
            document.update(quantity=self.quantity, news_proofs=[_wire_dataclass(row) for row in self.news_proofs])
        if self.family == THEME_STUDY_FAMILY:
            document["theme_proofs"] = [_wire_dataclass(row) for row in self.theme_proofs]
        return document


class MinuteExperimentRun:
    """Keep observed setups and actual paper fills together for one run."""

    def __init__(self, config: MinuteExperiment, references) -> None:
        self.config = config
        self.references = references
        self.setups: dict[str, Any] = {}
        self.armed: dict[str, Any] = {}
        self.reduced: dict[str, int] = {}

    def observe_fills(self, events, position) -> bool:
        closed = False
        for event in events:
            if event.event_type == "FILL" and event.side == "SELL":
                if position is None:
                    self.setups.pop(event.symbol, None)
                    self.reduced.pop(event.symbol, None)
                    closed = True
                else:
                    self.reduced[event.symbol] = self.reduced.get(event.symbol, 0) + event.quantity
        return closed

    def entry_failure_price(self, decision) -> int | None:
        if self.config.family in BREAKOUT_STUDY_FAMILIES and decision.final_action == "ENTER":
            return self.setups[decision.symbol].failure_price
        return None

    def evaluate(self, *, run_id, bar, bars, cursor, universe_ref, engine, just_closed) -> StrategyEvaluation:
        position = engine.portfolio.position
        pending = engine.portfolio.pending_order
        if position is not None and position.symbol != bar.code:
            position = None
        setup = self.setups.get(bar.code) if position is not None else None
        quantity = 0
        refs = ()
        if just_closed:
            action, reason = "WAIT", "position_closed_at_this_observation"
        elif pending is not None and pending.symbol == bar.code:
            action, reason = "WAIT", "order_still_pending"
        elif self.config.family == PANIC_FAMILY:
            from kiwoom_monitor.application.panic_rebound import evaluate_panic_rebound

            decision = evaluate_panic_rebound(
                evaluation_bar=bar, bar_history=bars, rules=self.config.policy,
                news_proofs=self.config.news_proofs,
                setup=setup if position is not None else self.armed.get(bar.code),
                position_opened_at=datetime.fromisoformat(position.opened_at) if position is not None else None,
            )
            action, reason, setup, refs = decision.action, decision.reason, decision.setup, decision.input_refs
            if position is None and setup is not None and action in {"ARM", "ENTER"}:
                self.armed[bar.code] = setup
            else:
                self.armed.pop(bar.code, None)
            quantity = self.config.quantity if action == "ENTER" else position.quantity if action == "EXIT" else 0
        elif self.config.family in BREAKOUT_STUDY_FAMILIES:
            from kiwoom_monitor.application.breakout_theme_research import BreakoutThemePosition, evaluate_breakout_theme

            holding = (BreakoutThemePosition(position.quantity, position.entry_price, datetime.fromisoformat(position.opened_at))
                       if position is not None else None)
            decision = evaluate_breakout_theme(self.config.policy, bar=bar, history=bars, proofs=self.config.theme_proofs,
                                               position=holding, setup=setup)
            action, reason, setup = decision.action, decision.reason, decision.setup
            quantity, refs = decision.quantity, decision.evidence_refs
        else:
            from kiwoom_monitor.application.close_and_ipo import CloseIpoPosition, evaluate_close_and_ipo

            holding = None
            if position is not None:
                holding = CloseIpoPosition(setup, position.quantity, position.entry_price, self.reduced.get(bar.code, 0))
                if setup.entry_date != datetime.fromisoformat(bar.bar_start).astimezone(KST).date():
                    bars = (*cursor.bars_for_date(bar.code, setup.entry_date), *bars)
            decision = evaluate_close_and_ipo(
                self.config.policy, bar=bar, history=bars, calendar=self.references.calendar,
                listings=self.references.listings, position=holding,
            )
            action, reason, setup = decision.action, decision.reason, decision.setup
            quantity, refs = decision.quantity, decision.evidence_refs
        if action == "ENTER":
            if engine.portfolio.position is None and pending is None:
                self.setups[bar.code] = setup
            else:
                action, reason, quantity = "WAIT", "paper_slot_in_use", 0
        elif position is not None and setup is not None:
            self.setups[bar.code] = setup
        return self._evaluation(run_id, bar, universe_ref, engine.strategy_state, action, reason, quantity, setup, refs)

    def _evaluation(self, run_id, bar, universe_ref, state, action, reason, quantity, setup, refs) -> StrategyEvaluation:
        at = datetime.fromisoformat(bar.available_at).astimezone(timezone.utc).isoformat()
        strategy_id, version = self.config.family.rsplit("/", 1)
        input_refs = tuple(dict.fromkeys(ref for ref in (bar.revision_id, universe_ref, *refs) if ref))
        snapshot_body = {
            "run_id": run_id, "decision_time": at, "input_cutoff": at, "symbol": bar.code,
            "universe_ref": universe_ref, "input_revision_refs": input_refs, "strategy_id": strategy_id,
            "strategy_version": version, "policy_version": self.config.family, "state_before": state.to_dict(),
            "feature_values": ({"factor_id": self.config.family, "status": action, "reason": reason,
                                "setup": _wire_dataclass(setup) if setup is not None else None},),
        }
        snapshot = FeatureSnapshot(_id("snapshot", snapshot_body), **snapshot_body)
        final_action = "NO_TRADE" if action in {"WAIT", "ARM"} else action
        body = dict(run_id=run_id, snapshot_id=snapshot.snapshot_id, symbol=bar.code, proposal=action,
                    final_action=final_action, quantity=quantity, signal_reference_price=bar.close,
                    required_capital_won=bar.close * quantity if action == "ENTER" else 0,
                    reasons=(reason,), constraints=(), state_before=state.to_dict(), state_after=state.to_dict(),
                    decided_at=at)
        decision = StrategyDecision(_id("decision", body), **body)
        candidate = None
        if final_action == "ENTER":
            fields = dict(run_id=run_id, dedup_key=decision.decision_id, symbol=bar.code,
                          strategy_id=strategy_id, strategy_version=version, setup=strategy_id,
                          reference_revision_id=bar.revision_id, transition="flat_to_candidate", quantity=quantity,
                          signal_reference_price=bar.close, available_at=at,
                          snapshot_id=snapshot.snapshot_id, decision_id=decision.decision_id)
            candidate = CandidateEvent(_id("candidate", fields), **fields)
        return StrategyEvaluation(snapshot, decision, candidate, state)


def _wire_dataclass(value) -> dict[str, Any]:
    def encode(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"unsupported research evidence: {type(item).__name__}")

    return json.loads(json.dumps(asdict(value), default=encode, allow_nan=False))


def _id(prefix: str, value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}_{hashlib.sha256(encoded.encode()).hexdigest()}"
