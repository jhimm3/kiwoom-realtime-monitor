"""Build a news-only immutable candidate on top of the verified active NAS source.

Reads the mapped share. Writes only this checkout's diagnostic staging folder.
Never copies a stale whole development checkout over the deployed product.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE_RELEASE = "2026.10.14-recorder-shared-10m-v1-af0f8072f960415b"
BUILD = "2026.10.15-morning-market-brief-v1"
TRACKED = (
    "src/kiwoom_monitor/central_server/ai_service.py",
    "src/kiwoom_monitor/central_server/app.py",
    "src/kiwoom_monitor/central_server/operational_settings_routes.py",
    "src/kiwoom_monitor/central_server/news_service_routes.py",
    "src/kiwoom_monitor/central_server/news_jobs.py",
    "src/kiwoom_monitor/central_server/database_documents.py",
    "src/kiwoom_monitor/central_server/diagnostic_read_routes.py",
    "src/kiwoom_monitor/central_server/diagnostic_workloads.py",
    "src/kiwoom_monitor/central_server/diagnostic_writer_registry.py",
    "src/kiwoom_monitor/infrastructure/central_ai_client.py",
    "src/kiwoom_monitor/infrastructure/naver_news.py",
    "src/kiwoom_monitor/infrastructure/news_ai.py",
    "src/kiwoom_monitor/infrastructure/persistence/settings_backup.py",
    "src/kiwoom_monitor/presentation/news_execution.py",
    "src/kiwoom_monitor/presentation/news_settings_dialog.py",
    "src/kiwoom_monitor/presentation/news_workers.py",
    "src/kiwoom_monitor/presentation/stock_news_window.py",
    "src/kiwoom_monitor/presentation/main_window.py",
    "tests/regression_profiles.json",
    "tests/ci_groups.json",
)
ADDED = (
    "docs/workstreams/morning-market-brief-output.schema.json",
    "src/kiwoom_monitor/infrastructure/chatgpt_auth.py",
    "src/kiwoom_monitor/infrastructure/chatgpt_plan.py",
    "src/kiwoom_monitor/infrastructure/chatgpt_linux_store.py",
    "src/kiwoom_monitor/central_server/chatgpt_sessions.py",
    "tests/unit/test_chatgpt_plan.py", "tests/unit/test_chatgpt_plan_settings.py",
    "tests/unit/test_nas_chatgpt_plan.py", "scripts/validate_news_chatgpt_plan.py",
    "scripts/nas_chatgpt_session.py", "scripts/move_chatgpt_session_to_nas.py",
    "scripts/install_nas_chatgpt_runtime.py", "scripts/nas_chatgpt_session_operator.py",
    "tests/unit/test_chatgpt_nas_transfer.py",
    "src/kiwoom_monitor/application/morning_market_brief.py",
    "src/kiwoom_monitor/central_server/morning_market_brief.py",
    "src/kiwoom_monitor/presentation/morning_market_brief_dialog.py",
    "tests/unit/test_morning_market_brief.py", "tests/unit/test_morning_market_ai.py",
    "tests/unit/test_morning_market_brief_dialog.py", "scripts/validate_morning_market_brief.py",
    "tests/integration/test_chatgpt_linux_store.py",
)


def sha(data): return hashlib.sha256(data).hexdigest()


def prepare(share: Path, output: Path):
    output = output.resolve()
    output.resolve().relative_to((ROOT / "data/diagnostics/news-chatgpt-plan").resolve())
    if output.exists(): raise RuntimeError("staging destination already exists")
    store = share / "source-runtime"
    # active.json is root-private: status is checked separately through the
    # installed operator. This fixed release is independently hashed here.
    baseline = store / "releases" / BASE_RELEASE
    manifest_raw = (baseline / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest["release_id"] != BASE_RELEASE: raise RuntimeError("baseline mismatch")
    candidate = output / "candidate"
    candidate.mkdir(parents=True)
    for name, expected in manifest["files"].items():
        if name != "pyproject.toml" and not name.startswith(("src/", "tests/", "scripts/")):
            raise RuntimeError("baseline path outside code scope")
        source, target = baseline / name, candidate / name
        target.resolve().relative_to(candidate.resolve())
        cached = ROOT / "data/diagnostics/news-chatgpt-plan/nas-stage-01/candidate" / name
        data = cached.read_bytes() if cached.is_file() else source.read_bytes()
        if sha(data) != expected:
            data = source.read_bytes()
        if sha(data) != expected: raise RuntimeError("baseline checksum mismatch: " + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    patch = subprocess.check_output(["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--", *TRACKED], cwd=ROOT)
    patch_path = output / "news-only.patch"
    patch_path.write_bytes(patch)
    app_path = candidate / "src/kiwoom_monitor/central_server/app.py"
    head_app = subprocess.check_output(["git", "show", "HEAD:src/kiwoom_monitor/central_server/app.py"], cwd=ROOT).decode()
    head_marker = re.search(r'^SERVER_BUILD = "[A-Za-z0-9._-]+"$', head_app, re.M).group()
    app_path.write_text(re.sub(r'^SERVER_BUILD = "[A-Za-z0-9._-]+"$', head_marker,
        app_path.read_text(encoding="utf-8"), flags=re.M), encoding="utf-8", newline="\n")
    directory = candidate.relative_to(ROOT).as_posix()
    subprocess.run(["git", "apply", "--check", "--directory=" + directory, str(patch_path)], cwd=ROOT, check=True)
    subprocess.run(["git", "apply", "--directory=" + directory, str(patch_path)], cwd=ROOT, check=True)
    # The active NAS predates the unrelated RAM-selection client API. Add only
    # subscription methods to its older operational client, preserving that base.
    name = "src/kiwoom_monitor/infrastructure/central_operational_settings.py"
    old = (candidate / name).read_text(encoding="utf-8")
    own = (ROOT / name).read_text(encoding="utf-8")
    methods = own[own.index("    def chatgpt_status("):own.index("    def _request(")]
    before = '    def _request(self, method: str, body: dict[str, object] | None = None) -> dict[str, object]:'
    after = '    def _request(self, method: str, body: dict[str, object] | None = None, *, path: str = "/api/v1/settings/operations") -> dict[str, object]:'
    if old.count(before) != 1: raise RuntimeError("operational client integration base changed")
    old = old.replace(before, methods + after)
    old = old.replace("{self._source.server_url.rstrip('/')}/api/v1/settings/operations", "{self._source.server_url.rstrip('/')}{path}")
    old = old.replace('        self._revision = revision if isinstance(revision, int) and not isinstance(revision, bool) else None',
        '        if path == "/api/v1/settings/operations":\n            self._revision = revision if isinstance(revision, int) and not isinstance(revision, bool) else None')
    (candidate / name).write_text(old, encoding="utf-8", newline="\n")
    for name in ADDED:
        target = candidate / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    app = candidate / "src/kiwoom_monitor/central_server/app.py"
    text = app.read_text(encoding="utf-8")
    text, changed = re.subn(r'^SERVER_BUILD = "[A-Za-z0-9._-]+"$', f'SERVER_BUILD = "{BUILD}"', text, flags=re.M)
    if changed != 1: raise RuntimeError("build marker absent")
    app.write_text(text, encoding="utf-8", newline="\n")
    # The Linux mock gate keeps the deployed dependency contract. It is never
    # eligible for production without the separate runtime dependency check.
    files = {name: sha((candidate / name).read_bytes()) for name in sorted(set(manifest["files"]) | set(ADDED))}
    content = sha(json.dumps(files, sort_keys=True).encode())
    result = {**manifest, "release_id": BUILD + "-" + content[:16], "server_build": BUILD,
              "files": files, "src_hash": sha(json.dumps({name: value for name, value in files.items()
                  if name.startswith("src/")}, sort_keys=True).encode()), "working_tree_dirty": True}
    (candidate / "manifest.json").write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    (output / "preparation.json").write_text(json.dumps({
        "base_release": BASE_RELEASE, "base_manifest_sha256": sha(manifest_raw),
        "worktree_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "patch_sha256": sha(patch), "candidate_release": result["release_id"],
        "runtime_dependency_verified": False, "operationally_deployed": False,
    }, indent=2) + "\n", encoding="utf-8")
    return {"base_release": BASE_RELEASE, "candidate_release": result["release_id"], "files": len(files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--share", type=Path, default=Path("X:/kiwoom-monitor"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.share, args.output)))
