from __future__ import annotations

from collections.abc import Collection, Sequence

from kiwoom_monitor.application.news_grouping import NewsEventGroup
from kiwoom_monitor.infrastructure.naver_news import StockNewsItem
from kiwoom_monitor.infrastructure.persistence.news_ai_repository import news_identity


def auto_candidate_identities(
    groups: Sequence[NewsEventGroup],
    analyzed_identities: Collection[str],
    limit: int,
    *,
    new_identities: Collection[str] | None = None,
) -> tuple[str, ...]:
    if limit <= 0:
        return ()
    candidates: list[str] = []
    for group in groups:
        representative = group.representative
        identity = news_identity(representative)
        if new_identities is not None and not any(
            news_identity(item) in new_identities for item in group.items
        ):
            continue
        if not (representative.link or representative.original_link):
            continue
        if identity in analyzed_identities:
            continue
        candidates.append(identity)
        if len(candidates) >= limit:
            break
    return tuple(candidates)


def unanalyzed_groups_from(
    groups: Sequence[NewsEventGroup],
    start: int,
    analyzed_identities: Collection[str],
    limit: int,
) -> tuple[NewsEventGroup, ...]:
    return tuple(
        group for group in groups[max(0, start):]
        if news_identity(group.representative) not in analyzed_identities
        and (group.representative.link or group.representative.original_link)
    )[:max(0, limit)]


def next_auto_groups(
    visible_items: Sequence[StockNewsItem],
    visible_groups: Sequence[NewsEventGroup],
    pending_identities: Collection[str],
    analyzed_identities: Collection[str],
    request_mode: str,
    batch_size: int,
) -> tuple[NewsEventGroup, ...]:
    limit = 1 if request_mode == "single" else max(1, batch_size)
    candidates: list[NewsEventGroup] = []
    for row, item in enumerate(visible_items):
        identity = news_identity(item)
        if identity not in pending_identities or identity in analyzed_identities:
            continue
        if not (item.link or item.original_link) or row >= len(visible_groups):
            continue
        candidates.append(visible_groups[row])
        if len(candidates) >= limit:
            break
    return tuple(candidates)
