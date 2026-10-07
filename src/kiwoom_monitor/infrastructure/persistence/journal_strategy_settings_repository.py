"""개인 매매원칙과 전략팩의 journal_settings 저장소."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.application.personal_trade_rules import StructuredTradeRule
from kiwoom_monitor.application.strategy_pack import MIMOSA_MANIFEST, StrategyPackManifest
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft, StrategyRuleDraft


class JournalStrategySettingsRepositoryMixin:
    _path: Path
    def save_personal_rules(self, rules: tuple[str, ...], now: datetime | None = None) -> None:
        """원본 문서가 이동되어도 복기에 쓸 수 있도록 추출한 원칙만 로컬 DB에 보관한다."""
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        payload = json.dumps(list(rules), ensure_ascii=False)
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO journal_settings VALUES ('personal_trade_rules', ?, ?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (payload, updated_at),
                )

    def load_strategy_packs(self) -> tuple[StrategyPackManifest, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT value_json FROM journal_settings WHERE setting_key='strategy_packs'"
            ).fetchone()
        if not row:
            self.save_strategy_packs((MIMOSA_MANIFEST,))
            return (MIMOSA_MANIFEST,)
        try:
            values = json.loads(str(row[0]))
            packs = tuple(StrategyPackManifest.from_dict(value) for value in values if isinstance(value, dict))
        except (TypeError, ValueError, json.JSONDecodeError):
            packs = ()
        if not any(pack.pack_id == MIMOSA_MANIFEST.pack_id for pack in packs):
            packs = (*packs, MIMOSA_MANIFEST)
            self.save_strategy_packs(packs)
        return tuple(sorted(packs, key=lambda pack: (not pack.enabled, pack.priority, pack.name)))

    def save_strategy_packs(self, packs: tuple[StrategyPackManifest, ...], now: datetime | None = None) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        payload = json.dumps([pack.to_dict() for pack in packs], ensure_ascii=False)
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO journal_settings VALUES ('strategy_packs', ?, ?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (payload, updated_at),
                )

    def save_strategy_pack_draft(self, pack_id: str, draft: ExtractedStrategyDraft, now: datetime | None = None) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        payload = json.dumps({
            "rules": [rule.to_dict() for rule in draft.rules],
            # 원문 파일이 이동되어도 재검토할 수 있도록 추출 텍스트만 로컬 DB에 보존한다.
            "source_texts": draft.source_texts,
        }, ensure_ascii=False)
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO journal_settings VALUES (?, ?, ?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (f"strategy_pack_draft:{pack_id}", payload, updated_at),
                )

    def load_strategy_pack_draft(self, pack_id: str) -> ExtractedStrategyDraft | None:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT value_json FROM journal_settings WHERE setting_key=?", (f"strategy_pack_draft:{pack_id}",),
            ).fetchone()
        if not row:
            return None
        try:
            value = json.loads(str(row[0]))
            rules = tuple(StrategyRuleDraft.from_dict(item) for item in value.get("rules", ()) if isinstance(item, dict))
            sources = {str(key): str(text) for key, text in dict(value.get("source_texts", {})).items()}
            return ExtractedStrategyDraft(rules, sources)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def delete_strategy_pack_draft(self, pack_id: str) -> None:
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute("DELETE FROM journal_settings WHERE setting_key=?", (f"strategy_pack_draft:{pack_id}",))

    def save_strategy_pack_version(
        self, pack: StrategyPackManifest, draft: ExtractedStrategyDraft | None,
        now: datetime | None = None,
    ) -> None:
        """강의 추가 전 상태를 복원 가능한 버전으로 보관한다."""
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        payload = json.dumps({
            "manifest": pack.to_dict(),
            "draft": None if draft is None else {
                "rules": [rule.to_dict() for rule in draft.rules],
                "source_texts": draft.source_texts,
            },
        }, ensure_ascii=False)
        key = f"strategy_pack_history:{pack.pack_id}:{pack.version}"
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO journal_settings VALUES (?, ?, ?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (key, payload, updated_at),
                )

    def load_strategy_pack_versions(
        self, pack_id: str,
    ) -> tuple[tuple[StrategyPackManifest, ExtractedStrategyDraft | None], ...]:
        prefix = f"strategy_pack_history:{pack_id}:"
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT value_json FROM journal_settings WHERE setting_key LIKE ?", (prefix + "%",),
            ).fetchall()
        result = []
        for row in rows:
            try:
                value = json.loads(str(row[0])); manifest = StrategyPackManifest.from_dict(value["manifest"])
                raw = value.get("draft")
                draft = None if not isinstance(raw, dict) else ExtractedStrategyDraft(
                    tuple(StrategyRuleDraft.from_dict(item) for item in raw.get("rules", ()) if isinstance(item, dict)),
                    {str(key): str(text) for key, text in dict(raw.get("source_texts", {})).items()},
                )
                result.append((manifest, draft))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return tuple(sorted(result, key=lambda item: item[0].version, reverse=True))

    def load_personal_rules(self) -> tuple[str, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT value_json FROM journal_settings WHERE setting_key='personal_trade_rules'"
            ).fetchone()
        if not row:
            return ()
        try:
            values = json.loads(str(row[0]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
        return tuple(str(value).strip() for value in values if str(value).strip()) if isinstance(values, list) else ()

    def save_structured_personal_rules(
        self, rules: tuple[StructuredTradeRule, ...], now: datetime | None = None,
    ) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        payload = json.dumps([rule.to_dict() for rule in rules], ensure_ascii=False)
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO journal_settings VALUES ('personal_trade_rules_structured', ?, ?) "
                    "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                    (payload, updated_at),
                )

    def load_structured_personal_rules(self) -> tuple[StructuredTradeRule, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT value_json FROM journal_settings WHERE setting_key='personal_trade_rules_structured'"
            ).fetchone()
        if not row:
            return ()
        try:
            values = json.loads(str(row[0]))
            return tuple(StructuredTradeRule.from_dict(value) for value in values if isinstance(value, dict))
        except (TypeError, ValueError, json.JSONDecodeError):
            return ()
