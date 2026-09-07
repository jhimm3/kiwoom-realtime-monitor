"""위탁종합거래내역(kt00015)에서 체결일 기준 실제 수수료·세금을 읽는다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import TradeEpisode


class RestClient(Protocol):
    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]: ...


@dataclass(frozen=True)
class DailyTradeCost:
    fill_date: date
    settlement_date: date
    stock_code: str
    side: str
    gross_amount: int
    settlement_amount: int
    commission: int
    tax: int
    total_cost: int


@dataclass(frozen=True)
class EpisodeTradeCost:
    total_cost: int
    net_realized_profit: int | None
    complete: bool
    allocated: bool


def estimate_episode_cost(
    episode: TradeEpisode, buy_rate_percent: float = 0.015,
    sell_rate_percent: float = 0.215,
) -> int:
    """정산 전 완결 매매의 매수 수수료와 매도 수수료·세금을 예상한다."""
    if episode.summary.open_quantity != 0 or episode.summary.sell_amount <= 0:
        return 0
    # 증권사 정산 전 표시이므로 원 미만은 반올림하지 않고 절삭한다.
    buy_cost = int(episode.summary.buy_amount * max(0.0, buy_rate_percent) / 100)
    sell_cost = int(episode.summary.sell_amount * max(0.0, sell_rate_percent) / 100)
    return buy_cost + sell_cost


def allocate_episode_cost(
    episode: TradeEpisode, all_fills: tuple[TradeFill, ...], costs: tuple[DailyTradeCost, ...],
) -> EpisodeTradeCost:
    cost_map = {(value.fill_date, value.stock_code, value.side): value for value in costs}
    all_amounts: dict[tuple[date, str, str], int] = {}
    episode_amounts: dict[tuple[date, str, str], int] = {}
    for fill in all_fills:
        key = (fill.filled_at.date(), fill.stock_code, fill.side)
        all_amounts[key] = all_amounts.get(key, 0) + fill.quantity * fill.price
    for fill in episode.fills:
        key = (fill.filled_at.date(), fill.stock_code, fill.side)
        episode_amounts[key] = episode_amounts.get(key, 0) + fill.quantity * fill.price
    total_cost = 0
    complete = bool(episode_amounts)
    allocated = False
    for key, amount in episode_amounts.items():
        actual = cost_map.get(key)
        if actual is None:
            complete = False
            continue
        denominator = all_amounts.get(key, amount)
        allocated = allocated or denominator != amount
        total_cost += round(actual.total_cost * amount / denominator) if denominator else 0
    net_profit = episode.summary.realized_profit - total_cost if complete and episode.summary.open_quantity == 0 and episode.summary.matched_cost else None
    return EpisodeTradeCost(total_cost, net_profit, complete, allocated)


class TradeCostService:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def load_period(self, start: date, end: date, today: date | None = None) -> tuple[DailyTradeCost, ...]:
        today = today or date.today()
        settlement_end = min(today, end + timedelta(days=10))
        if settlement_end < start:
            return ()
        body = {
            "strt_dt": start.strftime("%Y%m%d"), "end_dt": settlement_end.strftime("%Y%m%d"),
            "tp": "0", "stk_cd": "", "crnc_cd": "", "gds_tp": "1",
            "frgn_stex_code": "", "dmst_stex_tp": "%", "qry_sort_tp": "1",
        }
        records: list[dict[str, Any]] = []
        cont_yn, next_key = "N", ""
        for _ in range(20):
            response, has_next, response_next_key = self._client.request_with_continuation(
                "kt00015", "/api/dostk/acnt", body, cont_yn=cont_yn, next_key=next_key,
            )
            values = response.get("trst_ovrl_trde_prps_array", [])
            if isinstance(values, list):
                records.extend(value for value in values if isinstance(value, dict))
            if not has_next or not response_next_key:
                break
            cont_yn, next_key = "Y", response_next_key
        grouped: dict[tuple[date, date, str, str], list[int]] = {}
        for record in records:
            value = self._to_cost(record)
            if value is None or not start <= value.fill_date <= end:
                continue
            key = (value.fill_date, value.settlement_date, value.stock_code, value.side)
            totals = grouped.setdefault(key, [0, 0, 0, 0, 0])
            for index, amount in enumerate((value.gross_amount, value.settlement_amount, value.commission, value.tax, value.total_cost)):
                totals[index] += amount
        return tuple(
            DailyTradeCost(*key, *totals) for key, totals in sorted(grouped.items())
        )

    @staticmethod
    def _to_cost(record: dict[str, Any]) -> DailyTradeCost | None:
        try:
            fill_date = _date(record.get("cntr_dt"))
            settlement_date = _date(record.get("trde_dt"))
        except ValueError:
            return None
        code = _stock_code(record.get("stk_cd"))
        side_text = str(record.get("io_tp_nm", ""))
        side = "매도" if "매도" in side_text else ("매수" if "매수" in side_text else "")
        if not code or not side:
            return None
        commission = _number(record.get("cmsn"))
        tax = _number(record.get("trde_agri_tax"))
        combined = _number(record.get("tax_sum_cmsn"))
        return DailyTradeCost(
            fill_date, settlement_date, code, side, _number(record.get("trde_amt")),
            _number(record.get("exct_amt")), commission, tax, combined or commission + tax,
        )


def _digits(value: object) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


def _stock_code(value: object) -> str:
    """API 접두 문자만 제거하고 종목코드 내부 영문자는 보존한다."""
    code = "".join(character for character in str(value or "").strip().upper() if character.isalnum())
    if len(code) == 7 and code[0] in "AJQ":
        code = code[1:]
    return code[-6:]


def _date(value: object) -> date:
    digits = _digits(value)
    return date.fromisoformat(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}")


def _number(value: object) -> int:
    try:
        return abs(int(float(str(value or "0").replace(",", "").strip())))
    except ValueError:
        return 0
