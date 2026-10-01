"""Publish complete source snapshots and start/test one pinned NAS release.

The release store is bind-mounted once. Publishing never changes the active
source directory; a manual server restart selects the atomically published ID.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


FORMAT = 1
DEFAULT_IMAGE = "kiwoom-monitor-server:2026.10.01-db-writer-candidate-fixes-v1"
CONTRACT_FILES = (
    "pyproject.toml", "deploy/synology/server.Dockerfile",
    "src/kiwoom_monitor/central_server/central_schema.py",
    "src/kiwoom_monitor/central_server/schema_migrations.py",
)
BUILD_FILE = "src/kiwoom_monitor/central_server/app.py"
RELEASE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict:
    if path.stat().st_size > 2_000_000:
        raise RuntimeError(f"metadata exceeds size limit: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("format") != FORMAT:
        raise RuntimeError(f"unsupported metadata: {path.name}")
    return value


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_marker(root: Path) -> str:
    match = re.search(r'^SERVER_BUILD = "([A-Za-z0-9._-]+)"$',
                      (root / BUILD_FILE).read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise RuntimeError("SERVER_BUILD marker is missing")
    return match.group(1)


def source_contract(root: Path) -> dict[str, str]:
    return {name: digest(root / name) for name in CONTRACT_FILES}


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    if not relative or "\\" in relative or path.is_symlink():
        raise RuntimeError("unsafe source path")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError("source path escapes release") from error
    if not relative.startswith(("src/", "tests/", "scripts/")) and relative != "pyproject.toml":
        raise RuntimeError("source path is outside the code allowlist")
    return path


def release(root: Path, release_id: str | None = None) -> tuple[Path, dict]:
    if release_id is None:
        release_id = read_json(root / "active.json")["release_id"]
    if not isinstance(release_id, str) or not RELEASE_PATTERN.fullmatch(release_id):
        raise RuntimeError("invalid release ID")
    path = root / "releases" / release_id
    if path.is_symlink() or path.resolve().parent != (root / "releases").resolve():
        raise RuntimeError("release path escapes store")
    manifest = read_json(path / "manifest.json")
    if manifest.get("release_id") != release_id:
        raise RuntimeError("release ID does not match manifest")
    runtime = read_json(root / "runtime.json")
    accepted = [runtime.get("contract"), *runtime.get("accepted_contracts", [])]
    if manifest.get("contract") not in accepted:
        raise RuntimeError("runtime/dependency/schema contract changed; use a reviewed runtime update")
    content_hash = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()
    if release_id != f"{manifest['server_build']}-{content_hash[:16]}":
        raise RuntimeError("manifest content does not match release ID")
    for name, expected in manifest["files"].items():
        if digest(checked_path(path, name)) != expected:
            raise RuntimeError(f"release file changed: {name}")
    if build_marker(path) != manifest["server_build"]:
        raise RuntimeError("source build marker does not match manifest")
    return path, manifest


def stage(source: Path, root: Path, image: str) -> dict:
    source, root = source.resolve(), root.resolve()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]*", image):
        raise RuntimeError("invalid dependency image reference")
    if root == source or source.is_relative_to(root):
        raise RuntimeError("release store must not contain the working checkout")
    contract = source_contract(source)
    root.mkdir(parents=True, exist_ok=True)
    runtime_path = root / "runtime.json"
    runner_bytes = Path(__file__).read_bytes()
    if runtime_path.exists():
        runtime = read_json(runtime_path)
        if runtime["image"] != image or runtime["contract"] != contract:
            raise RuntimeError("dependency/Dockerfile/schema changed; prepare a new runtime before source deployment")
        if (root / "runner.py").read_bytes() != runner_bytes:
            raise RuntimeError("runtime launcher changed; a new runtime store must be prepared")
    else:
        (root / "runner.py").write_bytes(runner_bytes)
        atomic_json(runtime_path, {"format": FORMAT, "image": image, "contract": contract})
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
         "src", "tests", "scripts", "pyproject.toml"], cwd=source,
    ).decode("utf-8").split("\0")
    files = {name: digest(checked_path(source, name)) for name in sorted(set(names)) if name}
    src_hash = hashlib.sha256(json.dumps(
        {name: value for name, value in files.items() if name.startswith("src/")},
        sort_keys=True,
    ).encode()).hexdigest()
    marker = build_marker(source)
    for old in (root / "releases").glob("*/manifest.json"):
        old_manifest = read_json(old)
        if old_manifest["server_build"] == marker and old_manifest["src_hash"] != src_hash:
            raise RuntimeError("SERVER_BUILD must change when server source changes")
    content_hash = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    release_id = f"{marker}-{content_hash[:16]}"
    destination = root / "releases" / release_id
    manifest = {"format": FORMAT, "release_id": release_id, "server_build": marker,
                "src_hash": src_hash, "files": files, "contract": contract,
                "git_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=source, text=True).strip(),
                "working_tree_dirty": bool(subprocess.check_output(
                    ["git", "status", "--porcelain", "--untracked-files=normal", "--",
                     "src", "tests", "scripts", "pyproject.toml"], cwd=source, text=True).strip())}
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        temporary = Path(tempfile.mkdtemp(prefix=".staging-", dir=destination.parent))
        try:
            for name, expected in files.items():
                target = checked_path(temporary, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(checked_path(source, name), target)
                if digest(target) != expected:
                    raise RuntimeError(f"source changed during publication: {name}")
            atomic_json(temporary / "manifest.json", manifest)
            os.rename(temporary, destination)
        finally:
            if temporary.exists():
                if temporary.resolve().parent != destination.parent.resolve():
                    raise RuntimeError("temporary release cleanup escaped the store")
                shutil.rmtree(temporary)
    _, verified = release(root, release_id)
    return {"release_id": release_id, "server_build": marker, "files": len(files),
            "git_commit": verified["git_commit"], "working_tree_dirty": verified["working_tree_dirty"],
            "active_changed": False}


def schema_plan(source: Path) -> list:
    code = (
        "import json; from kiwoom_monitor.central_server.central_schema import central_schema_migrations; "
        "print(json.dumps([[m.version,m.name,m.sqlite_statements,m.postgres_statements] "
        "for m in central_schema_migrations()]))"
    )
    environment = dict(os.environ, PYTHONPATH=str(source / "src"), PYTHONDONTWRITEBYTECODE="1")
    return json.loads(subprocess.check_output([sys.executable, "-c", code], cwd=source, env=environment, text=True))


def review_schema_update(source: Path, root: Path) -> dict:
    """Explicit append-only schema review; dependencies and active ID stay fixed.

    Kept contracts make an old release verifiable, not DB-compatible. The NAS
    deployment guard must still perform the offline DB compatibility step.
    """
    source, root = source.resolve(), root.resolve()
    if root == source or source.is_relative_to(root):
        raise RuntimeError("release store must not contain the working checkout")
    lock = root / "deploy.lock"
    lock.mkdir()  # Same ownership boundary as the NAS deployment shell.
    try:
        previous, manifest = release(root)
        runtime = read_json(root / "runtime.json")
        contract = source_contract(source)
        schema_file = "src/kiwoom_monitor/central_server/central_schema.py"
        for name in CONTRACT_FILES:
            if name != schema_file and contract[name] != runtime["contract"][name]:
                raise RuntimeError("dependency/driver/Dockerfile changed; schema-only review refused")
        old_plan, new_plan = schema_plan(previous), schema_plan(source)
        if not old_plan or len(new_plan) <= len(old_plan) or new_plan[:len(old_plan)] != old_plan:
            raise RuntimeError("schema review requires a strictly append-only migration plan")
        # This review also supplies the matching, specifically implemented
        # downgrade guard. Future migration families require their own review.
        if [item[:2] for item in new_plan[len(old_plan):]] != [[21, "shadow_checkpoint_frames"]]:
            raise RuntimeError("only the reviewed shadow checkpoint migration can be accepted")
        if not (source / "scripts/check_source_database.py").is_file():
            raise RuntimeError("source database compatibility guard is missing")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        for name in ("runtime.json", "runner.py"):
            shutil.copyfile(root / name, root / f"{name}.before-schema-{stamp}")
        accepted = list(runtime.get("accepted_contracts", []))
        if runtime["contract"] not in accepted:
            accepted.append(runtime["contract"])
        runner_temp = root / f".runner.{stamp}.tmp"
        try:
            with runner_temp.open("xb") as stream:
                stream.write(Path(__file__).read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
            # The new launcher accepts the old runtime contract, so this order
            # also remains usable if interrupted between the two replacements.
            os.replace(runner_temp, root / "runner.py")
            try:
                atomic_json(root / "runtime.json", {**runtime, "contract": contract, "accepted_contracts": accepted})
            except OSError:
                shutil.copyfile(root / f"runner.py.before-schema-{stamp}", runner_temp)
                os.replace(runner_temp, root / "runner.py")
                atomic_json(root / "runtime.json", runtime)
                raise
        finally:
            runner_temp.unlink(missing_ok=True)
        return {"schema_reviewed": 21, "active_changed": False,
                "previous_release": manifest["release_id"], "backups_suffix": f"before-schema-{stamp}"}
    finally:
        lock.rmdir()


def runtime_environment(root: Path, release_id: str | None) -> tuple[Path, dict, dict]:
    path, manifest = release(root, release_id)
    if digest(Path("/app/pyproject.toml")) != manifest["contract"]["pyproject.toml"]:
        raise RuntimeError("image dependency specification does not match source release")
    environment = dict(os.environ, PYTHONPATH=str(path / "src"), PYTHONDONTWRITEBYTECODE="1")
    return path, manifest, environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/app/source-runtime"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    staging = subparsers.add_parser("stage")
    staging.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    staging.add_argument("--image", default=DEFAULT_IMAGE)
    reviewing = subparsers.add_parser("review-schema-update")
    reviewing.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    for name in ("check", "select", "test", "launch", "info"):
        command = subparsers.add_parser(name)
        command.add_argument("--release", required=name == "select")
        if name == "test":
            command.add_argument("--test", action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "stage":
        print(json.dumps(stage(args.source, root, args.image), ensure_ascii=False))
        return 0
    if args.command == "review-schema-update":
        print(json.dumps(review_schema_update(args.source, root), ensure_ascii=False))
        return 0
    if args.command == "info":
        path, manifest = release(root, args.release)
        print(json.dumps({"release_id": manifest["release_id"], "server_build": manifest["server_build"],
                          "runtime_image": read_json(root / "runtime.json")["image"],
                          "source_path": str(path / "src")}, ensure_ascii=False))
        return 0
    path, manifest, environment = runtime_environment(root, args.release)
    if args.command == "select":
        atomic_json(root / "active.json", {"format": FORMAT, "release_id": manifest["release_id"]})
        print(json.dumps({"selected": manifest["release_id"], "server_build": manifest["server_build"]}))
        return 0
    if args.command == "check":
        code = ("import json; from kiwoom_monitor.central_server import app; "
                f"assert app.SERVER_BUILD == {manifest['server_build']!r}; "
                "print(json.dumps({'server_build': app.SERVER_BUILD, 'module': app.__file__}))")
        return subprocess.run([sys.executable, "-c", code], cwd=path, env=environment).returncode
    if args.command == "test":
        command = [sys.executable, str(path / "scripts/run_postgres_access_integration.py")]
        for name in args.test:
            command += ["--test", name]
        return subprocess.run(command, cwd=path, env=environment).returncode
    # Resolve the concrete release once. Publishing/selecting the next release
    # cannot change sys.path or lazily imported modules in this running process.
    os.chdir(path)
    os.execvpe(sys.executable, [sys.executable, "-m", "kiwoom_monitor.central_server"], environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
