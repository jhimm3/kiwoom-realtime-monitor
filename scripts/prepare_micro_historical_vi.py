"""Freeze one symbol/day of KRX archive VI into a portable MICRO scenario."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path

from kiwoom_monitor.application.micro_execution_evidence import MICRO_FROZEN_EVIDENCE_VERSION
from kiwoom_monitor.application.micro_historical_vi import MICRO_HISTORICAL_VI_VERSION, HistoricalMicroVi
from kiwoom_monitor.domain.time_basis import KST, aware_iso_kst


def _direction(static_pct: float, dynamic_pct: float) -> str | None:
    if min(static_pct, dynamic_pct) >= 0 and max(static_pct, dynamic_pct) > 0:
        return "UP"
    if max(static_pct, dynamic_pct) <= 0 and min(static_pct, dynamic_pct) < 0:
        return "DOWN"
    return None


def prepare_scenario(manifest_path: Path, symbol: str, day: date, *, base: dict | None = None) -> dict:
    if re.fullmatch(r"[0-9A-Z]{6}", symbol) is None:
        raise ValueError("MICRO requires a six-character stock code")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = manifest.get("database", {})
    if (manifest.get("schema") != "historical-market-context-nas/v1"
            or not isinstance(manifest.get("run"), str) or not manifest["run"]
            or identity.get("name") != "historical_market_context.sqlite3"
            or identity.get("integrity_check") != "ok"
            or re.fullmatch(r"[0-9a-f]{64}", str(identity.get("sha256", ""))) is None):
        raise ValueError("expected a published KRX market context archive manifest")
    database = (manifest_path.parent / identity["name"]).absolute()
    if database.stat().st_size != identity.get("bytes"):
        raise ValueError("archive database size differs from the published manifest")
    uri = database.as_uri()
    if database.drive.startswith("\\\\"):
        uri = "file:////" + uri.removeprefix("file://")
    with closing(sqlite3.connect(uri + "?mode=ro&immutable=1", uri=True)) as connection:
        start, end = connection.execute("SELECT MIN(first_date), MAX(last_date) FROM krx_vi_imports").fetchone()
        rows = connection.execute(
            "SELECT event_id,triggered_at,released_at,trigger_price,vi_type,source_sha256,"
            "static_deviation_pct,dynamic_deviation_pct FROM historical_vi_events "
            "WHERE code=? AND trade_date=? AND provider='krx_data_marketplace' ORDER BY triggered_at,event_id",
            (symbol, day.isoformat()),
        ).fetchall()
    source = {"source_ref": f"krx-market-context:{manifest['run']}", "source_version": f"sha256:{identity['sha256']}"}
    scenario = dict(base) if base is not None else {"version": MICRO_FROZEN_EVIDENCE_VERSION,
        "symbol": symbol, "venue": "KRX", "clock_id": "krx-archive-vi", "events": [],
        "provenance": {"source_kind": "historical_reconstruction", **source}}
    if ((scenario.get("version"), scenario.get("symbol"), scenario.get("venue"))
            != (MICRO_FROZEN_EVIDENCE_VERSION, symbol, "KRX") or "historical_vi" in scenario):
        raise ValueError("expected an unattached frozen scenario for the selected KRX stock")
    scenario.setdefault("decision_at", datetime.combine(day, time.max, KST).isoformat())
    evidence = {"version": MICRO_HISTORICAL_VI_VERSION, "symbol": symbol, "trading_date": day.isoformat(),
        "archive_start": start, "archive_end": end, **source,
        "events": [{"event_id": event_id, "triggered_at": trigger, "released_at": release,
                    "trigger_price_won": price, "vi_type": vi_type, "source_sha256": digest,
                    "direction": _direction(static, dynamic)}
                   for event_id, trigger, release, price, vi_type, digest, static, dynamic in rows]}
    HistoricalMicroVi.from_document(evidence, symbol=symbol, venue="KRX", cut=aware_iso_kst(scenario["decision_at"]))
    return {**scenario, "historical_vi": evidence}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--scenario", type=Path, help="existing frozen MICRO evidence with ordered market inputs")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = json.loads(args.scenario.read_text(encoding="utf-8")) if args.scenario else None
    scenario = prepare_scenario(args.manifest, args.symbol, args.date, base=base)
    encoded = json.dumps(scenario, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    with args.output.open("xb") as stream:
        stream.write(encoded)
    print(json.dumps({"dataset": str(args.output), "dataset_sha256": hashlib.sha256(encoded).hexdigest(),
                      "archived_vi_count": len(scenario["historical_vi"]["events"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
