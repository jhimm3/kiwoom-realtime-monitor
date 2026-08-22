from __future__ import annotations
import sqlite3
from difflib import get_close_matches
from pathlib import Path

class ThemeRepository:
    def __init__(self, path: Path) -> None:
        self._path=path
        self._merge_case_insensitive_themes()

    def _merge_case_insensitive_themes(self) -> None:
        """기존 DB에 이미 저장된 AI/ai 같은 중복 테마를 하나로 합친다."""
        con=sqlite3.connect(self._path)
        try:
            themes=con.execute("SELECT theme_id, theme_name FROM themes ORDER BY theme_id").fetchall()
            canonical: dict[str, int] = {}
            for duplicate_id, name in themes:
                key=str(name).casefold()
                if key not in canonical:
                    canonical[key]=int(duplicate_id)
                    continue
                target_id=canonical[key]
                assignments=con.execute(
                    "SELECT stock_code, custom_color FROM stock_themes WHERE theme_id=?", (duplicate_id,)
                ).fetchall()
                for code, color in assignments:
                    con.execute(
                        "INSERT OR IGNORE INTO stock_themes(stock_code, theme_id, custom_color) VALUES (?, ?, ?)",
                        (code, target_id, color),
                    )
                    if color:
                        con.execute(
                            "UPDATE stock_themes SET custom_color=COALESCE(custom_color, ?) WHERE stock_code=? AND theme_id=?",
                            (color, code, target_id),
                        )
                con.execute("DELETE FROM stock_themes WHERE theme_id=?", (duplicate_id,))
                con.execute("DELETE FROM themes WHERE theme_id=?", (duplicate_id,))
            con.commit()
        finally: con.close()
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

    def find_stock_candidates(self, name: str, limit: int = 8) -> tuple[tuple[str, str], ...]:
        query=name.strip()
        if not query:
            return ()
        con=sqlite3.connect(self._path)
        try:
            rows=con.execute("SELECT code, name FROM stocks WHERE name LIKE ? ORDER BY name LIMIT ?",(f"%{query}%",limit)).fetchall()
            if len(rows) < limit:
                all_rows=con.execute("SELECT code, name FROM stocks ORDER BY name").fetchall()
                used={str(code) for code, _ in rows}
                similar=set(get_close_matches(query,[str(stock_name) for _, stock_name in all_rows],n=limit,cutoff=0.35))
                rows += [row for row in all_rows if str(row[1]) in similar and str(row[0]) not in used]
        finally: con.close()
        return tuple((str(code),str(stock_name)) for code,stock_name in rows[:limit])

    def replace_for_stock(self, code: str, themes: tuple[str, ...]) -> None:
        con=sqlite3.connect(self._path)
        try:
            con.execute("DELETE FROM stock_themes WHERE stock_code=?",(code,))
            palette=("#DCE6F1", "#FFF2CC", "#E2F0D9", "#FCE4D6", "#E4DFEC")
            seen: set[str] = set()
            for raw_name in themes:
                name=raw_name.strip()
                if not name or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                color=palette[sum(map(ord,name)) % len(palette)]
                existing=con.execute("SELECT theme_id FROM themes WHERE theme_name=? COLLATE NOCASE ORDER BY theme_id LIMIT 1",(name,)).fetchone()
                if existing:
                    theme_id=existing[0]
                else:
                    con.execute("INSERT INTO themes(theme_name,default_color) VALUES(?,?)",(name,color))
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
        try: row=con.execute("SELECT default_color FROM themes WHERE theme_name=? COLLATE NOCASE",(name,)).fetchone()
        finally: con.close()
        return str(row[0]) if row else "#DCE6F1"

    def color_for_stock_theme(self, code: str, name: str) -> str:
        con=sqlite3.connect(self._path)
        try:
            row=con.execute("SELECT COALESCE(st.custom_color, t.default_color) FROM stock_themes st JOIN themes t ON t.theme_id=st.theme_id WHERE st.stock_code=? AND t.theme_name=? COLLATE NOCASE",(code,name)).fetchone()
        finally: con.close()
        return str(row[0]) if row else "#DCE6F1"

    def list_themes(self) -> tuple[tuple[str, str], ...]:
        con=sqlite3.connect(self._path)
        try: rows=con.execute("SELECT theme_name, default_color FROM themes ORDER BY theme_name").fetchall()
        finally: con.close()
        return tuple((str(name), str(color)) for name, color in rows)

    def set_color(self, name: str, color: str) -> None:
        con=sqlite3.connect(self._path)
        try: con.execute("UPDATE themes SET default_color=? WHERE theme_name=? COLLATE NOCASE",(color,name)); con.commit()
        finally: con.close()

    def set_stock_theme_color(self, code: str, name: str, color: str) -> None:
        con=sqlite3.connect(self._path)
        try:
            con.execute("UPDATE stock_themes SET custom_color=? WHERE stock_code=? AND theme_id=(SELECT theme_id FROM themes WHERE theme_name=? COLLATE NOCASE)",(color,code,name)); con.commit()
        finally: con.close()

    def delete_themes(self, names: tuple[str, ...]) -> None:
        con=sqlite3.connect(self._path)
        try:
            for name in names:
                row=con.execute("SELECT theme_id FROM themes WHERE theme_name=? COLLATE NOCASE", (name,)).fetchone()
                if row:
                    con.execute("DELETE FROM stock_themes WHERE theme_id=?", (row[0],))
                    con.execute("DELETE FROM themes WHERE theme_id=?", (row[0],))
            con.commit()
        finally: con.close()

    def rename_theme(self, before: str, after: str) -> None:
        after = after.strip()
        if not after:
            raise ValueError("새 테마명이 비어 있습니다.")
        con=sqlite3.connect(self._path)
        try:
            source=con.execute("SELECT theme_id FROM themes WHERE theme_name=? COLLATE NOCASE", (before,)).fetchone()
            if not source:
                return
            target=con.execute("SELECT theme_id FROM themes WHERE theme_name=? COLLATE NOCASE", (after,)).fetchone()
            if target and target[0] != source[0]:
                assignments=con.execute("SELECT stock_code, custom_color FROM stock_themes WHERE theme_id=?", (source[0],)).fetchall()
                for code, color in assignments:
                    con.execute("INSERT OR IGNORE INTO stock_themes(stock_code, theme_id, custom_color) VALUES (?, ?, ?)", (code, target[0], color))
                con.execute("DELETE FROM stock_themes WHERE theme_id=?", (source[0],))
                con.execute("DELETE FROM themes WHERE theme_id=?", (source[0],))
            else:
                con.execute("UPDATE themes SET theme_name=? WHERE theme_id=?", (after, source[0]))
            con.commit()
        finally: con.close()

    def clear_all_themes(self) -> None:
        con=sqlite3.connect(self._path)
        try:
            con.execute("DELETE FROM stock_themes")
            con.execute("DELETE FROM themes")
            con.commit()
        finally: con.close()
