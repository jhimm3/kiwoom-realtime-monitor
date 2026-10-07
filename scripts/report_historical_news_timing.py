"""Summarize the PC historical collector's append-only request timing log."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path


def _percentiles(values: list[int]) -> dict[str, int | None]:
    if not values:
        return {"median_ms": None, "p90_ms": None, "p95_ms": None, "max_ms": None}
    ordered = sorted(values)
    def rank(fraction: float) -> int:
        return ordered[max(0, int((len(ordered) * fraction + 0.999999)) - 1)]
    return {"median_ms": rank(.5), "p90_ms": rank(.9),
            "p95_ms": rank(.95), "max_ms": ordered[-1]}


def _lines(path: Path):
    with path.open("r", encoding="utf-8") as source:
        yield from source


def summarize(path: Path) -> dict[str, object]:
    requests: dict[tuple[str, str], list[int]] = defaultdict(list)
    statuses: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    stages: dict[str, list[int]] = defaultdict(list)
    preparation: dict[str, list[int]] = defaultdict(list)
    role_samples: dict[str, list[int]] = defaultdict(list)
    role_statuses: dict[str, Counter[str]] = defaultdict(Counter)
    cooldowns: Counter[str] = Counter()
    malformed_lines = 0
    for raw in _lines(path):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            malformed_lines += 1
            continue
        event = row.get("event")
        if isinstance(event, str) and event.startswith("market_prepare_"):
            for field, value in row.items():
                if field.endswith("_ms") and isinstance(value, (int, float)):
                    preparation[f"{event}.{field}"].append(int(value))
        if event in {"article_request", "search_request", "market_body_request",
                     "market_page_request"}:
            key = (str(row.get("host") or "unknown"),
                   str(row.get("url_role") or
                       ("market_page" if event == "market_page_request" else "search")))
            statuses[key][str(row.get("status") or "unknown")] += 1
            role_statuses[key[1]][str(row.get("status") or "unknown")] += 1
            duration = row.get("fetch_ms" if event == "search_request" else "elapsed_ms")
            if isinstance(duration, (int, float)):
                requests[key].append(int(duration))
                role_samples[key[1]].append(int(duration))
        elif event in {"search_cooldown", "market_page_retry_wait", "market_day_retry_wait"}:
            cooldowns[str(row.get("http_status") or "unknown")] += 1
        else:
            duration = row.get("elapsed_ms")
            if isinstance(duration, (int, float)):
                stages[str(event)].append(int(duration))
    hosts = []
    for key in sorted(statuses, key=lambda item: -sum(statuses[item].values())):
        samples = requests[key]
        hosts.append({"host": key[0], "role": key[1],
                      "attempts": sum(statuses[key].values()),
                      "timed_attempts": len(samples),
                      "over_2s": sum(value > 2000 for value in samples),
                      "at_least_5s": sum(value >= 5000 for value in samples),
                      "statuses": dict(statuses[key]), **_percentiles(samples)})
    return {"file": str(path), "hosts": hosts,
            "roles": {role: {"attempts": sum(role_statuses[role].values()),
                             "over_2s": sum(value > 2000 for value in samples),
                             "at_least_5s": sum(value >= 5000 for value in samples),
                             "statuses": dict(role_statuses[role]), **_percentiles(samples)}
                      for role in sorted(role_statuses)
                      for samples in [role_samples[role]]},
            "cooldowns_by_http_status": dict(cooldowns),
            "stages": {name: {"count": len(values), **_percentiles(values)}
                       for name, values in sorted(stages.items())},
            "preparation": {name: {"count": len(values), **_percentiles(values)}
                            for name, values in sorted(preparation.items())},
            "malformed_lines": malformed_lines}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.path), ensure_ascii=False))


if __name__ == "__main__":
    main()
