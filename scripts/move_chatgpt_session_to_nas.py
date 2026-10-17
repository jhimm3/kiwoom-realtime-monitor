"""Move one protected Windows session over SSH; persist PC retirement before send.

An uncertain acknowledgement leaves the PC fenced. Never retry a rotating
credential automatically and never revoke the session that NAS now owns.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTAuth, ChatGPTCredentialStore, ChatGPTPlanError, DIRECT_SCOPE


def ssh(action: str, payload: bytes | None = None) -> dict:
    result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=10", "k379@192.168.0.5",
        "sudo -n /usr/local/sbin/kiwoom-news-chatgpt-session " + action],
        input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
    if result.returncode or len(result.stdout) > 8192:
        raise ChatGPTPlanError("storage_unavailable")
    value = json.loads(result.stdout)
    if not isinstance(value, dict) or value.get("error_code"):
        raise ChatGPTPlanError("storage_unavailable")
    return value


def move(store, transport=ssh):
    prepared = transport("prepare")
    if not prepared.get("empty") or not str(prepared.get("host_id", "")).startswith("urn:uuid:"):
        raise ChatGPTPlanError("account_mismatch")
    with store.locked():
        value = store.load()
        account = value["accounts"].get(value.get("active"))
        if (not account or not account.get("refresh_token") or account.get("refresh_pending")
                or DIRECT_SCOPE not in account.get("scopes", [])):
            raise ChatGPTPlanError("not_connected")
        if prepared.get("client_id") and prepared["client_id"] != account["client_id"]:
            raise ChatGPTPlanError("client_mismatch")
        payload = {"host_id": prepared["host_id"], "account": dict(account)}
        for key in ("refresh_pending", "transfer_pending", "transferred_to"):
            payload["account"].pop(key, None)
        # The receiver binds the record to its own previously persisted host ID.
        payload["account"]["host_id"] = prepared["host_id"]
        account["refresh_pending"] = True
        account["transfer_pending"] = prepared["host_id"]
        store.save(value)
        imported = transport("import", json.dumps(payload, separators=(",", ":")).encode())
        if not imported.get("imported") or imported.get("host_id") != prepared["host_id"]:
            raise ChatGPTPlanError("rotation_unsaved")
        for key in ("access_token", "refresh_token", "id_token"):
            account[key] = ""
        account.pop("transfer_pending", None)
        account["transferred_to"] = prepared["host_id"]
        store.save(value)
    return {"imported": True, "pc_session_retired": True}


def login_for_nas(credentials: Path):
    """Subsequent authorization uses the NAS host ID in a separate DPAPI profile."""
    prepared = ssh("prepare")
    if not prepared.get("empty"):
        raise ChatGPTPlanError("account_mismatch")
    store = ChatGPTCredentialStore(credentials.with_name("nas-authorization.dpapi"))
    with store.locked():
        value = store.load()
        if value.get("host_id") and value["host_id"] != prepared["host_id"]:
            raise ChatGPTPlanError("client_mismatch")
        value["host_id"] = prepared["host_id"]
        account_id = str(prepared.get("client_id") or value.get("active") or "")
        if account_id and account_id not in value["accounts"]:
            value["pending_client_id"] = account_id
            account_id = ""
        store.save(value)
    ChatGPTAuth(store).connect(Event(), account_id=account_id)
    return store


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--login", action="store_true", help="Open local OAuth for NAS authorization/reconnection")
    args = parser.parse_args()
    try:
        store = login_for_nas(args.credentials) if args.login else ChatGPTCredentialStore(args.credentials)
        print(json.dumps(move(store)))
    except Exception as error:
        print(json.dumps({"error_code": error.code if isinstance(error, ChatGPTPlanError) else "storage_unavailable",
                          "pc_may_be_fenced": True}))
        raise SystemExit(1)
