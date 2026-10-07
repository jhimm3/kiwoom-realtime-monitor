from __future__ import annotations

import asyncio
import unittest
from dataclasses import asdict
from types import SimpleNamespace

import test_mock_account as support
from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import KiwoomMockAccountReader, KiwoomRealAccountReader


class RealAccountReaderTests(unittest.TestCase):
    def reader(self, broker):
        return KiwoomRealAccountReader(broker, environment="real", account_ref="scoped-account",
                                     now_provider=lambda: support.NOW)

    def test_integrated_orders_and_two_balance_views_do_not_double_positions(self):
        broker = support._Broker()
        recovery = asyncio.run(self.reader(broker).read())
        self.assertEqual({"005930": 10}, recovery.account.positions)
        self.assertEqual(500000, recovery.account.available_cash_won - recovery.account.reserved_open_buy_won)
        self.assertEqual(["ka10075", "ka10075", "ka10076", "kt00018", "kt00018", "kt00001"],
                         [call[0] for call in broker.calls])
        self.assertTrue(all(call[2]["stex_tp"] == "0" for call in broker.calls if call[0] in {"ka10075", "ka10076"}))
        self.assertEqual(["KRX", "NXT"], [call[2]["dmst_stex_tp"] for call in broker.calls if call[0] == "kt00018"])
        self.assertEqual("Y", broker.calls[1][3])

    def test_environment_guards_reject_before_tr(self):
        broker = support._Broker()
        with self.assertRaisesRegex(ValueError, "real credentials"):
            KiwoomRealAccountReader(broker, environment="mock", account_ref="account")
        with self.assertRaisesRegex(ValueError, "mock credentials"):
            KiwoomMockAccountReader(broker, environment="real", account_ref="account")
        broker._client = SimpleNamespace(environment="mock")
        with self.assertRaisesRegex(ValueError, "broker environment mismatch"):
            self.reader(broker)
        self.assertEqual([], broker.calls)

    def test_disagreeing_balance_quantities_fail_without_partial_recovery(self):
        class Broker(support._Broker):
            async def request(self, api_id, path, body, **kwargs):
                result = await super().request(api_id, path, body, **kwargs)
                if api_id == "kt00018" and body["dmst_stex_tp"] == "NXT":
                    result.payload["acnt_evlt_remn_indv_tot"][0]["rmnd_qty"] = "11"
                return result
        with self.assertRaisesRegex(ValueError, "conflicting quantities"):
            asyncio.run(self.reader(Broker()).read())

    def test_repeated_continuation_cursor_fails_instead_of_returning_partial_data(self):
        class Broker(support._Broker):
            async def request(self, *args, **kwargs):
                result = await super().request(*args, **kwargs)
                result.has_next, result.next_key = True, "repeat"
                return result
        broker = Broker()
        with self.assertRaisesRegex(ValueError, "cursor repeated"):
            asyncio.run(self.reader(broker).read())
        self.assertEqual(2, len(broker.calls))

    def test_duplicate_order_pages_are_not_double_counted_as_reserved_cash(self):
        class Broker(support._Broker):
            async def request(self, api_id, path, body, **kwargs):
                result = await super().request(api_id, path, body, **kwargs)
                if api_id == "ka10075" and kwargs.get("cont_yn") == "Y":
                    result.payload["oso"][0]["ord_no"] = "0001"
                return result
        with self.assertRaisesRegex(ValueError, "repeat a broker order id"):
            asyncio.run(self.reader(Broker()).read())

    def test_same_order_number_on_distinct_venues_is_not_merged(self):
        class Broker(support._Broker):
            async def request(self, api_id, path, body, **kwargs):
                result = await super().request(api_id, path, body, **kwargs)
                if api_id in {"ka10075", "ka10076"}:
                    for row in result.payload["oso" if api_id == "ka10075" else "cntr"]:
                        row["stex_tp"] = "1" if api_id == "ka10075" else "2"
                return result
        with self.assertRaisesRegex(ValueError, "conflicts across venues"):
            asyncio.run(self.reader(Broker()).read())

    def test_raw_account_number_does_not_enter_normalized_recovery(self):
        class Broker(support._Broker):
            async def request(self, api_id, path, body, **kwargs):
                result = await super().request(api_id, path, body, **kwargs)
                result.payload["acnt_no"] = "raw-private-account"
                for row in result.payload.get("oso", []):
                    row["acnt_no"] = "raw-private-account"
                return result
        self.assertNotIn("raw-private-account", str(asdict(asyncio.run(self.reader(Broker()).read()))))

    def test_missing_account_table_is_not_assumed_to_mean_no_orders(self):
        class Broker(support._Broker):
            async def request(self, api_id, path, body, **kwargs):
                result = await super().request(api_id, path, body, **kwargs)
                if api_id == "ka10075":
                    result.payload.pop("oso")
                return result
        with self.assertRaisesRegex(ValueError, "missing a complete account table"):
            asyncio.run(self.reader(Broker()).read())


if __name__ == "__main__":
    unittest.main()
