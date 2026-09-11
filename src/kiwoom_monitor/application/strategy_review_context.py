"""매매 복기에 표시할 전략팩 참고자료 범위를 결정한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kiwoom_monitor.application.strategy_pack import StrategyPackManifest


class StructuredRuleLike(Protocol):
    lesson: int


class DefaultStrategyPackLike(Protocol):
    manifest: StrategyPackManifest

    def source_text(self, setup_type: str) -> str: ...


@dataclass(frozen=True)
class StrategyReviewReference:
    pack_name: str
    source_text: str
    relevant_rule_count: int


_LESSON_BY_SETUP = {
    "주도주 돌파": 2,
    "테마주 돌파": 3,
    "신규주": 4,
    "종가베팅": 5,
    "과대낙폭": 6,
    "낙주": 6,
}


def strategy_review_reference(
    setup_type: str,
    *,
    default_pack: DefaultStrategyPackLike,
    structured_rules: tuple[StructuredRuleLike, ...],
    selected_pack: StrategyPackManifest | None = None,
    selected_pack_rule_count: int = 0,
) -> StrategyReviewReference:
    """기본 강의 또는 선택된 사용자 전략팩의 표시 정보를 반환한다."""
    if selected_pack is not None:
        return StrategyReviewReference(
            selected_pack.name,
            selected_pack.source_description,
            max(0, selected_pack_rule_count),
        )
    applicable_lessons = {1, _LESSON_BY_SETUP.get(setup_type, -1)}
    return StrategyReviewReference(
        default_pack.manifest.name,
        default_pack.source_text(setup_type),
        sum(1 for rule in structured_rules if rule.lesson in applicable_lessons),
    )
