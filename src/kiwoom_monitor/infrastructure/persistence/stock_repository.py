from __future__ import annotations
import sqlite3
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
    def find_code_by_name(self, name: str) -> str | None:
        con = sqlite3.connect(self._path)
        try:
            row=con.execute("SELECT code FROM stocks WHERE name=?",(name,)).fetchone()
            if row is None: row=con.execute("SELECT stock_code FROM stock_aliases WHERE alias=?",(name,)).fetchone()
        finally:
            con.close()
        return str(row[0]) if row else None
    def save_alias(self, alias: str, stock_code: str) -> None:
        con = sqlite3.connect(self._path)
        try:
            con.execute("INSERT INTO stock_aliases(alias, stock_code) VALUES (?, ?) ON CONFLICT(alias) DO UPDATE SET stock_code=excluded.stock_code", (alias, stock_code))
            con.commit()
        finally:
            con.close()
    def update_fundamentals(self, code: str, market_cap: float, float_ratio: float, high_250_price: int | None = None) -> None:
        con = sqlite3.connect(self._path)
        try:
            con.execute(
                "UPDATE stocks SET market_cap=?, float_ratio=?, circulating_market_cap=?, high_250_price=?, fundamentals_updated_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE code=?",
                (market_cap, float_ratio, market_cap * float_ratio / 100, high_250_price, code),
            )
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
                f"SELECT code, market_cap, float_ratio, high_250_price FROM stocks WHERE code IN ({placeholders}) AND market_cap IS NOT NULL AND float_ratio IS NOT NULL",
                codes,
            ).fetchall()
        finally:
            con.close()
        return {
            str(code): StockFundamentals(float(market_cap), float(float_ratio), int(high_price) if high_price else None)
            for code, market_cap, float_ratio, high_price in rows
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
            con.execute("UPDATE stocks SET nxt_enabled=?, nxt_checked_at=?, updated_at=CURRENT_TIMESTAMP WHERE code=?", (int(enabled), today, code))
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
