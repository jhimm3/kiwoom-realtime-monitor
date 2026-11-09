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
from kiwoom_monitor.application.market_session_schedule import KST, TradingDayEvidence, decode_strategy_references
from kiwoom_monitor.application.news_material import MaterialNews, decision_material_context
from kiwoom_monitor.domain.market_data_contract import ListingEvidence, ListingPhase
from kiwoom_monitor.domain.strategy_contract import MINUTE_SHADOW_VERSION, StrategyDecision


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
MINUTE_SHADOW_STRATEGY_IDS = frozenset(family.split("/", 1)[0] for family in MINUTE_EXPERIMENT_LABELS)


@dataclass(frozen=True)
class MinuteExperiment:
    family: str
    capital_won: int
    quantity: int
    policy: Any
    news_proofs: tuple = ()
    theme_proofs: tuple = ()
    breakout_entry_proofs: tuple = ()
    panic_allow_unresolved_news: bool = False
    material_news: MaterialNews | None = None
    panic_previous_session_news: int | str | None = None
    material_event_ref: str | None = None

    session_exit_policy = ""
    cooldown_seconds = 0
    rank_persistence_enabled = False

    @classmethod
    def from_document(
        cls, family: str, document: Mapping[str, Any], *, paper_request: bool = False,
    ) -> MinuteExperiment:
        fields = {"policy", "capital_won"}
        if paper_request and "material_news" in document:
            fields.add("material_news")
            if family == BREAKOUT_STUDY_FAMILY and "material_event_ref" in document:
                fields.add("material_event_ref")
        if family == PANIC_FAMILY:
            fields |= {"quantity", "news_proofs"}
            if paper_request and "panic_allow_unresolved_news" in document:
                fields.add("panic_allow_unresolved_news")
            if "material_news" in fields and "panic_previous_session_news" in document:
                fields.add("panic_previous_session_news")
        if family == THEME_STUDY_FAMILY:
            fields.add("theme_proofs")
        if family == BREAKOUT_STUDY_FAMILY and "entry_proofs" in document:
            fields.add("entry_proofs")
        if set(document) != fields or not isinstance(document["policy"], Mapping):
            raise ValueError("invalid minute experiment fields")
        capital = document["capital_won"]
        if type(capital) is not int or capital <= 0:
            raise ValueError("minute experiment capital_won must be a positive integer")
        policy_document = dict(document["policy"])
        material = MaterialNews.from_documents(document["material_news"]) if "material_news" in fields else None
        if family == PANIC_FAMILY:
            from kiwoom_monitor.application.panic_rebound import parse_panic_parameters

            if material is not None and document["news_proofs"] != []:
                raise ValueError("material news and legacy PANIC proofs are mutually exclusive")
            previous_session = document.get("panic_previous_session_news")
            if "panic_previous_session_news" in document and previous_session != "session" and (
                type(previous_session) is not int or previous_session <= 0
            ):
                raise ValueError("PANIC prior-session news requires positive minutes from the open or 'session'")
            parameters = {key: value for key, value in document.items()
                          if key not in {"material_news", "panic_previous_session_news"}}
            capital, quantity, rules, proofs, allow_unresolved = parse_panic_parameters(parameters, paper_request=paper_request)
            return cls(family, capital, quantity, rules, proofs,
                       panic_allow_unresolved_news=allow_unresolved, material_news=material,
                       panic_previous_session_news=previous_session)
        if family in BREAKOUT_STUDY_FAMILIES:
            from kiwoom_monitor.application.breakout_theme_research import BreakoutEntryProof, BreakoutThemePolicy, ThemeRoleProof

            policy = BreakoutThemePolicy.from_document(policy_document)
            if (policy.variant == "TH") != (family == THEME_STUDY_FAMILY):
                raise ValueError("breakout study policy differs from the selected family")
            proofs = document.get("theme_proofs", [])
            entry_proofs = document.get("entry_proofs", [])
            if not isinstance(proofs, list) or not isinstance(entry_proofs, list):
                raise ValueError("breakout proofs must be arrays")
            if entry_proofs and policy.variant not in {"B2", "B3"}:
                raise ValueError("entry proofs apply only to B2 and B3")
            parsed_entries = tuple(BreakoutEntryProof.from_document(row) for row in entry_proofs)
            selected = document.get("material_event_ref")
            if "material_event_ref" in document and (
                policy.variant != "B3" or not policy.entry_filters.require_catalyst
                or not isinstance(selected, str) or selected not in {chain[0].ref for chain in material.chains}
                or any(proof.kind == "catalyst" for proof in parsed_entries)
            ):
                raise ValueError("selected material requires B3 catalyst policy, a known event and no duplicate catalyst proof")
            return cls(family, capital, policy.quantity, policy, theme_proofs=tuple(
                ThemeRoleProof.from_document(row) for row in proofs
            ), breakout_entry_proofs=parsed_entries, material_news=material, material_event_ref=selected)
        from kiwoom_monitor.application.close_and_ipo import close_ipo_policy_from_document

        policy = close_ipo_policy_from_document(family, policy_document)
        return cls(family, capital, policy.quantity, policy, material_news=material)

    def to_dict(self) -> dict[str, Any]:
        document = {"capital_won": self.capital_won, "policy": _wire_dataclass(self.policy)}
        if self.family == PANIC_FAMILY:
            document.update(quantity=self.quantity, news_proofs=[_wire_dataclass(row) for row in self.news_proofs])
            if self.panic_allow_unresolved_news:
                document["panic_allow_unresolved_news"] = True
            if self.panic_previous_session_news is not None:
                document["panic_previous_session_news"] = self.panic_previous_session_news
        if self.material_news is not None:
            revisions = []
            for chain in self.material_news.chains:
                for row in chain:
                    revision = _wire_dataclass(row)
                    if row.correction is None:
                        revision.pop("correction")
                    revisions.append(revision)
            document["material_news"] = revisions
        if self.material_event_ref is not None:
            document["material_event_ref"] = self.material_event_ref
        if self.family == THEME_STUDY_FAMILY:
            document["theme_proofs"] = [_wire_dataclass(row) for row in self.theme_proofs]
        if self.breakout_entry_proofs:
            document["entry_proofs"] = [_wire_dataclass(row) for row in self.breakout_entry_proofs]
        return document

