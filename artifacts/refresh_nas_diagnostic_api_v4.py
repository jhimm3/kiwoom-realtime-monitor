"""Refresh only the evolving v4 API block on the staged NAS source tree."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from stage_nas_diagnostic_api_v4 import PROJECT, between, once, read


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.name != "kiwoom-monitor":
        raise RuntimeError("unexpected NAS root")
    relative = Path("src/kiwoom_monitor/central_server/app.py")
    target = root / relative
    local = read(PROJECT / relative)
    staged = read(target)
    start = "    from .diagnostic_workloads import (\n"
    end = '    @app.put("/api/v1/settings/operations"'
    updated = once(staged, between(staged, start, end), between(local, start, end))
    if updated == staged:
        raise RuntimeError("app API block already matches")
    runs_relative = Path("src/kiwoom_monitor/central_server/diagnostic_runs.py")
    runs_target = root / runs_relative
    backup = root.parent / "kiwoom-monitor-backups" / "20260929-diagnostic-api-v4-refresh-1"
    if backup.exists():
        raise RuntimeError("refresh backup already exists")
    for path in (target, runs_target):
        saved = backup / path.relative_to(root)
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, saved)
    temporary = target.with_name(target.name + ".diagnostic-v4-refresh")
    temporary.write_text(updated, encoding="utf-8", newline="\n")
    temporary.replace(target)
    shutil.copy2(PROJECT / runs_relative, runs_target)
    print("refreshed staged app API and run owner; previous staged files backed up")


if __name__ == "__main__":
    main()
