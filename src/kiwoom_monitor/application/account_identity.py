"""Bind one credential profile to a verified broker account scope."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from kiwoom_monitor.domain.order_contract import (
    AccountBinding,
    AccountScope,
    AccountScopeAlias,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import (
    KiwoomAccountIdentityReader,
)


class AccountRegistryStore(Protocol):
    def register_account_identity(self, value: dict[str, Any]) -> str: ...
    def append_account_binding(self, value: dict[str, Any]) -> dict[str, Any]: ...
    def register_account_scope_alias(self, value: dict[str, Any]) -> dict[str, Any]: ...


class AccountBindingMirror(Protocol):
    def save_binding(self, binding: AccountBinding) -> None: ...


async def verify_and_bind_account(
    reader: KiwoomAccountIdentityReader,
    store: AccountRegistryStore,
    *,
    credential_profile_id: str,
    binding_mirror: AccountBindingMirror | None = None,
) -> AccountBinding:
    if not credential_profile_id.strip():
        raise ValueError("credential_profile_id is required")
    identity = await reader.verify()
    return bind_verified_account_identity(
        identity, store, credential_profile_id=credential_profile_id,
        binding_mirror=binding_mirror,
    )


def bind_verified_account_identity(
    identity: Any,
    store: AccountRegistryStore,
    *,
    credential_profile_id: str,
    binding_mirror: AccountBindingMirror | None = None,
) -> AccountBinding:
    """Persist an identity already verified in this process without a second TR."""
    if not credential_profile_id.strip():
        raise ValueError("credential_profile_id is required")
    prepared = prepare_verified_account_identity(identity, store, credential_profile_id=credential_profile_id)
    stored = store.append_account_binding(prepared)
    binding = AccountBinding(
        credential_profile_id=str(stored["credential_profile_id"]),
        scope=AccountScope(
            broker=str(stored["broker"]),
            environment=identity.environment,
            account_ref=str(stored["account_ref"]),
        ),
        binding_revision=int(stored["binding_revision"]),
        verified_at=identity.verified_at,
        verification_method=str(stored["verification_method"]),
    )
    if binding_mirror is not None:
        binding_mirror.save_binding(binding)
    return binding


def prepare_verified_account_identity(
    identity: Any, store: AccountRegistryStore, *, credential_profile_id: str,
) -> dict[str, Any]:
    """Register a verified UUID without publishing a new binding revision.

    An unused registry UUID is harmless if candidate activation is cancelled.
    The caller commits a binding only after the encrypted vault commit.
    """
    if not credential_profile_id.strip():
        raise ValueError("credential_profile_id is required")
    account_ref = store.register_account_identity({
        "broker": identity.broker,
        "environment": identity.environment.value,
        "identity_fingerprint": identity.identity_fingerprint,
        "created_at": identity.verified_at.isoformat(),
    })
    return {
        "credential_profile_id": credential_profile_id,
        "broker": identity.broker,
        "environment": identity.environment.value,
        "account_ref": account_ref,
        "verified_at": identity.verified_at.isoformat(),
        "verification_method": identity.verification_method,
    }


def link_local_scope_to_verified_binding(
    store: AccountRegistryStore,
    *,
    origin_scope: AccountScope,
    binding: AccountBinding,
    verified_at: datetime,
) -> AccountScopeAlias:
    alias = AccountScopeAlias(
        origin_scope=origin_scope,
        canonical_scope=binding.scope,
        credential_profile_id=binding.credential_profile_id,
        binding_revision=binding.binding_revision,
        verified_at=verified_at,
        verification_method=binding.verification_method,
    )
    stored = store.register_account_scope_alias({
        "origin_account_ref": alias.origin_scope.account_ref,
        "canonical_account_ref": alias.canonical_scope.account_ref,
        "broker": alias.origin_scope.broker,
        "environment": alias.origin_scope.environment.value,
        "credential_profile_id": alias.credential_profile_id,
        "binding_revision": alias.binding_revision,
        "verified_at": alias.verified_at.isoformat(),
        "verification_method": alias.verification_method,
    })
    return AccountScopeAlias(
        origin_scope=origin_scope,
        canonical_scope=AccountScope(
            broker=str(stored["broker"]),
            environment=origin_scope.environment,
            account_ref=str(stored["canonical_account_ref"]),
        ),
        credential_profile_id=str(stored["credential_profile_id"]),
        binding_revision=int(stored["binding_revision"]),
        verified_at=datetime.fromisoformat(str(stored["verified_at"])),
        verification_method=str(stored["verification_method"]),
    )
