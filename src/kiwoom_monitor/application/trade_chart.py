"""매매일지 차트용 OHLCV 봉 주기 변환."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class RestClient(Protocol):
    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class DailyTradeChartService:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def load(self, code: str, base_day: datetime) -> tuple[tuple[object, ...], ...]:
        response = self._client.request(
            "ka10081", "/api/dostk/chart",
            {"stk_cd": code, "base_dt": base_day.strftime("%Y%m%d"), "upd_stkpc_tp": "1"},
        )
        records = response.get("stk_dt_pole_chart_qry", response.get("stk_ddwkmm", []))
        if not isinstance(records, list):
            raise ValueError("일봉 목록 형식이 올바르지 않습니다.")
        rows: list[tuple[object, ...]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            day = str(record.get("date", record.get("dt", ""))).strip()
            if len(day) != 8 or not day.isdigit():
                continue
            try:
                open_price = abs(int(str(record.get("open_pric", "")).replace(",", "")))
                high_price = abs(int(str(record.get("high_pric", "")).replace(",", "")))
                low_price = abs(int(str(record.get("low_pric", "")).replace(",", "")))
                close_price = abs(int(str(record.get("cur_prc", "")).replace(",", "")))
                volume = abs(int(str(record.get("trde_qty", 0)).replace(",", "")))
                trade_value = abs(float(str(record.get("trde_prica", 0)).replace(",", ""))) / 100
            except (TypeError, ValueError):
                continue
            rows.append((
                datetime.strptime(day, "%Y%m%d").isoformat(timespec="minutes"), open_price, high_price,
                low_price, close_price, volume, trade_value, "daily_confirmed",
            ))
        return tuple(sorted(rows, key=lambda row: str(row[0]))[-250:])


def aggregate_chart_rows(
    rows: tuple[tuple[object, ...], ...], interval: str,
) -> tuple[tuple[object, ...], ...]:
    minutes = {"1분": 1, "3분": 3, "5분": 5, "10분": 10, "30분": 30, "60분": 60}.get(interval)
    if minutes == 1 or not rows:
        return rows
    grouped: dict[datetime, list[tuple[object, ...]]] = {}
    for row in rows:
        minute = datetime.fromisoformat(str(row[0]))
        if interval == "일봉":
            key = minute.replace(hour=0, minute=0, second=0, microsecond=0)
        elif minutes:
            total = minute.hour * 60 + minute.minute
            bucket = total - total % minutes
            key = minute.replace(hour=bucket // 60, minute=bucket % 60, second=0, microsecond=0)
        else:
            return rows
        grouped.setdefault(key, []).append(row)
    values: list[tuple[object, ...]] = []
    for key, bucket in sorted(grouped.items()):
        available_trade_values = [float(row[6]) for row in bucket if row[6] is not None]
        values.append((
            key.isoformat(timespec="minutes"), bucket[0][1],
            max(float(row[2]) for row in bucket), min(float(row[3]) for row in bucket), bucket[-1][4],
            sum(int(row[5]) for row in bucket),
            sum(available_trade_values) if available_trade_values else None,
            "확정" if all(str(row[7]) in ("api_confirmed", "after_close_confirmed", "확정") for row in bucket) else str(bucket[-1][7]),
        ))
    return tuple(values)
