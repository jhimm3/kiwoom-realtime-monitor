from __future__ import annotations

import sqlite3
from difflib import get_close_matches
from pathlib import Path

from kiwoom_monitor.application.theme_matching import extract_known_stocks_and_unknown_fragments, split_concatenated_stock_name


class ThemeRepository:
    """Profile-scoped themes stored alongside the shared stock catalog."""

    def __init__(self, path: Path, profile_name: str = "기본 테마") -> None:
        self._path = path
        self._profile_name = profile_name.strip() or "기본 테마"
        self._ensure_active_profile()
        self._merge_case_insensitive_themes()

    @property
    def active_profile(self) -> str:
        return self._profile_name

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _profile_id(self, connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (self._profile_name,)).fetchone()
        if row is None:
            connection.execute("INSERT INTO theme_profiles(profile_name) VALUES (?)", (self._profile_name,))
            connection.commit()
            row = connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name=?", (self._profile_name,)).fetchone()
        return int(row[0])

    def _ensure_active_profile(self) -> None:
        connection = self._connect()
        try:
            self._profile_id(connection)
        finally:
            connection.close()

    def list_profiles(self) -> tuple[str, ...]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT profile_name FROM theme_profiles ORDER BY profile_name COLLATE NOCASE").fetchall()
        finally:
            connection.close()
        return tuple(str(row[0]) for row in rows)

    def select_profile(self, name: str) -> str:
        self._profile_name = name.strip() or "기본 테마"
        self._ensure_active_profile()
        self._merge_case_insensitive_themes()
        return self._profile_name

    def create_profile(self, name: str, *, copy_current: bool = False) -> str:
        clean = name.strip()
        if not clean:
            raise ValueError("프로필 이름을 입력하세요.")
        connection = self._connect()
        try:
            if connection.execute("SELECT 1 FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (clean,)).fetchone():
                raise ValueError("같은 이름의 테마 프로필이 이미 있습니다.")
            source_id = self._profile_id(connection)
            connection.execute("INSERT INTO theme_profiles(profile_name) VALUES (?)", (clean,))
            target_id = int(connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name=?", (clean,)).fetchone()[0])
            if copy_current:
                connection.execute("INSERT INTO profile_themes(profile_id, theme_name, default_color) SELECT ?, theme_name, default_color FROM profile_themes WHERE profile_id=?", (target_id, source_id))
                connection.execute("INSERT INTO profile_stock_themes(profile_id, stock_code, theme_name, custom_color) SELECT ?, stock_code, theme_name, custom_color FROM profile_stock_themes WHERE profile_id=?", (target_id, source_id))
            connection.commit()
        finally:
            connection.close()
        return clean

    def delete_profile(self, name: str) -> None:
        connection = self._connect()
        try:
            if int(connection.execute("SELECT COUNT(*) FROM theme_profiles").fetchone()[0]) <= 1:
                raise ValueError("마지막 테마 프로필은 삭제할 수 없습니다.")
            row = connection.execute("SELECT profile_id FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (name,)).fetchone()
            if row is not None:
                connection.execute("DELETE FROM theme_profiles WHERE profile_id=?", (row[0],))
                connection.commit()
        finally:
            connection.close()

    def rename_profile(self, before: str, after: str) -> str:
        clean = after.strip()
        if not clean:
            raise ValueError("새 프로필 이름을 입력하세요.")
        connection = self._connect()
        try:
            source = connection.execute(
                "SELECT profile_id FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (before,)
            ).fetchone()
            if source is None:
                raise ValueError("변경할 테마 프로필을 찾을 수 없습니다.")
            duplicate = connection.execute(
                "SELECT profile_id FROM theme_profiles WHERE profile_name=? COLLATE NOCASE", (clean,)
            ).fetchone()
            if duplicate is not None and int(duplicate[0]) != int(source[0]):
                raise ValueError("같은 이름의 테마 프로필이 이미 있습니다.")
            connection.execute("UPDATE theme_profiles SET profile_name=? WHERE profile_id=?", (clean, source[0]))
            connection.commit()
        finally:
            connection.close()
        if self._profile_name.casefold() == before.strip().casefold():
            self._profile_name = clean
        return clean

    def _merge_case_insensitive_themes(self) -> None:
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            rows = connection.execute("SELECT theme_name FROM profile_themes WHERE profile_id=? ORDER BY theme_name", (profile_id,)).fetchall()
            canonical: dict[str, str] = {}
            for (raw_name,) in rows:
                name = str(raw_name)
                key = name.casefold()
                if key not in canonical:
                    canonical[key] = name
                    continue
                target = canonical[key]
                connection.execute("INSERT OR IGNORE INTO profile_stock_themes(profile_id, stock_code, theme_name, custom_color) SELECT ?, stock_code, ?, custom_color FROM profile_stock_themes WHERE profile_id=? AND theme_name=?", (profile_id, target, profile_id, name))
                connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=? AND theme_name=?", (profile_id, name))
                connection.execute("DELETE FROM profile_themes WHERE profile_id=? AND theme_name=?", (profile_id, name))
            connection.commit()
        finally:
            connection.close()

    def themes_for_stock(self, code: str) -> tuple[str, ...]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT theme_name FROM profile_stock_themes WHERE profile_id=? AND stock_code=? ORDER BY theme_name", (self._profile_id(connection), code)).fetchall()
        finally:
            connection.close()
        return tuple(str(row[0]) for row in rows)

    def find_code_by_name(self, name: str) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT code FROM stocks WHERE name=?", (name,)).fetchone()
            if row is None:
                row = connection.execute("SELECT stock_code FROM stock_aliases WHERE alias=?", (name,)).fetchone()
        finally:
            connection.close()
        return str(row[0]) if row else None

    def pending_name_change(self, old_name: str) -> tuple[str, str, str, str] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT stock_code,old_name,new_name,source FROM stock_name_history "
                "WHERE old_name=? AND decision='pending'",
                (old_name.strip(),),
            ).fetchone()
        finally:
            connection.close()
        return tuple(str(value) for value in row) if row is not None else None  # type: ignore[return-value]

    def review_name_changes(self, decisions: dict[tuple[str, str], bool]) -> None:
        if not decisions:
            return
        connection = self._connect()
        try:
            with connection:
                for (stock_code, old_name), approved in decisions.items():
                    row = connection.execute(
                        "SELECT 1 FROM stock_name_history WHERE stock_code=? AND old_name=? AND decision='pending'",
                        (stock_code, old_name),
                    ).fetchone()
                    if row is None:
                        continue
                    if approved:
                        owner = connection.execute("SELECT code FROM stocks WHERE name=? AND code<>?", (old_name, stock_code)).fetchone()
                        if owner is None:
                            connection.execute(
                                "INSERT INTO stock_aliases(alias,stock_code) VALUES(?,?) "
                                "ON CONFLICT(alias) DO UPDATE SET stock_code=excluded.stock_code",
                                (old_name, stock_code),
                            )
                    connection.execute(
                        "UPDATE stock_name_history SET decision=? WHERE stock_code=? AND old_name=?",
                        ("approved" if approved else "rejected", stock_code, old_name),
                    )
        finally:
            connection.close()

    def find_stock_candidates(self, name: str, limit: int = 8) -> tuple[tuple[str, str], ...]:
        query = name.strip()
        if not query:
            return ()
        connection = self._connect()
        try:
            rows = connection.execute("SELECT code, name FROM stocks WHERE name LIKE ? ORDER BY name LIMIT ?", (f"%{query}%", limit)).fetchall()
            if len(rows) < limit:
                all_rows = connection.execute("SELECT code, name FROM stocks ORDER BY name").fetchall()
                used = {str(code) for code, _ in rows}
                similar = set(get_close_matches(query, [str(stock_name) for _, stock_name in all_rows], n=limit, cutoff=0.35))
                rows += [row for row in all_rows if str(row[1]) in similar and str(row[0]) not in used]
        finally:
            connection.close()
        return tuple((str(code), str(stock_name)) for code, stock_name in rows[:limit])

    def find_concatenated_stocks(self, name: str) -> tuple[tuple[str, str], ...]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT code, name FROM stocks ORDER BY LENGTH(name) DESC, name").fetchall()
        finally:
            connection.close()
        return split_concatenated_stock_name(
            name, tuple((str(code), str(stock_name)) for code, stock_name in rows)
        )

    def find_partial_concatenated_stocks(self, name: str) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT code, name FROM stocks ORDER BY LENGTH(name) DESC, name").fetchall()
        finally:
            connection.close()
        return extract_known_stocks_and_unknown_fragments(
            name, tuple((str(code), str(stock_name)) for code, stock_name in rows)
        )

    def replace_for_stock(self, code: str, themes: tuple[str, ...]) -> None:
        palette = ("#DCE6F1", "#FFF2CC", "#E2F0D9", "#FCE4D6", "#E4DFEC")
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=? AND stock_code=?", (profile_id, code))
            seen: set[str] = set()
            for raw_name in themes:
                name = raw_name.strip()
                if not name or name.casefold() in seen:
                    continue
                seen.add(name.casefold())
                color = palette[sum(map(ord, name)) % len(palette)]
                connection.execute("INSERT OR IGNORE INTO profile_themes(profile_id, theme_name, default_color) VALUES (?, ?, ?)", (profile_id, name, color))
                connection.execute("INSERT INTO profile_stock_themes(profile_id, stock_code, theme_name) VALUES (?, ?, ?)", (profile_id, code, name))
            connection.commit()
        finally:
            connection.close()

    def all_by_name(self) -> dict[str, str]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT s.name, GROUP_CONCAT(st.theme_name, ', ') FROM stocks s JOIN profile_stock_themes st ON st.stock_code=s.code AND st.profile_id=? GROUP BY s.code, s.name", (self._profile_id(connection),)).fetchall()
        finally:
            connection.close()
        return {"".join(str(name).split()): str(themes) for name, themes in rows}

    def search(self, text: str = "") -> tuple[tuple[str, str, str], ...]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT s.code, s.name, GROUP_CONCAT(st.theme_name, ', ') FROM stocks s LEFT JOIN profile_stock_themes st ON st.stock_code=s.code AND st.profile_id=? WHERE s.name LIKE ? GROUP BY s.code, s.name ORDER BY s.name", (self._profile_id(connection), f"%{text}%")).fetchall()
        finally:
            connection.close()
        return tuple((str(code), str(name), str(themes or "")) for code, name, themes in rows)

    def color_for_theme(self, name: str) -> str:
        connection = self._connect()
        try:
            row = connection.execute("SELECT default_color FROM profile_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (self._profile_id(connection), name)).fetchone()
        finally:
            connection.close()
        return str(row[0]) if row else "#DCE6F1"

    def color_for_stock_theme(self, code: str, name: str) -> str:
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            row = connection.execute("SELECT COALESCE(st.custom_color, pt.default_color) FROM profile_stock_themes st JOIN profile_themes pt ON pt.profile_id=st.profile_id AND pt.theme_name=st.theme_name WHERE st.profile_id=? AND st.stock_code=? AND st.theme_name=? COLLATE NOCASE", (profile_id, code, name)).fetchone()
        finally:
            connection.close()
        return str(row[0]) if row else "#DCE6F1"

    def list_themes(self) -> tuple[tuple[str, str], ...]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT theme_name, default_color FROM profile_themes WHERE profile_id=? ORDER BY theme_name", (self._profile_id(connection),)).fetchall()
        finally:
            connection.close()
        return tuple((str(name), str(color)) for name, color in rows)

    def set_color(self, name: str, color: str) -> None:
        connection = self._connect()
        try:
            connection.execute("UPDATE profile_themes SET default_color=? WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (color, self._profile_id(connection), name))
            connection.commit()
        finally:
            connection.close()

    def set_stock_theme_color(self, code: str, name: str, color: str) -> None:
        connection = self._connect()
        try:
            connection.execute("UPDATE profile_stock_themes SET custom_color=? WHERE profile_id=? AND stock_code=? AND theme_name=? COLLATE NOCASE", (color, self._profile_id(connection), code, name))
            connection.commit()
        finally:
            connection.close()

    def delete_themes(self, names: tuple[str, ...]) -> None:
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            for name in names:
                connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (profile_id, name))
                connection.execute("DELETE FROM profile_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (profile_id, name))
            connection.commit()
        finally:
            connection.close()

    def rename_theme(self, before: str, after: str) -> None:
        after = after.strip()
        if not after:
            raise ValueError("새 테마명이 비어 있습니다.")
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            source = connection.execute("SELECT 1 FROM profile_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (profile_id, before)).fetchone()
            if not source:
                return
            target = connection.execute("SELECT 1 FROM profile_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (profile_id, after)).fetchone()
            if target:
                connection.execute("INSERT OR IGNORE INTO profile_stock_themes(profile_id, stock_code, theme_name, custom_color) SELECT profile_id, stock_code, ?, custom_color FROM profile_stock_themes WHERE profile_id=? AND theme_name=?", (after, profile_id, before))
                connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=? AND theme_name=?", (profile_id, before))
                connection.execute("DELETE FROM profile_themes WHERE profile_id=? AND theme_name=?", (profile_id, before))
            else:
                # profile_stock_themes가 profile_themes의 복합 키를 참조한다.
                # 부모/자식 이름을 한 문장으로 동시에 바꿀 수 없으므로 커밋
                # 시점까지 FK 검사를 미뤄 두 UPDATE를 하나의 거래로 처리한다.
                connection.execute("PRAGMA defer_foreign_keys = ON")
                connection.execute("UPDATE profile_themes SET theme_name=? WHERE profile_id=? AND theme_name=?", (after, profile_id, before))
                connection.execute("UPDATE profile_stock_themes SET theme_name=? WHERE profile_id=? AND theme_name=?", (after, profile_id, before))
            connection.commit()
        finally:
            connection.close()

    def split_theme(self, before: str, targets: tuple[str, ...]) -> None:
        clean_targets: list[str] = []
        for raw_name in targets:
            name = raw_name.strip()
            if name and all(name.casefold() != existing.casefold() for existing in clean_targets):
                clean_targets.append(name)
        if not clean_targets:
            raise ValueError("나눌 새 테마명을 입력하세요.")
        if len(clean_targets) == 1:
            self.rename_theme(before, clean_targets[0])
            return

        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            source = connection.execute(
                "SELECT theme_name, default_color FROM profile_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE",
                (profile_id, before),
            ).fetchone()
            if source is None:
                return
            source_name, source_color = str(source[0]), str(source[1])
            keep_source = False
            for target in clean_targets:
                if target.casefold() == source_name.casefold():
                    keep_source = True
                    continue
                connection.execute(
                    "INSERT OR IGNORE INTO profile_themes(profile_id, theme_name, default_color) VALUES (?, ?, ?)",
                    (profile_id, target, source_color),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO profile_stock_themes(profile_id, stock_code, theme_name, custom_color) "
                    "SELECT profile_id, stock_code, ?, custom_color FROM profile_stock_themes "
                    "WHERE profile_id=? AND theme_name=?",
                    (target, profile_id, source_name),
                )
            if not keep_source:
                connection.execute(
                    "DELETE FROM profile_stock_themes WHERE profile_id=? AND theme_name=?",
                    (profile_id, source_name),
                )
                connection.execute(
                    "DELETE FROM profile_themes WHERE profile_id=? AND theme_name=?",
                    (profile_id, source_name),
                )
            connection.commit()
        finally:
            connection.close()

    def clear_all_themes(self) -> None:
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=?", (profile_id,))
            connection.execute("DELETE FROM profile_themes WHERE profile_id=?", (profile_id,))
            connection.commit()
        finally:
            connection.close()
