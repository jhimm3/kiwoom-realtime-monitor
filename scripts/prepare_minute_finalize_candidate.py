"""Create an inactive minute-finalization replay candidate from a pinned release.

This one-off publisher carries forward the previous experiment verbatim and
overlays only the reviewed implementation, its PostgreSQL tests, and build ID.
It never changes active.json or the runtime/dependency contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


BUILD = "2026.10.09-realtime-minute-batch-v2"
BASE_RELEASE = "2026.10.09-minute-finalize-batch-v6-13cb047145671dd8"
ACTIVE_RELEASE = "2026.10.08-trace-ram-8g-5m-v1-e1cc01dde5bacbb9"
SOURCE = Path(__file__).resolve().parents[1]
APP = "src/kiwoom_monitor/central_server/app.py"
OVERLAY = (
    "src/kiwoom_monitor/central_server/database_market_bars.py",
    "tests/integration/test_storage_boundary_postgres.py",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def checked(root: Path, relative: str) -> Path:
    if not relative or "\\" in relative or relative.startswith("/"):
        raise ValueError("unsafe_release_path")
    if any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError("unsafe_release_path")
    path = root.joinpath(*relative.split("/"))
    path.resolve().relative_to(root.resolve())
    if path.is_symlink():
        raise ValueError("symlink_release_path")
    return path


def publish(nas_root: Path) -> dict:
    store = nas_root.resolve() / "source-runtime"
    active_bytes = (store / "active.json").read_bytes()
    active = json.loads(active_bytes)["release_id"]
    if active != ACTIVE_RELEASE:
        raise ValueError("active_release_changed")

    base = checked(store / "releases", BASE_RELEASE)
    base_manifest_bytes = (base / "manifest.json").read_bytes()
    manifest = json.loads(base_manifest_bytes)
    if manifest.get("release_id") != BASE_RELEASE:
        raise ValueError("base_manifest_release_mismatch")
    runtime = read_json(store / "runtime.json")
    if manifest.get("contract") not in [runtime.get("contract"), *runtime.get("accepted_contracts", [])]:
        raise ValueError("runtime_contract_mismatch")

    # Verify the pinned source before deriving a candidate from it.
    for name, expected in manifest["files"].items():
        if digest(checked(base, name).read_bytes()) != expected:
            raise ValueError("base_release_hash_mismatch")

    files = dict(manifest["files"])
    payloads = {name: checked(SOURCE, name).read_bytes() for name in OVERLAY}
    app = checked(base, APP).read_bytes()
    if digest(app) != files.get(APP):
        raise ValueError("base_app_hash_mismatch")
    app, replacements = re.subn(
        rb'^SERVER_BUILD = "[A-Za-z0-9._-]+"$',
        f'SERVER_BUILD = "{BUILD}"'.encode(), app, flags=re.MULTILINE,
    )
    if replacements != 1:
        raise ValueError("server_build_marker_missing")
    payloads[APP] = app
    for name, data in payloads.items():
        if name not in files:
            raise ValueError("overlay_path_missing_from_base")
        files[name] = digest(data)

    content_hash = digest(json.dumps(files, sort_keys=True).encode())
    release_id = f"{BUILD}-{content_hash[:16]}"
    target = store / "releases" / release_id
    if target.exists():
        raise ValueError("candidate_already_exists")
    temporary = Path(tempfile.mkdtemp(prefix=".minute-finalize-", dir=target.parent))
    try:
        for name, expected in files.items():
            data = payloads.get(name)
            if data is None:
                data = checked(base, name).read_bytes()
            if digest(data) != expected:
                raise ValueError("candidate_payload_hash_mismatch")
            out = checked(temporary, name)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
        candidate = dict(
            manifest,
            release_id=release_id,
            server_build=BUILD,
            files=files,
            src_hash=digest(json.dumps(
                {key: value for key, value in files.items() if key.startswith("src/")},
                sort_keys=True,
            ).encode()),
            working_tree_dirty=True,
            replay_experiment_overlay={
                "base_release": BASE_RELEASE,
                "files": [*OVERLAY, APP],
                "source_state_equivalent": False,
                "deployed": False,
            },
        )
        (temporary / "manifest.json").write_text(
            json.dumps(candidate, sort_keys=True) + "\n", encoding="utf-8",
        )
        if json.loads((store / "active.json").read_bytes()) != json.loads(active_bytes):
            raise ValueError("active_release_changed_during_publish")
        if (base / "manifest.json").read_bytes() != base_manifest_bytes:
            raise ValueError("base_manifest_changed_during_publish")
        temporary.replace(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "release_id": release_id,
        "server_build": BUILD,
        "base_release": BASE_RELEASE,
        "files": len(files),
        "overlay": [*OVERLAY, APP],
        "active_release_unchanged": True,
        "runtime_contract_unchanged": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nas-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.nas_root), sort_keys=True))
