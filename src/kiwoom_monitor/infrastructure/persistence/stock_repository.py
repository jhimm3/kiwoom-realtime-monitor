from __future__ import annotations
import sqlite3
from difflib import get_close_matches
from pathlib import Path

from kiwoom_monitor.application.trade_strength import StockFundamentals

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
            changed = row is None or row[0] != market_cap or row[1] != float_ratio or row[2] != high_250_price or row[3] != float_shares
            if changed:
                con.execute(
                    "UPDATE stocks SET market_cap=?, float_ratio=?, float_shares=?, circulating_market_cap=?, high_250_price=?, fundamentals_updated_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE code=?",
                    (market_cap, float_ratio, float_shares, market_cap * float_ratio / 100, high_250_price, code),
                )
            else:
                # 값은 보존하고, 다음 날 재조회하지 않도록 확인 시각만 갱신한다.
                con.execute("UPDATE stocks SET fundamentals_updated_at=CURRENT_TIMESTAMP WHERE code=?", (code,))
            con.commit()
        finally:
            con.close()

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
