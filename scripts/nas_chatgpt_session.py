"""Container-only, fixed OAuth session import. Transport is SSH stdin, never HTTP."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiwoom_monitor.infrastructure.chatgpt_auth import ChatGPTPlanError, DIRECT_SCOPE, ISSUER
from kiwoom_monitor.infrastructure.chatgpt_linux_store import LinuxChatGPTCredentialStore


def validate_import(payload: dict, host_id: str) -> dict:
    if set(payload) != {"host_id", "account"} or payload["host_id"] != host_id:
        raise ChatGPTPlanError("client_mismatch")
    account = payload["account"]
    required = {"client_id", "issuer", "subject", "access_token", "refresh_token", "scopes", "expires_at", "token_type"}
    allowed = required | {"host_id", "email", "id_token", "expires_in", "saved_at", "earliest_refresh_at", "revision", "welcome_seen"}
    if (not isinstance(account, dict) or not required <= set(account) or set(account) - allowed
            or account["issuer"] != ISSUER or account["token_type"] != "Bearer"
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", account["client_id"])
            or not isinstance(account["scopes"], list) or DIRECT_SCOPE not in account["scopes"]
            or any(not isinstance(value, str) for value in account["scopes"])
            or any(not isinstance(account[key], str) or not account[key] for key in ("subject", "access_token", "refresh_token"))
            or not math.isfinite(float(account["expires_at"]))):
        raise ChatGPTPlanError("response_invalid")
    return {**account, "host_id": host_id}


def operate(store, action, payload=None):
    with store.locked():
        value = store.load()
        if not value.get("host_id"):
            value["host_id"] = "urn:uuid:" + str(uuid.uuid4())
            store.save(value)
        if action == "prepare":
            connected = any(account.get("access_token") and not account.get("refresh_pending")
                            for account in value["accounts"].values())
            return {"host_id": value["host_id"], "empty": not bool(connected), "client_id": value.get("active", "")}
        if any(account.get("access_token") and not account.get("refresh_pending") for account in value["accounts"].values()):
            raise ChatGPTPlanError("account_mismatch")
        account = validate_import(payload, value["host_id"])
        old = value["accounts"].get(account["client_id"])
        if value["accounts"] and (old is None or old.get("subject") != account["subject"]):
            raise ChatGPTPlanError("account_mismatch")
        value.update(active=account["client_id"], accounts={account["client_id"]: account},
                     generation=time.time_ns(), analysis_pause="")
        store.save(value)
        # Reopen the encrypted publication before acknowledging PC retirement.
        saved = store.load()
        if saved["generation"] != value["generation"]:
            raise ChatGPTPlanError("storage_unavailable")
        return {"imported": True, "host_id": value["host_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "import"))
    args = parser.parse_args()
    try:
        if os.name != "posix" or os.geteuid() != 0:
            raise ChatGPTPlanError("storage_unavailable")
        secret_dir = os.environ.get("KIWOOM_SERVER_SECRET_DIR", "")
        if secret_dir != "/app/secrets": raise ChatGPTPlanError("storage_unavailable")
        payload = None
        if args.action == "import":
            raw = sys.stdin.buffer.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024: raise ChatGPTPlanError("response_invalid")
            payload = json.loads(raw)
        value = operate(LinuxChatGPTCredentialStore(Path(secret_dir) / "chatgpt-plan/session.enc"), args.action, payload)
        print(json.dumps(value))
        return 0
    except ChatGPTPlanError as error:
        print(json.dumps({"error_code": error.code}))
    except Exception:
        print(json.dumps({"error_code": "response_invalid"}))
    return 1


if __name__ == "__main__": raise SystemExit(main())
