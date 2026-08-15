from __future__ import annotations
import sqlite3
from pathlib import Path

class ThemeRepository:
    def __init__(self, path: Path) -> None: self._path=path
    def themes_for_stock(self, code: str) -> tuple[str, ...]:
        con=sqlite3.connect(self._path)
        try: rows=con.execute("SELECT t.theme_name FROM stock_themes st JOIN themes t ON t.theme_id=st.theme_id WHERE st.stock_code=? ORDER BY t.theme_name",(code,)).fetchall()
        finally: con.close()
        return tuple(str(row[0]) for row in rows)

    def find_code_by_name(self, name: str) -> str | None:
        con=sqlite3.connect(self._path)
        try: row=con.execute("SELECT code FROM stocks WHERE name=?",(name,)).fetchone()
        finally: con.close()
        return str(row[0]) if row else None

    def replace_for_stock(self, code: str, themes: tuple[str, ...]) -> None:
        con=sqlite3.connect(self._path)
        try:
            con.execute("DELETE FROM stock_themes WHERE stock_code=?",(code,))
            palette=("#DCE6F1", "#FFF2CC", "#E2F0D9", "#FCE4D6", "#E4DFEC")
            for name in themes:
                color=palette[sum(map(ord,name)) % len(palette)]
                con.execute("INSERT OR IGNORE INTO themes(theme_name,default_color) VALUES(?,?)",(name,color))
                theme_id=con.execute("SELECT theme_id FROM themes WHERE theme_name=?",(name,)).fetchone()[0]
                con.execute("INSERT INTO stock_themes(stock_code,theme_id) VALUES(?,?)",(code,theme_id))
            con.commit()
        finally: con.close()

    def all_by_name(self) -> dict[str, str]:
        con=sqlite3.connect(self._path)
        try:
            rows=con.execute("SELECT s.name, GROUP_CONCAT(t.theme_name, ', ') FROM stocks s JOIN stock_themes st ON st.stock_code=s.code JOIN themes t ON t.theme_id=st.theme_id GROUP BY s.code, s.name").fetchall()
        finally: con.close()
        return {"".join(str(name).split()): str(themes) for name,themes in rows}

    def search(self, text: str = "") -> tuple[tuple[str, str, str], ...]:
        con=sqlite3.connect(self._path)
        try:
            rows=con.execute("SELECT s.code,s.name,GROUP_CONCAT(t.theme_name, ', ') FROM stocks s LEFT JOIN stock_themes st ON st.stock_code=s.code LEFT JOIN themes t ON t.theme_id=st.theme_id WHERE s.name LIKE ? GROUP BY s.code,s.name ORDER BY s.name",(f"%{text}%",)).fetchall()
        finally: con.close()
        return tuple((str(code),str(name),str(themes or "")) for code,name,themes in rows)

    def color_for_theme(self, name: str) -> str:
        con=sqlite3.connect(self._path)
        try: row=con.execute("SELECT default_color FROM themes WHERE theme_name=?",(name,)).fetchone()
        finally: con.close()
        return str(row[0]) if row else "#DCE6F1"

    def color_for_stock_theme(self, code: str, name: str) -> str:
        con=sqlite3.connect(self._path)
        try:
            row=con.execute("SELECT COALESCE(st.custom_color, t.default_color) FROM stock_themes st JOIN themes t ON t.theme_id=st.theme_id WHERE st.stock_code=? AND t.theme_name=?",(code,name)).fetchone()
        finally: con.close()
        return str(row[0]) if row else "#DCE6F1"

    def list_themes(self) -> tuple[tuple[str, str], ...]:
        con=sqlite3.connect(self._path)
        try: rows=con.execute("SELECT theme_name, default_color FROM themes ORDER BY theme_name").fetchall()
        finally: con.close()
        return tuple((str(name), str(color)) for name, color in rows)

    def set_color(self, name: str, color: str) -> None:
        con=sqlite3.connect(self._path)
        try: con.execute("UPDATE themes SET default_color=? WHERE theme_name=?",(color,name)); con.commit()
        finally: con.close()

    def set_stock_theme_color(self, code: str, name: str, color: str) -> None:
        con=sqlite3.connect(self._path)
        try:
            con.execute("UPDATE stock_themes SET custom_color=? WHERE stock_code=? AND theme_id=(SELECT theme_id FROM themes WHERE theme_name=?)",(color,code,name)); con.commit()
        finally: con.close()
