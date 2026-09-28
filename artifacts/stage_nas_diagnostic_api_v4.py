"""Selectively stage the diagnostic API on the NAS's divergent v2 source tree.

This is a one-time staging helper. It never builds or restarts containers.
"""
from __future__ import annotations

import argparse
import difflib
import shutil
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
OLD = "2026.09.28-db-observability-v2"
NEW = "2026.09.29-diagnostic-api-v4"


def once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor, got {count}: {old[:100]!r}")
    return text.replace(old, new, 1)


def between(text: str, start: str, end: str) -> str:
    if text.count(start) != 1:
        raise RuntimeError(f"ambiguous start: {start!r}")
    first = text.index(start)
    last = text.index(end, first)
    return text[first:last]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build(root: Path) -> dict[str, str]:
    source = PROJECT
    app_name = "src/kiwoom_monitor/central_server/app.py"
    db_name = "src/kiwoom_monitor/central_server/database.py"
    work_name = "src/kiwoom_monitor/central_server/diagnostic_workloads.py"
    compose_name = "deploy/synology/docker-compose.yml"
    dockerfile_name = "deploy/synology/server.Dockerfile"
    app = read(root / app_name)
    local_app = read(source / app_name)
    app = once(app, f'SERVER_BUILD = "{OLD}"', f'SERVER_BUILD = "{NEW}"')
    app = once(app,
               "    active = settings or CentralServerSettings.from_environment()\n",
               "    active = settings or CentralServerSettings.from_environment()\n"
               "    diagnostic_runs = None\n")
    app = once(app, "        yield\n        if credential_runtime is not None:\n",
               "        yield\n        if diagnostic_runs is not None:\n"
               "            await asyncio.to_thread(diagnostic_runs.close)\n"
               "        if credential_runtime is not None:\n")
    app = once(app, "    async def lifespan(_app: Any):\n        try:\n",
               "    async def lifespan(_app: Any):\n        try:\n"
               "            _app.state.diagnostic_loop = asyncio.get_running_loop()\n")
    models = between(local_app, "    class DiagnosticControlRequest(BaseModel):\n",
                     "    class QueryRequest(BaseModel):\n")
    app = once(app, "    class QueryRequest(BaseModel):\n",
               models + "    class QueryRequest(BaseModel):\n")
    app = once(app,
               "        return await asyncio.to_thread(explain)\n\n"
               "    @app.put(\"/api/v1/settings/operations\"",
               "        result = await asyncio.to_thread(explain)\n"
               "        if len(json.dumps(result).encode(\"utf-8\")) > 1_048_576:\n"
               "            raise HTTPException(503, detail=\"DIAGNOSTIC_PLAN_TOO_LARGE\")\n"
               "        return result\n\n"
               "    @app.put(\"/api/v1/settings/operations\"")
    routes = between(local_app, "    from .diagnostic_workloads import (\n",
                     "    @app.put(\"/api/v1/settings/operations\"")
    app = once(app, "    @app.put(\"/api/v1/settings/operations\"",
               routes + "    @app.put(\"/api/v1/settings/operations\"")

    database = read(root / db_name)
    local_database = read(source / db_name)
    method_start = "    def explain_news_job_claim_plan(self) -> dict[str, Any]:\n"
    method_end = "        now_epoch = time()\n"
    old_method = between(database, method_start, method_end)
    new_method = between(local_database, method_start, method_end)
    database = once(database, old_method, new_method)

    work = read(source / work_name)
    work = once(work, '    "historical_news_archive",\n', "")
    compose = once(read(root / compose_name), OLD, NEW)
    dockerfile = once(read(root / dockerfile_name), OLD, NEW)
    dockerfile = once(dockerfile,
                      "    && grep -q '/api/v1/diagnostics/news-job-claim-plan' /app/src/kiwoom_monitor/central_server/app.py \\\n",
                      "    && grep -q '/api/v1/diagnostics/news-job-claim-plan' /app/src/kiwoom_monitor/central_server/app.py \\\n"
                      "    && grep -q '/api/v1/diagnostics/capabilities' /app/src/kiwoom_monitor/central_server/app.py \\\n")
    result = {app_name: app, db_name: database, work_name: work,
              compose_name: compose, dockerfile_name: dockerfile}
    for relative in (
        "src/kiwoom_monitor/central_server/diagnostic_metrics.py",
        "src/kiwoom_monitor/central_server/diagnostic_runs.py",
        "src/kiwoom_monitor/central_server/diagnostic_sampling.py",
        "scripts/nas_workload_diagnostic.py",
    ):
        result[relative] = read(source / relative)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if root.name != "kiwoom-monitor" or not (root / "deploy/synology/docker-compose.yml").is_file():
        raise RuntimeError("unexpected NAS project root")
    output = build(root)
    for relative, updated in output.items():
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise RuntimeError(f"target escapes NAS project: {relative}")
        original = read(target) if target.exists() else ""
        diff = list(difflib.unified_diff(original.splitlines(), updated.splitlines(), n=0))
        print(f"{relative}: original={len(original)} new={len(updated)} diff_lines={len(diff)}")
    if not args.apply:
        return
    backup = root.parent / "kiwoom-monitor-backups" / "20260929-diagnostic-api-v4"
    if backup.exists():
        raise RuntimeError(f"backup already exists: {backup}")
    for relative in output:
        target = root / relative
        if target.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
    for relative, updated in output.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".diagnostic-v4-staging")
        temporary.write_text(updated, encoding="utf-8", newline="\n")
        temporary.replace(target)
    print(f"staged {len(output)} files; backups at {backup}")


if __name__ == "__main__":
    main()
