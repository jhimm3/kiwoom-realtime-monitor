"""전략 계산 없이 기록된 TOP20 후보군을 결정론적으로 재생한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.market_session_schedule import (
    KST,
    KRX_REGULAR_RESEARCH_PROFILE,
    MarketSession,
    TradingDayEvidence,
    calendar_research_bar_window,
    research_session_key,
    research_bar_allowed,
    schedule_version_for,
    session_window_at,
    trading_day_as_of,
)
from kiwoom_monitor.domain.ranking import normalize_stock_code


@dataclass(frozen=True)
class CandidateUniverseFrame:
    revision_id: str
    observation_key: str
    available_at: str
    codes: tuple[str, ...]
    accepted_sequence: int = 0


@dataclass(frozen=True)
class KrxMinuteBarFrame:
    revision_id: str
    observation_key: str
    code: str
    bar_start: str
    bar_end: str
    available_at: str
    open: int
    high: int
    low: int
    close: int
    volume: int
    trade_value_million_won: int
    session_finalized: bool
    capture_quality: str
    finalization_source: str
    session_profile: str = ""
    research_session: str = ""
    market_session: str = ""
    market_phase: str = ""
    schedule_version: str = ""


def normalize_candidate_codes(observation: Mapping[str, Any]) -> tuple[str, ...]:
    payload = observation.get("payload", {})
    if not isinstance(payload, Mapping):
        return ()
    raw_codes: list[object] = []
    kind = observation.get("kind")
    if kind in {"top20_membership", "historical_candidate_population"} and isinstance(payload.get("codes"), list):
        raw_codes = list(payload["codes"])
    elif isinstance(payload.get("items"), list):
        raw_codes = [
            row.get("stk_cd", row.get("code", ""))
            for row in payload["items"] if isinstance(row, Mapping)
        ]
    normalized = (normalize_stock_code(value) for value in raw_codes)
    values = tuple(dict.fromkeys(code for code in normalized if code))
    return values if kind == "historical_candidate_population" else values[:20]


def replay_candidate_universe(
    observations: Iterable[Mapping[str, Any]], *, as_of: datetime | None = None,
    chronological: bool = True,
    kinds: tuple[str, ...] = ("top20_membership",),
) -> tuple[CandidateUniverseFrame, ...]:
    """Order membership by actual availability, then recorded ingest tie-breakers.

    Explicit chronological=False preserves the legacy timestamp-text comparison.
    """
    cutoff: datetime | None = None
    if as_of is not None:
        if as_of.tzinfo is None:
            raise ValueError("replay clock must be timezone-aware")
        cutoff = as_of.astimezone(timezone.utc)
    result: list[CandidateUniverseFrame] = []
    for value in observations:
        if value.get("kind") not in kinds:
            continue
        available_text = str(value.get("available_at", ""))
        try:
            available = datetime.fromisoformat(available_text)
        except ValueError:
            continue
        if available.tzinfo is None:
            continue
        if cutoff is not None and available.astimezone(timezone.utc) > cutoff:
            continue
        result.append(CandidateUniverseFrame(
            revision_id=str(value.get("revision_id", "")),
            observation_key=str(value.get("observation_key", "")),
            available_at=available_text,
            codes=normalize_candidate_codes(value),
            accepted_sequence=int(value.get("accepted_sequence", 0)),
        ))
    return tuple(sorted(result, key=lambda frame: (
        datetime.fromisoformat(frame.available_at).astimezone(timezone.utc) if chronological else frame.available_at,
        frame.accepted_sequence, frame.revision_id,
    )))


def _krx_calendar_by_day(evidence: Iterable[TradingDayEvidence]) -> dict[date, list[TradingDayEvidence]]:
    by_day: dict[date, list[TradingDayEvidence]] = {}
    for revision in evidence:
        if revision.venue == "KRX":
            by_day.setdefault(revision.trading_date, []).append(revision)
    return by_day


def replay_krx_minute_bars(
    observations: Iterable[Mapping[str, Any]], *, as_of: datetime | None = None,
    strict: bool = True,
    session_profile: str = KRX_REGULAR_RESEARCH_PROFILE,
    calendar_evidence: Iterable[TradingDayEvidence] | Mapping[date, Iterable[TradingDayEvidence]] | None = None,
) -> tuple[KrxMinuteBarFrame, ...]:
    """Read latest KRX minute revisions with their original availability cut."""
    cutoff = _utc_cutoff(as_of)
    calendar_by_day = (calendar_evidence if isinstance(calendar_evidence, Mapping)
                       else _krx_calendar_by_day(calendar_evidence or ()))
    calendar_at_receipt = {}
    latest: dict[tuple[str, str], Mapping[str, Any]] = {}
    for value in observations:
        if value.get("kind") != "minute_bar" or value.get("venue") != "KRX":
            continue
        available = _aware_datetime(value.get("available_at"))
        if available is None or (cutoff is not None and available.astimezone(timezone.utc) > cutoff):
            continue
        payload = value.get("payload")
        if not isinstance(payload, Mapping) or str(payload.get("market")) != "KRX":
            continue
        if strict and not bool(payload.get("window_closed")):
            continue
        key = (str(value.get("subject", "")), str(value.get("observation_key", "")))
        previous = latest.get(key)
        if previous is None or _revision_order(value) > _revision_order(previous):
            latest[key] = value

    result: list[KrxMinuteBarFrame] = []
    for value in latest.values():
        payload = value["payload"]
        assert isinstance(payload, Mapping)
        bar_start = _aware_datetime(payload.get("bar_start"))
        bar_end = _aware_datetime(payload.get("bar_end"))
        available = _aware_datetime(value.get("available_at"))
        if bar_start is None or bar_end is None or available is None or bar_end <= bar_start:
            continue
        declared = {
            "declared_session": _optional_text(payload.get("session")),
            "declared_phase": _optional_text(payload.get("phase")),
            "declared_schedule_version": _optional_text(payload.get("schedule_version")),
        }
        trading_date = bar_start.astimezone(KST).date()
        if calendar_evidence is None:
            if not research_bar_allowed(
                bar_start, bar_end, venue="KRX", session_profile=session_profile, **declared,
            ):
                continue
        if strict and (
            payload.get("capture_quality") != "complete"
            or value.get("completeness") != "complete"
            or value.get("value_kind") != "actual"
            or available < bar_end
        ):
            continue
        if calendar_evidence is None:
            scheduled = session_window_at(bar_start, venue="KRX")
            session_key = (
                research_session_key(bar_start.astimezone(KST), session_profile=session_profile) or ""
                if session_profile != "legacy-unfiltered-krx/v0" else ""
            )
            schedule_version = schedule_version_for(trading_date)
        else:
            key = (trading_date, available.astimezone(timezone.utc))
            if key not in calendar_at_receipt:
                calendar_at_receipt[key] = trading_day_as_of(
                    trading_date, venue="KRX", as_of=available, evidence=calendar_by_day.get(trading_date, ()),
                )
            scheduled = calendar_research_bar_window(
                bar_start, bar_end, trading_day=calendar_at_receipt[key], decision_at=available,
                session_profile=session_profile, **declared,
            )
            if scheduled is None:
                continue
            segment = "KRX_AFTER" if scheduled.session is MarketSession.KRX_AFTER_MARKET else "KRX_REGULAR"
            session_key = f"{trading_date.isoformat()}:{segment}"
            schedule_version = scheduled.schedule_version
        try:
            result.append(KrxMinuteBarFrame(
                revision_id=str(value.get("revision_id", "")),
                observation_key=str(value.get("observation_key", "")),
                code=normalize_stock_code(payload.get("code")),
                bar_start=bar_start.isoformat(),
                bar_end=bar_end.isoformat(),
                available_at=available.isoformat(),
                open=int(payload["open"]), high=int(payload["high"]), low=int(payload["low"]),
                close=int(payload["close"]), volume=int(payload["volume"]),
                trade_value_million_won=int(payload["trade_value_million_won"]),
                session_finalized=bool(payload.get("session_finalized", False)),
                capture_quality=str(payload.get("capture_quality", "")),
                finalization_source=str(payload.get("finalization_source", "")),
                session_profile=session_profile,
                research_session=session_key,
                market_session=declared["declared_session"] or scheduled.session.value,
                market_phase=declared["declared_phase"] or scheduled.phase.value,
                schedule_version=declared["declared_schedule_version"] or schedule_version,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(sorted(result, key=lambda row: (row.bar_start, row.code, row.revision_id)))


def _utc_cutoff(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("replay clock must be timezone-aware")
    return value.astimezone(timezone.utc)


class ResearchReplayCursor:
    """Consume ordered ingests once; keep latest-revision selection identical to full replay."""
    def __init__(
        self, observations, *, session_profile, checkpoint=lambda: None,
        universe_kinds: tuple[str, ...] = ("top20_membership",),
        calendar_evidence: Iterable[TradingDayEvidence] | None = None,
    ):
        self.observations = observations
        self.session_profile = session_profile
        self.checkpoint = checkpoint
        self.universe_kinds = universe_kinds
        self.calendar_by_day = _krx_calendar_by_day(calendar_evidence) if calendar_evidence is not None else None
        self.offset = 0
        self.latest = {}
        self.latest_by_code_day = {}
        self.universe = []

    def advance(self, through):
        self._advance_state(through)
        return (tuple(sorted(self.latest.values(), key=lambda row: (row.bar_start, row.code, row.revision_id))),
                tuple(self.universe))

    def advance_for_observation(self, observation):
        """Return the current bar and only same-code, same-day factor history."""
        self._advance_state(_revision_order(observation))
        key = (str(observation.get('subject', '')), str(observation.get('observation_key', '')))
        current = self.latest.get(key)
        if current is None or current.revision_id != str(observation.get('revision_id', '')):
            return None, (), tuple(self.universe)
        group = self._bar_group(current)
        return current, self.bars_for_date(*group), tuple(self.universe)

    def bars_for_date(self, code: str, trading_date: date) -> tuple[KrxMinuteBarFrame, ...]:
        return tuple(sorted(self.latest_by_code_day.get((code, trading_date), {}).values(),
                            key=lambda row: (row.bar_start, row.code, row.revision_id)))

    @staticmethod
    def _bar_group(frame):
        return frame.code, datetime.fromisoformat(frame.bar_start).astimezone(KST).date()

    def _advance_state(self, through):
        while self.offset < len(self.observations):
            value = self.observations[self.offset]
            if _revision_order(value) > through:
                break
            self.offset += 1
            self.checkpoint()
            if value.get('kind') in self.universe_kinds:
                self.universe.extend(replay_candidate_universe(
                    (value,), chronological=True, kinds=self.universe_kinds,
                ))
            if value.get('kind') != 'minute_bar' or value.get('venue') != 'KRX':
                continue
            payload = value.get('payload')
            if not isinstance(payload, Mapping) or str(payload.get('market')) != 'KRX' or not payload.get('window_closed'):
                continue
            key = (str(value.get('subject', '')), str(value.get('observation_key', '')))
            frames = replay_krx_minute_bars(
                (value,), strict=True, session_profile=self.session_profile,
                calendar_evidence=self.calendar_by_day,
            )
            # A latest invalid correction hides its older valid revision, just as full replay does.
            previous = self.latest.get(key)
            if previous is not None:
                previous_group = self._bar_group(previous)
                group_rows = self.latest_by_code_day[previous_group]
                group_rows.pop(key, None)
                if not group_rows:
                    del self.latest_by_code_day[previous_group]
            if frames:
                self.latest[key] = frames[0]
                self.latest_by_code_day.setdefault(self._bar_group(frames[0]), {})[key] = frames[0]
            else:
                self.latest.pop(key, None)


def _aware_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _revision_order(value: Mapping[str, Any]) -> tuple[datetime, int, str]:
    available = _aware_datetime(value.get("available_at"))
    assert available is not None
    return (
        available.astimezone(timezone.utc),
        int(value.get("accepted_sequence", 0)),
        str(value.get("revision_id", "")),
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def trim_bar_frames(bars, code: str, keep_count: int) -> dict:
    rows = sorted(
        (frame for frame in bars.values() if frame.code == code),
        key=lambda frame: (frame.bar_start, frame.available_at, frame.revision_id),
        reverse=True,
    )
    keep = {(frame.code, frame.observation_key) for frame in rows[:keep_count]}
    return {key: frame for key, frame in bars.items() if frame.code != code or key in keep}


def trim_universe_frames(universes, latest_at: str, horizon: int, *, use_maximum: bool) -> list:
    latest = datetime.fromisoformat(latest_at).astimezone(timezone.utc)
    if use_maximum and universes:
        latest = max(datetime.fromisoformat(frame.available_at).astimezone(timezone.utc)
                     for frame in universes)
    return [frame for frame in universes
            if (latest - datetime.fromisoformat(frame.available_at).astimezone(timezone.utc))
            .total_seconds() <= horizon][-5000:]


class MinuteStrategyInputs:
    """Own the bounded live minute window; never own execution progress or IO.

    Unlike ResearchReplayCursor, this window keeps the previous valid frame
    when an individually delivered correction is invalid. Bootstrap and legacy
    recovery retain their existing codecs and publication boundary.
    """

    def __init__(self, *, session_profile: str, keep_bars: int, universe_horizon: int):
        self.session_profile = session_profile
        self.keep_bars = keep_bars
        self.universe_horizon = universe_horizon
        self._bars: dict[tuple[str, str], KrxMinuteBarFrame] = {}
        self._universes: list[CandidateUniverseFrame] = []

    def accept_observation(self, observation: Mapping[str, Any]) -> KrxMinuteBarFrame | None:
        if observation.get("kind") == "top20_membership":
            frames = replay_candidate_universe((observation,))
            if frames:
                self._universes.append(frames[0])
                self._universes = trim_universe_frames(
                    self._universes, frames[0].available_at, self.universe_horizon, use_maximum=False,
                )
            return None
        frames = replay_krx_minute_bars(
            (observation,), strict=True, session_profile=self.session_profile,
        )
        if not frames:
            return None
        bar = frames[0]
        self._bars[(bar.code, bar.observation_key)] = bar
        self._bars = trim_bar_frames(self._bars, bar.code, self.keep_bars)
        return bar

    def seed(self, observations) -> None:
        self._universes = list(replay_candidate_universe(observations))
        self._bars = {}
        if observations:
            for frame in replay_krx_minute_bars(
                observations, strict=True, session_profile=self.session_profile,
            ):
                self._bars[(frame.code, frame.observation_key)] = frame
            for code in {frame.code for frame in self._bars.values()}:
                self._bars = trim_bar_frames(self._bars, code, self.keep_bars)
            if self._universes:
                self._universes = trim_universe_frames(
                    self._universes, self._universes[-1].available_at,
                    self.universe_horizon, use_maximum=False,
                )

    def evaluate_bar(self, *, family, run_id, evaluation_bar, config, state):
        return family.evaluate_bar(
            run_id=run_id, evaluation_bar=evaluation_bar,
            bar_history=tuple(self._bars.values()), universe_frames=tuple(self._universes),
            config=config, state=state,
        )

    def clock_price_frame(self, symbol, through, observations):
        frames = tuple(self._bars.values()) + tuple(replay_krx_minute_bars(
            observations, strict=True, session_profile=self.session_profile,
        ))
        bars = [bar for bar in frames if bar.code == symbol
                and datetime.fromisoformat(bar.available_at) <= through]
        return max(bars, key=lambda value: (
            datetime.fromisoformat(value.bar_end), datetime.fromisoformat(value.available_at), value.revision_id),
                   default=None)

    def restore_bars(self, values) -> None:
        self._bars = {
            (frame.code, frame.observation_key): frame
            for frame in (KrxMinuteBarFrame(**value) for value in values)
        }

    def restore_universes(self, values) -> None:
        self._universes = [CandidateUniverseFrame(**value) for value in values]

    def install_frames(self, *, bars=None, universes=None) -> None:
        """Transfer already decoded frames after the episode's checkpoint ACK."""
        if bars is not None:
            self._bars = bars
        if universes is not None:
            self._universes = universes

    def frames_for_checkpoint(self):
        return self._bars.values(), self._universes
