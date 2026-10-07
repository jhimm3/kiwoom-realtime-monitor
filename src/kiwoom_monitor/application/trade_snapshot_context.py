"""매매 회차에 체결 스냅샷을 연결하고 분석 가능 범위를 계산한다."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, TypeVar

from kiwoom_monitor.domain.snapshot_provenance import (
    SnapshotLike,
    SnapshotOrigin,
    snapshot_field_origins,
)


class TradeCycleLike(Protocol):
    started_at: datetime
    ended_at: datetime


class TimedSnapshotLike(SnapshotLike, Protocol):
    executed_at: datetime
    side: str


SnapshotT = TypeVar("SnapshotT", bound=TimedSnapshotLike)


def snapshots_for_cycle(
    cycle: TradeCycleLike,
    snapshots: tuple[SnapshotT, ...],
    *,
    tolerance: timedelta = timedelta(minutes=1),
) -> tuple[SnapshotT, ...]:
    """회차 시작·종료 경계의 체결 시각 오차를 포함해 관련 스냅샷을 고른다."""
    start = cycle.started_at - tolerance
    end = cycle.ended_at + tolerance
    return tuple(snapshot for snapshot in snapshots if start <= snapshot.executed_at <= end)


def entry_snapshot_for_cycle(
    cycle: TradeCycleLike,
    snapshots: tuple[SnapshotT, ...],
) -> SnapshotT | None:
    """매수 스냅샷을 우선하고, 매수 자료가 없으면 회차의 첫 스냅샷을 사용한다."""
    related = snapshots_for_cycle(cycle, snapshots)
    if not related:
        return None
    return next((snapshot for snapshot in related if snapshot.side == "매수"), related[0])


def resolve_unverifiable_items(
    cycle: TradeCycleLike,
    snapshots: tuple[SnapshotT, ...],
    original: tuple[str, ...],
) -> tuple[str, ...]:
    """실시간 관측·장후 보완·진짜 누락을 구분해 판정 불가 항목을 정리한다."""
    entry = entry_snapshot_for_cycle(cycle, snapshots)
    if entry is None:
        return original

    origins = snapshot_field_origins(entry)
    result: list[str] = []
    for item in original:
        if "시장 주도주 여부" in item and origins["rank"] is SnapshotOrigin.REALTIME:
            result.append("1강 기준의 지속적인 시장 관심 여부(진입 순간 순위는 확인됨)")
            continue
        if item.startswith("당시 뉴스") or item.startswith("급락 원인"):
            missing: list[str] = []
            backfilled: list[str] = []
            for key, label in (
                ("news", "뉴스·재료"),
                ("themes", "테마"),
                ("orderbook", "호가·매수세"),
                ("investor_flow", "외국인·기관 수급"),
                ("market_state", "시장 상태"),
            ):
                if origins[key] is SnapshotOrigin.MISSING:
                    missing.append(label)
                elif origins[key] is SnapshotOrigin.BACKFILLED:
                    backfilled.append(label)
            if missing:
                result.append("당시 " + "·".join(missing))
            if backfilled:
                result.append("체결 당시 미확인(장후 보완값 있음): " + "·".join(backfilled))
            continue
        result.append(item)
    return tuple(dict.fromkeys(result))
