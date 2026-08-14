from __future__ import annotations
import sqlite3
from pathlib import Path

class StockRepository:
    def __init__(self, path: Path) -> None: self._path = path
    def upsert(self, code: str, name: str, market: str = "") -> None:
        con = sqlite3.connect(self._path)
        try:
            con.execute("INSERT INTO stocks(code,name,market,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(code) DO UPDATE SET name=excluded.name, market=excluded.market, updated_at=CURRENT_TIMESTAMP", (code,name,market))
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
    def update_fundamentals(self, code: str, market_cap: float, float_ratio: float) -> None:
        con = sqlite3.connect(self._path)
        try:
            con.execute(
                "UPDATE stocks SET market_cap=?, float_ratio=?, circulating_market_cap=?, updated_at=CURRENT_TIMESTAMP WHERE code=?",
                (market_cap, float_ratio, market_cap * float_ratio / 100, code),
            )
            con.commit()
        finally:
            con.close()
