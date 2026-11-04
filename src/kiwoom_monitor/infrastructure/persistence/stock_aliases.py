from __future__ import annotations

import sqlite3
from datetime import datetime

from kiwoom_monitor.domain.time_basis import as_kst, kst_now


# 과거 종목명으로 작성된 테마 자료도 현재 종목코드에 연결한다.
KNOWN_STOCK_ALIASES = (
    ("네온테크", "306620"),  # 현재 종목명: 지아이에스
    ("KT", "030200"),  # 현재 종목명: 케이티
    ("알엔티엑스", "123010"),  # 현재 종목명: MSDI
    ("RNTX", "123010"),
    ("파수", "150900"),  # 현재 종목명: 파수AI
    ("파수에이아이", "150900"),
    ("핸드소프트", "220180"),  # 흔한 오기, 현재 종목명: 폴라리스AI핸디
    ("핸디소프트", "220180"),  # 변경 전 종목명
)


def seed_known_stock_aliases(connection: sqlite3.Connection, *, observed_at: datetime | None = None) -> None:
    # Explicit role stores already own their selected aliases. Static legacy
    # review must not reclassify that seed during a later catalog refresh.
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='role_settings_schema'"
    ).fetchone() is not None:
        return
    # Database initialization is the explicit startup fallback; catalog commands
    # pass their existing corrected observation once for the whole transaction.
    changed_at = as_kst(observed_at if observed_at is not None else kst_now()).isoformat()
    for alias, stock_code in KNOWN_STOCK_ALIASES:
        row = connection.execute("SELECT name FROM stocks WHERE code=?", (stock_code,)).fetchone()
        if row is None:
            continue
        current_name = str(row[0]).strip()
        connection.execute(
            "INSERT OR IGNORE INTO stock_name_history(stock_code,old_name,new_name,source,decision,changed_at) "
            "VALUES(?,?,?,'기본 과거명 자료','pending',?)",
            (stock_code, alias, current_name, changed_at),
        )
        # 예전 버전에서 자동 생성한 기본 별칭도 한 번은 사용자가 확인하게 한다.
        connection.execute(
            "DELETE FROM stock_aliases WHERE alias=? AND stock_code=? AND EXISTS ("
            "SELECT 1 FROM stock_name_history WHERE stock_code=? AND old_name=? AND decision='pending')",
            (alias, stock_code, stock_code, alias),
        )
