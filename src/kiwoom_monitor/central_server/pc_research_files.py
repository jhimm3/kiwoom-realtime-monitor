"""Archive published app research files and proven app campaign results."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from kiwoom_monitor.domain.observation_archive import archive_json, archive_sha256
from kiwoom_monitor.domain.pc_history_archive import RESEARCH_FILE_CATALOG, RESEARCH_FILE_CHUNK_BYTES, RESEARCH_FILE_SOURCE
from kiwoom_monitor.infrastructure.persistence.backup_file import write_immutable_bytes
from kiwoom_monitor.infrastructure.research_archive_ownership import APP_RESEARCH_OWNERS
from kiwoom_monitor.infrastructure.research_data_source import _bundle_child_path, _document_hash
from kiwoom_monitor.research_result_delivery import _source as completed_run_source


@dataclass
class ResearchArchiveSources:
    runs: dict[Path, set[str]]
    artifacts: list[tuple[Path, str, str, bool]]
    desktop_databases: tuple[Path, ...]


def _local_path(value: str, root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    path = path.absolute()
    if path.as_posix().startswith("//") or ".." in path.parts:
        raise ValueError("research archive sources must be direct local paths")
    for candidate in (*reversed(path.parents), path):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            break
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("research archive sources must be direct local paths")
    return path


def _on_hold(path: Path) -> bool:
    return any((parent / "nas-transfer-hold.json").is_file() for parent in (path, *path.parents))


def _register_run(sources: ResearchArchiveSources, database: Path, runs: Path, run_id: str,
                  expected: str | None = None) -> None:
    if _on_hold(database) or _on_hold(runs):
        raise ValueError("research transfer is on hold")
    path = _local_path(str(runs / run_id / "manifest.json"), runs)
    _, source = completed_run_source(database, runs, run_id)
    digest = source["manifest_sha256"]
    if expected is not None and digest != expected:
        raise ValueError("research run differs from the app's published bytes")
    namespace = archive_sha256(str(database).encode())
    sources.runs.setdefault(database, set()).add(run_id)
    sources.artifacts.append((path, f"runs/{namespace}/{run_id}/manifest.json", digest, False))


def _load_campaign(research: Path, sources: ResearchArchiveSources, issues: list[str]) -> None:
    selection = research / "research_campaign_selection.json"
    if not selection.is_file():
        return
    try:
        document = json.loads(_local_path(selection.name, research).read_bytes())
        if document["version"] != "research_campaign_selection/v1":
            raise ValueError("unrecognized research campaign selection")
        database = _local_path(document["database"], research)
        runs = _local_path(document["runs_dir"], research)
        if _on_hold(database) or _on_hold(runs):
            raise ValueError("research transfer is on hold")
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            owned = db.execute("""
                SELECT 'run',t.run_id,'' FROM research_campaign_jobs c
                JOIN research_search_jobs j ON j.job_id=c.job_id
                JOIN research_search_trials t ON t.experiment_id=j.experiment_id
                JOIN research_runs r ON r.run_id=t.run_id
                WHERE c.campaign_id=? AND r.status='completed'
                UNION
                SELECT 'input',a.input_path,a.manifest_hash FROM research_campaign_input_acceptances a
                JOIN research_campaign_storage_operations p
                    ON p.source_id=a.source_id AND p.input_path=a.input_path
                WHERE p.campaign_id=? AND p.state='PUBLISHED'
                ORDER BY 1,2
                """, (document["campaign_id"], document["campaign_id"])).fetchall()
        for kind, identity, digest in owned:
            try:
                if kind == "run":
                    _register_run(sources, database, runs, identity)
                else:
                    path = _local_path(str(Path(identity) / "manifest.json"), research)
                    if _on_hold(path):
                        raise ValueError("research transfer is on hold")
                    sources.artifacts.append((path, f"input/{digest}", digest, True))
            except (OSError, ValueError, RuntimeError, sqlite3.Error):
                issues.append(f"app_research_campaign_member_unavailable:{identity}")
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        issues.append("app_research_campaign_unavailable")


def discover_research_sources(data_directory: Path, issues: list[str]) -> ResearchArchiveSources:
    research = data_directory / "research"
    sources = ResearchArchiveSources({}, [], ())
    try:
        desktop = _local_path("monitor.sqlite3", data_directory)
        if desktop.is_file() and not _on_hold(desktop):
            sources.desktop_databases = (desktop,)
    except (OSError, ValueError):
        issues.append("desktop_monitor_database_unavailable")
    try:
        marker_root = _local_path(APP_RESEARCH_OWNERS, research)
    except (OSError, ValueError):
        issues.append("app_research_ownership_directory_unavailable")
        marker_root = None
    for marker in sorted(marker_root.glob("*.json")) if marker_root is not None else ():
        try:
            raw = _local_path(str(marker), research).read_bytes()
            if marker.stem != archive_sha256(raw):
                raise ValueError("research ownership record does not match its hash")
            record = json.loads(raw)
            path = _local_path(record["path"], research)
            if _on_hold(path):
                raise ValueError("research transfer is on hold")
            digest = record["sha256"]
            if record["database"]:
                database = _local_path(record["database"], research)
                _register_run(sources, database, path.parent.parent, record["run_id"], digest)
            elif record["bundle"]:
                sources.artifacts.append((path, f"input/{digest}", digest, True))
            else:
                sources.artifacts.append((path, f"evidence/{digest}/{path.name}", digest, False))
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error):
            issues.append(f"app_research_artifact_unavailable:{marker.name}")
    _load_campaign(research, sources, issues)
    return sources


def _frozen_members(root: Path, expected: str, label: str, *, child: bool = False):
    manifest_path = _local_path("manifest.json", root)
    encoded = manifest_path.read_bytes()
    manifest = json.loads(encoded)
    digest = archive_sha256(encoded)
    if (_document_hash(manifest) if child else digest) != expected:
        raise ValueError("research input manifest does not match its published bytes")
    if manifest.get("bundle_version") is not None:
        if child:
            raise ValueError("nested research bundles are unsupported")
        for entry in manifest["children"]:
            relative = entry["path"]
            directory = _bundle_child_path(root, relative)
            yield from _frozen_members(directory, entry["manifest_hash"], f"{label}/{relative}", child=True)
    else:
        fields = [(manifest[field], manifest[f"{field}_hash"]) for field in (
            "observations_file", "theme_snapshots_file", "decisions_file", "items_file",
        ) if manifest.get(field) is not None]
        if manifest.get("contract_version") == "historical_news_development_inputs/v1":
            fields.extend((entry["name"], entry["sha256"]) for entry in manifest["files"].values())
        if not fields:
            raise ValueError("unrecognized app research bundle")
        for name, checksum in fields:
            if not isinstance(name, str) or Path(name).name != name or any(token in name for token in "/\\:"):
                raise ValueError("research input member must be a local file")
            yield _local_path(name, root), f"{label}/{name}", checksum
    yield manifest_path, label + "/manifest.json", digest


def _archive_members(sources: ResearchArchiveSources, issues: list[str]):
    for path, label, expected, bundle in sources.artifacts:
        try:
            if bundle:
                if path.name != "manifest.json":
                    raise ValueError("app research bundle must have a manifest")
                yield from _frozen_members(path.parent, expected, label)
            else:
                yield path, label, expected
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            issues.append(f"app_research_input_unavailable:{path}")





def _verified_chunks(path: Path, expected: str, size: int, count: int):
    # A wrong file must never replace an accepted chunk under the expected digest.
    with path.open("rb") as stream:
        digest, hashes = hashlib.sha256(), []
        for _ in range(count):
            chunk = stream.read(RESEARCH_FILE_CHUNK_BYTES)
            digest.update(chunk)
            hashes.append(archive_sha256(chunk))
        if stream.tell() != size or stream.read(1) or digest.hexdigest() != expected:
            raise ValueError("research file does not match the declared bytes")
        if count == 1:
            yield chunk, hashes[0]
            return
        stream.seek(0)
        for checksum in hashes:
            chunk = stream.read(RESEARCH_FILE_CHUNK_BYTES)
            if archive_sha256(chunk) != checksum:
                raise ValueError("research file changed after hash verification")
            yield chunk, checksum


def archived_research_rows(sources: ResearchArchiveSources, markers: Path, target: str,
                           files: list[dict], issues: list[str]):
    """Yield bounded chunks; each subsequent iteration follows verification of the last one."""
    visited = set()
    failed_inputs = set()
    for path, label, expected in _archive_members(sources, issues):
        group = "/".join(label.split("/")[:2]) if label.startswith("input/") else ""
        try:
            if (label, expected) in visited:
                continue
            if label == f"{group}/manifest.json" and group in failed_inputs:
                raise ValueError("research input has unverified members")
            path = _local_path(str(path), path.parent)
            if _on_hold(path):
                raise ValueError("research file transfer is on hold")
            if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
                raise ValueError("research member is missing its original hash")
            stat = path.stat()
            size, modified = stat.st_size, stat.st_mtime_ns
            chunks = max(1, (size + RESEARCH_FILE_CHUNK_BYTES - 1) // RESEARCH_FILE_CHUNK_BYTES)
            record = {"file": label, "sha256": expected, "bytes": size, "chunks": chunks}
            stamp = archive_sha256(archive_json([str(path), label, expected, size, modified, target]))
            marker = markers / f"{stamp}.json"
            if not marker.is_file():
                for index, (chunk, checksum) in enumerate(_verified_chunks(path, expected, size, chunks)):
                    yield RESEARCH_FILE_SOURCE, {
                        "key": f"{expected}:{index:012d}", "subject": expected, "sort": f"{index:012d}",
                        "data": {"file_sha256": expected, "total_bytes": size, "chunk_count": chunks,
                                 "chunk_index": index, "chunk_sha256": checksum,
                                 "bytes_base64": base64.b64encode(chunk).decode("ascii")},
                    }
                current = path.stat()
                if (current.st_size, current.st_mtime_ns) != (size, modified):
                    raise ValueError("research artifact changed while being archived")
                yield RESEARCH_FILE_CATALOG, {"key": label, "subject": label.split("/", 1)[0],
                                              "sort": label, "data": record}
                write_immutable_bytes(marker, archive_json(record))
            files.append(record)
            visited.add((label, expected))
        except (OSError, TypeError, ValueError):
            if group:
                failed_inputs.add(group)
            issues.append(f"research_file:{label}")
