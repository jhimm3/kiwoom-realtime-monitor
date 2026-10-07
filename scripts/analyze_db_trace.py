"""Verify and summarize a compact NAS DB trace without displaying payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def summarize(directory: Path, *, start: float | None = None,
              end: float | None = None, window_seconds: int | None = None) -> dict:
    if window_seconds is not None and (type(window_seconds) is not int
                                       or not 1 <= window_seconds <= 3900):
        raise ValueError("window_seconds_out_of_bounds")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    event_count = 0
    starts = {}
    ends = {}
    window_buckets = {}
    operation_starts = {}
    operation_ends = set()
    duplicate_operation_ids = 0
    collector_prefix_events = []
    unknown_window_time_events = 0
    started_mono_ns = manifest.get("started_mono_ns")
    finished_mono_ns = manifest.get("finished_mono_ns")
    if (type(started_mono_ns) is int and type(finished_mono_ns) is int
            and finished_mono_ns >= started_mono_ns):
        capture_duration = (finished_mono_ns - started_mono_ns) / 1e9
    else:
        capture_duration = max(0.0, float(manifest.get("finished_at", manifest["started_at"]))
                               - float(manifest["started_at"]))
    problems = []
    previous_seq = 0
    missing_sequence_count = 0
    sequence_gap_examples = []
    for chunk in manifest["chunks"]:
        name = chunk["name"]
        if not isinstance(name, str) or re.fullmatch(r"[0-9]{6}\.jsonl", name) is None:
            raise ValueError("unsafe_trace_chunk")
        root_path = directory / name
        copied_path = directory / "chunks" / name
        path = root_path if root_path.is_file() else copied_path
        if (path.is_symlink() or (directory / "chunks").is_symlink()
                or path.resolve().parent not in {
                    directory.resolve(), directory.resolve() / "chunks"
                }):
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
                if len(sequence_gap_examples) < 10:
                    sequence_gap_examples.append({"after": previous_seq, "next": row["seq"]})
                missing_sequence_count += max(0, row["seq"] - previous_seq - 1)
                if row["seq"] <= previous_seq:
                    problems.append(f"sequence_not_increasing_after:{previous_seq}")
            previous_seq = row["seq"]
            if row["event_type"] == "call_start":
                starts[row["call_id"]] = row
            elif row["event_type"] == "call_end":
                ends[row["call_id"]] = row
            if window_seconds is not None:
                event_type = row.get("event_type", "unknown")
                if event_type == "operation_start":
                    event_time = row.get("entered_mono_ns", row.get("mono_ns"))
                else:
                    event_time = row.get("mono_ns")
                if type(event_time) is int and type(started_mono_ns) is int:
                    offset = (event_time - started_mono_ns) / 1e9
                elif type(row.get("wall_ns")) is int:
                    offset = row["wall_ns"] / 1e9 - float(manifest["started_at"])
                else:
                    offset = None
                if offset is None or offset < 0 or offset >= capture_duration:
                    unknown_window_time_events += 1
                else:
                    index = int(offset // window_seconds)
                    bucket = window_buckets.setdefault(index, {
                        "event_types": Counter(), "operations": Counter(),
                        "writers": Counter(), "rejections": Counter(),
                        "operation_payload_refs": set(), "missing_operation_payload_refs": 0,
                    })
                    bucket["event_types"][event_type] += 1
                    if event_type == "operation_start":
                        workload = row.get("workload_id") or "unknown"
                        method = row.get("method") or "unknown"
                        writer_kind = row.get("writer_kind") or method
                        bucket["operations"][workload] += 1
                        bucket["writers"][writer_kind] += 1
                        payload_ref = row.get("payload_ref")
                        if isinstance(payload_ref, str):
                            bucket["operation_payload_refs"].add(payload_ref)
                        else:
                            bucket["missing_operation_payload_refs"] += 1
                        operation_id = row.get("operation_id")
                        if isinstance(operation_id, str) and operation_id:
                            if operation_id in operation_starts:
                                duplicate_operation_ids += 1
                            else:
                                operation_starts[operation_id] = (index, workload)
                    elif event_type == "operation_end":
                        operation_id = row.get("operation_id")
                        if isinstance(operation_id, str) and operation_id:
                            operation_ends.add(operation_id)
                    elif event_type == "input_rejected":
                        bucket["rejections"][(row.get("workload_id") or "unknown",
                                               row.get("reason") or row.get("rejection_reason") or "unknown")] += 1
                    elif event_type == "collector_input":
                        component = row.get("producer_component") or "unknown"
                        payload_ref = row.get("payload_ref")
                        collector_prefix_events.append((offset, component, payload_ref))
        event_count += len(rows)
    if event_count != manifest["written"]:
        problems.append("manifest_written_mismatch")
    if previous_seq != manifest["last_seq"]:
        problems.append("manifest_last_seq_mismatch")
    if missing_sequence_count != manifest["known_dropped"]:
        problems.append("manifest_known_dropped_mismatch")

    window_inventory = None
    if window_seconds is not None:
        duration_windows = int((capture_duration + window_seconds - 1e-9) // window_seconds)
        if capture_duration > 0 and duration_windows == 0:
            duration_windows = 1
        collector_prefixes = defaultdict(lambda: {
            "event_count": 0, "payload_refs": set(), "missing_payload_refs": 0,
        })
        prefix_events = sorted(collector_prefix_events, key=lambda item: item[0])
        prefix_cursor = 0
        windows = []
        blobs = manifest.get("blobs", {})

        def declared_payload_bytes(payload_refs):
            total = 0
            missing = 0
            for digest in payload_refs:
                part = blobs.get(digest) if isinstance(blobs, dict) else None
                size = part.get("bytes") if isinstance(part, dict) else None
                if type(size) is int and size >= 0:
                    total += size
                else:
                    missing += 1
            return total, missing

        for index in range(duration_windows):
            window_start = index * window_seconds
            window_end = min(capture_duration, window_start + window_seconds)
            while prefix_cursor < len(prefix_events) and prefix_events[prefix_cursor][0] < window_end:
                _, component, digest = prefix_events[prefix_cursor]
                entry = collector_prefixes[component]
                entry["event_count"] += 1
                if isinstance(digest, str):
                    entry["payload_refs"].add(digest)
                else:
                    entry["missing_payload_refs"] += 1
                prefix_cursor += 1
            bucket = window_buckets.get(index, {
                "event_types": Counter(), "operations": Counter(), "writers": Counter(),
                "rejections": Counter(), "operation_payload_refs": set(),
                "missing_operation_payload_refs": 0,
            })
            selected_operation_ids = [operation_id for operation_id, (bucket_index, _) in operation_starts.items()
                                      if bucket_index == index]
            complete_operation_count = sum(operation_id in operation_ends
                                            for operation_id in selected_operation_ids)
            collector_detail = {}
            collector_payload_refs = set()
            for component, entry in sorted(collector_prefixes.items()):
                size, missing = declared_payload_bytes(entry["payload_refs"])
                collector_detail[component] = {
                    "prefix_input_events": entry["event_count"],
                    "distinct_payload_refs": len(entry["payload_refs"]),
                    "input_events_without_payload_ref": entry["missing_payload_refs"],
                    "declared_payload_bytes": size,
                    "payload_refs_missing_from_manifest": missing,
                }
                collector_payload_refs.update(entry["payload_refs"])
            operation_bytes, operation_missing = declared_payload_bytes(bucket["operation_payload_refs"])
            combined_bytes, combined_missing = declared_payload_bytes(
                collector_payload_refs | bucket["operation_payload_refs"])
            windows.append({
                "start_seconds": window_start,
                "end_seconds": window_end,
                "event_types": dict(sorted(bucket["event_types"].items())),
                "operations_by_workload": dict(sorted(bucket["operations"].items())),
                "operation_starts_by_writer": dict(sorted(bucket["writers"].items())),
                "operation_starts": len(selected_operation_ids),
                "operation_ends_for_starts": complete_operation_count,
                "operation_starts_without_end": len(selected_operation_ids) - complete_operation_count,
                "rejections_by_workload_and_reason": [
                    {"workload": workload, "reason": reason, "count": count}
                    for (workload, reason), count in sorted(bucket["rejections"].items())
                ],
                "operation_payload_refs": len(bucket["operation_payload_refs"]),
                "operation_payload_bytes_declared": operation_bytes,
                "operation_payload_refs_missing_from_manifest": (
                    operation_missing + bucket["missing_operation_payload_refs"]),
                "collector_prefix_by_component": collector_detail,
                "collector_prefix_payload_bytes_declared": declared_payload_bytes(
                    collector_payload_refs)[0],
                "combined_unique_payload_bytes_declared": combined_bytes,
                "combined_payload_refs_missing_from_manifest": combined_missing + (
                    bucket["missing_operation_payload_refs"]),
                "metadata_only_preflight": True,
                "replay_ready": False,
            })
        window_inventory = {
            "window_seconds": window_seconds,
            "capture_duration_seconds": round(capture_duration, 3),
            "windows": windows,
            "operation_ids_duplicate_starts": duplicate_operation_ids,
            "operation_starts_without_any_end": sum(operation_id not in operation_ends
                                                     for operation_id in operation_starts),
            "events_without_usable_window_time": unknown_window_time_events,
            "scope_note": ("Metadata-only inventory. Payload byte counts are declared distinct blob lengths, "
                           "not checksum verification. Rejection/drop checks, codec/allowlist validation, "
                           "collector state equivalence, and the replay compiler still determine support. "
                           "Windows cover the full capture regardless of --start-seconds/--end-seconds. "
                           "A zero count for an event/workload is not proof of complete capture."),
        }
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
        "input_rejected": manifest.get("input_rejected", 0),
        "input_capture_censored": manifest.get("input_capture_censored", False),
        "payload_capture": manifest.get("payload_capture", {}),
        "input_coverage": manifest.get("input_coverage", {}),
        "capture_integrity_ok": (manifest["state"] == "complete"
                             and manifest["accepted"] == manifest["written"]
                             and manifest["known_dropped"] == 0
                             and missing_sequence_count == 0
                             and manifest.get("input_rejected", 0) == 0
                             and not manifest.get("input_capture_censored", False)
                             and not problems),
        "bytes_written": manifest["bytes_written"], "chunks": len(manifest["chunks"]),
        "missing_sequence_count": missing_sequence_count,
        "sequence_gap_examples": sequence_gap_examples,
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
        "window_inventory": window_inventory,
        "scope_note": ("Instrumented DB calls only; a running manifest excludes unflushed RAM events. "
                       "capture_integrity_ok checks recorded chunks and counters, not payload blobs, "
                       "workload-specific replay support, or source-state equivalence."),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--start-seconds", type=float)
    parser.add_argument("--end-seconds", type=float)
    parser.add_argument("--window-seconds", type=int,
                        help="include metadata-only capture-relative workload/rejection/payload windows")
    args = parser.parse_args()
    print(json.dumps(summarize(args.directory, start=args.start_seconds,
                               end=args.end_seconds, window_seconds=args.window_seconds),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
