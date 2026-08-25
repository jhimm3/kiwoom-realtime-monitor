"""수정주가 기준의 역사적 신고가를 연봉 차트에서 계산한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol


class HistoricalChartClient(Protocol):
    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = ""
    ) -> tuple[dict[str, Any], bool, str]: ...


@dataclass(frozen=True)
class HistoricalHighTarget:
    """조회 가능한 가장 오래된 연도까지의 수정주가 최고가."""

    price: int | None
    first_year: int | None
    last_year: int | None


class HistoricalHighService:
    """ka10094 연봉을 연속조회해 KRX·NXT 통합 역사적 고가를 계산한다."""

    def __init__(self, client: HistoricalChartClient, *, include_nxt: bool = False) -> None:
        self._client = client
        self._include_nxt = include_nxt

    def load(self, code: str) -> HistoricalHighTarget:
        values = self._load_yearly_highs(code)
        if self._include_nxt:
            try:
                values.extend(self._load_yearly_highs(f"{code}_NX"))
            except Exception:
                # NXT 연봉이 아직 없거나 일시적으로 실패해도 KRX 결과는 사용한다.
                pass
        if not values:
            return HistoricalHighTarget(None, None, None)
        return HistoricalHighTarget(max(high for _, high in values), min(year for year, _ in values), max(year for year, _ in values))

    def _load_yearly_highs(self, code: str) -> list[tuple[int, int]]:
        values: list[tuple[int, int]] = []
        cont_yn, next_key = "N", ""
        # 서버 이상 응답이 무한 연속조회로 이어지지 않도록 안전 상한을 둔다.
        for _ in range(20):
            response, has_next, next_key = self._client.request_with_continuation(
                "ka10094", "/api/dostk/chart", {"stk_cd": code, "base_dt": date.today().strftime("%Y%m%d"), "upd_stkpc_tp": "1"},
                cont_yn=cont_yn, next_key=next_key,
            )
            rows = response.get("stk_yr_pole_chart_qry", [])
            if not isinstance(rows, list):
                raise ValueError("ka10094 연봉 목록 형식이 올바르지 않습니다.")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                year = _year(row)
                high = _price(row.get("high_pric"))
                if year is not None and high is not None:
                    values.append((year, high))
            if not has_next or not next_key:
                break
            cont_yn = "Y"
        return values


def _year(row: dict[str, Any]) -> int | None:
    value = str(row.get("date", row.get("dt", ""))).strip()
    try:
        year = int(value[:4])
    except ValueError:
        return None
    return year if 1900 <= year <= 2100 else None


def _price(value: object) -> int | None:
    try:
        price = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None
