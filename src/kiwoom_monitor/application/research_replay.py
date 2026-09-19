"""전략 계산 없이 기록된 TOP20 후보군을 결정론적으로 재생한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from kiwoom_monitor.application.market_session_schedule import (
    KST,
    KRX_REGULAR_RESEARCH_PROFILE,
    research_session_key,
    research_bar_allowed,
    schedule_version_for,
    session_window_at,
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
    if observation.get("kind") == "top20_membership" and isinstance(payload.get("codes"), list):
        raw_codes = list(payload["codes"])
    elif isinstance(payload.get("items"), list):
        raw_codes = [
            row.get("stk_cd", row.get("code", ""))
            for row in payload["items"] if isinstance(row, Mapping)
        ]
    normalized = (normalize_stock_code(value) for value in raw_codes)
    return tuple(dict.fromkeys(code for code in normalized if code))[:20]


def replay_candidate_universe(
    observations: Iterable[Mapping[str, Any]], *, as_of: datetime | None = None,
    chronological: bool = False,
) -> tuple[CandidateUniverseFrame, ...]:
    cutoff: datetime | None = None
    if as_of is not None:
        if as_of.tzinfo is None:
            raise ValueError("replay clock must be timezone-aware")
        cutoff = as_of.astimezone(timezone.utc)
    result: list[CandidateUniverseFrame] = []
    for value in observations:
        if value.get("kind") != "top20_membership":
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


def replay_krx_minute_bars(
    observations: Iterable[Mapping[str, Any]], *, as_of: datetime | None = None,
    strict: bool = True,
    session_profile: str = KRX_REGULAR_RESEARCH_PROFILE,
) -> tuple[KrxMinuteBarFrame, ...]:
    """가상 시각과 명시 세션 프로필에 허용된 최신 KRX 분봉을 반환한다."""
    cutoff = _utc_cutoff(as_of)
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
        if not research_bar_allowed(
            bar_start,
            bar_end,
            venue=str(value.get("venue", "")),
            session_profile=session_profile,
            declared_session=_optional_text(payload.get("session")),
            declared_phase=_optional_text(payload.get("phase")),
            declared_schedule_version=_optional_text(payload.get("schedule_version")),
        ):
            continue
        if strict and (
            payload.get("capture_quality") != "complete"
            or value.get("completeness") != "complete"
            or value.get("value_kind") != "actual"
            or available < bar_end
        ):
            continue
        try:
            scheduled = session_window_at(bar_start, venue="KRX")
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
                research_session=(
                    research_session_key(
                        bar_start.astimezone(KST), session_profile=session_profile,
                    ) or ""
                    if session_profile != "legacy-unfiltered-krx/v0" else ""
                ),
                market_session=_optional_text(payload.get("session")) or scheduled.session.value,
                market_phase=_optional_text(payload.get("phase")) or scheduled.phase.value,
                schedule_version=(
                    _optional_text(payload.get("schedule_version"))
                    or schedule_version_for(bar_start.astimezone(KST).date())
                ),
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
    def __init__(self, observations, *, session_profile, checkpoint=lambda: None):
        self.observations = observations
        self.session_profile = session_profile
        self.checkpoint = checkpoint
        self.offset = 0
        self.latest = {}
        self.universe = []

    def advance(self, through):
        while self.offset < len(self.observations):
            value = self.observations[self.offset]
            if _revision_order(value) > through:
                break
            self.offset += 1
            self.checkpoint()
            if value.get('kind') == 'top20_membership':
                self.universe.extend(replay_candidate_universe((value,), chronological=True))
            if value.get('kind') != 'minute_bar' or value.get('venue') != 'KRX':
                continue
            payload = value.get('payload')
            if not isinstance(payload, Mapping) or str(payload.get('market')) != 'KRX' or not payload.get('window_closed'):
                continue
            key = (str(value.get('subject', '')), str(value.get('observation_key', '')))
            frames = replay_krx_minute_bars((value,), strict=True, session_profile=self.session_profile)
            # A latest invalid correction hides its older valid revision, just as full replay does.
            if frames:
                self.latest[key] = frames[0]
            else:
                self.latest.pop(key, None)
        return (tuple(sorted(self.latest.values(), key=lambda row: (row.bar_start, row.code, row.revision_id))),
                tuple(self.universe))


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
