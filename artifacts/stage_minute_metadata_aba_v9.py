"""Stage the short-lived minute metadata A/B/A diagnostic on NAS v8."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


ROOT = Path(r"X:\kiwoom-monitor")
BACKUP = ROOT / ".codex-backups" / "20260930-minute-metadata-aba-v9"
OLD_BUILD = "2026.09.30-minute-metadata-diagnostics-v8"
NEW_BUILD = "2026.09.30-minute-metadata-aba-v9"
EXPECTED = {
    "src/kiwoom_monitor/central_server/database.py": "3498D0BECE115B7F170FCBE2968F50D4A59DE4DFED3AA2DB40D8F354718E8CD9",
    "src/kiwoom_monitor/central_server/diagnostic_metrics.py": "E911FB47E0C9C7D9D5F41B7EF9EF51A81C9DCD1FF1B886FA5E0ED2F7F90ED73E",
    "src/kiwoom_monitor/central_server/diagnostic_workloads.py": "D29F9E90FC41C666F16AF97FEA8D22F96E17B14952F0CA1C21825432E6BCE1CB",
    "src/kiwoom_monitor/central_server/app.py": "34A92F0C23A4A14642BE79C48CF351652456F1F884D823BD3E53AD1DC4343715",
    "deploy/synology/server.Dockerfile": "A5DC88655AA9DE28CB6E776F07BED33FC1695F1A756B3290B0904D3CF6B0A325",
    "deploy/synology/docker-compose.yml": "488FCF381BDE1C8DF26CEACCF6038E9695CF0B77FC0FB5EF7BD6B6BD58179E8D",
}


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"expected one match: {old[:70]!r}")
    return source.replace(old, new)


def patch_database(source: str) -> str:
    source = replace_once(source,
        'capture_enabled = bool(refresh_capture_state().get("enabled"))\n        if capture_enabled:',
        'capture_enabled = bool(refresh_capture_state().get("enabled"))\n'
        '        # Sample the diagnostic lease once per call; expiry restores the next call.\n'
        '        from .diagnostic_workloads import is_paused\n'
        '        metadata_suppressed_rows = (\n'
        '            len(observations or ()) if minute and is_paused("minute_query_metadata") else 0\n'
        '        )\n'
        '        if capture_enabled:')
    source = replace_once(source,
        '                _save_postgres_metadata(metadata_cursor, observations, multirow=True)',
        '                if not metadata_suppressed_rows:\n'
        '                    _save_postgres_metadata(metadata_cursor, observations, multirow=True)')
    source = replace_once(source,
        'observations=len(observations or ()), connect_ms=connect_ms,',
        'observations=len(observations or ()), connect_ms=connect_ms,\n'
        '            metadata_suppressed_rows=metadata_suppressed_rows,')
    return source


def patch_metrics(source: str) -> str:
    source = replace_once(source,
        '                           db_call_id: str | None = None) -> None:\n    if not _capture_is_enabled():',
        '                           metadata_suppressed_rows: int = 0,\n'
        '                           db_call_id: str | None = None) -> None:\n    if not _capture_is_enabled():')
    source = replace_once(source,
        '             "rows": rows, "observations": observations,',
        '             "rows": rows, "observations": observations,\n'
        '             "metadata_suppressed_rows": metadata_suppressed_rows,')
    source = replace_once(source,
        '                "observation_rows": sum(int(row["observations"]) for row in rows),',
        '                "observation_rows": sum(int(row["observations"]) for row in rows),\n'
        '                "metadata_suppressed_rows": sum(\n'
        '                    int(row.get("metadata_suppressed_rows", 0)) for row in rows\n'
        '                ),')
    source = replace_once(source,
        '                        "at", "api_id", "db_call_id", "rows", "observations",',
        '                        "at", "api_id", "db_call_id", "rows", "observations",\n'
        '                        "metadata_suppressed_rows",')
    return source


def main() -> None:
    if not ROOT.is_dir() or not (ROOT / "deploy/synology/.env").is_file():
        raise RuntimeError("NAS project root does not match the expected share")
    if BACKUP.exists():
        raise RuntimeError("backup already exists; refusing to overwrite")
    originals = {}
    for name, digest in EXPECTED.items():
        raw = (ROOT / name).read_bytes()
        if hashlib.sha256(raw).hexdigest().upper() != digest:
            raise RuntimeError(f"NAS source changed: {name}")
        originals[name] = raw
    patched = {}
    for name, raw in originals.items():
        source = raw.decode("utf-8").replace("\r\n", "\n")
        if name.endswith("/database.py"):
            source = patch_database(source)
        elif name.endswith("/diagnostic_metrics.py"):
            source = patch_metrics(source)
        elif name.endswith("/diagnostic_workloads.py"):
            source = replace_once(source,
                '    "news_query_set", "news_market_feed", "external_market", "candidate_monitor",\n',
                '    "news_query_set", "news_market_feed", "external_market", "candidate_monitor",\n'
                '    "minute_query_metadata",\n')
        elif name.endswith("/app.py"):
            source = replace_once(source,
                '            "minute_backfill": bool(top20_service and top20_service._minute_backfill_enabled),',
                '            "minute_backfill": bool(top20_service and top20_service._minute_backfill_enabled),\n'
                '            "minute_query_metadata": bool(active.database_url.startswith("postgres")),')
        if name.endswith("/app.py") or name.endswith(".Dockerfile") or name.endswith("docker-compose.yml"):
            source = replace_once(source, OLD_BUILD, NEW_BUILD)
        patched[name] = source.encode("utf-8")
    for name, raw in patched.items():
        if name.endswith(".py"):
            compile(raw, name, "exec")
        print("candidate", name, hashlib.sha256(raw).hexdigest().upper())
    if "--check" in sys.argv:
        print("preflight passed; NAS source unchanged")
        return
    for name, raw in originals.items():
        target = BACKUP / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    for name, raw in patched.items():
        target = ROOT / name
        target.write_bytes(raw)
        if target.read_bytes() != raw:
            raise RuntimeError(f"NAS write did not verify: {name}")
    print("staged six diagnostic source/build files; backup:", BACKUP)


if __name__ == "__main__":
    main()
