from __future__ import annotations
import sqlite3
from difflib import get_close_matches
from pathlib import Path

from kiwoom_monitor.application.trade_strength import StockFundamentals
from kiwoom_monitor.application.historical_high_service import HistoricalHighCache, HistoricalHighEvidence, HistoricalHighTarget

class StockRepository:
    def __init__(self, path: Path) -> None: self._path = path
    def upsert(self, code: str, name: str, market: str = "") -> None:
        con = sqlite3.connect(self._path)
        try:
            con.execute("INSERT INTO stocks(code,name,market,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market", (code,name,market))
            con.commit()
        finally:
            con.close()
    def upsert_many(self, stocks: tuple[tuple[str, str, str], ...]) -> None:
        if not stocks:
            return
        con = sqlite3.connect(self._path)
        try:
            con.executemany(
                "INSERT INTO stocks(code,name,market,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, updated_at=CURRENT_TIMESTAMP",
                stocks,
            )
            con.commit()
        finally:
            con.close()
    def find_code_by_name(self, name: str) -> str | None:
        con = sqlite3.connect(self._path)
        try:
            row=con.execute("SELECT code FROM stocks WHERE name=?",(name,)).fetchone()
            if row is None: row=con.execute("SELECT stock_code FROM stock_aliases WHERE alias=?",(name,)).fetchone()
        finally:
            con.close()
        return str(row[0]) if row else None
    def find_stock_candidates(self, name: str, limit: int = 8) -> tuple[tuple[str, str], ...]:
        query = name.strip()
        if not query:
            return ()
        con = sqlite3.connect(self._path)
        try:
            rows = con.execute("SELECT code, name FROM stocks WHERE name LIKE ? ORDER BY name LIMIT ?", (f"%{query}%", limit)).fetchall()
            if len(rows) < limit:
                all_rows = con.execute("SELECT code, name FROM stocks ORDER BY name").fetchall()
                used = {str(code) for code, _ in rows}
                names = [str(stock_name) for _, stock_name in all_rows]
                similar = set(get_close_matches(query, names, n=limit, cutoff=0.35))
                rows += [row for row in all_rows if str(row[1]) in similar and str(row[0]) not in used]
        finally:
            con.close()
        return tuple((str(code), str(stock_name)) for code, stock_name in rows[:limit])
    def save_alias(self, alias: str, stock_code: str) -> None:
        con = sqlite3.connect(self._path)
        try:
            con.execute("INSERT INTO stock_aliases(alias, stock_code) VALUES (?, ?) ON CONFLICT(alias) DO UPDATE SET stock_code=excluded.stock_code", (alias, stock_code))
            con.commit()
        finally:
            con.close()
    def update_fundamentals(
        self,
        code: str,
        market_cap: float,
        float_ratio: float,
        high_250_price: int | None = None,
        float_shares: int | None = None,
    ) -> None:
        con = sqlite3.connect(self._path)
        try:
            row = con.execute("SELECT market_cap, float_ratio, high_250_price, float_shares FROM stocks WHERE code=?", (code,)).fetchone()
            saved_high_250_price = high_250_price if high_250_price is not None else (int(row[2]) if row is not None and row[2] else None)
            changed = row is None or row[0] != market_cap or row[1] != float_ratio or row[2] != saved_high_250_price or row[3] != float_shares
            if changed:
                con.execute(
                    "UPDATE stocks SET market_cap=?, float_ratio=?, float_shares=?, circulating_market_cap=?, high_250_price=?, fundamentals_updated_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE code=?",
                    (market_cap, float_ratio, float_shares, market_cap * float_ratio / 100, saved_high_250_price, code),
                )
            else:
                # 값은 보존하고, 다음 날 재조회하지 않도록 확인 시각만 갱신한다.
                con.execute("UPDATE stocks SET fundamentals_updated_at=CURRENT_TIMESTAMP WHERE code=?", (code,))
            con.commit()
        finally:
            con.close()

    def update_adjusted_high_250_price(self, code: str, high_250_price: int | None) -> None:
        """ka10081 수정주가 기준 250일 최고가를 보관한다."""
        if not code or high_250_price is None or high_250_price <= 0:
            return
        con = sqlite3.connect(self._path)
        try:
            con.execute(
                "UPDATE stocks SET high_250_price=?, updated_at=CURRENT_TIMESTAMP WHERE code=?",
                (high_250_price, code),
            )
            con.commit()
        finally:
            con.close()

    def update_historical_high_price(self, code: str, price: int | None, first_year: int | None, last_year: int | None, checked_on: str, *, occurred_on: str | None = None, evidence: tuple[object, ...] = ()) -> None:
        """정밀 계산한 역사적 신고가와 계산 근거 봉을 함께 보관한다."""
        if not code or price is None or price <= 0:
            return
        con = sqlite3.connect(self._path)
        try:
            con.execute(
                "UPDATE stocks SET historical_high_price=?, historical_high_first_year=?, historical_high_last_year=?, historical_high_occurred_on=?, historical_high_checked_on=?, historical_high_updated_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE code=?",
                (price, first_year, last_year, occurred_on, checked_on, code),
            )
            con.execute("DELETE FROM historical_high_evidence WHERE stock_code=?", (code,))
            con.executemany(
                "INSERT OR REPLACE INTO historical_high_evidence(stock_code,period,trade_date,high_price,adjustment_types,adjustment_rate,adjustment_event) VALUES(?,?,?,?,?,?,?)",
                tuple(
                    (code, str(getattr(item, "period")), str(getattr(item, "trade_date")), int(getattr(item, "high_price")), str(getattr(item, "adjustment_types", "")), str(getattr(item, "adjustment_rate", "")), str(getattr(item, "adjustment_event", "")))
                    for item in evidence
                ),
            )
            con.commit()
        finally:
            con.close()

    def load_historical_high_prices(self, codes: tuple[str, ...]) -> dict[str, int]:
        if not codes:
            return {}
        placeholders = ",".join("?" for _ in codes)
        con = sqlite3.connect(self._path)
        try:
            rows = con.execute(
                f"SELECT code, historical_high_price FROM stocks WHERE code IN ({placeholders}) AND historical_high_price IS NOT NULL",
                codes,
            ).fetchall()
        finally:
            con.close()
        return {str(code): int(price) for code, price in rows if price is not None and int(price) > 0}

    def load_historical_high_cache(self, code: str) -> HistoricalHighCache | None:
        con = sqlite3.connect(self._path)
        try:
            row = con.execute(
                "SELECT historical_high_price,historical_high_first_year,historical_high_last_year,historical_high_occurred_on,historical_high_checked_on FROM stocks WHERE code=?",
                (code,),
            ).fetchone()
            evidence_rows = con.execute(
                "SELECT period,trade_date,high_price,adjustment_types,adjustment_rate,adjustment_event FROM historical_high_evidence WHERE stock_code=? ORDER BY trade_date",
                (code,),
            ).fetchall()
        finally:
            con.close()
        if row is None or row[0] is None or not row[4] or not evidence_rows:
            return None
        evidence = tuple(HistoricalHighEvidence(str(period), str(day), int(high), str(types), str(rate), str(event)) for period, day, high, types, rate, event in evidence_rows)
        target = HistoricalHighTarget(int(row[0]), int(row[1]) if row[1] else None, int(row[2]) if row[2] else None, str(row[3]) if row[3] else None, evidence)
        return HistoricalHighCache(target, str(row[4]))

    def load_high_250_price(self, code: str) -> int | None:
        con = sqlite3.connect(self._path)
        try:
            row = con.execute("SELECT high_250_price FROM stocks WHERE code=?", (code,)).fetchone()
        finally:
            con.close()
        return int(row[0]) if row is not None and row[0] is not None and int(row[0]) > 0 else None

    def historical_high_checked_today(self, codes: tuple[str, ...], checked_on: str) -> set[str]:
        if not codes:
            return set()
        placeholders = ",".join("?" for _ in codes)
        con = sqlite3.connect(self._path)
        try:
            rows = con.execute(
                f"SELECT code FROM stocks WHERE code IN ({placeholders}) AND historical_high_checked_on=?",
                (*codes, checked_on),
            ).fetchall()
        finally:
            con.close()
        return {str(code) for (code,) in rows}

    def load_fundamentals(self, codes: tuple[str, ...]) -> dict[str, StockFundamentals]:
        if not codes:
            return {}
        placeholders = ",".join("?" for _ in codes)
        con = sqlite3.connect(self._path)
        try:
            rows = con.execute(
                f"SELECT code, market_cap, float_ratio, high_250_price, float_shares FROM stocks WHERE code IN ({placeholders}) AND market_cap IS NOT NULL AND float_ratio IS NOT NULL",
                codes,
            ).fetchall()
        finally:
            con.close()
        return {
            str(code): StockFundamentals(float(market_cap), float(float_ratio), int(high_price) if high_price else None, int(float_shares) if float_shares else None)
            for code, market_cap, float_ratio, high_price, float_shares in rows
            if float(market_cap) > 0 and float(float_ratio) >= 0
        }

    def load_nxt_enabled(self, codes: tuple[str, ...], today: str) -> dict[str, bool]:
        if not codes:
            return {}
        placeholders = ",".join("?" for _ in codes)
        con = sqlite3.connect(self._path)
        try:
            rows = con.execute(f"SELECT code, nxt_enabled FROM stocks WHERE code IN ({placeholders}) AND nxt_checked_at=?", (*codes, today)).fetchall()
        finally:
            con.close()
        return {str(code): bool(enabled) for code, enabled in rows if enabled is not None}

    def update_nxt_enabled(self, code: str, enabled: bool, today: str) -> None:
        con = sqlite3.connect(self._path)
        try:
            row = con.execute("SELECT nxt_enabled FROM stocks WHERE code=?", (code,)).fetchone()
            if row is None or row[0] is None or bool(row[0]) != enabled:
                con.execute("UPDATE stocks SET nxt_enabled=?, nxt_checked_at=?, updated_at=CURRENT_TIMESTAMP WHERE code=?", (int(enabled), today, code))
            else:
                # NXT 가능 여부 값은 그대로 두고 오늘 확인했다는 정보만 남긴다.
                con.execute("UPDATE stocks SET nxt_checked_at=? WHERE code=?", (today, code))
            con.commit()
        finally:
            con.close()

    def load_last_prices(self, codes: tuple[str, ...]) -> dict[str, int]:
        """마지막 실시간 체결가를 불러와 장 종료 뒤에도 현재가로 표시한다."""
        if not codes:
            return {}
        placeholders = ",".join("?" for _ in codes)
        con = sqlite3.connect(self._path)
        try:
            rows = con.execute(
                f"SELECT code, last_price FROM stocks WHERE code IN ({placeholders}) AND last_price IS NOT NULL",
                codes,
            ).fetchall()
        finally:
            con.close()
        return {str(code): int(price) for code, price in rows if price is not None and int(price) > 0}

    def update_last_prices(self, prices: dict[str, int]) -> None:
        """실시간 체결로 받은 마지막 현재가를 묶어서 저장한다."""
        values = tuple((int(price), code) for code, price in prices.items() if code and int(price) > 0)
        if not values:
            return
        con = sqlite3.connect(self._path)
        try:
            con.executemany(
                "UPDATE stocks SET last_price=?, last_price_updated_at=CURRENT_TIMESTAMP WHERE code=?",
                values,
            )
            con.commit()
        finally:
            con.close()

    def load_new_highs(self, periods: tuple[int, ...]) -> dict[int, set[str]]:
        if not periods:
            return {}
        placeholders = ",".join("?" for _ in periods)
        con = sqlite3.connect(self._path)
        try:
            rows = con.execute(f"SELECT period, stock_code FROM new_high_snapshot WHERE period IN ({placeholders})", periods).fetchall()
        finally:
            con.close()
        result = {period: set() for period in periods}
        for period, code in rows:
            result.setdefault(int(period), set()).add(str(code))
        return result

    def update_new_highs(self, values: dict[int, set[str]], checked_at: str) -> None:
        """신고가 구성 종목이 바뀐 경우에만 추가·삭제하고 확인 시각은 항상 갱신한다."""
        con = sqlite3.connect(self._path)
        try:
            for period, current_values in values.items():
                existing = {str(row[0]) for row in con.execute("SELECT stock_code FROM new_high_snapshot WHERE period=?", (period,))}
                for code in existing - current_values:
                    con.execute("DELETE FROM new_high_snapshot WHERE period=? AND stock_code=?", (period, code))
                for code in current_values - existing:
                    con.execute("INSERT OR IGNORE INTO new_high_snapshot(period, stock_code) VALUES (?, ?)", (period, code))
                con.execute(
                    "INSERT INTO new_high_snapshot_meta(period, checked_at) VALUES (?, ?) ON CONFLICT(period) DO UPDATE SET checked_at=excluded.checked_at",
                    (period, checked_at),
                )
            con.commit()
        finally:
            con.close()

    def fundamentals_to_refresh(self, codes: tuple[str, ...], today: str) -> tuple[str, ...]:
        if not codes:
            return ()
        placeholders = ",".join("?" for _ in codes)
        con = sqlite3.connect(self._path)
        try:
            rows = con.execute(
                f"SELECT code, market_cap, float_ratio, high_250_price, fundamentals_updated_at FROM stocks WHERE code IN ({placeholders})",
                codes,
            ).fetchall()
        finally:
            con.close()
        cached = {str(code): (market_cap, float_ratio, high_price, updated_at) for code, market_cap, float_ratio, high_price, updated_at in rows}
        return tuple(
            code for code in codes
            if code not in cached
            or cached[code][0] is None
            or cached[code][1] is None
            or cached[code][2] is None
            or str(cached[code][3] or "")[:10] != today
        )
