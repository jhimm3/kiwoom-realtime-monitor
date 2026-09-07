from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
import re
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(os.environ.get("KIWOOM_AUDIT_SOURCE_ROOT", str(ROOT / "src")))
sys.path.insert(0, str(SOURCE_ROOT))

from kiwoom_monitor.application.trade_history_service import TradeFill
from kiwoom_monitor.application.trade_journal_summary import group_trade_episodes, split_trade_episode_cycles
from kiwoom_monitor.application.trade_setup_classification import classify_trade_setup_cycles


def entry_shape_flags(episode, minute_rows, daily_rows) -> tuple[str, ...]:
    buys = tuple(sorted((fill for fill in episode.fills if fill.side == "매수"), key=lambda value: value.filled_at))
    if not buys:
        return ()
    entry = buys[0]
    entry_minute = entry.filled_at.replace(second=0, microsecond=0)
    parsed = tuple((datetime.fromisoformat(str(row[0])), row) for row in minute_rows)
    prior = tuple(
        row for minute, row in parsed
        if minute.date() == entry_minute.date()
        and minute >= entry_minute.replace(hour=8, minute=0)
        and minute < entry_minute
    )
    if not prior:
        return ()
    entry_price = float(entry.price)
    day_open = float(prior[0][1])
    prior_high = max(float(row[2]) for row in prior[-30:])
    session_high = max(float(row[2]) for row in prior)
    session_low = min(float(row[3]) for row in prior)
    rise_before = prior_high / day_open - 1 if day_open else 0.0
    drawdown = entry_price / prior_high - 1 if prior_high else 0.0
    rebound = entry_price / session_low - 1 if session_low else 0.0
    flags: list[str] = []
    if len(prior) >= 3 and entry_price >= prior_high * 0.998:
        flags.append("30분고점돌파")
    if rise_before >= 0.03 and -0.08 <= drawdown <= -0.01:
        flags.append("눌림구간")
    if entry_price / session_high - 1 <= -0.08:
        flags.append("당일고점대비급락")
    prior_daily = tuple(
        row for row in daily_rows
        if datetime.fromisoformat(str(row[0])).date() < entry_minute.date()
    )
    recent_peak = max((float(row[2]) for row in prior_daily[-20:]), default=0.0)
    if recent_peak and entry_price / recent_peak - 1 <= -0.15 and rebound >= 0.01:
        flags.append("20일고점대비과대낙폭")
    return tuple(flags)