@dataclass(frozen=True)
class MinuteShadowConfig:
    strategy: MinuteExperiment
    signal_valid_seconds: int
    reference_json: str = ""
    calendar: tuple[TradingDayEvidence, ...] = ()
    listings: tuple[ListingEvidence, ...] = ()
    symbols: tuple[str, ...] = ()

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> MinuteShadowConfig:
        required = {"version", "strategy", "signal_valid_seconds"}
        if (not required <= set(document) <= required | {"family", "references", "symbols"}
                or document["version"] != MINUTE_SHADOW_VERSION
                or not isinstance(document["strategy"], Mapping)):
            raise ValueError("invalid Section 12 minute Shadow fields")
        validity = document["signal_valid_seconds"]
        if type(validity) is not int or validity <= 0:
            raise ValueError("Section 12 minute Shadow validity must be a positive integer")
        symbols = document.get("symbols", [])
        if (not isinstance(symbols, list) or ("symbols" in document and not symbols)
                or any(not isinstance(code, str) or len(code) != 6 or not code.isascii()
                       or not code.isalnum() or code != code.upper() for code in symbols)
                or len(set(symbols)) != len(symbols)):
            raise ValueError("Section 12 minute Shadow symbols must be distinct six-character stock codes")
        scope = tuple(sorted(symbols))
        family = document.get("family", BREAKOUT_STUDY_FAMILY)
        if not isinstance(family, str) or family not in MINUTE_EXPERIMENT_LABELS:
            raise ValueError("unsupported Section 12 minute Shadow family")
        try:
            strategy = MinuteExperiment.from_document(family, document["strategy"])
        except (KeyError, AttributeError) as error:
            raise ValueError("invalid Section 12 minute Shadow policy evidence") from error
        uses_calendar = family in {CLOSE_FAMILY, IPO_CLOSE_FAMILY, IPO_BREAKOUT_FAMILY}
        if uses_calendar != ("references" in document):
            raise ValueError("CLOSE and IPO require their versioned calendar/listing reference document")
        if uses_calendar:
            calendar, listings = decode_strategy_references(document["references"])
            return cls(strategy, validity, json.dumps(document["references"], sort_keys=True), calendar, listings, scope)
        return cls(strategy, validity, symbols=scope)

    def to_dict(self) -> dict[str, Any]:
        document = {"version": MINUTE_SHADOW_VERSION, "strategy": self.strategy.to_dict(),
                    "signal_valid_seconds": self.signal_valid_seconds}
        if self.strategy.family != BREAKOUT_STUDY_FAMILY:
            document["family"] = self.strategy.family
        if self.reference_json:
            document["references"] = json.loads(self.reference_json)
        if self.symbols:
            document["symbols"] = list(self.symbols)
        return document


