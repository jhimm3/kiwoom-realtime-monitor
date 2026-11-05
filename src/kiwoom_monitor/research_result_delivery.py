"""Explicit delivery of completed local research reports; no calculation or retry loop."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from kiwoom_monitor.domain.research_contract import (
    ReportedResearchResult, REPORTED_RESEARCH_RESULT_KIND, encode_reported_result_owner,
    SECTION12_EVIDENCE_EXPERIMENT_FORMAT, is_section12_evidence_manifest,
)
from kiwoom_monitor.domain.time_basis import as_kst
from kiwoom_monitor.infrastructure.central_content_client import CentralContentClient, CentralContentHttpError, validate_producer_connection
from kiwoom_monitor.infrastructure.central_server_config import DataSourceConfig
from kiwoom_monitor.infrastructure.persistence.backup_file import write_immutable_bytes


TARGET_FILE = "reported-result-target.json"
ACK_FILE = "reported-result-ack.json"


@dataclass(frozen=True)
class ResearchResultTarget:
    server_url: str
    owner: str
    access_token: str = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "server_url", validate_producer_connection(self.server_url, self.owner, self.access_token))

    @property
    def identity(self):
        return {"server_url": self.server_url, "owner": self.owner, "kind": REPORTED_RESEARCH_RESULT_KIND}


def load_result_target(connection: Path, owner: str) -> ResearchResultTarget:
    settings = DataSourceConfig(Path(connection)).load()
    if settings.mode not in {"local_server", "personal_server"}:
        raise ValueError("the explicit result connection must select a server")
    return ResearchResultTarget(settings.server_url, owner, settings.access_token)


def _paths(runs_dir: Path, run_id: str):
    if (not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id
            or run_id in {".", ".."} or ":" in run_id or "\\" in run_id or "/" in run_id):
        raise ValueError("one local run ID is required")
    root = Path(runs_dir).resolve()
    directory = root / run_id
    paths = tuple(directory / name for name in ("manifest.json", TARGET_FILE, ACK_FILE))
    if directory.resolve() != directory or any(path.resolve() != path for path in paths):
        raise ValueError("result artifacts must remain inside their original run directory")
    return paths


def _source(database: Path, runs_dir: Path, run_id: str):
    from kiwoom_monitor.infrastructure.persistence.research_repository import ResearchRepository

    manifest_path, target_path, ack_path = _paths(runs_dir, run_id)
    database = Path(database).resolve()
    if database in {manifest_path, target_path, ack_path}:
        raise ValueError("research database must not alias result artifacts")
    run = ResearchRepository(database, read_only=True).load_run(run_id)
    if run is None or run["status"] != "completed":
        raise ValueError("an existing completed research run is required")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or manifest.get("run_id") != run_id
            or manifest.get("logical_result_hash") != run["logical_result_hash"]
            or manifest.get("spec") != run["spec"]):
        raise ValueError("original completed run and manifest do not match")
    if manifest.get("format") == SECTION12_EVIDENCE_EXPERIMENT_FORMAT:
        if not is_section12_evidence_manifest(manifest):
            raise ValueError("invalid Section 12 evidence experiment manifest")
        spec = manifest["spec"]
        input_identity = {key: spec[key] for key in ("frozen_dataset_id", "frozen_manifest_sha256", "revision_ids_hash")
                          if key in spec}
        input_identity.update(dataset_id=spec.get("frozen_dataset_id", spec["dataset_sha256"]),
                              source_kind=spec["source_kind"])
        if (run["input_manifest"] != input_identity
                or run_id != "run_" + hashlib.sha256(_canonical(spec)).hexdigest()
                or hashlib.sha256(_canonical(manifest["result"])).hexdigest() != run["logical_result_hash"]):
            raise ValueError("original completed evidence run and manifest do not match")
    elif (manifest.get("format") != "kiwoom-monitor-research-run"
          or type(manifest.get("version")) is not int or manifest["version"] != 3
          or manifest.get("repository") != database.name
          or manifest.get("input_dataset_id") != run["input_manifest"].get("dataset_id", "")
          or manifest.get("input_revision_ids_hash") != run["input_manifest"].get("revision_ids_hash", "")):
        raise ValueError("original completed run and manifest do not match")
    finished_at = run["finished_at"]
    moment = datetime.fromisoformat(finished_at)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("original completion timestamp must be timezone-aware")
    source = {"run_id": run_id, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
              "finished_at": finished_at, "produced_at": as_kst(moment).isoformat()}
    return manifest, source


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _receipt(receipt, value: ReportedResearchResult, owner: str):
    if (not isinstance(receipt, dict) or receipt.get("status") != "stored" or receipt.get("owner") != owner
            or receipt.get("result_id") != value.result_id or receipt.get("kind") != REPORTED_RESEARCH_RESULT_KIND
            or receipt.get("content_hash") != value.content_hash or receipt.get("storage_only") is not True
            or receipt.get("scientific_verified") is not False or receipt.get("order_authorized") is not False):
        raise ValueError("reported storage receipt does not match the pinned result")
    moment = datetime.fromisoformat(receipt["accepted_at"])
    if moment.tzinfo is None or moment.utcoffset() != timedelta(hours=9):
        raise ValueError("original storage receipt must retain its KST timestamp")
    return receipt


def _accepted(accepted, value, owner):
    if not isinstance(accepted, dict) or set(accepted) != {"receipt", "result"}:
        raise ValueError("invalid reported storage response")
    if ReportedResearchResult.from_document(accepted["result"]).content_hash != value.content_hash:
        raise ValueError("stored result differs from the original report")
    return _receipt(accepted["receipt"], value, owner)


def deliver_completed_result(database: Path, runs_dir: Path, run_id: str, target: ResearchResultTarget,
                             *, cancel_requested: Callable[[], bool] = lambda: False) -> dict:
    """One explicit attempt. A server commit/receipt never changes local calculation state."""
    stage, durable = "source", False
    try:
        _, target_path, ack_path = _paths(runs_dir, run_id)
        durable = target_path.is_file()
        manifest, source = _source(database, runs_dir, run_id)
        descriptor = {"version": 1, "target": target.identity, "source": source}
        stage = "descriptor"
        write_immutable_bytes(target_path, _canonical(descriptor))
        durable = True
        stage = "payload"
        value = ReportedResearchResult.from_document({"version": 1, "kind": REPORTED_RESEARCH_RESULT_KIND,
            "result_id": run_id, "produced_at": source["produced_at"], "payload": manifest})
        if ack_path.exists():
            stage = "ack"
            ack = json.loads(ack_path.read_bytes())
            if (not isinstance(ack, dict) or set(ack) != {"version", "target", "source", "receipt"}
                    or type(ack["version"]) is not int or ack["version"] != 1
                    or ack["target"] != target.identity or ack["source"] != source):
                raise ValueError("first ACK does not match the immutable target/source")
            return {"status": "delivered", "receipt": _receipt(ack["receipt"], value, target.owner)}
        if cancel_requested():
            return {"status": "pending", "stage": "before_post", "reason": "cancelled"}
        client = CentralContentClient(target.server_url, target.access_token)
        stage = "post"
        try:
            accepted = client.accept_research_result(value.document, expected_owner=target.owner)
            receipt = _accepted(accepted, value, target.owner)
        except CentralContentHttpError:
            # A definite HTTP rejection retains its original status. In particular,
            # an existing row must not hide denied authority or conflicting content.
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as post_error:
            if cancel_requested():
                raise
            # Only an uncertain/lost response needs another read. A normal POST
            # already returns the complete first receipt after native COMMIT/close.
            stage = "confirm"
            try:
                confirmed = client.load_research_result(run_id, expected_owner=target.owner)
                receipt = _accepted(confirmed, value, target.owner)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as confirm_error:
                pending = {"status": "pending", "stage": stage, "reason": type(post_error).__name__,
                           "confirmation_reason": type(confirm_error).__name__}
                if isinstance(confirm_error, CentralContentHttpError):
                    pending["confirmation_http_status"] = confirm_error.status_code
                return pending
        if cancel_requested():
            return {"status": "pending", "stage": "after_post", "reason": "cancelled"}
        stage = "source"
        if _source(database, runs_dir, run_id)[1] != source:
            raise ValueError("source changed during delivery")
        if cancel_requested():
            return {"status": "pending", "stage": "before_ack", "reason": "cancelled"}
        stage = "ack"
        if json.loads(target_path.read_bytes()) != descriptor:
            raise ValueError("target changed during delivery")
        write_immutable_bytes(ack_path, _canonical({**descriptor, "receipt": receipt}))
        return {"status": "delivered", "receipt": receipt}
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as error:
        # Neither credential-bearing config repr nor arbitrary server/OS detail is logged.
        outcome = {"status": "pending" if durable else "not_prepared", "stage": stage, "reason": type(error).__name__}
        if isinstance(error, CentralContentHttpError):
            outcome["http_status"] = error.status_code
        return outcome


def attach_result_delivery(result: dict, database: Path, runs_dir: Path, run_id: str,
                           target: ResearchResultTarget, *, cancel_requested=lambda: False) -> dict:
    delivery = deliver_completed_result(database, runs_dir, run_id, target, cancel_requested=cancel_requested)
    return {**result, "status": "ok" if delivery["status"] == "delivered" else "delivery_" + delivery["status"],
            "calculation_status": "completed", "delivery": delivery}


def resend_completed_result(database: Path, runs_dir: Path, run_id: str, connection: Path) -> dict:
    stage, durable = "descriptor", False
    try:
        _, path, _ = _paths(runs_dir, run_id)
        durable = path.is_file()
        descriptor = json.loads(path.read_bytes())
        stage = "connection"
        target = load_result_target(connection, descriptor["target"]["owner"])
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {"status": "pending" if durable else "not_prepared", "stage": stage, "reason": type(error).__name__}
    return deliver_completed_result(database, runs_dir, run_id, target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Explicitly resend one completed immutable research report.")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--runs-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--result-connection", required=True, type=Path)
    args = parser.parse_args(argv)
    delivery = resend_completed_result(args.database, args.runs_dir, args.run_id, args.result_connection)
    print(json.dumps({"status": "ok" if delivery["status"] == "delivered" else "delivery_" + delivery["status"],
                      "delivery": delivery}, ensure_ascii=False))
    return 0 if delivery["status"] == "delivered" else 4


if __name__ == "__main__":
    raise SystemExit(main())