def rows(connection: sqlite3.Connection, sql: str, values: tuple[object, ...] = ()) -> tuple[tuple[object, ...], ...]:
    return tuple(connection.execute(sql, values).fetchall())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--name", default="")
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--compact", action="store_true")
    parser.add_argument("--metrics-only", action="store_true")
    parser.add_argument("--subtype-filter", default="")
    args = parser.parse_args()
    connection = sqlite3.connect(f"file:{args.database.resolve().as_posix()}?mode=ro", uri=True)
    fill_rows = rows(
        connection,
        "SELECT order_no, stock_code, stock_name, side, filled_at, quantity, price, order_type, market "
        "FROM trade_fills WHERE filled_at>=? ORDER BY filled_at",
        (args.since,),
    )
    fills = tuple(TradeFill(
        str(row[0]), str(row[1]), str(row[2]), str(row[3]), datetime.fromisoformat(str(row[4])),
        int(row[5]), int(row[6]), str(row[7] or ""), str(row[8] or ""),
    ) for row in fill_rows)
    overrides = dict(rows(connection, "SELECT fill_key, group_id FROM trade_group_overrides"))
    episodes = group_trade_episodes(fills, overrides)
    report: list[dict[str, object]] = []
    for episode in episodes:
        if args.name and args.name not in episode.summary.stock_name:
            continue
        day_from = (episode.started_at.date() - timedelta(days=1)).isoformat()
        day_to = (episode.ended_at.date() + timedelta(days=1)).isoformat()
        minute_rows = rows(
            connection,
            "SELECT minute, open_price, high_price, low_price, close_price, volume, trade_value_eok, source "
            "FROM journal_minute_bars WHERE stock_code=? AND trade_date>=? AND trade_date<=? ORDER BY minute",
            (episode.summary.stock_code, day_from, day_to),
        )
        daily_rows = rows(
            connection,
            "SELECT trade_date || 'T00:00', open_price, high_price, low_price, close_price, volume, "
            "COALESCE(trade_value_eok, 0), 'daily' FROM journal_daily_bars "
            "WHERE stock_code=? AND trade_date<=? ORDER BY trade_date DESC LIMIT 250",
            (episode.summary.stock_code, episode.ended_at.date().isoformat()),
        )[::-1]
        app_saved = connection.execute(
            "SELECT automatic_type, confidence, evidence_json, manual_type FROM trade_setup_classifications WHERE group_id=?",
            (episode.group_id,),
        ).fetchone()
        calculated = classify_trade_setup_cycles(episode, minute_rows, daily_rows)
        cycles = split_trade_episode_cycles(episode)
        report.append({
            "group_id": episode.group_id,
            "date": episode.started_at.date().isoformat(),
            "stock": episode.summary.stock_name,
            "code": episode.summary.stock_code,
            "fill_count": len(episode.fills),
            "minute_count": len(minute_rows),
            "daily_count": len(daily_rows),
            "app_saved": app_saved,
            "cycles": [{
                "index": index + 1,
                "start": cycle.started_at.isoformat(timespec="seconds"),
                "end": cycle.ended_at.isoformat(timespec="seconds"),
                "fills": [
                    {"side": fill.side, "time": fill.filled_at.isoformat(timespec="seconds"),
                     "price": fill.price, "quantity": fill.quantity}
                    for fill in cycle.fills
                ],
                "calculated_type": calculated[index].setup_type,
                "subtype": calculated[index].subtype,
                "confidence": calculated[index].confidence,
                "return_rate": cycle.summary.return_rate,
                "prior_minute_count": sum(
                    1 for row in minute_rows
                    if datetime.fromisoformat(str(row[0])).date() == cycle.started_at.date()
                    and datetime.fromisoformat(str(row[0])) < cycle.started_at.replace(second=0, microsecond=0)
                ),
                "evidence": calculated[index].evidence,
                "warnings": calculated[index].warnings,
                "unverifiable": calculated[index].unverifiable,
                "audit_flags": entry_shape_flags(cycle, minute_rows, daily_rows),
            } for index, cycle in enumerate(cycles)],
        })
    if args.subtype_filter:
        filtered = []
        for item in report:
            matching = tuple(
                cycle for cycle in item["cycles"]
                if args.subtype_filter in str(cycle["subtype"] or "세부 유형 없음")
            )
            if matching:
                filtered.append({**item, "cycles": matching})
        output = {"episode_count": len(filtered), "episodes": filtered}
    elif args.summary:
        saved_types = Counter()
        calculated_types = Counter()
        subtype_counts = Counter()
        subtype_samples: dict[str, list[dict[str, object]]] = {}
        changed: list[dict[str, object]] = []
        unresolved: list[dict[str, object]] = []
        old_to_new = Counter()
        outcome_by_subtype: dict[str, list[float]] = {}
        low_context_by_subtype = Counter()
        low_context_samples: list[dict[str, object]] = []
        disagreement_samples: dict[str, list[dict[str, object]]] = {}
        overlap_counts = Counter()
        overlap_samples: dict[str, list[dict[str, object]]] = {}
        for item in report:
            saved = item["app_saved"]
            saved_type = str(saved[0]) if saved else "미저장"
            saved_types[saved_type] += 1
            calculated = " · ".join(str(cycle["calculated_type"]) for cycle in item["cycles"])
            calculated_types[calculated] += 1
            for cycle in item["cycles"]:
                subtype = str(cycle["subtype"] or "세부 유형 없음")
                subtype_counts[subtype] += 1
                outcome_by_subtype.setdefault(subtype, []).append(float(cycle["return_rate"]))
                if int(cycle["prior_minute_count"]) < 3:
                    low_context_by_subtype[subtype] += 1
                    low_context_samples.append({
                        "date": item["date"], "stock": item["stock"], "subtype": subtype,
                        "start": cycle["start"], "prior_minute_count": cycle["prior_minute_count"],
                        "evidence": cycle["evidence"],
                    })
                subtype_samples.setdefault(subtype, []).append({
                    "date": item["date"], "stock": item["stock"],
                    "start": cycle["start"], "evidence": cycle["evidence"],
                })
                flags = tuple(str(value) for value in cycle.get("audit_flags", ()))
                if len(flags) > 1:
                    key = " + ".join(flags)
                    overlap_counts[key] += 1
                    overlap_samples.setdefault(key, []).append({
                        "date": item["date"], "stock": item["stock"],
                        "start": cycle["start"], "current": subtype,
                    })
            saved_cycles = tuple(
                re.sub(r"^\d+차\s+", "", value).strip()
                for value in saved_type.split(" · ")
            )
            current_cycles = tuple(str(cycle["subtype"] or "기타") for cycle in item["cycles"])
            if len(saved_cycles) == len(current_cycles):
                for old, new, cycle in zip(saved_cycles, current_cycles, item["cycles"]):
                    old_to_new[(old, new)] += 1
                    expected = {
                        "돌파": ("고점 돌파",), "눌림": ("눌림",),
                        "추격매수": ("고점 근접",), "과대낙폭": ("과대낙폭",),
                        "종가베팅": ("장 마감",), "시가베팅": ("장 시작", "기타"),
                    }.get(old, (old,))
                    if old not in ("기타", "미저장") and not any(token in new for token in expected):
                        key = f"{old} -> {new}"
                        disagreement_samples.setdefault(key, []).append({
                            "date": item["date"], "stock": item["stock"],
                            "start": cycle["start"], "evidence": cycle["evidence"],
                        })
            normalized_saved = saved_type.replace("돌파", "주도주 돌파").replace("눌림", "주도주 돌파")
            if not saved or normalized_saved != calculated:
                changed.append({"date": item["date"], "stock": item["stock"], "saved": saved_type, "current": calculated})
            if any(cycle["calculated_type"] == "기타" for cycle in item["cycles"]):
                unresolved.append({"date": item["date"], "stock": item["stock"], "cycles": item["cycles"]})
        output = {
            "episode_count": len(report), "saved_types": saved_types,
            "calculated_types": calculated_types, "subtype_counts": subtype_counts,
            "subtype_samples": {key: value[:3] for key, value in subtype_samples.items()},
            "old_to_new": {f"{old} -> {new}": count for (old, new), count in old_to_new.most_common()},
            "outcomes_by_subtype": {
                key: {
                    "count": len(values),
                    "win_rate": round(sum(value > 0 for value in values) / len(values) * 100, 1),
                    "average_return": round(sum(values) / len(values), 3),
                    "median_return": round(sorted(values)[len(values) // 2], 3),
                }
                for key, values in outcome_by_subtype.items()
            },
            "low_context_by_subtype": low_context_by_subtype,
            "low_context_samples": low_context_samples[:20],
            "disagreement_samples": {key: value[:3] for key, value in disagreement_samples.items()},
            "overlap_counts": overlap_counts,
            "overlap_samples": {key: value[:5] for key, value in overlap_samples.items()},
            "changed_count": len(changed),
            "changed_samples": changed[:30], "unresolved_count": len(unresolved),
            "unresolved_samples": unresolved[:20],
        }
        if args.compact:
            output.pop("unresolved_samples", None)
            output["changed_samples"] = changed[:10]
        if args.metrics_only:
            output = {
                "episode_count": output["episode_count"],
                "subtype_counts": output["subtype_counts"],
                "outcomes_by_subtype": output["outcomes_by_subtype"],
                "unresolved_count": output["unresolved_count"],
                "low_context_by_subtype": output["low_context_by_subtype"],
                "low_context_samples": output["low_context_samples"],
                "overlap_counts": output["overlap_counts"],
                "overlap_samples": output["overlap_samples"],
            }
    else:
        output = {"episode_count": len(report), "episodes": report}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
