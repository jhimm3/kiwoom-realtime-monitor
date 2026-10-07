"""Publish immutable CREON raw responses alongside the historical DB run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path


DEFAULT_SOURCE = Path("data/historical_collection")
DEFAULT_NAS_PROJECT = Path(r"X:\kiwoom-monitor")
RAW_FOLDERS = ("daishin", "daishin-retry")


def raw_files(source_root: Path) -> list[tuple[Path, Path]]:
    source_root = source_root.resolve(strict=True)
    files: list[tuple[Path, Path]] = []
    for folder_name in RAW_FOLDERS:
        folder = source_root / folder_name
        if not folder.is_dir():
            raise FileNotFoundError(folder)
        for directory, subdirectories, names in os.walk(folder):
            subdirectories.sort()
            for name in subdirectories:
                if (Path(directory) / name).is_symlink():
                    raise ValueError(f"Unexpected CREON raw directory link: {Path(directory) / name}")
            for name in sorted(names):
                source = Path(directory) / name
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"Unexpected CREON raw artifact: {source}")
                relative = source.resolve(strict=True).relative_to(source_root)
                files.append((source, relative))
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_and_verify(source: Path, target: Path) -> dict[str, object]:
    before = source.stat()
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open("rb") as incoming, target.open("wb") as outgoing:
        for chunk in iter(lambda: incoming.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
            outgoing.write(chunk)
    shutil.copystat(source, target)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"CREON raw artifact changed during copy: {source}")
    if target.stat().st_size != before.st_size or _sha256(target) != digest.hexdigest():
        raise RuntimeError(f"CREON raw artifact copy differs from source: {source}")
    return {"name": target.name, "bytes": before.st_size, "sha256": digest.hexdigest()}


def publish(source_root: Path, nas_project: Path) -> dict[str, object]:
    if not (nas_project / "AGENTS.md").is_file():
        raise RuntimeError("NAS project sentinel is missing")
    server_data = nas_project / "deploy" / "synology" / "server-data"
    if not server_data.is_dir():
        raise RuntimeError("NAS server-data directory is missing")
    destination = server_data / "historical-intelligence" / "daishin-raw-v1"
    runs = destination / "runs"
    backup = nas_project / ".codex-backups" / "historical-daishin-raw"
    runs.mkdir(parents=True, exist_ok=True)
    backup.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    staging = runs / f".staging-{uuid.uuid4().hex}"
    staging.mkdir()
    entries: list[dict[str, object]] = []
    sources = raw_files(source_root)
    for index, (source, relative) in enumerate(sources, start=1):
        target = staging / "raw" / relative
        record = _copy_and_verify(source, target)
        record["name"] = (Path("raw") / relative).as_posix()
        entries.append(record)
        if index % 100 == 0 or index == len(sources):
            print(json.dumps({"event": "raw_copy_progress", "completed": index,
                              "total": len(sources),
                              "bytes": sum(int(item["bytes"]) for item in entries)}), flush=True)
    database_latest = server_data / "historical-intelligence" / "v1" / "latest.json"
    database_run = None
    if database_latest.is_file():
        database_run = json.loads(database_latest.read_text(encoding="utf-8"))["run"]
    inventory_hash = hashlib.sha256(
        json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    run_name = f"{stamp}-{inventory_hash[:12]}"
    final = runs / run_name
    if final.exists():
        raise RuntimeError(f"NAS run already exists: {final}")
    manifest = {
        "schema": "historical-daishin-raw-nas/v1",
        "published_at": datetime.now(UTC).isoformat(),
        "run": run_name,
        "database_run": database_run,
        "files": len(entries),
        "bytes": sum(int(entry["bytes"]) for entry in entries),
        "inventory_sha256": inventory_hash,
        "artifacts": entries,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(staging, final)
    latest = destination / "latest.json"
    if latest.exists():
        shutil.copy2(latest, backup / f"{stamp}-latest.json")
    latest_temp = destination / f".latest-{uuid.uuid4().hex}.json"
    latest_temp.write_text(
        json.dumps({
            "schema": "historical-daishin-raw-latest/v1",
            "run": run_name,
            "manifest": f"runs/{run_name}/manifest.json",
            "inventory_sha256": inventory_hash,
            "database_run": database_run,
        }, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(latest_temp, latest)
    return {"status": "published", "run": str(final), "files": len(entries),
            "bytes": manifest["bytes"], "database_run": database_run}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--nas-project", type=Path, default=DEFAULT_NAS_PROJECT)
    args = parser.parse_args()
    source_root = args.source_root.resolve(strict=True)
    expected_source = DEFAULT_SOURCE.resolve(strict=True)
    if os.path.normcase(str(source_root)) != os.path.normcase(str(expected_source)):
        raise SystemExit(f"Refusing unexpected raw source: {source_root}")
    nas_project = args.nas_project.resolve(strict=True)
    expected_nas = DEFAULT_NAS_PROJECT.resolve(strict=True)
    if os.path.normcase(str(nas_project)) != os.path.normcase(str(expected_nas)):
        raise SystemExit(f"Refusing unexpected NAS project: {nas_project}")
    print(json.dumps(publish(source_root, nas_project), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
