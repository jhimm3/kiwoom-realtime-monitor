from __future__ import annotations

import unittest
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from kiwoom_monitor.central_server.account_query import AccountQuerySessionManager
from kiwoom_monitor.domain.order_contract import AccountBinding, AccountEnvironment, AccountScope


def binding(account_ref: str, revision: int = 1) -> AccountBinding:
    return AccountBinding(
        "nas-real-default",
        AccountScope("kiwoom", AccountEnvironment.REAL, account_ref),
        revision,
        datetime(2026, 9, 13, tzinfo=UTC),
    )


class Broker:
    def __init__(self) -> None:
        self.calls = []

    async def request(self, api_id, path, body, *, cont_yn="N", next_key=""):
        self.calls.append((dict(body), cont_yn, next_key))
        first = cont_yn == "N"
        return SimpleNamespace(
            payload={"page": len(self.calls)}, has_next=first,
            next_key="cursor-1" if first else "", cache_hit=False,
        )


class CentralAccountQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_change_during_broker_call_rejects_late_response(self):
        current = [binding("11111111-1111-1111-1111-111111111111")]
        entered, release = asyncio.Event(), asyncio.Event()
        broker = Broker()
        original = broker.request
        async def blocked(*args, **kwargs):
            entered.set(); await release.wait()
            return await original(*args, **kwargs)
        broker.request = blocked
        manager = AccountQuerySessionManager(broker, lambda: current[0])
        task = asyncio.create_task(manager.query(api_id="kt00007", path="/api/dostk/acnt", body={}))
        await entered.wait()
        current[0] = binding("22222222-2222-2222-2222-222222222222", 2)
        release.set()
        with self.assertRaisesRegex(RuntimeError, "ACCOUNT_CONTEXT_MISMATCH"): await task
        self.assertEqual({}, manager._sessions)

    async def test_cancelled_waiter_does_not_release_owned_query_before_close(self):
        entered, release = asyncio.Event(), asyncio.Event()
        broker = Broker(); original = broker.request
        async def blocked(*args, **kwargs):
            entered.set(); await release.wait()
            return await original(*args, **kwargs)
        broker.request = blocked
        manager = AccountQuerySessionManager(broker, lambda: binding("11111111-1111-1111-1111-111111111111"))
        waiter = asyncio.create_task(manager.query(api_id="kt00007", path="/api/dostk/acnt", body={}))
        await entered.wait()
        actual = next(iter(manager._tasks))
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError): await waiter
        closing = asyncio.create_task(manager.close()); await asyncio.sleep(0)
        self.assertFalse(closing.done()); self.assertFalse(actual.done())
        release.set(); await closing
        self.assertEqual({}, manager._sessions)
        self.assertTrue(actual.done())
        with self.assertRaisesRegex(RuntimeError, "ACCOUNT_QUERY_CLOSED"):
            await manager.query(api_id="kt00007", path="/api/dostk/acnt", body={})

    async def test_two_pages_keep_body_cursor_and_binding(self) -> None:
        current = [binding("11111111-1111-1111-1111-111111111111")]
        broker = Broker()
        manager = AccountQuerySessionManager(broker, lambda: current[0])
        first = await manager.query(
            api_id="kt00007", path="/api/dostk/acnt", body={"ord_dt": "20260913"},
        )
        second = await manager.query(
            api_id="kt00007", path="/api/dostk/acnt", body={"ord_dt": "20260913"},
            batch_id=str(first["batch_id"]), page_index=1, next_key="cursor-1",
        )
        self.assertFalse(first["complete"])
        self.assertTrue(second["complete"])
        self.assertEqual(current[0].scope.account_ref, second["context"]["account_ref"])
        self.assertEqual(["N", "Y"], [call[1] for call in broker.calls])

    async def test_changed_body_or_cursor_is_rejected_before_broker(self) -> None:
        broker = Broker()
        manager = AccountQuerySessionManager(
            broker, lambda: binding("11111111-1111-1111-1111-111111111111"),
        )
        first = await manager.query(
            api_id="kt00007", path="/api/dostk/acnt", body={"ord_dt": "20260913"},
        )
        with self.assertRaisesRegex(ValueError, "BODY_MISMATCH"):
            await manager.query(
                api_id="kt00007", path="/api/dostk/acnt", body={"ord_dt": "20260912"},
                batch_id=str(first["batch_id"]), page_index=1, next_key="cursor-1",
            )
        self.assertEqual(1, len(broker.calls))

    async def test_binding_change_invalidates_unfinished_batch(self) -> None:
        current = [binding("11111111-1111-1111-1111-111111111111")]
        broker = Broker()
        manager = AccountQuerySessionManager(broker, lambda: current[0])
        first = await manager.query(
            api_id="kt00015", path="/api/dostk/acnt", body={"tp": "0"},
        )
        current[0] = binding("22222222-2222-2222-2222-222222222222", 2)
        with self.assertRaisesRegex(RuntimeError, "ACCOUNT_CONTEXT_MISMATCH"):
            await manager.query(
                api_id="kt00015", path="/api/dostk/acnt", body={"tp": "0"},
                batch_id=str(first["batch_id"]), page_index=1, next_key="cursor-1",
            )
        self.assertEqual(1, len(broker.calls))


if __name__ == "__main__":
    unittest.main()
