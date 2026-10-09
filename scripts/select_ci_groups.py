"""Select extra CI environments conservatively from reviewed changed paths."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("windows_fast", "windows_long", "postgres", "sealed", "operator")


def select(paths: list[str], mode: str) -> dict[str, object]:
    if mode == "weekly" or mode == "comprehensive":
        chosen = set(GROUPS)
        reason = "scheduled_or_requested_comprehensive"
    elif mode == "nightly":
        chosen = {"windows_fast", "postgres"}
        reason = "daily_extended"
    elif mode == "auto":
        chosen = set()
        reason = "changed_paths"
        for raw in paths:
            path = raw.replace("\\", "/")
            if path.startswith("docs/") or path in {"README.md", "CHANGELOG.md"}:
                continue
            chosen.add("windows_fast")
            if path.startswith("src/kiwoom_monitor/presentation/"):
                continue
            if path.startswith("tests/integration/test_"):
                name = path.removeprefix("tests/integration/").removesuffix(".py")
                if name == "test_nas_operator_linux":
                    chosen.add("operator")
                elif name in {"test_recorded_replay_baseline_postgres",
                              "test_recorded_execution_postgres", "test_replay_cache_baseline_postgres",
                              "test_replay_cache_execution_postgres", "test_top20_replay_runtime_postgres",
                              "test_top20_session_postgres"}:
                    chosen.update(("sealed", "windows_long"))
                elif name.startswith("test_") and name.endswith("_postgres"):
                    chosen.add("postgres")
                else:
                    chosen.update(GROUPS)
                continue
            if path.startswith("src/kiwoom_monitor/central_server/") or path.startswith(
                "src/kiwoom_monitor/application/"
            ):
                chosen.add("postgres")
                if any(term in path for term in (
                    "database", "replay", "top20", "recorded", "observation",
                    "market_event", "diagnostic", "cache", "snapshot",
                )):
                    chosen.update(("sealed", "windows_long"))
                else:
                    chosen.update(GROUPS)
                continue
            # Runner, fixture, unknown source and deployment changes have no proven narrow boundary.
            chosen.update(GROUPS)
        if not paths:
            chosen.update(GROUPS)
            reason = "unknown_change_set"
    else:
        raise ValueError(f"Unknown CI selection mode: {mode}")
    return {"mode": mode, "reason": reason, "changed_paths": paths,
            "groups": {name: name in chosen for name in GROUPS}}


def _changed_paths(base: str) -> tuple[list[str], bool]:
    if not base or set(base) == {"0"}:
        return [], False
    result = subprocess.run(["git", "diff", "--name-only", "--no-ext-diff", base, "HEAD"],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        return [], False
    risky = subprocess.run(["git", "diff", "--name-only", "--diff-filter=DR", base, "HEAD"],
                           cwd=ROOT, capture_output=True, text=True)
    if risky.returncode:
        return [], False
    return result.stdout.splitlines(), not bool(risky.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "nightly", "weekly", "comprehensive"), required=True)
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.mode == "auto":
        paths, certain = _changed_paths(args.base_ref)
        result = select(paths, "auto" if certain else "comprehensive")
        if not certain:
            result["reason"] = "unavailable_base_or_rename_delete"
    else:
        result = select([], args.mode)
    result["base_ref"] = args.base_ref
    result["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                             cwd=ROOT, text=True).strip()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.report.exists():
        raise FileExistsError(f"Refusing to overwrite CI selection: {args.report}")
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            for name, enabled in result["groups"].items():
                stream.write(f"{name}={'true' if enabled else 'false'}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
