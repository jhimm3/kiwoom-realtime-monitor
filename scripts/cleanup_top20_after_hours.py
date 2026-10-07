from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


WHERE_AFTER_20 = "substr(minute, 12, 5) BETWEEN '20:00' AND '23:59'"
WHERE_BEFORE_08 = "substr(minute, 12, 5) BETWEEN '00:00' AND '07:59'"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--before-08", action="store_true")
    arguments = parser.parse_args()
    database = arguments.database.resolve()
    if not database.is_file() or database.name != "monitor.sqlite3":
        raise SystemExit("정확한 monitor.sqlite3 파일을 지정해야 합니다.")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    where_clause = WHERE_BEFORE_08 if arguments.before_08 else WHERE_AFTER_20
    label = "before_08" if arguments.before_08 else "after_20"
    try:
        rows = connection.execute(
            f"SELECT * FROM top20_trade_value_index WHERE {where_clause} ORDER BY minute"
        ).fetchall()
        print(json.dumps({"database": str(database), "count": len(rows), "first": rows[0]["minute"] if rows else None, "last": rows[-1]["minute"] if rows else None}, ensure_ascii=True))
        if not arguments.apply or not rows:
            return 0
        backup_dir = database.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"top20_{label}_{datetime.now():%Y%m%d_%H%M%S}.json"
        backup.write_text(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")
        connection.execute(f"DELETE FROM top20_trade_value_index WHERE {where_clause}")
        connection.commit()
        remaining = connection.execute(f"SELECT COUNT(*) FROM top20_trade_value_index WHERE {where_clause}").fetchone()[0]
        print(json.dumps({"deleted": len(rows), "remaining": remaining, "backup": str(backup)}, ensure_ascii=True))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
