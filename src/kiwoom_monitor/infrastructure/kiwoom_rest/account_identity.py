"""Verify Kiwoom account identity without retaining the broker account number."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Protocol

from kiwoom_monitor.domain.order_contract import AccountEnvironment


ACCOUNT_ID_API = "ka00001"
ACCOUNT_PATH = "/api/dostk/acnt"


class AccountIdentityQueryResult(Protocol):
    payload: dict[str, Any]


class AccountIdentityBroker(Protocol):
    async def request(
        self, api_id: str, path: str, body: dict[str, Any], *,
        cont_yn: str = "N", next_key: str = "",
    ) -> AccountIdentityQueryResult: ...


@dataclass(frozen=True)
class VerifiedAccountIdentity:
    broker: str
    environment: AccountEnvironment
    identity_fingerprint: str
    verified_at: datetime
    verification_method: str = ACCOUNT_ID_API

    def __post_init__(self) -> None:
        if self.broker != "kiwoom" or len(self.identity_fingerprint) != 64:
            raise ValueError("verified account identity is invalid")
        if self.environment not in {AccountEnvironment.REAL, AccountEnvironment.MOCK}:
            raise ValueError("verified account identity requires a real or mock environment")
        if self.verified_at.tzinfo is None or self.verified_at.utcoffset() is None:
            raise ValueError("verified_at must be timezone-aware")


class KiwoomAccountIdentityReader:
    def __init__(
        self,
        broker: AccountIdentityBroker,
        *,
        environment: AccountEnvironment,
        hmac_key: bytes,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        if len(hmac_key) < 32:
            raise ValueError("account identity HMAC key must be at least 32 bytes")
        if environment not in {AccountEnvironment.REAL, AccountEnvironment.MOCK}:
            raise ValueError("account identity requires a real or mock environment")
        self._broker = broker
        self._environment = environment
        self._hmac_key = bytes(hmac_key)
        self._now = now_provider or (lambda: datetime.now(UTC))

    async def verify(self) -> VerifiedAccountIdentity:
        result = await self._broker.request(ACCOUNT_ID_API, ACCOUNT_PATH, {})
        return self.identity_from_payload(result.payload)

    def identity_from_payload(self, payload: dict[str, Any]) -> VerifiedAccountIdentity:
        """Convert an already verified ka00001 response without repeating the TR."""
        account_number = _normalized_account_number(payload)
        fingerprint = account_identity_fingerprint(
            account_number, self._environment, self._hmac_key,
        )
        verified_at = self._now()
        return VerifiedAccountIdentity(
            "kiwoom", self._environment, fingerprint, verified_at,
        )


def _normalized_account_number(payload: dict[str, Any]) -> str:
    raw = payload.get("acctNo")
    if not isinstance(raw, str):
        raise ValueError("ka00001 response is missing acctNo")
    normalized = "".join(character for character in raw if character.isdecimal())
    if not 8 <= len(normalized) <= 16:
        raise ValueError("ka00001 returned an invalid account number")
    return normalized


def account_identity_fingerprint(
    account_number: str, environment: AccountEnvironment, hmac_key: bytes,
) -> str:
    """Convert a transient broker account number to the registry fingerprint."""
    if len(hmac_key) < 32:
        raise ValueError("account identity HMAC key must be at least 32 bytes")
    normalized = "".join(character for character in str(account_number) if character.isdecimal())
    if not 8 <= len(normalized) <= 16:
        raise ValueError("invalid realtime account number")
    return hmac.new(
        hmac_key,
        f"kiwoom\0{environment.value}\0{normalized}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
