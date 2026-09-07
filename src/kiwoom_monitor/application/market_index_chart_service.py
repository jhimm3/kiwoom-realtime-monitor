"""키움 ka20005 코스피·코스닥 업종 분봉 보완."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class MarketIndexChartService:
    CODES = {"kospi": "001", "kosdaq": "101"}

    def __init__(self, client: RestClient) -> None:
        self._client = client

    def load(self, market: str, base: datetime, maximum: int = 1_500) -> dict[tuple[str, datetime], tuple[float, float, float, float, float | None]]:
        code = self.CODES[market]; body = {"inds_cd": code, "tic_scope": "1", "base_dt": base.strftime("%Y%m%d")}
        response, has_next, next_key = self._request(body); records = self._records(response)
        while has_next and next_key and len(records) < maximum:
            response, has_next, next_key = self._request(body, "Y", next_key); records.extend(self._records(response))
        result: dict[tuple[str, datetime], tuple[float, float, float, float, float | None]] = {}
        for record in records[:maximum]:
            try:
                minute = datetime.strptime(str(record.get("cntr_tm", "")), "%Y%m%d%H%M%S").replace(second=0)
                values = tuple(_index_value(record.get(key)) for key in ("open_pric", "high_pric", "low_pric", "cur_prc"))
                if any(value is None for value in values): continue
                result[(market, minute)] = (values[0], values[1], values[2], values[3], None)  # type: ignore[arg-type]
            except ValueError:
                continue
        return result

    def load_daily(self, market: str, base: datetime) -> tuple[tuple[object, ...], ...]:
        body = {"inds_cd": self.CODES[market], "base_dt": base.strftime("%Y%m%d")}
        response = self._client.request("ka20006", "/api/dostk/chart", body)
        records = response.get("inds_dt_pole_qry", [])
        if not isinstance(records, list): raise ValueError("ka20006 업종 일봉 형식이 올바르지 않습니다.")
        rows = []
        for record in records:
            try:
                day = datetime.strptime(str(record.get("dt", "")), "%Y%m%d")
                o, h, l, c = (_index_value(record.get(key)) for key in ("open_pric", "high_pric", "low_pric", "cur_prc"))
                if None in (o, h, l, c): continue
                volume = abs(int(str(record.get("trde_qty", 0)).replace(",", "")))
                trade_value = abs(float(str(record.get("trde_prica", 0)).replace(",", ""))) / 100
                rows.append((day.isoformat(timespec="minutes"), o, h, l, c, volume, trade_value, "market_index_daily"))
            except (TypeError, ValueError): continue
        return tuple(sorted(rows, key=lambda row: str(row[0]))[-250:])

    def _request(self, body: dict[str, Any], cont_yn: str = "N", next_key: str = "") -> tuple[dict[str, Any], bool, str]:
        continuation = getattr(self._client, "request_with_continuation", None)
        if callable(continuation):
            response, has_next, key = continuation("ka20005", "/api/dostk/chart", body, cont_yn=cont_yn, next_key=next_key)
            return response, bool(has_next), str(key or "")
        return self._client.request("ka20005", "/api/dostk/chart", body), False, ""

    @staticmethod
    def _records(response: dict[str, Any]) -> list[dict[str, Any]]:
        rows = response.get("inds_min_pole_qry", [])
        if not isinstance(rows, list): raise ValueError("ka20005 업종 분봉 형식이 올바르지 않습니다.")
        return rows


def _index_value(value: object) -> float | None:
    try: return abs(float(str(value).strip().replace(",", ""))) / 100
    except (TypeError, ValueError): return None
