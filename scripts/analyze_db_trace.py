"""Verify and summarize a compact NAS DB trace without displaying payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def summarize(directory: Path, *, start: float | None = None,
              end: float | None = None) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    events = []
    problems = []
    previous_seq = 0
    for chunk in manifest["chunks"]:
        path = directory / chunk["name"]
        if path.is_symlink() or path.parent.resolve() != directory.resolve():
            raise ValueError("unsafe_trace_chunk")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != chunk["sha256"]:
            problems.append(f"checksum:{chunk['name']}")
            continue
        rows = [json.loads(line) for line in content.splitlines()]
        if len(rows) != chunk["count"] or not rows or (
            rows[0]["seq"] != chunk["first_seq"] or rows[-1]["seq"] != chunk["last_seq"]
        ):
            problems.append(f"boundary:{chunk['name']}")
        for row in rows:
            if row["seq"] != previous_seq + 1:
                problems.append(f"seq_gap_after:{previous_seq}")
            previous_seq = row["seq"]
        events.extend(rows)
    if len(events) != manifest["written"]:
        problems.append("manifest_written_mismatch")
    starts = {row["call_id"]: row for row in events if row["event_type"] == "call_start"}
    ends = {row["call_id"]: row for row in events if row["event_type"] == "call_end"}
    completed = []
    for call_id, first in starts.items():
        last = ends.get(call_id)
        if last is None:
            continue
        offset = (first["wall_ns"] / 1e9) - manifest["started_at"]
        if start is not None and offset < start:
            continue
        if end is not None and offset >= end:
            continue
        completed.append((first, last, offset))
    groups = defaultdict(list)
    seconds = Counter()
    for first, last, offset in completed:
        groups[first["writer_family"] + "/" + first["writer_kind"]].append(last)
        seconds[int(offset)] += 1
    commit_windows = [(first, last, float(last["commit_started_at"]),
                       float(last["commit_finished_at"]))
                      for first, last, _ in completed
                      if last.get("commit_started_at") is not None
                      and last.get("commit_finished_at") is not None]
    slow = sorted((item for item in commit_windows
                   if float(item[1].get("commit_ms") or 0) >= 500),
                  key=lambda item: float(item[1]["commit_ms"]), reverse=True)
    slow_examples = []
    for first, last, started, finished in slow[:12]:
        overlaps = Counter(other_first["writer_kind"]
                           for other_first, other_last, other_started, other_finished
                           in commit_windows
                           if other_last["call_id"] != last["call_id"]
                           and other_started < finished and other_finished > started)
        slow_examples.append({"kind": first["writer_kind"],
                              "offset_seconds": round(started - manifest["started_at"], 3),
                              "commit_ms": last["commit_ms"],
                              "other_overlapping_commits": dict(overlaps)})

    def quantiles(rows: list, field: str) -> dict:
        values = sorted(float(row[field]) for row in rows if row.get(field) is not None)
        if not values:
            return {"count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
        return {"count": len(values), "p50_ms": round(median(values), 3),
                "p95_ms": round(values[(len(values) * 95 + 99) // 100 - 1], 3),
                "max_ms": round(values[-1], 3)}

    return {
        "trace_id": manifest["trace_id"], "state": manifest["state"],
        "started_at_utc": datetime.fromtimestamp(manifest["started_at"], timezone.utc).isoformat(),
        "finished_at_utc": (datetime.fromtimestamp(manifest["finished_at"], timezone.utc).isoformat()
                             if manifest.get("finished_at") else None),
        "events_written": manifest["written"], "events_accepted": manifest["accepted"],
        "known_dropped": manifest["known_dropped"], "queued_at_manifest": manifest.get("queued"),
        "bytes_written": manifest["bytes_written"], "chunks": len(manifest["chunks"]),
        "verified_chunks": len(manifest["chunks"]) - sum(value.startswith("checksum:") for value in problems),
        "problems": problems,
        "starts": len(starts), "ends": len(ends),
        "unmatched_starts": len(starts.keys() - ends.keys()),
        "unmatched_ends": len(ends.keys() - starts.keys()),
        "selected_completed_calls": len(completed),
        "busiest_seconds": seconds.most_common(10),
        "slow_commits_ge_500ms": len(slow),
        "slow_commit_examples": slow_examples,
        "groups": {kind: {"calls": len(rows),
                          "commits": sum(int(row.get("commits") or 0) for row in rows),
                          "db_errors": sum(row.get("outcome") not in ("committed", "read", "closed")
                                           for row in rows),
                          "execute": quantiles(rows, "execute_ms"),
                          "commit": quantiles(rows, "commit_ms"),
                          "total": quantiles(rows, "total_ms")}
                   for kind, rows in sorted(groups.items())},
        "scope_note": "Instrumented DB calls only; a running manifest excludes unflushed RAM events",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--start-seconds", type=float)
    parser.add_argument("--end-seconds", type=float)
    args = parser.parse_args()
    print(json.dumps(summarize(args.directory, start=args.start_seconds,
                               end=args.end_seconds), ensure_ascii=False))


if __name__ == "__main__":
    main()