class MinuteExperimentRun:
    """Keep observed setups and actual paper fills together for one run."""

    def __init__(self, config: MinuteExperiment, references, *, reconstructed_market_values=None,
                 research_context: bool = False) -> None:
        self.config = config
        self.references = references
        self.reconstructed_market_values = reconstructed_market_values
        self.research_context = research_context
        self.setups: dict[str, Any] = {}
        self.armed: dict[str, Any] = {}
        self.reduced: dict[str, int] = {}
        self._entry_cuts: dict[str, datetime] = {}
        self._panic_calendar_day = None
        self._panic_prior_session = None

    def observe_fills(self, events, position) -> bool:
        closed = False
        for event in events:
            if event.event_type == "FILL" and event.side == "SELL":
                if position is None:
                    self.setups.pop(event.symbol, None)
                    self.reduced.pop(event.symbol, None)
                    self._entry_cuts.pop(event.symbol, None)
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
        cut = datetime.fromisoformat(bar.available_at)
        material = self.config.material_news if self.research_context else None
        quantity = 0
        refs = ()
        if just_closed:
            action, reason = "WAIT", "position_closed_at_this_observation"
        elif pending is not None and pending.symbol == bar.code:
            action, reason = "WAIT", "order_still_pending"
        elif self.config.family == PANIC_FAMILY:
            from kiwoom_monitor.application.panic_rebound import (
                evaluate_panic_rebound, material_panic_proofs, panic_prior_session,
            )

            previous_session = None
            if (position is None and self.research_context and self.references is not None
                    and self.config.panic_previous_session_news is not None):
                day = cut.astimezone(KST).date()
                if day != self._panic_calendar_day:
                    self._panic_prior_session = panic_prior_session(
                        self.references.calendar, cut, self.config.panic_previous_session_news,
                    )
                    self._panic_calendar_day = day
                previous_session = self._panic_prior_session

            decision = evaluate_panic_rebound(
                evaluation_bar=bar, bar_history=bars, rules=self.config.policy,
                news_proofs=(material_panic_proofs(self.config.material_news, bar.code, cut, prior_session=previous_session)
                             if self.research_context and self.config.material_news is not None else self.config.news_proofs),
                setup=setup if position is not None else self.armed.get(bar.code),
                position_opened_at=datetime.fromisoformat(position.opened_at) if position is not None else None,
                allow_unresolved_news=self.research_context and self.config.panic_allow_unresolved_news,
                prior_session=previous_session,
            )
            action, reason, setup, refs = decision.action, decision.reason, decision.setup, decision.input_refs
            if position is None and setup is not None and action in {"ARM", "ENTER"}:
                self.armed[bar.code] = setup
            else:
                self.armed.pop(bar.code, None)
            quantity = self.config.quantity if action == "ENTER" else position.quantity if action == "EXIT" else 0
        elif self.config.family in BREAKOUT_STUDY_FAMILIES:
            from kiwoom_monitor.application.breakout_theme_research import (
                BreakoutEntryProof, BreakoutThemePosition, evaluate_breakout_theme,
            )

            holding = (BreakoutThemePosition(position.quantity, position.entry_price, datetime.fromisoformat(position.opened_at))
                       if position is not None else None)
            def entry_proofs():
                yield from self.config.breakout_entry_proofs
                if material is None or self.config.material_event_ref is None:
                    return
                for root, revision, effect, visible in material.as_of(bar.code, cut):
                    if root == self.config.material_event_ref:
                        yield BreakoutEntryProof(
                            "catalyst", bar.code, revision.effective_at, revision.available_at,
                            revision.source_ref, revision.ref, tuple(row.ref for row in visible),
                            "reconstructed_as_of",
                            {"INTACT": "supported", "DAMAGED": "unsupported",
                             "FUNDAMENTAL_ADVERSE": "unsupported"}.get(effect),
                        )
                        return

            decision = evaluate_breakout_theme(self.config.policy, bar=bar, history=bars, proofs=self.config.theme_proofs,
                                               entry_proofs=entry_proofs(), position=holding, setup=setup,
                                               reconstructed_market_proxy=self.reconstructed_market_values.get(
                                                   datetime.fromisoformat(bar.bar_end),
                                               ) if self.reconstructed_market_values is not None else None,
                                               research_context=self.research_context)
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
                if material is not None:
                    self._entry_cuts[bar.code] = cut
            else:
                action, reason, quantity = "WAIT", "paper_slot_in_use", 0
        elif position is not None and setup is not None:
            self.setups[bar.code] = setup
        material_context = None
        if material is not None and (action == "ENTER" or position is not None or reason.startswith("breakout_catalyst_")):
            material_context = decision_material_context(
                material, bar.code, cut, entry_cut=self._entry_cuts.get(bar.code) if position is not None else None,
            )
        return build_minute_evaluation(self.config.family, run_id, bar, universe_ref,
                                       engine.strategy_state, action, reason, quantity, setup, refs,
                                       material_context=material_context)


def build_minute_evaluation(family, run_id, bar, universe_ref, state, action, reason, quantity, setup, refs, *,
                            material_context=None) -> StrategyEvaluation:
    at = datetime.fromisoformat(bar.available_at).astimezone(timezone.utc).isoformat()
    strategy_id, version = family.rsplit("/", 1)
    input_refs = tuple(dict.fromkeys(ref for ref in (bar.revision_id, universe_ref, *refs) if ref))
    snapshot_body = {
        "run_id": run_id, "decision_time": at, "input_cutoff": at, "symbol": bar.code,
        "universe_ref": universe_ref, "input_revision_refs": input_refs, "strategy_id": strategy_id,
        "strategy_version": version, "policy_version": family, "state_before": state.to_dict(),
        "feature_values": ({"factor_id": family, "status": action, "reason": reason,
                            "setup": _wire_dataclass(setup) if setup is not None else None},),
    }
    if material_context is not None:
        snapshot_body["feature_values"] += (material_context,)
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
