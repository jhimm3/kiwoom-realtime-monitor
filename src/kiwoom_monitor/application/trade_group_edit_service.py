"""사용자가 매매 묶음을 합치고 나누거나 자동분류로 복원하는 쓰기 흐름."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable, Protocol

from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import TradeEpisode, trade_fill_key


class TradeGroupEditRepository(Protocol):
    def assign_group(self, fill_keys: tuple[str, ...], group_id: str) -> None: ...

    def clear_group_assignments(self, fill_keys: tuple[str, ...]) -> None: ...


@dataclass(frozen=True)
class TradeGroupEditResult:
    changed: bool
    message: str


class TradeGroupEditService:
    """묶음 편집 규칙을 검증하고 유효한 변경만 저장한다."""

    def __init__(
        self,
        repository: TradeGroupEditRepository,
        group_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._group_id_factory = group_id_factory or (lambda: "manual:" + uuid.uuid4().hex)

    def merge(self, episodes: tuple[TradeEpisode, ...]) -> TradeGroupEditResult:
        if len(episodes) < 2:
            return TradeGroupEditResult(False, "합칠 매매 묶음을 두 개 이상 선택하세요.")
        if len({episode.summary.stock_code for episode in episodes}) != 1:
            return TradeGroupEditResult(False, "서로 같은 종목의 매매 묶음만 합칠 수 있습니다.")
        keys = tuple(trade_fill_key(fill) for episode in episodes for fill in episode.fills)
        self._repository.assign_group(keys, self._group_id_factory())
        return TradeGroupEditResult(True, f"매매 묶음 {len(episodes)}개를 합쳤습니다.")

    def split(self, fills: tuple[TradeFill, ...]) -> TradeGroupEditResult:
        if not fills:
            return TradeGroupEditResult(False, "새 묶음으로 분리할 체결행을 선택하세요.")
        if len({fill.stock_code for fill in fills}) != 1:
            return TradeGroupEditResult(False, "같은 종목 체결만 하나의 묶음으로 만들 수 있습니다.")
        self._repository.assign_group(tuple(trade_fill_key(fill) for fill in fills), self._group_id_factory())
        return TradeGroupEditResult(True, f"선택 체결 {len(fills)}건을 새 묶음으로 분리했습니다.")

    def reset(self, episodes: tuple[TradeEpisode, ...]) -> TradeGroupEditResult:
        if not episodes:
            return TradeGroupEditResult(False, "자동분류로 되돌릴 묶음을 선택하세요.")
        keys = tuple(trade_fill_key(fill) for episode in episodes for fill in episode.fills)
        self._repository.clear_group_assignments(keys)
        return TradeGroupEditResult(True, "선택 묶음을 자동분류로 되돌렸습니다.")
