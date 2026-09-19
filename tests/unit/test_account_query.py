from __future__ import annotations

import unittest
from datetime import UTC, datetime

from kiwoom_monitor.domain.order_contract import (
    AccountBinding,
    AccountEnvironment,
    AccountScope,
    LEGACY_ACCOUNT_SCOPE,
)
from kiwoom_monitor.infrastructure.kiwoom_rest.account_query import (
    BoundDirectAccountQueryAdapter,
    IncompleteAccountQueryError,
    LegacyAccountQueryAdapter,
)


class Pages:
    def __init__(self, count: int, *, repeat_cursor: bool = False) -> None:
        self.count = count
        self.repeat_cursor = repeat_cursor
        self.calls: list[tuple[str, str]] = []

    def request_with_continuation(self, api_id, path, body, *, cont_yn="N", next_key=""):
        self.calls.append((cont_yn, next_key))
        index = len(self.calls)
        has_next = index < self.count
        cursor = next_key if self.repeat_cursor and index > 1 else (f"page-{index}" if has_next else "")
        return {"page": index}, has_next, cursor


class AccountQueryTests(unittest.TestCase):
    def test_legacy_adapter_keeps_unverified_scope_and_all_pages(self) -> None:
        source = Pages(2)
        result = LegacyAccountQueryAdapter(source).query_account_pages("kt00007", "/api/dostk/acnt", {})
        self.assertEqual(({"page": 1}, {"page": 2}), result.pages)
        self.assertEqual(LEGACY_ACCOUNT_SCOPE, result.context.scope)
        self.assertEqual([("N", ""), ("Y", "page-1")], source.calls)

    def test_page_limit_is_incomplete_instead_of_silent_success(self) -> None:
        with self.assertRaises(IncompleteAccountQueryError):
            LegacyAccountQueryAdapter(Pages(21)).query_account_pages(
                "kt00007", "/api/dostk/acnt", {}, max_pages=20,
            )

    def test_repeated_cursor_is_rejected(self) -> None:
        with self.assertRaises(IncompleteAccountQueryError):
            LegacyAccountQueryAdapter(Pages(3, repeat_cursor=True)).query_account_pages(
                "kt00007", "/api/dostk/acnt", {},
            )

    def test_bound_direct_adapter_uses_verified_binding(self) -> None:
        binding = AccountBinding(
            "local-real-default",
            AccountScope("kiwoom", AccountEnvironment.REAL, "11111111-1111-1111-1111-111111111111"),
            3, datetime(2026, 9, 13, tzinfo=UTC),
        )
        result = BoundDirectAccountQueryAdapter(Pages(1), binding).query_account_pages(
            "kt00015", "/api/dostk/acnt", {},
        )
        self.assertEqual(binding.scope, result.context.scope)
        self.assertEqual("direct", result.context.transport)


if __name__ == "__main__":
    unittest.main()
