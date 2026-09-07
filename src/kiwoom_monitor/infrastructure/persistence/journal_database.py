"""매매일지 전용 SQLite 저장소."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from kiwoom_monitor.application.minute_trade_value import MinuteOhlcv
from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import TradeReview
from kiwoom_monitor.application.trade_cost_service import DailyTradeCost
from kiwoom_monitor.application.trade_setup_classification import TradeSetupClassification
from kiwoom_monitor.application.personal_trade_rules import StructuredTradeRule
from kiwoom_monitor.application.strategy_pack import MIMOSA_MANIFEST, StrategyPackManifest
from kiwoom_monitor.application.strategy_pack_extraction import ExtractedStrategyDraft, StrategyRuleDraft


@dataclass(frozen=True)
class BarBackfillState:
    trade_date: date
    stock_code: str
    state: str
    bar_count: int
    message: str = ""


@dataclass(frozen=True)
class TradeEntrySnapshot:
    execution_key: str
    order_no: str
    stock_code: str
    stock_name: str
    side: str
    executed_at: datetime
    price: int
    quantity: int
    market: str
    rank: int | None = None
    trade_value_1m_eok: float | None = None
    trade_value_5m_eok: float | None = None
    themes: tuple[str, ...] = ()
    theme_ranks: dict[str, int] | None = None
    high_distance_percent: float | None = None
    news: tuple[dict[str, object], ...] = ()
    investor_flow: dict[str, object] | None = None
    orderbook: dict[str, object] | None = None
    market_state: dict[str, object] | None = None
    capture_state: str = "realtime_partial"


def initialize_journal_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.executescript("""
        CREATE TABLE IF NOT EXISTS journal_minute_bars (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, minute TEXT NOT NULL,
            open_price INTEGER NOT NULL, high_price INTEGER NOT NULL,
            low_price INTEGER NOT NULL, close_price INTEGER NOT NULL,
            volume INTEGER NOT NULL, trade_value_eok REAL NOT NULL,
            source TEXT NOT NULL, confirmed_at TEXT,
            PRIMARY KEY(trade_date, stock_code, minute)
        );
        CREATE INDEX IF NOT EXISTS idx_journal_bars_code_date
            ON journal_minute_bars(stock_code, trade_date, minute);
        CREATE TABLE IF NOT EXISTS journal_stocks (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT 'opened', last_opened_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS trade_fills (
            order_no TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL,
            side TEXT NOT NULL, filled_at TEXT NOT NULL, quantity INTEGER NOT NULL,
            price INTEGER NOT NULL, order_type TEXT NOT NULL DEFAULT '', market TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(order_no, stock_code, filled_at, side)
        );
        CREATE INDEX IF NOT EXISTS idx_trade_fills_time ON trade_fills(filled_at DESC);
        CREATE TABLE IF NOT EXISTS trade_group_overrides (
            fill_key TEXT PRIMARY KEY, group_id TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trade_reviews (
            group_id TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '',
            review TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '',
            rating TEXT NOT NULL DEFAULT '보통', status TEXT NOT NULL DEFAULT '미작성',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_trade_costs (
            fill_date TEXT NOT NULL, settlement_date TEXT NOT NULL,
            stock_code TEXT NOT NULL, side TEXT NOT NULL,
            gross_amount INTEGER NOT NULL, settlement_amount INTEGER NOT NULL,
            commission INTEGER NOT NULL, tax INTEGER NOT NULL, total_cost INTEGER NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY(fill_date, stock_code, side)
        );
        CREATE TABLE IF NOT EXISTS journal_bar_backfill (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
            state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS journal_daily_bars (
            trade_date TEXT NOT NULL, stock_code TEXT NOT NULL,
            open_price INTEGER NOT NULL, high_price INTEGER NOT NULL,
            low_price INTEGER NOT NULL, close_price INTEGER NOT NULL,
            volume INTEGER NOT NULL, trade_value_eok REAL NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY(trade_date, stock_code)
        );
        CREATE TABLE IF NOT EXISTS journal_settings (
            setting_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trade_setup_classifications (
            group_id TEXT PRIMARY KEY, automatic_type TEXT NOT NULL, confidence INTEGER NOT NULL,
            evidence_json TEXT NOT NULL, manual_type TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trade_setup_cycle_overrides (
            group_id TEXT NOT NULL, cycle_index INTEGER NOT NULL,
            manual_type TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            PRIMARY KEY(group_id, cycle_index)
        );
        CREATE TABLE IF NOT EXISTS trade_entry_snapshots (
            execution_key TEXT PRIMARY KEY, order_no TEXT NOT NULL,
            stock_code TEXT NOT NULL, stock_name TEXT NOT NULL, side TEXT NOT NULL,
            executed_at TEXT NOT NULL, price INTEGER NOT NULL, quantity INTEGER NOT NULL,
            market TEXT NOT NULL DEFAULT '', rank INTEGER,
            trade_value_1m_eok REAL, trade_value_5m_eok REAL,
            themes_json TEXT NOT NULL DEFAULT '[]', theme_ranks_json TEXT NOT NULL DEFAULT '{}',
            high_distance_percent REAL, news_json TEXT NOT NULL DEFAULT '[]',
            investor_flow_json TEXT NOT NULL DEFAULT '{}', orderbook_json TEXT NOT NULL DEFAULT '{}',
            market_state_json TEXT NOT NULL DEFAULT '{}', capture_state TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trade_entry_snapshots_time
            ON trade_entry_snapshots(stock_code, executed_at);
            """)
            _repair_legacy_alpha_cost_codes(connection)


def _repair_legacy_alpha_cost_codes(connection: sqlite3.Connection) -> None:
    """과거 버전이 내부 영문자를 제거해 저장한 비용 종목코드를 복구한다."""
    mappings = connection.execute(
        "SELECT DISTINCT substr(filled_at,1,10), stock_code FROM trade_fills "
        "WHERE stock_code GLOB '*[A-Z]*'"
    ).fetchall()
    for fill_date, stock_code in mappings:
        legacy_code = "".join(character for character in str(stock_code) if character.isdigit())
        if not legacy_code or legacy_code == stock_code:
            continue
        connection.execute(
            "UPDATE OR REPLACE daily_trade_costs SET stock_code=? WHERE fill_date=? AND stock_code=?",
            (stock_code, fill_date, legacy_code),
        )


class JournalRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        initialize_journal_database(path)

    def remember_stock(self, code: str, name: str, now: datetime) -> None:
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                "INSERT INTO journal_stocks VALUES (?, ?, ?, 'opened', ?) "
                "ON CONFLICT(trade_date, stock_code) DO UPDATE SET stock_name=excluded.stock_name, last_opened_at=excluded.last_opened_at",
                    (now.date().isoformat(), code, name, now.isoformat(timespec="seconds")),
                )

    def save_entry_snapshot(self, value: TradeEntrySnapshot, now: datetime | None = None) -> None:
        """실시간 체결 시점 자료를 중복 체결번호 기준으로 안전하게 저장한다."""
        captured_at = (now or datetime.now()).isoformat(timespec="seconds")
        payload = (
            value.execution_key, value.order_no, value.stock_code, value.stock_name, value.side,
            value.executed_at.isoformat(timespec="seconds"), value.price, value.quantity, value.market,
            value.rank, value.trade_value_1m_eok, value.trade_value_5m_eok,
            json.dumps(value.themes, ensure_ascii=False),
            json.dumps(value.theme_ranks or {}, ensure_ascii=False), value.high_distance_percent,
            json.dumps(value.news, ensure_ascii=False), json.dumps(value.investor_flow or {}, ensure_ascii=False),
            json.dumps(value.orderbook or {}, ensure_ascii=False), json.dumps(value.market_state or {}, ensure_ascii=False),
            value.capture_state, captured_at,
        )
        with closing(sqlite3.connect(self._path, timeout=0.2)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_entry_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(execution_key) DO UPDATE SET "
                    "rank=COALESCE(excluded.rank,trade_entry_snapshots.rank), trade_value_1m_eok=COALESCE(excluded.trade_value_1m_eok,trade_entry_snapshots.trade_value_1m_eok), "
                    "trade_value_5m_eok=COALESCE(excluded.trade_value_5m_eok,trade_entry_snapshots.trade_value_5m_eok), themes_json=excluded.themes_json, "
                    "theme_ranks_json=excluded.theme_ranks_json, high_distance_percent=COALESCE(excluded.high_distance_percent,trade_entry_snapshots.high_distance_percent), "
                    "news_json=excluded.news_json, investor_flow_json=CASE "
                    "WHEN COALESCE(json_extract(trade_entry_snapshots.investor_flow_json,'$.backfilled'),0)=1 "
                    "AND COALESCE(json_extract(excluded.investor_flow_json,'$.backfilled'),0)<>1 "
                    "THEN trade_entry_snapshots.investor_flow_json ELSE excluded.investor_flow_json END, "
                    "orderbook_json=excluded.orderbook_json, "
                    "market_state_json=excluded.market_state_json, capture_state=excluded.capture_state, captured_at=excluded.captured_at",
                    payload,
                )

    def load_entry_snapshots(self, code: str, start: datetime, end: datetime) -> tuple[TradeEntrySnapshot, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT execution_key,order_no,stock_code,stock_name,side,executed_at,price,quantity,market,rank,"
                "trade_value_1m_eok,trade_value_5m_eok,themes_json,theme_ranks_json,high_distance_percent,"
                "news_json,investor_flow_json,orderbook_json,market_state_json,capture_state "
                "FROM trade_entry_snapshots WHERE stock_code=? AND executed_at>=? AND executed_at<? ORDER BY executed_at",
                (code, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return tuple(TradeEntrySnapshot(
            row[0], row[1], row[2], row[3], row[4], datetime.fromisoformat(row[5]), row[6], row[7], row[8], row[9],
            row[10], row[11], tuple(json.loads(row[12])), dict(json.loads(row[13])), row[14], tuple(json.loads(row[15])),
            dict(json.loads(row[16])), dict(json.loads(row[17])), dict(json.loads(row[18])), row[19],
        ) for row in rows)

    def program_backfill_candidates(self, trade_date: date, codes: tuple[str, ...]) -> tuple[TradeEntrySnapshot, ...]:
        if not codes:
            return ()
        start = datetime.combine(trade_date, time.min)
        end = start + timedelta(days=1)
        values: list[TradeEntrySnapshot] = []
        for code in codes:
            values.extend(
                snapshot for snapshot in self.load_entry_snapshots(code, start, end)
                if not bool(((snapshot.investor_flow or {}).get("program_trade") or {}).get("available"))
            )
        return tuple(values)

    def investor_backfill_candidates(self, trade_date: date, codes: tuple[str, ...]) -> tuple[TradeEntrySnapshot, ...]:
        if not codes:
            return ()
        start = datetime.combine(trade_date, time.min); end = start + timedelta(days=1)
        values: list[TradeEntrySnapshot] = []
        for code in codes:
            for snapshot in self.load_entry_snapshots(code, start, end):
                investor = snapshot.investor_flow or {}
                legacy_zero = (
                    investor.get("foreign_net_buy_quantity") == 0
                    and investor.get("institution_net_buy_quantity") == 0
                    and str(investor.get("as_of_date", "")) == trade_date.strftime("%Y%m%d")
                )
                # 과거 버전은 장중에 외국인만 0이고 기관 값이 먼저 들어온 경우도
                # 확정·보완 완료로 잘못 저장했다. 이 형태도 한 번 다시 조회한다.
                legacy_partial_zero = (
                    investor.get("foreign_net_buy_quantity") == 0
                    and investor.get("institution_net_buy_quantity") is not None
                    and str(investor.get("as_of_date", "")) == trade_date.strftime("%Y%m%d")
                )
                missing_values = (
                    investor.get("foreign_net_buy_quantity") is None
                    or investor.get("institution_net_buy_quantity") is None
                    or str(investor.get("as_of_date", "")) != trade_date.strftime("%Y%m%d")
                )
                requested_before_close = False
                if not bool(investor.get("backfilled")):
                    try:
                        requested = datetime.fromisoformat(str(investor.get("requested_at", "")))
                        requested_before_close = (
                            requested.date() == trade_date and requested.time() < time(20, 5)
                        )
                    except ValueError:
                        pass
                if (
                    str(investor.get("status", "")) == "pending_close"
                    or not bool(investor.get("available"))
                    or legacy_zero
                    or legacy_partial_zero
                    or missing_values
                    or requested_before_close
                ):
                    values.append(snapshot)
        return tuple(values)

    def entry_snapshot_codes(self, trade_date: date) -> tuple[str, ...]:
        start = datetime.combine(trade_date, time.min); end = start + timedelta(days=1)
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT DISTINCT stock_code FROM trade_entry_snapshots WHERE executed_at>=? AND executed_at<?",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return tuple(str(row[0]) for row in rows if row and row[0])

    def save_investor_flow_backfill(self, execution_key: str, value: dict[str, object]) -> None:
        with closing(sqlite3.connect(self._path, timeout=0.2)) as connection:
            row = connection.execute(
                "SELECT investor_flow_json FROM trade_entry_snapshots WHERE execution_key=?", (execution_key,),
            ).fetchone()
            if not row:
                return
            try:
                previous = dict(json.loads(row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                previous = {}
            program = previous.get("program_trade")
            merged = dict(value)
            if isinstance(program, dict):
                merged["program_trade"] = program
            with connection:
                connection.execute(
                    "UPDATE trade_entry_snapshots SET investor_flow_json=?, captured_at=? WHERE execution_key=?",
                    (json.dumps(merged, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"), execution_key),
                )

    def save_snapshot_news_backfill(self, execution_key: str, news: tuple[dict[str, object], ...]) -> None:
        with closing(sqlite3.connect(self._path, timeout=0.2)) as connection:
            with connection:
                connection.execute(
                    "UPDATE trade_entry_snapshots SET news_json=?, captured_at=? WHERE execution_key=?",
                    (json.dumps(news, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"), execution_key),
                )

    def save_program_trade_backfill(self, execution_key: str, program_trade: dict[str, object]) -> None:
        with closing(sqlite3.connect(self._path, timeout=0.2)) as connection:
            row = connection.execute(
                "SELECT investor_flow_json FROM trade_entry_snapshots WHERE execution_key=?", (execution_key,),
            ).fetchone()
            if not row:
                return
            try:
                investor = dict(json.loads(row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                investor = {}
            investor["program_trade"] = program_trade
            with connection:
                connection.execute(
                    "UPDATE trade_entry_snapshots SET investor_flow_json=?, captured_at=? WHERE execution_key=?",
                    (json.dumps(investor, ensure_ascii=False), datetime.now().isoformat(timespec="seconds"), execution_key),
                )

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

    def save_trade_setup(
        self, group_id: str, classification: TradeSetupClassification, manual_type: str = "",
        now: datetime | None = None,
    ) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_setup_classifications VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET automatic_type=excluded.automatic_type, "
                    "confidence=excluded.confidence, evidence_json=excluded.evidence_json, "
                    "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                    (group_id, classification.setup_type, classification.confidence,
                     json.dumps(list(classification.evidence), ensure_ascii=False), manual_type, updated_at),
                )

    def load_trade_setup(self, group_id: str) -> tuple[TradeSetupClassification, str] | None:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT automatic_type, confidence, evidence_json, manual_type "
                "FROM trade_setup_classifications WHERE group_id=?", (group_id,),
            ).fetchone()
        if not row:
            return None
        try:
            evidence = tuple(str(value) for value in json.loads(str(row[2])))
        except (TypeError, ValueError):
            evidence = ()
        return TradeSetupClassification(str(row[0]), int(row[1]), evidence), str(row[3] or "")

    def save_trade_setup_cycle_override(
        self, group_id: str, cycle_index: int, manual_type: str, now: datetime | None = None,
    ) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                if manual_type:
                    connection.execute(
                        "INSERT INTO trade_setup_cycle_overrides VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(group_id, cycle_index) DO UPDATE SET "
                        "manual_type=excluded.manual_type, updated_at=excluded.updated_at",
                        (group_id, cycle_index, manual_type, updated_at),
                    )
                else:
                    connection.execute(
                        "DELETE FROM trade_setup_cycle_overrides WHERE group_id=? AND cycle_index=?",
                        (group_id, cycle_index),
                    )

    def load_trade_setup_cycle_overrides(self, group_id: str) -> dict[int, str]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT cycle_index, manual_type FROM trade_setup_cycle_overrides WHERE group_id=?",
                (group_id,),
            ).fetchall()
        return {int(index): str(value) for index, value in rows if str(value)}

    def upsert_fills(self, fills: tuple[TradeFill, ...]) -> None:
        rows = [(
            fill.order_no, fill.stock_code, fill.stock_name, fill.side,
            fill.filled_at.isoformat(timespec="seconds"), fill.quantity, fill.price,
            fill.order_type, fill.market,
        ) for fill in fills]
        if not rows:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO trade_fills VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(order_no, stock_code, filled_at, side) DO UPDATE SET "
                    "stock_name=excluded.stock_name, quantity=excluded.quantity, price=excluded.price, "
                    "order_type=excluded.order_type, market=excluded.market",
                    rows,
                )

    def load_fills(self, start: datetime, end: datetime) -> tuple[TradeFill, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT order_no, stock_code, stock_name, side, filled_at, quantity, price, order_type, market "
                "FROM trade_fills WHERE filled_at>=? AND filled_at<? ORDER BY filled_at DESC",
                (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return tuple(TradeFill(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5], row[6], row[7], row[8]) for row in rows)

    def load_fills_with_entry_context(
        self, start: datetime, end: datetime, lookback_days: int = 365,
    ) -> tuple[TradeFill, ...]:
        """Load prior fills only for stocks traded in the requested period.

        This lets a sell inside the date filter find its earlier buy without
        mixing unrelated stocks or completed older episodes into the screen.
        """
        with closing(sqlite3.connect(self._path)) as connection:
            codes = tuple(
                str(row[0]) for row in connection.execute(
                    "SELECT DISTINCT stock_code FROM trade_fills WHERE filled_at>=? AND filled_at<?",
                    (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
                ).fetchall()
            )
            if not codes:
                return ()
            context_start = start - timedelta(days=max(0, int(lookback_days)))
            placeholders = ",".join("?" for _ in codes)
            rows = connection.execute(
                "SELECT order_no, stock_code, stock_name, side, filled_at, quantity, price, order_type, market "
                f"FROM trade_fills WHERE stock_code IN ({placeholders}) AND filled_at>=? AND filled_at<? "
                "ORDER BY filled_at DESC",
                (*codes, context_start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
            ).fetchall()
        return tuple(
            TradeFill(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5], row[6], row[7], row[8])
            for row in rows
        )

    def load_group_overrides(self) -> dict[str, str]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute("SELECT fill_key, group_id FROM trade_group_overrides").fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def assign_group(self, fill_keys: tuple[str, ...], group_id: str, now: datetime | None = None) -> None:
        if not fill_keys or not group_id:
            return
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO trade_group_overrides VALUES (?, ?, ?) "
                    "ON CONFLICT(fill_key) DO UPDATE SET group_id=excluded.group_id, updated_at=excluded.updated_at",
                    ((key, group_id, updated_at) for key in fill_keys),
                )

    def clear_group_assignments(self, fill_keys: tuple[str, ...]) -> None:
        if not fill_keys:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executemany("DELETE FROM trade_group_overrides WHERE fill_key=?", ((key,) for key in fill_keys))

    def load_review(self, group_id: str) -> TradeReview:
        with closing(sqlite3.connect(self._path)) as connection:
            row = connection.execute(
                "SELECT reason, review, tags, rating, status FROM trade_reviews WHERE group_id=?", (group_id,)
            ).fetchone()
        return TradeReview(group_id, *(str(value or "") for value in row)) if row else TradeReview(group_id)

    def save_review(self, review: TradeReview, now: datetime | None = None) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO trade_reviews VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(group_id) DO UPDATE SET reason=excluded.reason, review=excluded.review, "
                    "tags=excluded.tags, rating=excluded.rating, status=excluded.status, updated_at=excluded.updated_at",
                    (review.group_id, review.reason, review.review, review.tags, review.rating, review.status, updated_at),
                )

    def upsert_trade_costs(self, costs: tuple[DailyTradeCost, ...], now: datetime | None = None) -> None:
        if not costs:
            return
        confirmed_at = (now or datetime.now()).isoformat(timespec="seconds")
        rows = tuple((
            value.fill_date.isoformat(), value.settlement_date.isoformat(), value.stock_code, value.side,
            value.gross_amount, value.settlement_amount, value.commission, value.tax, value.total_cost, confirmed_at,
        ) for value in costs)
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO daily_trade_costs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(fill_date, stock_code, side) DO UPDATE SET "
                    "settlement_date=excluded.settlement_date, gross_amount=excluded.gross_amount, "
                    "settlement_amount=excluded.settlement_amount, commission=excluded.commission, "
                    "tax=excluded.tax, total_cost=excluded.total_cost, confirmed_at=excluded.confirmed_at",
                    rows,
                )

    def load_trade_costs(self, start: datetime, end: datetime) -> tuple[DailyTradeCost, ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT fill_date, settlement_date, stock_code, side, gross_amount, settlement_amount, "
                "commission, tax, total_cost FROM daily_trade_costs WHERE fill_date>=? AND fill_date<?",
                (start.date().isoformat(), end.date().isoformat()),
            ).fetchall()
        return tuple(DailyTradeCost(date.fromisoformat(row[0]), date.fromisoformat(row[1]), *row[2:]) for row in rows)

    def mark_bar_backfill(self, code: str, day: date, state: str, message: str = "", now: datetime | None = None) -> None:
        updated_at = (now or datetime.now()).isoformat(timespec="seconds")
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO journal_bar_backfill VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(trade_date, stock_code) DO UPDATE SET state=excluded.state, "
                    "message=excluded.message, updated_at=excluded.updated_at",
                    (day.isoformat(), code, state, message[:500], updated_at),
                )

    def bar_backfill_state(self, code: str, day: date) -> BarBackfillState:
        with closing(sqlite3.connect(self._path)) as connection:
            bar_row = connection.execute(
                "SELECT COUNT(*), SUM(CASE WHEN source='after_close_confirmed' THEN 1 ELSE 0 END) "
                "FROM journal_minute_bars WHERE trade_date=? AND stock_code=?",
                (day.isoformat(), code),
            ).fetchone()
            status_row = connection.execute(
                "SELECT state, message FROM journal_bar_backfill WHERE trade_date=? AND stock_code=?",
                (day.isoformat(), code),
            ).fetchone()
        count = int(bar_row[0] or 0) if bar_row else 0
        confirmed = int(bar_row[1] or 0) if bar_row else 0
        if confirmed > 0:
            return BarBackfillState(day, code, "확정", count)
        if status_row and status_row[0] == "실패":
            return BarBackfillState(day, code, "실패", count, str(status_row[1] or ""))
        if count > 0:
            return BarBackfillState(day, code, "일부", count)
        return BarBackfillState(day, code, "미조회", 0)

    def bar_backfill_candidates(self, before: date) -> tuple[tuple[str, date], ...]:
        """저장된 체결 중 장이 끝난 날짜·종목만 오래된 순서로 반환한다."""
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT DISTINCT stock_code, substr(filled_at, 1, 10) AS trade_date "
                "FROM trade_fills WHERE substr(filled_at, 1, 10) < ? "
                "ORDER BY trade_date, stock_code",
                (before.isoformat(),),
            ).fetchall()
        return tuple((str(row[0]), date.fromisoformat(str(row[1]))) for row in rows)

    def list_bar_backfill_states(self, before: date | None = None) -> tuple[BarBackfillState, ...]:
        cutoff = before or date.max
        return tuple(
            self.bar_backfill_state(code, day)
            for code, day in self.bar_backfill_candidates(cutoff)
        )

    def upsert_daily_bars(
        self, code: str, rows: tuple[tuple[object, ...], ...], now: datetime | None = None,
    ) -> None:
        confirmed_at = (now or datetime.now()).isoformat(timespec="seconds")
        values: list[tuple[object, ...]] = []
        for row in rows:
            if len(row) < 7:
                continue
            try:
                values.append((
                    datetime.fromisoformat(str(row[0])).date().isoformat(), code,
                    int(row[1]), int(row[2]), int(row[3]), int(row[4]), int(row[5]),
                    float(row[6]) if row[6] is not None else 0.0, confirmed_at,
                ))
            except (TypeError, ValueError):
                # 손상된 일봉 한 행 때문에 매매일지 프로세스 전체를 종료하지 않는다.
                continue
        if not values:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO journal_daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(trade_date, stock_code) DO UPDATE SET open_price=excluded.open_price, "
                    "high_price=excluded.high_price, low_price=excluded.low_price, close_price=excluded.close_price, "
                    "volume=excluded.volume, trade_value_eok=excluded.trade_value_eok, confirmed_at=excluded.confirmed_at",
                    values,
                )

    def load_daily_bars(self, code: str, base_day: date, limit: int = 250) -> tuple[tuple[object, ...], ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            rows = connection.execute(
                "SELECT trade_date, open_price, high_price, low_price, close_price, volume, trade_value_eok "
                "FROM journal_daily_bars WHERE stock_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT ?",
                (code, base_day.isoformat(), limit),
            ).fetchall()
        return tuple((
            datetime.combine(date.fromisoformat(str(row[0])), time()).isoformat(timespec="minutes"),
            *row[1:], "daily_confirmed",
        ) for row in reversed(rows))

    def import_monitor_daily_bars(self, monitor_path: Path, code: str, base_day: date, limit: int = 250) -> int:
        if not monitor_path.is_file():
            return 0
        uri = monitor_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
            rows = connection.execute(
                "SELECT trade_date, open_price, high_price, low_price, close_price, volume, "
                "COALESCE(trade_value_eok, 0) "
                "FROM daily_bars WHERE stock_code=? AND trade_date<=? AND open_price IS NOT NULL "
                "AND high_price IS NOT NULL AND low_price IS NOT NULL AND close_price IS NOT NULL "
                "AND volume IS NOT NULL ORDER BY trade_date DESC LIMIT ?",
                (code, base_day.isoformat(), limit),
            ).fetchall()
        converted = tuple((datetime.combine(date.fromisoformat(str(row[0])), time()).isoformat(timespec="minutes"), *row[1:]) for row in reversed(rows))
        self.upsert_daily_bars(code, converted)
        return len(converted)

    def upsert_bars(self, code: str, bars: tuple[MinuteOhlcv, ...], source: str, confirmed_at: datetime | None = None,
                    *, preserve_confirmed: bool = False) -> None:
        rows = [(
            bar.minute.date().isoformat(), code, bar.minute.isoformat(timespec="minutes"),
            bar.open_price, bar.high_price, bar.low_price, bar.close_price, bar.volume,
            float(bar.trade_value_eok), source,
            confirmed_at.isoformat(timespec="seconds") if confirmed_at else None,
        ) for bar in bars]
        if not rows:
            return
        with closing(sqlite3.connect(self._path)) as connection:
            update_guard = " WHERE journal_minute_bars.source NOT IN ('api_confirmed', 'after_close_confirmed')" if preserve_confirmed else ""
            with connection:
                connection.executemany(
                    "INSERT INTO journal_minute_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(trade_date, stock_code, minute) DO UPDATE SET "
                    "open_price=excluded.open_price, high_price=excluded.high_price, low_price=excluded.low_price, "
                    "close_price=excluded.close_price, volume=excluded.volume, trade_value_eok=excluded.trade_value_eok, "
                    "source=excluded.source, confirmed_at=excluded.confirmed_at" + update_guard,
                    rows,
                )

    def import_live_bars(self, monitor_path: Path, code: str, now: datetime) -> int:
        """메인 DB를 읽기 전용으로 짧게 열어 실시간 봉을 일지 DB로 복사한다."""
        if not monitor_path.is_file():
            return 0
        uri = monitor_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
            rows = connection.execute(
                "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok "
                "FROM minute_bars WHERE trade_date=? AND stock_code=? ORDER BY minute",
                (now.date().isoformat(), code),
            ).fetchall()
        bars = tuple(MinuteOhlcv(datetime.fromisoformat(row[0]), *map(int, row[1:6]), trade_value_eok_override=float(row[6] or 0)) for row in rows)
        self.upsert_bars(code, bars, "realtime_partial", preserve_confirmed=True)
        return len(bars)

    def load_bars(self, code: str, now: datetime) -> tuple[tuple[object, ...], ...]:
        with closing(sqlite3.connect(self._path)) as connection:
            return tuple(connection.execute(
                "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok, source "
                "FROM journal_minute_bars WHERE trade_date=? AND stock_code=? ORDER BY minute",
                (now.date().isoformat(), code),
            ).fetchall())

    def load_chart_bars(self, code: str, now: datetime) -> tuple[tuple[object, ...], ...]:
        """선택일과 DB에 존재하는 직전 거래일 분봉을 시간순으로 읽는다."""
        target = now.date().isoformat()
        with closing(sqlite3.connect(self._path)) as connection:
            previous = connection.execute(
                "SELECT MAX(trade_date) FROM journal_minute_bars WHERE stock_code=? AND trade_date<?",
                (code, target),
            ).fetchone()[0]
            days = (target,) if not previous else (previous, target)
            placeholders = ",".join("?" for _ in days)
            return tuple(connection.execute(
                "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok, source "
                f"FROM journal_minute_bars WHERE stock_code=? AND trade_date IN ({placeholders}) ORDER BY minute",
                (code, *days),
            ).fetchall())

    def load_chart_bars_range(
        self, code: str, start_day: date, end_day: date,
    ) -> tuple[tuple[object, ...], ...]:
        """매매 묶음의 첫날부터 마지막 날까지와 직전 저장 거래일을 읽는다."""
        if start_day > end_day:
            start_day, end_day = end_day, start_day
        start_text, end_text = start_day.isoformat(), end_day.isoformat()
        with closing(sqlite3.connect(self._path)) as connection:
            previous = connection.execute(
                "SELECT MAX(trade_date) FROM journal_minute_bars WHERE stock_code=? AND trade_date<?",
                (code, start_text),
            ).fetchone()[0]
            conditions = "trade_date BETWEEN ? AND ?"
            parameters: tuple[object, ...] = (code, start_text, end_text)
            if previous:
                conditions = f"({conditions} OR trade_date=?)"
                parameters = (*parameters, previous)
            return tuple(connection.execute(
                "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok, source "
                f"FROM journal_minute_bars WHERE stock_code=? AND {conditions} ORDER BY minute",
                parameters,
            ).fetchall())

    def load_monitor_market_index_bars(
        self, monitor_path: Path, market: str, start_day: date, end_day: date,
    ) -> tuple[tuple[object, ...], ...]:
        """메인 DB에 저장된 지수 1분봉에서 선택 기간과 직전 거래일을 읽는다."""
        if market not in {"kospi", "kosdaq"} or not monitor_path.is_file():
            return ()
        if start_day > end_day:
            start_day, end_day = end_day, start_day
        uri = monitor_path.resolve().as_uri() + "?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
                if not connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_index_minute_bars'"
                ).fetchone():
                    return ()
                start_text, end_text = start_day.isoformat(), end_day.isoformat()
                previous = connection.execute(
                    "SELECT MAX(trade_date) FROM market_index_minute_bars WHERE market=? AND trade_date<?",
                    (market, start_text),
                ).fetchone()[0]
                conditions = "trade_date BETWEEN ? AND ?"
                parameters: tuple[object, ...] = (market, start_text, end_text)
                if previous:
                    conditions = f"({conditions} OR trade_date=?)"
                    parameters = (*parameters, previous)
                raw = connection.execute(
                    "SELECT minute,open_value,high_value,low_value,close_value,0,"
                    "trade_value_eok,'market_index' FROM market_index_minute_bars "
                    f"WHERE market=? AND {conditions} ORDER BY minute", parameters,
                ).fetchall()
                result = []; previous_by_day: dict[str, float] = {}
                for row in raw:
                    day = str(row[0])[:10]; cumulative = row[6]
                    minute_value = None
                    if cumulative is not None:
                        current = float(cumulative); previous_value = previous_by_day.get(day)
                        minute_value = max(0.0, current - previous_value) if previous_value is not None else None
                        previous_by_day[day] = current
                    result.append((*row[:6], minute_value, row[7]))
                return tuple(result)
        except sqlite3.Error:
            return ()

    def load_monitor_market_index_daily(self, monitor_path: Path, market: str, base_day: date) -> tuple[tuple[object, ...], ...]:
        if market not in {"kospi", "kosdaq"} or not monitor_path.is_file(): return ()
        try:
            uri = monitor_path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
                if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='market_index_daily_bars'").fetchone(): return ()
                return tuple((*row, "market_index_daily") for row in connection.execute(
                    "SELECT trade_date||'T00:00',open_value,high_value,low_value,close_value,volume,trade_value_eok "
                    "FROM market_index_daily_bars WHERE market=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 250",
                    (market, base_day.isoformat()),
                ).fetchall()[::-1])
        except sqlite3.Error: return ()

    def load_monitor_stock_catalog(self, monitor_path: Path) -> tuple[tuple[str, str], ...]:
        if not monitor_path.is_file(): return ()
        try:
            uri = monitor_path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.05)) as connection:
                return tuple((str(code), str(name)) for code, name in connection.execute(
                    "SELECT code,name FROM stocks WHERE code<>'' AND name<>'' ORDER BY name"
                ).fetchall())
        except sqlite3.Error:
            return ()
