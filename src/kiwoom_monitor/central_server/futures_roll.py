"""선물 월물 코드와 자동 롤오버 판단 규칙."""

from __future__ import annotations

from dataclasses import dataclass
import re
from datetime import datetime, timedelta
from typing import Any


MONTH_CODES = "FGHJKMNQUVXZ"
QUARTERLY_MONTH_CODES = "HMUZ"
_CONTRACT_PATTERN = re.compile(r"^(.+?)([FGHJKMNQUVXZ])(\d{1,2})(\.[A-Za-z0-9]+)$")


def next_futures_contract(instrument: str, contract: str) -> str:
    """현재 Yahoo 월물 코드에서 해당 상품의 다음 월물 코드를 만든다."""
    match = _CONTRACT_PATTERN.fullmatch(contract.strip())
    if match is None:
        raise ValueError(f"지원하지 않는 선물 월물 코드입니다: {contract}")
    root, month_code, year_text, suffix = match.groups()
    cycle = QUARTERLY_MONTH_CODES if instrument.strip().upper() == "NASDAQ_FUTURES" else MONTH_CODES
    if month_code not in cycle:
        raise ValueError(f"{instrument}의 월물 주기와 맞지 않습니다: {contract}")
    index = cycle.index(month_code)
    next_index = (index + 1) % len(cycle)
    year = int(year_text) + (1 if next_index == 0 else 0)
    year_width = len(year_text)
    return f"{root}{cycle[next_index]}{year % (10 ** year_width):0{year_width}d}{suffix}"


def latest_bar_date(rows: list[dict[str, Any]]) -> str:
    dated = [str(row["bar_time"])[:10] for row in rows if row.get("bar_time")]
    return max(dated, default="")


def trailing_volume(rows: list[dict[str, Any]], end_time: str, hours: int = 24) -> float:
    """UTC 날짜 경계에 흔들리지 않도록 공통 종료시각 직전 구간의 거래량을 합산한다."""
    try:
        end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return 0.0
    start = end - timedelta(hours=max(1, hours))
    total = 0.0
    for row in rows:
        if row.get("volume") is None:
            continue
        try:
            moment = datetime.fromisoformat(str(row["bar_time"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if start < moment <= end:
            total += float(row["volume"])
    return total


def latest_session_volume(rows: list[dict[str, Any]], session_date: str = "") -> float:
    """두 월물을 같은 기준으로 비교하기 위한 최신 UTC 날짜 거래량 합계."""
    dated = [row for row in rows if row.get("bar_time") and row.get("volume") is not None]
    if not dated:
        return 0.0
    latest_date = session_date or max(str(row["bar_time"])[:10] for row in dated)
    return sum(float(row["volume"]) for row in dated if str(row["bar_time"])[:10] == latest_date)


def latest_price(rows: list[dict[str, Any]]) -> float | None:
    values = [row for row in rows if row.get("close") is not None]
    return float(values[-1]["close"]) if values else None


def previous_daily_close(rows: list[dict[str, Any]]) -> float | None:
    values = [row for row in rows if row.get("close") is not None]
    if len(values) < 2:
        return None
    return float(values[-2]["close"])


def change_percent(current: float | None, reference: float | None) -> float | None:
    if current is None or reference in {None, 0.0}:
        return None
    return (float(current) / float(reference) - 1.0) * 100.0


@dataclass(frozen=True)
class RollEvaluation:
    active_contract: str
    confirmation_count: int
    rolled: bool


def evaluate_roll(
    active_contract: str,
    next_contract: str,
    active_volume: float,
    next_volume: float,
    confirmation_count: int,
    required_confirmations: int,
) -> RollEvaluation:
    """차월물 거래량 우위가 연속 확인될 때 한 방향으로만 롤한다."""
    if next_volume > 0 and next_volume > active_volume:
        count = confirmation_count + 1
    else:
        count = 0
    if count >= max(1, required_confirmations):
        return RollEvaluation(next_contract, 0, True)
    return RollEvaluation(active_contract, count, False)
