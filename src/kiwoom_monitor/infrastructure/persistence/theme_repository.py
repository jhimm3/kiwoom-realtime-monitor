from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path
from typing import Callable

from kiwoom_monitor.application.theme_matching import extract_known_stocks_and_unknown_fragments, split_concatenated_stock_name
from kiwoom_monitor.application.theme_suggestions import ProfileThemeSuggestion, ThemeSuggestion


class ThemeRepository:
    """Profile-scoped themes stored alongside the shared stock catalog."""

    def __init__(self, path: Path, profile_name: str = "기본 테마") -> None:
        self._path = path
        self._profile_name = profile_name.strip() or "기본 테마"
        self._change_callback: Callable[[], None] | None = None
        self._stock_color_cache: dict[tuple[str, str], str] | None = None
        self._ensure_active_profile()
        self._merge_case_insensitive_themes()

    @property
    def active_profile(self) -> str:
        return self._profile_name

    def set_change_callback(self, callback: Callable[[], None] | None) -> None:
        self._change_callback = callback

    def _changed(self) -> None:
        self._stock_color_cache = None
        if self._change_callback is not None:
            self._change_callback()

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
        self._stock_color_cache = None
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
                connection.execute(
                    "INSERT INTO profile_theme_name_decisions(profile_id,decision_kind,source_name,target_name,decision_source,updated_at) "
                    "SELECT ?,decision_kind,source_name,target_name,decision_source,updated_at "
                    "FROM profile_theme_name_decisions WHERE profile_id=?",
                    (target_id, source_id),
                )
                connection.execute(
                    "INSERT INTO profile_theme_suggestions("
                    "profile_id,stock_code,news_identity,raw_theme_name,evidence,confidence,provider,model,"
                    "body_hash,analyzed_at,status,reviewed_theme_names,reviewed_at) "
                    "SELECT ?,stock_code,news_identity,raw_theme_name,evidence,confidence,provider,model,"
                    "body_hash,analyzed_at,status,reviewed_theme_names,reviewed_at "
                    "FROM profile_theme_suggestions WHERE profile_id=?",
                    (target_id, source_id),
                )
            connection.commit()
        finally:
            connection.close()
        self._changed()
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
        self._changed()

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
        self._changed()
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
        self.replace_many(((code, themes),))

    def replace_many(self, changes: tuple[tuple[str, tuple[str, ...]], ...]) -> None:
        """Apply an import as one transaction instead of reopening SQLite per stock."""
        if not changes:
            return
        palette = ("#DCE6F1", "#FFF2CC", "#E2F0D9", "#FCE4D6", "#E4DFEC")
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            for code, themes in changes:
                connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=? AND stock_code=?", (profile_id, code))
                seen: set[str] = set()
                for raw_name in themes:
                    for name in self._resolve_theme_names(connection, profile_id, raw_name):
                        if name.casefold() in seen:
                            continue
                        seen.add(name.casefold())
                        color = palette[sum(map(ord, name)) % len(palette)]
                        connection.execute("INSERT OR IGNORE INTO profile_themes(profile_id, theme_name, default_color) VALUES (?, ?, ?)", (profile_id, name, color))
                        connection.execute("INSERT INTO profile_stock_themes(profile_id, stock_code, theme_name) VALUES (?, ?, ?)", (profile_id, code, name))
            connection.commit()
        finally:
            connection.close()
        self._changed()

    def all_by_name(self) -> dict[str, str]:
        # External theme snapshot restores can update this database without using
        # this repository's write methods; a full list reload resets badge colors.
        self._stock_color_cache = None
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
        if self._stock_color_cache is None:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT st.stock_code, st.theme_name, COALESCE(st.custom_color, pt.default_color) "
                    "FROM profile_stock_themes st JOIN profile_themes pt "
                    "ON pt.profile_id=st.profile_id AND pt.theme_name=st.theme_name "
                    "WHERE st.profile_id=?",
                    (self._profile_id(connection),),
                ).fetchall()
            finally:
                connection.close()
            self._stock_color_cache = {
                (str(stock_code), str(theme_name).casefold()): str(color)
                for stock_code, theme_name, color in rows
            }
        return self._stock_color_cache.get((code, name.casefold()), "#DCE6F1")

    def list_themes(self) -> tuple[tuple[str, str], ...]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT theme_name, default_color FROM profile_themes WHERE profile_id=? ORDER BY theme_name", (self._profile_id(connection),)).fetchall()
        finally:
            connection.close()
        return tuple((str(name), str(color)) for name, color in rows)

    def resolve_theme_names(self, name: str) -> tuple[str, ...]:
        """Resolve one imported/LLM label through this profile's user decisions."""
        connection = self._connect()
        try:
            return self._resolve_theme_names(connection, self._profile_id(connection), name)
        finally:
            connection.close()

    def theme_name_decisions(self) -> tuple[tuple[str, str, str, str], ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT decision_kind,source_name,target_name,decision_source "
                "FROM profile_theme_name_decisions WHERE profile_id=? "
                "ORDER BY decision_kind,source_name,target_name",
                (self._profile_id(connection),),
            ).fetchall()
        finally:
            connection.close()
        return tuple(tuple(str(value) for value in row) for row in rows)  # type: ignore[return-value]

    def import_ai_theme_suggestions(self, suggestions: tuple[ThemeSuggestion, ...]) -> int:
        if not suggestions:
            return 0
        connection = self._connect()
        imported = 0
        try:
            profile_id = self._profile_id(connection)
            with connection:
                for value in suggestions:
                    if not value.stock_code or not value.news_identity or not value.raw_theme_name.strip():
                        continue
                    cursor = connection.execute(
                        "INSERT INTO profile_theme_suggestions("
                        "profile_id,stock_code,news_identity,raw_theme_name,evidence,confidence,provider,model,"
                        "body_hash,analyzed_at,article_title,article_published_at,article_url) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(profile_id,stock_code,news_identity,raw_theme_name) DO UPDATE SET "
                        "evidence=excluded.evidence,confidence=excluded.confidence,provider=excluded.provider,"
                        "model=excluded.model,body_hash=excluded.body_hash,analyzed_at=excluded.analyzed_at,"
                        "article_title=excluded.article_title,"
                        "article_published_at=excluded.article_published_at,article_url=excluded.article_url",
                        (profile_id, value.stock_code, value.news_identity, value.raw_theme_name.strip(),
                         value.evidence, max(0, min(100, value.confidence)), value.provider, value.model,
                         value.body_hash, value.analyzed_at.isoformat(), value.article_title,
                         value.article_published_at.isoformat() if value.article_published_at else "",
                         value.article_url),
                    )
                    imported += max(0, cursor.rowcount)
        finally:
            connection.close()
        return imported

    def list_ai_theme_suggestions(self, status: str = "pending") -> tuple[ProfileThemeSuggestion, ...]:
        if status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("지원하지 않는 테마 제안 상태입니다.")
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            where = "" if status == "all" else "AND p.status=?"
            parameters: tuple[object, ...] = (profile_id,) if status == "all" else (profile_id, status)
            rows = connection.execute(
                "SELECT p.stock_code,COALESCE(s.name,p.stock_code),p.news_identity,p.raw_theme_name,"
                "p.evidence,p.confidence,p.status,p.provider,p.model,p.analyzed_at,p.reviewed_theme_names,"
                "p.article_title,p.article_published_at,p.article_url "
                "FROM profile_theme_suggestions p LEFT JOIN stocks s ON s.code=p.stock_code "
                f"WHERE p.profile_id=? {where} ORDER BY p.analyzed_at DESC,p.stock_code,p.raw_theme_name",
                parameters,
            ).fetchall()
            result: list[ProfileThemeSuggestion] = []
            for row in rows:
                reviewed = tuple(map(str, json.loads(str(row[10]))))
                resolved = reviewed or self._resolve_theme_names(connection, profile_id, str(row[3]))
                result.append(ProfileThemeSuggestion(
                    str(row[0]), str(row[1]), str(row[2]), str(row[3]), resolved,
                    str(row[4]), int(row[5]), str(row[6]), str(row[7]), str(row[8]),
                    datetime.fromisoformat(str(row[9])), str(row[11]),
                    _optional_datetime(row[12]), str(row[13]),
                ))
            return tuple(result)
        finally:
            connection.close()

    def review_ai_theme_suggestion(
        self, key: tuple[str, str, str], *, approved: bool,
        theme_names: tuple[str, ...] = (),
    ) -> None:
        stock_code, news_identity, raw_theme_name = key
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            row = connection.execute(
                "SELECT status FROM profile_theme_suggestions WHERE profile_id=? AND stock_code=? "
                "AND news_identity=? AND raw_theme_name=? COLLATE NOCASE",
                (profile_id, stock_code, news_identity, raw_theme_name),
            ).fetchone()
            if row is None:
                raise ValueError("검토할 AI 테마 제안을 찾을 수 없습니다.")
            clean: list[str] = []
            if approved:
                default_names = self._resolve_theme_names(connection, profile_id, raw_theme_name)
                proposed = theme_names or default_names
                for name in proposed:
                    value = name.strip()
                    if value and all(value.casefold() != existing.casefold() for existing in clean):
                        clean.append(value)
                if not clean:
                    raise ValueError("승인할 테마명을 입력하세요.")
            with connection:
                if approved:
                    if theme_names and tuple(value.casefold() for value in clean) != tuple(
                        value.casefold() for value in default_names
                    ):
                        if len(clean) == 1:
                            self._record_alias(
                                connection, profile_id, raw_theme_name, clean[0], "user_review",
                            )
                            self._merge_theme_rows(
                                connection, profile_id, raw_theme_name, clean[0],
                            )
                        else:
                            connection.execute(
                                "DELETE FROM profile_theme_name_decisions WHERE profile_id=? "
                                "AND source_name=? COLLATE NOCASE AND decision_kind IN ('alias','split_to')",
                                (profile_id, raw_theme_name),
                            )
                            for target in clean:
                                self._insert_name_decision(
                                    connection, profile_id, "split_to", raw_theme_name,
                                    target, "user_review",
                                )
                            for index, first in enumerate(clean):
                                for second in clean[index + 1:]:
                                    left, right = self._ordered_pair(first, second)
                                    self._insert_name_decision(
                                        connection, profile_id, "keep_separate", left, right,
                                        "user_review",
                                    )
                    palette = ("#DCE6F1", "#FFF2CC", "#E2F0D9", "#FCE4D6", "#E4DFEC")
                    for name in clean:
                        color = palette[sum(map(ord, name)) % len(palette)]
                        connection.execute(
                            "INSERT OR IGNORE INTO profile_themes(profile_id,theme_name,default_color) VALUES(?,?,?)",
                            (profile_id, name, color),
                        )
                        connection.execute(
                            "INSERT OR IGNORE INTO profile_stock_themes(profile_id,stock_code,theme_name) VALUES(?,?,?)",
                            (profile_id, stock_code, name),
                        )
                connection.execute(
                    "UPDATE profile_theme_suggestions SET status=?,reviewed_theme_names=?,reviewed_at=CURRENT_TIMESTAMP "
                    "WHERE profile_id=? AND stock_code=? AND news_identity=? AND raw_theme_name=? COLLATE NOCASE",
                    ("approved" if approved else "rejected", json.dumps(clean, ensure_ascii=False),
                     profile_id, stock_code, news_identity, raw_theme_name),
                )
        finally:
            connection.close()
        self._changed()

    def keep_themes_separate(
        self, first: str, second: str, *, decision_source: str = "user",
    ) -> None:
        left, right = self._ordered_pair(first, second)
        if not left or not right or left.casefold() == right.casefold():
            raise ValueError("서로 다른 두 테마명을 입력하세요.")
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            with connection:
                connection.execute(
                    "DELETE FROM profile_theme_name_decisions WHERE profile_id=? "
                    "AND decision_kind='alias' AND ((source_name=? COLLATE NOCASE AND target_name=? COLLATE NOCASE) "
                    "OR (source_name=? COLLATE NOCASE AND target_name=? COLLATE NOCASE))",
                    (profile_id, left, right, right, left),
                )
                self._insert_name_decision(
                    connection, profile_id, "keep_separate", left, right, decision_source,
                )
        finally:
            connection.close()
        self._changed()

    def set_theme_alias(
        self, alias: str, canonical: str, *, decision_source: str = "user",
    ) -> None:
        alias, canonical = alias.strip(), canonical.strip()
        if not alias or not canonical or alias.casefold() == canonical.casefold():
            raise ValueError("서로 다른 별칭과 대표 테마명을 입력하세요.")
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            with connection:
                resolved = self._resolve_theme_names(connection, profile_id, canonical)
                if len(resolved) != 1:
                    raise ValueError("여러 테마로 분리된 이름은 대표 테마로 지정할 수 없습니다.")
                canonical = resolved[0]
                if alias.casefold() == canonical.casefold():
                    raise ValueError("순환하는 테마 별칭은 저장할 수 없습니다.")
                if self._is_kept_separate(connection, profile_id, alias, canonical):
                    raise ValueError("사용자가 분리 유지한 테마는 별칭으로 병합할 수 없습니다.")
                self._record_alias(
                    connection, profile_id, alias, canonical, decision_source,
                )
                self._merge_theme_rows(connection, profile_id, alias, canonical)
        finally:
            connection.close()
        self._changed()

    def set_color(self, name: str, color: str) -> None:
        connection = self._connect()
        try:
            connection.execute("UPDATE profile_themes SET default_color=? WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (color, self._profile_id(connection), name))
            connection.commit()
        finally:
            connection.close()
        self._changed()

    def set_stock_theme_color(self, code: str, name: str, color: str) -> None:
        connection = self._connect()
        try:
            connection.execute("UPDATE profile_stock_themes SET custom_color=? WHERE profile_id=? AND stock_code=? AND theme_name=? COLLATE NOCASE", (color, self._profile_id(connection), code, name))
            connection.commit()
        finally:
            connection.close()
        self._changed()

    def delete_themes(self, names: tuple[str, ...]) -> None:
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            for name in names:
                connection.execute(
                    "DELETE FROM profile_theme_name_decisions WHERE profile_id=? "
                    "AND (source_name=? COLLATE NOCASE OR target_name=? COLLATE NOCASE)",
                    (profile_id, name, name),
                )
                connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (profile_id, name))
                connection.execute("DELETE FROM profile_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE", (profile_id, name))
            connection.commit()
        finally:
            connection.close()
        self._changed()

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
            self._record_alias(connection, profile_id, before, after, "user")
            connection.commit()
        finally:
            connection.close()
        self._changed()

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
            connection.execute(
                "DELETE FROM profile_theme_name_decisions WHERE profile_id=? "
                "AND source_name=? COLLATE NOCASE AND decision_kind IN ('alias','split_to')",
                (profile_id, source_name),
            )
            keep_source = False
            for target in clean_targets:
                self._insert_name_decision(
                    connection, profile_id, "split_to", source_name, target, "user",
                )
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
            for index, first in enumerate(clean_targets):
                for second in clean_targets[index + 1:]:
                    left, right = self._ordered_pair(first, second)
                    self._insert_name_decision(
                        connection, profile_id, "keep_separate", left, right, "user",
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
        self._changed()

    def clear_all_themes(self) -> None:
        connection = self._connect()
        try:
            profile_id = self._profile_id(connection)
            connection.execute("DELETE FROM profile_theme_name_decisions WHERE profile_id=?", (profile_id,))
            connection.execute("DELETE FROM profile_stock_themes WHERE profile_id=?", (profile_id,))
            connection.execute("DELETE FROM profile_themes WHERE profile_id=?", (profile_id,))
            connection.commit()
        finally:
            connection.close()
        self._changed()

    @staticmethod
    def _ordered_pair(first: str, second: str) -> tuple[str, str]:
        values = sorted((first.strip(), second.strip()), key=lambda value: value.casefold())
        return values[0], values[1]

    @staticmethod
    def _insert_name_decision(
        connection: sqlite3.Connection, profile_id: int, kind: str,
        source: str, target: str, decision_source: str,
    ) -> None:
        connection.execute(
            "INSERT INTO profile_theme_name_decisions("
            "profile_id,decision_kind,source_name,target_name,decision_source,updated_at) "
            "VALUES(?,?,?,?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(profile_id,decision_kind,source_name,target_name) DO UPDATE SET "
            "decision_source=excluded.decision_source,updated_at=CURRENT_TIMESTAMP",
            (profile_id, kind, source.strip(), target.strip(), decision_source.strip() or "user"),
        )

    def _record_alias(
        self, connection: sqlite3.Connection, profile_id: int,
        alias: str, canonical: str, decision_source: str,
    ) -> None:
        resolved = self._resolve_theme_names(connection, profile_id, canonical)
        if any(alias.casefold() == target.casefold() for target in resolved):
            raise ValueError("순환하는 테마 별칭은 저장할 수 없습니다.")
        if len(resolved) != 1:
            raise ValueError("여러 테마로 분리된 이름은 대표 테마로 지정할 수 없습니다.")
        canonical = resolved[0]
        if self._is_kept_separate(connection, profile_id, alias, canonical):
            raise ValueError("사용자가 분리 유지한 테마는 별칭으로 병합할 수 없습니다.")
        connection.execute(
            "DELETE FROM profile_theme_name_decisions WHERE profile_id=? "
            "AND source_name=? COLLATE NOCASE AND decision_kind IN ('alias','split_to')",
            (profile_id, alias),
        )
        self._insert_name_decision(
            connection, profile_id, "alias", alias, canonical, decision_source,
        )

    @staticmethod
    def _is_kept_separate(
        connection: sqlite3.Connection, profile_id: int, first: str, second: str,
    ) -> bool:
        left, right = ThemeRepository._ordered_pair(first, second)
        return connection.execute(
            "SELECT 1 FROM profile_theme_name_decisions WHERE profile_id=? "
            "AND decision_kind='keep_separate' AND source_name=? COLLATE NOCASE "
            "AND target_name=? COLLATE NOCASE",
            (profile_id, left, right),
        ).fetchone() is not None

    @staticmethod
    def _merge_theme_rows(
        connection: sqlite3.Connection, profile_id: int, alias: str, canonical: str,
    ) -> None:
        source = connection.execute(
            "SELECT theme_name FROM profile_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE",
            (profile_id, alias),
        ).fetchone()
        if source is None:
            return
        target = connection.execute(
            "SELECT theme_name FROM profile_themes WHERE profile_id=? AND theme_name=? COLLATE NOCASE",
            (profile_id, canonical),
        ).fetchone()
        source_name = str(source[0])
        canonical_name = str(target[0]) if target is not None else canonical
        if target is None:
            color = connection.execute(
                "SELECT default_color FROM profile_themes WHERE profile_id=? AND theme_name=?",
                (profile_id, source_name),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO profile_themes(profile_id,theme_name,default_color) VALUES(?,?,?)",
                (profile_id, canonical_name, color),
            )
        connection.execute(
            "INSERT OR IGNORE INTO profile_stock_themes(profile_id,stock_code,theme_name,custom_color) "
            "SELECT profile_id,stock_code,?,custom_color FROM profile_stock_themes "
            "WHERE profile_id=? AND theme_name=?",
            (canonical_name, profile_id, source_name),
        )
        connection.execute(
            "DELETE FROM profile_stock_themes WHERE profile_id=? AND theme_name=?",
            (profile_id, source_name),
        )
        connection.execute(
            "DELETE FROM profile_themes WHERE profile_id=? AND theme_name=?",
            (profile_id, source_name),
        )

    @staticmethod
    def _resolve_theme_names(
        connection: sqlite3.Connection, profile_id: int, raw_name: str,
    ) -> tuple[str, ...]:
        start = raw_name.strip()
        if not start:
            return ()

        def resolve(name: str, seen: frozenset[str]) -> tuple[str, ...]:
            key = name.casefold()
            if key in seen:
                return (name,)
            next_seen = seen | {key}
            split_rows = connection.execute(
                "SELECT target_name FROM profile_theme_name_decisions WHERE profile_id=? "
                "AND decision_kind='split_to' AND source_name=? COLLATE NOCASE ORDER BY target_name",
                (profile_id, name),
            ).fetchall()
            if split_rows:
                values: list[str] = []
                for (target,) in split_rows:
                    values.extend(resolve(str(target), next_seen))
                return tuple(dict.fromkeys(values))
            alias = connection.execute(
                "SELECT target_name FROM profile_theme_name_decisions WHERE profile_id=? "
                "AND decision_kind='alias' AND source_name=? COLLATE NOCASE "
                "ORDER BY updated_at DESC,target_name LIMIT 1",
                (profile_id, name),
            ).fetchone()
            return resolve(str(alias[0]), next_seen) if alias is not None else (name,)

        return resolve(start, frozenset())


def _optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
