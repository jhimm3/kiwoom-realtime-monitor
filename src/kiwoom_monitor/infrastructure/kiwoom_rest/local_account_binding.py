"""Windows DPAPI mirror for verified account bindings."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

from kiwoom_monitor.domain.order_contract import (
    AccountBinding,
    AccountEnvironment,
    AccountScope,
)

from .local_config import _protect, _unprotect


_HEADER = "KIWOOM_ACCOUNT_BINDINGS_ENCRYPTED="
_FORMAT_VERSION = 1


class LocalAccountBindingConfig:
    """Stores the latest verified binding per local credential profile."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_bindings(self) -> tuple[AccountBinding, ...]:
        if not self._path.exists():
            return ()
        raw = self._path.read_text(encoding="utf-8").strip()
        if not raw.startswith(_HEADER):
            raise ValueError("local account binding file is not DPAPI protected")
        document = json.loads(
            _unprotect(base64.b64decode(raw.split("=", 1)[1])).decode("utf-8")
        )
        if document.get("version") != _FORMAT_VERSION or not isinstance(document.get("bindings"), list):
            raise ValueError("unsupported local account binding format")
        bindings = tuple(_binding_from_document(value) for value in document["bindings"])
        keys = {(item.credential_profile_id, item.scope.environment.value) for item in bindings}
        if len(keys) != len(bindings):
            raise ValueError("duplicate local account binding profile")
        return bindings

    def save_binding(self, binding: AccountBinding) -> None:
        keyed = {
            (item.credential_profile_id, item.scope.environment.value): item
            for item in self.load_bindings()
        }
        key = (binding.credential_profile_id, binding.scope.environment.value)
        previous = keyed.get(key)
        if previous is not None and binding.binding_revision < previous.binding_revision:
            raise ValueError("cannot replace a local account binding with an older revision")
        keyed[key] = binding
        ordered = sorted(keyed.values(), key=lambda item: (
            item.credential_profile_id, item.scope.environment.value,
        ))
        payload = json.dumps(
            {"version": _FORMAT_VERSION, "bindings": [_binding_document(item) for item in ordered]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.b64encode(_protect(payload)).decode("ascii")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f"{self._path.name}.tmp")
        temporary.write_text(f"{_HEADER}{encoded}\n", encoding="utf-8")
        temporary.replace(self._path)


def _binding_document(binding: AccountBinding) -> dict[str, object]:
    return {
        "credential_profile_id": binding.credential_profile_id,
        "broker": binding.scope.broker,
        "environment": binding.scope.environment.value,
        "account_ref": binding.scope.account_ref,
        "binding_revision": binding.binding_revision,
        "verified_at": binding.verified_at.isoformat(),
        "verification_method": binding.verification_method,
    }


def _binding_from_document(value: object) -> AccountBinding:
    if not isinstance(value, dict):
        raise ValueError("invalid local account binding")
    verified_at = datetime.fromisoformat(str(value.get("verified_at", "")))
    return AccountBinding(
        credential_profile_id=str(value.get("credential_profile_id", "")),
        scope=AccountScope(
            broker=str(value.get("broker", "")),
            environment=AccountEnvironment(str(value.get("environment", ""))),
            account_ref=str(value.get("account_ref", "")),
        ),
        binding_revision=int(value.get("binding_revision", 0)),
        verified_at=verified_at,
        verification_method=str(value.get("verification_method", "")),
    )
