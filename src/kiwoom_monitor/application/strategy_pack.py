"""매매 강의별 판정·필요 데이터·출처를 분리하는 전략팩 기반."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .personal_trade_rules import applicable_lesson_text
from .trade_setup_classification import (
    TRADE_SETUP_TYPES, TradeSetupClassification, classify_trade_setup,
    classify_trade_setup_cycles, lesson_unverifiable_for,
)


STRATEGY_RESULT_MODES = {
    "together": "기본분석·전략팩 함께 표시",
    "auto_replace": "조건 충족 시 자동 교체",
    "strategy_priority": "전략팩 우선",
    "base_priority": "기본분석 우선",
}


def select_strategy_candidate(
    candidates: tuple[tuple[str, TradeSetupClassification], ...], mode: str,
) -> tuple[str, TradeSetupClassification]:
    """표시 모드에 따라 최종 유형을 고르되 후보 결과 자체는 모두 보존한다."""
    if not candidates:
        raise ValueError("전략 판정 후보가 없습니다.")
    selected_name, selected = candidates[0]
    if mode == "auto_replace":
        for name, result in candidates[1:]:
            if selected.setup_type == "기타" or result.confidence >= selected.confidence + 5:
                selected_name, selected = name, result
        return selected_name, selected
    if mode != "strategy_priority":
        return selected_name, selected
    custom = candidates[1:]
    return max(custom, key=lambda item: item[1].confidence) if custom else (selected_name, selected)


@dataclass(frozen=True)
class StrategyPackManifest:
    pack_id: str
    name: str
    version: int
    enabled: bool
    priority: int
    setup_types: tuple[str, ...]
    required_data: tuple[str, ...]
    optional_data: tuple[str, ...] = ()
    source_description: str = ""
    source_files: tuple[str, ...] = ()
    review_status: str = "approved"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "StrategyPackManifest":
        return cls(
            pack_id=str(value.get("pack_id", "")), name=str(value.get("name", "")),
            version=int(value.get("version", 1)), enabled=bool(value.get("enabled", True)),
            priority=int(value.get("priority", 100)),
            setup_types=tuple(str(item) for item in value.get("setup_types", ()) if str(item)),
            required_data=tuple(str(item) for item in value.get("required_data", ()) if str(item)),
            optional_data=tuple(str(item) for item in value.get("optional_data", ()) if str(item)),
            source_description=str(value.get("source_description", "")),
            source_files=tuple(str(item) for item in value.get("source_files", ()) if str(item)),
            review_status=str(value.get("review_status", "draft")),
        )


MIMOSA_MANIFEST = StrategyPackManifest(
    pack_id="mimosa_v1", name="미모사 기본 전략팩", version=1, enabled=True, priority=10,
    setup_types=TRADE_SETUP_TYPES,
    required_data=("체결내역", "1분봉", "일봉", "거래대금", "신고가 거리"),
    optional_data=("실시간 순위", "테마", "뉴스", "외국인·기관 수급", "프로그램매매", "체결강도", "시장 상태"),
    source_description="미모사 기초 및 1~6강 구조화 원칙",
)


class MimosaStrategyPack:
    """기존 판정 함수를 그대로 위임해 기준값과 결과를 보존한다."""

    manifest = MIMOSA_MANIFEST

    def classify(self, episode: object, minute_rows: tuple[tuple[object, ...], ...], daily_rows: tuple[tuple[object, ...], ...] = ()) -> TradeSetupClassification:
        return classify_trade_setup(episode, minute_rows, daily_rows)  # type: ignore[arg-type]

    def classify_cycles(self, episode: object, minute_rows: tuple[tuple[object, ...], ...], daily_rows: tuple[tuple[object, ...], ...] = ()) -> tuple[TradeSetupClassification, ...]:
        return classify_trade_setup_cycles(episode, minute_rows, daily_rows)  # type: ignore[arg-type]

    @staticmethod
    def unverifiable_for(setup_type: str) -> tuple[str, ...]:
        return lesson_unverifiable_for(setup_type)

    @staticmethod
    def source_text(setup_type: str) -> str:
        return applicable_lesson_text(setup_type)


def default_strategy_pack() -> MimosaStrategyPack:
    return MimosaStrategyPack()
