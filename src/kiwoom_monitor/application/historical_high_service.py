"""수정주가 기준의 역사적 신고가를 연봉·월봉·일봉으로 정밀 계산한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from calendar import monthrange
from typing import Any, Callable, Protocol


class HistoricalChartClient(Protocol):
    def request_with_continuation(self, api_id: str, path: str, body: dict[str, Any], *, cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, Any], bool, str]: ...


@dataclass(frozen=True)
class HistoricalHighEvidence:
    period: str
    trade_date: str
    high_price: int
    adjustment_types: str = ""
    adjustment_rate: str = ""
    adjustment_event: str = ""


@dataclass(frozen=True)
class HistoricalHighTarget:
    price: int | None
    first_year: int | None
    last_year: int | None
    occurred_on: str | None = None
    evidence: tuple[HistoricalHighEvidence, ...] = ()


@dataclass(frozen=True)
class HistoricalHighCache:
    target: HistoricalHighTarget
    checked_on: str


class HistoricalHighService:
    """연봉의 수정 이벤트 구간만 월봉·일봉으로 좁혀 역사적 고가를 계산한다."""

    def __init__(self, client: HistoricalChartClient, *, include_nxt: bool = False, cache_loader: Callable[[str], HistoricalHighCache | None] | None = None, high_250_loader: Callable[[str], int | None] | None = None) -> None:
        self._client = client
        self._include_nxt = include_nxt
        self._cache_loader = cache_loader
        self._high_250_loader = high_250_loader

    def load(self, code: str) -> HistoricalHighTarget:
        high_250 = self._high_250_loader(code) if self._high_250_loader is not None else None
        cache = self._cache_loader(code) if self._cache_loader is not None else None
        if cache is not None and cache.target.evidence:
            return self._load_incremental(code, cache, high_250)
        return self._load_fresh(code, high_250)

    def _load_fresh(self, code: str, high_250: int | None) -> HistoricalHighTarget:
        """오늘 기준 수정주가만으로 계산 근거를 처음부터 다시 만든다."""
        evidence = self._load_market(code, high_250)
        if self._include_nxt:
            try:
                evidence.extend(self._load_market(f"{code}_NX", high_250))
            except Exception:
                pass
        if not evidence:
            return HistoricalHighTarget(None, None, None)
        winner = max(evidence, key=lambda item: item.high_price)
        years = [int(item.trade_date[:4]) for item in evidence if item.trade_date[:4].isdigit()]
        return HistoricalHighTarget(winner.high_price, min(years), max(years), winner.trade_date, tuple(evidence))

    def _load_incremental(self, code: str, cache: HistoricalHighCache, high_250: int | None) -> HistoricalHighTarget:
        checked = cache.checked_on.replace("-", "")[:8]
        since_month = checked[:6]
        today = date.today().strftime("%Y%m%d")
        yearly = self._load_rows("ka10094", "stk_yr_pole_chart_qry", code, today, since=checked[:4])
        floor = max(cache.target.price or 0, high_250 or 0)
        years_to_refine: set[str] = set()
        for row in yearly:
            high = _price(row.get("high_pric"))
            if _has_adjustment(row) or (high is not None and high > floor):
                years_to_refine.add(_date_text(row)[:4])
        if not years_to_refine:
            evidence = list(cache.target.evidence)
            if high_250 is not None:
                evidence.append(HistoricalHighEvidence("250day", today, high_250))
            winner = max(evidence, key=lambda item: item.high_price)
            years = [int(item.trade_date[:4]) for item in evidence if item.trade_date[:4].isdigit()]
            return HistoricalHighTarget(winner.high_price, min(years), max(years), winner.trade_date, tuple(evidence))
        monthly: list[dict[str, Any]] = []
        for year in sorted(year for year in years_to_refine if len(year) == 4 and year.isdigit()):
            # 과거 연말을 기준일로 쓰면 그 이후 액면분할·병합이 반영되지 않은
            # 당시 가격이 반환된다. 항상 오늘 기준 수정주가로 과거까지 거슬러
            # 조회한 뒤 필요한 연도만 고른다.
            rows = self._load_rows("ka10083", "stk_mth_pole_chart_qry", code, today, prefix=year)
            monthly.extend(row for row in rows if year != checked[:4] or _date_text(row)[:6] >= since_month)
        evidence = list(cache.target.evidence)
        for month_row in sorted(monthly, key=_date_text):
            month = _date_text(month_row)[:6]
            if _has_adjustment(month_row):
                daily = self._load_rows("ka10081", "stk_dt_pole_chart_qry", code, today, prefix=month)
                new_event_rows = [row for row in daily if _has_adjustment(row) and _date_text(row) > checked]
                if new_event_rows:
                    # 저장된 근거는 이전 조회일의 주식 단위다. 새 권리변동이
                    # 생기면 수정비율을 직접 곱하지 않고, 키움이 오늘 기준으로
                    # 보정한 전체 차트를 다시 받아 DB 근거를 통째로 교체한다.
                    return self._load_fresh(code, high_250)
                detailed = [item for row in daily if (item := _evidence("day", row)) is not None]
                evidence.extend(detailed)
            elif (item := _evidence("month", month_row)) is not None:
                evidence.append(item)
        by_key = {(item.period, item.trade_date): item for item in evidence}
        evidence = list(by_key.values())
        winner = max(evidence, key=lambda item: item.high_price)
        years = [int(item.trade_date[:4]) for item in evidence if item.trade_date[:4].isdigit()]
        return HistoricalHighTarget(winner.high_price, min(years), max(years), winner.trade_date, tuple(evidence))

    def _load_market(self, code: str, high_250: int | None = None) -> list[HistoricalHighEvidence]:
        yearly = self._load_rows("ka10094", "stk_yr_pole_chart_qry", code, date.today().strftime("%Y%m%d"))
        evidence_by_year: dict[str, list[HistoricalHighEvidence]] = {}
        yearly_rows: dict[str, dict[str, Any]] = {}
        for row in yearly:
            year = _date_text(row)[:4]
            if len(year) != 4 or not year.isdigit():
                continue
            yearly_rows[year] = row
            if (item := _evidence("year", row)) is not None:
                evidence_by_year[year] = [item]

        if high_250 is not None and evidence_by_year:
            annual_max = max(item.high_price for items in evidence_by_year.values() for item in items)
            if annual_max <= high_250:
                evidence_by_year.setdefault(date.today().strftime("%Y"), []).append(
                    HistoricalHighEvidence("250day", date.today().strftime("%Y%m%d"), high_250)
                )
                return [item for items in evidence_by_year.values() for item in items]

        refined: set[str] = set()
        # 수정 표식이 있는 연도는 모두 세분화한다. 표식이 누락된 오래된 연봉
        # 이상치도 잡기 위해 현재 최고 후보 연도 역시 월봉으로 확인하고,
        # 최고 후보가 바뀌면 새 후보 연도까지 반복 확인한다.
        for year, row in yearly_rows.items():
            if _has_adjustment(row):
                if high_250 is None or (_price(row.get("high_pric")) or 0) > high_250:
                    evidence_by_year[year] = self._refine_year(code, year, row, high_250)
                refined.add(year)
        while evidence_by_year:
            winner = max((item for items in evidence_by_year.values() for item in items), key=lambda item: item.high_price)
            winner_year = winner.trade_date[:4]
            if winner.period == "250day" or winner_year in refined or winner_year not in yearly_rows:
                break
            evidence_by_year[winner_year] = self._refine_year(code, winner_year, yearly_rows[winner_year], high_250)
            refined.add(winner_year)
        return [item for items in evidence_by_year.values() for item in items]

    def _refine_year(self, code: str, year: str, fallback: dict[str, Any], high_250: int | None = None) -> list[HistoricalHighEvidence]:
        today = date.today().strftime("%Y%m%d")
        monthly = self._load_rows("ka10083", "stk_mth_pole_chart_qry", code, today, prefix=year)
        if not monthly:
            item = _evidence("year", fallback)
            return [item] if item is not None else []
        monthly_evidence = [item for row in monthly if (item := _evidence("month", row)) is not None]
        if high_250 is not None and monthly_evidence and max(item.high_price for item in monthly_evidence) <= high_250:
            return monthly_evidence + [HistoricalHighEvidence("250day", date.today().strftime("%Y%m%d"), high_250)]
        evidence: list[HistoricalHighEvidence] = []
        for month_row in monthly:
            month = _date_text(month_row)[:6]
            if not _has_adjustment(month_row):
                if (item := _evidence("month", month_row)) is not None:
                    evidence.append(item)
                continue
            daily = self._load_rows("ka10081", "stk_dt_pole_chart_qry", code, today, prefix=month)
            detailed = [item for day_row in daily if (item := _evidence("day", day_row)) is not None]
            if detailed:
                evidence.extend(detailed)
            elif (item := _evidence("month", month_row)) is not None:
                evidence.append(item)
        if evidence:
            return evidence
        item = _evidence("year", fallback)
        return [item] if item is not None else []

    def _load_rows(self, api_id: str, key: str, code: str, base_date: str, *, prefix: str = "", since: str = "") -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        cont_yn, next_key = "N", ""
        for _ in range(20):
            response, has_next, next_key = self._client.request_with_continuation(
                api_id, "/api/dostk/chart", {"stk_cd": code, "base_dt": base_date, "upd_stkpc_tp": "1"}, cont_yn=cont_yn, next_key=next_key,
            )
            rows = response.get(key, [])
            if not isinstance(rows, list):
                raise ValueError(f"{api_id} 차트 목록 형식이 올바르지 않습니다.")
            dated_rows = [row for row in rows if isinstance(row, dict)]
            values.extend(row for row in dated_rows if (not prefix or _date_text(row).startswith(prefix)) and (not since or _date_text(row)[:len(since)] >= since))
            dates = [_date_text(row) for row in dated_rows if _date_text(row)]
            if prefix and dates and min(dates)[:len(prefix)] < prefix:
                break
            if since and dates and min(dates)[:len(since)] < since:
                break
            if not has_next or not next_key:
                break
            cont_yn = "Y"
        return values


def _date_text(row: dict[str, Any]) -> str:
    return str(row.get("dt", row.get("date", ""))).strip()


def _month_end(month: str) -> str:
    year_number, month_number = int(month[:4]), int(month[4:6])
    return f"{month}{monthrange(year_number, month_number)[1]:02d}"


def _has_adjustment(row: dict[str, Any]) -> bool:
    empty = {"", "0", "0.0", "+0.00", "-0.00"}
    return any(str(row.get(key, "")).strip() not in empty for key in ("upd_stkpc_tp", "upd_rt", "upd_stkpc_event"))


def _evidence(period: str, row: dict[str, Any]) -> HistoricalHighEvidence | None:
    high, trade_date = _price(row.get("high_pric")), _date_text(row)
    if high is None or len(trade_date) < 4:
        return None
    return HistoricalHighEvidence(period, trade_date, high, str(row.get("upd_stkpc_tp", "")).strip(), str(row.get("upd_rt", "")).strip(), str(row.get("upd_stkpc_event", "")).strip())


def _price(value: object) -> int | None:
    try:
        price = abs(int(str(value).strip().replace(",", "")))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None
