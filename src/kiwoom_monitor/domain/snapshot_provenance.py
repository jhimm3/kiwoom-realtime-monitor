"""체결 스냅샷 필드가 실시간 관측인지 장후 보완인지 판정한다."""

from __future__ import annotations

from typing import Protocol

from kiwoom_monitor.domain.market_data_contract import ObservationOrigin


class SnapshotLike(Protocol):
    rank: int | None
    news: tuple[dict[str, object], ...]
    themes: tuple[str, ...]
    investor_flow: dict[str, object] | None
    orderbook: dict[str, object] | None
    market_state: dict[str, object] | None


# 기존 import 계약은 유지하되 시장 데이터 전체가 같은 출처 enum을 쓴다.
SnapshotOrigin = ObservationOrigin


def snapshot_field_origins(snapshot: SnapshotLike) -> dict[str, SnapshotOrigin]:
    """분석에서 '당시 관측'과 나중에 복원한 자료를 섞지 않게 한다."""
    investor = snapshot.investor_flow or {}
    orderbook = snapshot.orderbook or {}
    market_state = snapshot.market_state or {}
    return {
        "rank": SnapshotOrigin.REALTIME if snapshot.rank is not None else SnapshotOrigin.MISSING,
        "news": _news_origin(snapshot.news),
        "themes": SnapshotOrigin.REALTIME if snapshot.themes else SnapshotOrigin.MISSING,
        "investor_flow": _mapping_origin(investor, require_available=True),
        "orderbook": _mapping_origin(orderbook),
        "market_state": _mapping_origin(market_state),
    }


def mark_news_backfilled(news: tuple[dict[str, object], ...]) -> tuple[dict[str, object], ...]:
    return tuple({**item, "backfilled": True} for item in news)


def _news_origin(news: tuple[dict[str, object], ...]) -> SnapshotOrigin:
    if not news:
        return SnapshotOrigin.MISSING
    return (
        SnapshotOrigin.BACKFILLED
        if all(bool(item.get("backfilled")) for item in news)
        else SnapshotOrigin.REALTIME
    )


def _mapping_origin(value: dict[str, object], *, require_available: bool = False) -> SnapshotOrigin:
    if not value or value.get("available") is False or (require_available and not bool(value.get("available"))):
        return SnapshotOrigin.MISSING
    return SnapshotOrigin.BACKFILLED if bool(value.get("backfilled")) else SnapshotOrigin.REALTIME
