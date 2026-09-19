"""Account-query pagination boundary shared by journal import services.

Market-data callers keep using the small ``QueryClient`` tuple contract.  Account
callers use this boundary so an incomplete continuation chain can never look like
a completed account import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from kiwoom_monitor.domain.order_contract import AccountBinding, AccountScope, LEGACY_ACCOUNT_SCOPE


class AccountQueryError(RuntimeError):
    """Base error for an account batch that must not be persisted as complete."""


class IncompleteAccountQueryError(AccountQueryError):
    """The account response still had another page or an invalid cursor."""


class AccountScopeMismatchError(AccountQueryError):
    """Two account datasets were produced under different account contexts."""


class AccountQueryCapabilityError(AccountQueryError):
    """The selected server does not provide the verified v2 account contract."""


class InterruptedAccountQueryError(AccountQueryError):
    """Transport failed after a batch context had already been observed."""

    def __init__(self, message: str, context: "AccountQueryContext | None" = None) -> None:
        super().__init__(message)
        self.context = context


@dataclass(frozen=True)
class AccountQueryContext:
    scope: AccountScope
    credential_profile_id: str
    binding_revision: int
    transport: str

    def __post_init__(self) -> None:
        if not self.credential_profile_id.strip():
            raise ValueError("credential_profile_id is required")
        if self.binding_revision < 0:
            raise ValueError("binding_revision cannot be negative")
        if self.transport not in {"legacy", "nas", "direct"}:
            raise ValueError("unsupported account query transport")


@dataclass(frozen=True)
class AccountQueryBatch:
    pages: tuple[dict[str, Any], ...]
    context: AccountQueryContext
    page_count: int
    complete: bool

    def __post_init__(self) -> None:
        if not self.complete:
            raise IncompleteAccountQueryError("incomplete account query batch")
        if self.page_count != len(self.pages) or self.page_count < 1:
            raise ValueError("account query page count is inconsistent")


class AccountBatchClient(Protocol):
    def query_account_pages(
        self, api_id: str, path: str, body: dict[str, Any], *, max_pages: int = 20,
    ) -> AccountQueryBatch: ...


class LegacyAccountQueryAdapter:
    """Makes the old accountless query path explicit and permanently legacy-scoped.

    This compatibility adapter never accepts a screen-selected account scope.  It
    can therefore read old installations without claiming that the payload belongs
    to a verified real or mock account.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._context = AccountQueryContext(
            scope=LEGACY_ACCOUNT_SCOPE,
            credential_profile_id="legacy-unverified",
            binding_revision=0,
            transport="legacy",
        )

    def query_account_pages(
        self, api_id: str, path: str, body: dict[str, Any], *, max_pages: int = 20,
    ) -> AccountQueryBatch:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        pages: list[dict[str, Any]] = []
        cont_yn, next_key = "N", ""
        for _ in range(max_pages):
            payload, has_next, response_next_key = self._client.request_with_continuation(
                api_id, path, body, cont_yn=cont_yn, next_key=next_key,
            )
            if not isinstance(payload, dict):
                raise AccountQueryError("account query payload must be an object")
            pages.append(payload)
            if not has_next:
                return AccountQueryBatch(tuple(pages), self._context, len(pages), True)
            if not response_next_key or response_next_key == next_key:
                raise IncompleteAccountQueryError("account query returned an invalid continuation cursor")
            cont_yn, next_key = "Y", response_next_key
        raise IncompleteAccountQueryError(
            f"account query exceeded the {max_pages}-page safety limit"
        )


class BoundDirectAccountQueryAdapter(LegacyAccountQueryAdapter):
    """Runs a whole direct query using one previously verified local binding."""

    def __init__(self, client: Any, binding: AccountBinding) -> None:
        super().__init__(client)
        self._context = AccountQueryContext(
            scope=binding.scope,
            credential_profile_id=binding.credential_profile_id,
            binding_revision=binding.binding_revision,
            transport="direct",
        )

    def request(self, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._client.request(api_id, path, body)

    def request_with_continuation(
        self, api_id: str, path: str, body: dict[str, Any], *,
        cont_yn: str = "N", next_key: str = "",
    ) -> tuple[dict[str, Any], bool, str]:
        return self._client.request_with_continuation(
            api_id, path, body, cont_yn=cont_yn, next_key=next_key,
        )

    def server_now(self):
        return self._client.server_now()

    def get_access_token(self) -> str:
        return self._client.get_access_token()

    def load_account_contexts(self):
        return (self._context,)

    def for_account_scope(self, scope):
        if scope != self._context.scope:
            raise AccountScopeMismatchError("선택 계좌와 PC의 검증된 API 계좌가 다릅니다.")
        return self


def as_account_batch_client(client: Any) -> AccountBatchClient:
    query = getattr(client, "query_account_pages", None)
    return client if callable(query) else LegacyAccountQueryAdapter(client)
