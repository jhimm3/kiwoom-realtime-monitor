"""Stage only minute metadata diagnostics onto the existing NAS v7 source."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(r"X:\kiwoom-monitor")
LOCAL = Path(__file__).resolve().parents[1]
BACKUP = ROOT / ".codex-backups" / "20260930-minute-metadata-diagnostics-v8"
EXPECTED = {
    "src/kiwoom_monitor/central_server/database.py": "29664D0C221ADAC6C74EA5DE1954453CF9EA3CE6FF56A5875978F5FA4C29DAFC",
    "src/kiwoom_monitor/central_server/diagnostic_metrics.py": "FA9BDEAA08816B45B5BD7E88E0D9F3FDDADFAC0489E85CCB7A5C39BFF6AEE08D",
    "src/kiwoom_monitor/central_server/app.py": "D99F98D9F8189C77B6E15F4A75CF030403645283520AB95ACD1D340576E2A4AF",
    "deploy/synology/server.Dockerfile": "14DB020C4EC33EC4B550C5790A38557B8C9D0C17FD65BD5723E5D38351F28BD1",
    "deploy/synology/docker-compose.yml": "8369E2E15958A72EF962FF66B5104A7BA6DB2706A4771EB67EB859B6D182B3A4",
}


def replace_once(value: str, old: str, new: str) -> str:
    if value.count(old) != 1:
        raise RuntimeError(f"expected exactly one match: {old[:65]!r}")
    return value.replace(old, new)


def stage_database(value: str) -> str:
    value = replace_once(
        value,
        '"""Narrow cursor proxy that records the waits for bar UPSERT statements."""',
        '"""Narrow cursor proxy that records waits for selected UPSERT statements."""',
    )
    value = replace_once(
        value,
        'record["sql_operations"] = 1\n            self._records.append(record)',
        'record["sql_operations"] = 1\n            record["affected_rows"] = getattr(self._cursor, "rowcount", None)\n            self._records.append(record)',
    )
    value = replace_once(
        value,
        'record["sql_operations"] = len(args[1]) if len(args) > 1 else None\n            self._records.append(record)',
        'record["sql_operations"] = len(args[1]) if len(args) > 1 else None\n            record["affected_rows"] = getattr(self._cursor, "rowcount", None)\n            self._records.append(record)',
    )
    value = replace_once(
        value,
        'bar_statement_diagnostics: list[dict[str, object]] = []\n        capture_enabled',
        'bar_statement_diagnostics: list[dict[str, object]] = []\n        metadata_statement_diagnostics: list[dict[str, object]] = []\n        capture_enabled',
    )
    value = replace_once(
        value,
        'phase_started = monotonic()\n                _save_postgres_metadata(cursor, observations, multirow=True)\n                metadata_ms',
        'phase_started = monotonic()\n                metadata_cursor = (\n                    _PostgresObservedCursor(\n                        cursor, self._database_url, bar_backend_pid,\n                        True, metadata_statement_diagnostics,\n                    ) if capture_enabled else cursor\n                )\n                _save_postgres_metadata(metadata_cursor, observations, multirow=True)\n                metadata_ms',
    )
    value = replace_once(
        value,
        'bar_statement_diagnostics=bar_statement_diagnostics,\n        )\n        from .diagnostic_metrics import record_writer_transaction',
        'bar_statement_diagnostics=bar_statement_diagnostics,\n            metadata_statement_diagnostics=metadata_statement_diagnostics,\n        )\n        from .diagnostic_metrics import record_writer_transaction',
    )
    return value


def main() -> None:
    if not ROOT.is_dir() or not (ROOT / "deploy/synology/.env").is_file():
        raise RuntimeError("NAS project root does not match the expected share")
    if BACKUP.exists():
        raise RuntimeError("backup target already exists; refusing to overwrite it")
    originals: dict[str, bytes] = {}
    for name, digest in EXPECTED.items():
        path = ROOT / name
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest().upper() != digest:
            raise RuntimeError(f"NAS source changed: {name}")
        originals[name] = raw
    replacements: dict[str, bytes] = {}
    db = "src/kiwoom_monitor/central_server/database.py"
    replacements[db] = stage_database(originals[db].decode("utf-8").replace("\r\n", "\n")).encode("utf-8")
    metrics = "src/kiwoom_monitor/central_server/diagnostic_metrics.py"
    replacements[metrics] = (LOCAL / metrics).read_bytes()
    for name in ("src/kiwoom_monitor/central_server/app.py", "deploy/synology/server.Dockerfile", "deploy/synology/docker-compose.yml"):
        raw = originals[name]
        if raw.count(b"2026.09.29-diagnostic-api-v7") != 1:
            raise RuntimeError(f"expected one v7 build marker: {name}")
        replacements[name] = raw.replace(b"2026.09.29-diagnostic-api-v7", b"2026.09.30-minute-metadata-diagnostics-v8")
    if replacements[metrics] == originals[metrics]:
        raise RuntimeError("metric source unexpectedly unchanged")
    for name, raw in replacements.items():
        print("candidate", name, hashlib.sha256(raw).hexdigest().upper())
    if "--check" in sys.argv:
        print("preflight passed; NAS source unchanged")
        return
    for name, raw in originals.items():
        backup_path = BACKUP / name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(raw)
    for name, raw in replacements.items():
        target = ROOT / name
        target.write_bytes(raw)
        if target.read_bytes() != raw:
            raise RuntimeError(f"NAS write did not verify: {name}")
    print("staged 5 diagnostics-only source/build-marker files; backup:", BACKUP)
    for name, raw in replacements.items():
        print(name, hashlib.sha256(raw).hexdigest().upper())


if __name__ == "__main__":
    main()
