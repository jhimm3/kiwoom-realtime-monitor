"""Request metadata admission must preserve exact identity and bounded retention."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kiwoom_monitor.central_server import diagnostic_rest_input as inputs
from kiwoom_monitor.central_server.diagnostic_replay_contract import capture_owner


def retained_bytes(value):
    seen = set()
    def size(item):
        if id(item) in seen:
            return 0
        seen.add(id(item))
        total = sys.getsizeof(item)
        if type(item) is dict:
            total += sum(size(key)+size(part) for key,part in item.items())
        elif type(item) is tuple:
            total += sum(size(part) for part in item)
        return total
    return size(value)


class RestRequestLaneCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_4000_distinct_probe_requests_fit_original_budget_and_bound_retention(self):
        owner = SimpleNamespace()
        legacy_charge = 0
        with capture_owner('top20', 'capacity-probe', 'capacity-mixed'), \
                patch.object(inputs.trace, 'reject_input') as rejected:
            for index in range(1000):
                for api, path in (('ka10080','/api/dostk/chart'), ('ka10081','/api/dostk/chart'),
                                  ('ka10001','/api/dostk/stkinfo'), ('ka10100','/api/dostk/stkinfo')):
                    signature = inputs._signature(inputs._request_key('market', api, path,
                        {'stk_cd':'005930','probe_round':index*30}, 'N','',True))
                    kind = ('request', signature)
                    legacy_charge += 512 + 4*(len('capacity-mixed')+len(str(kind)))
                    receipt = inputs._new(owner, token='fixture', kind=kind)
                    self.assertIsNotNone(receipt)
                    self.assertEqual(1, receipt['ordinal'])
            rejected.assert_not_called()
        state = owner._market_input_sequences
        self.assertGreater(legacy_charge, inputs.MAX_LANE_BYTES)
        self.assertEqual(4000, len(state[1]))
        self.assertLess(state[2], inputs.MAX_LANE_BYTES)
        self.assertGreaterEqual(state[2], retained_bytes(state[1]))

    async def test_unicode_identity_repeats_keep_independent_exact_ordinals(self):
        owner = SimpleNamespace()
        bodies = ({'stk_cd':'005930','note':'한글 😀 "\\' * 200},
                  {'note':'한글 😀 "\\' * 200,'stk_cd':'005930'},
                  {'stk_cd':'005930','note':'한글 😀 다른 요청' * 200})
        signatures = [inputs._signature(inputs._request_key('market','ka10001',
            '/api/dostk/stkinfo', body, 'N','',True)) for body in bodies]
        with capture_owner('top20','fixture','lane:unicode'):
            receipts = [inputs._new(owner, token='fixture',kind=('request',signature))
                        for signature in signatures]
            again = inputs._new(owner, token='fixture',kind=('request',signatures[-1]))
        self.assertEqual([1,2,1], [row['ordinal'] for row in receipts])
        self.assertEqual(2, again['ordinal'])
        state = owner._market_input_sequences
        self.assertEqual(set(signatures), {key[1][1].decode('utf-8') for key in state[1]})
        self.assertGreaterEqual(state[2], retained_bytes(state[1]))

    async def test_byte_and_entry_limits_still_reject_new_keys_and_reset_on_new_trace(self):
        owner = SimpleNamespace()
        with capture_owner('top20','fixture','lane:bounded'), \
                patch.object(inputs.trace,'reject_input') as rejected:
            first = inputs._new(owner, token='first',kind=('request','key-a'))
            before = owner._market_input_sequences[2]
            with patch.object(inputs,'MAX_LANE_BYTES',before):
                self.assertIsNone(inputs._new(owner,token='first',kind=('request','key-b')))
                self.assertEqual(2, inputs._new(owner,token='first',kind=('request','key-a'))['ordinal'])
            with patch.object(inputs,'MAX_LANES',1):
                self.assertIsNone(inputs._new(owner,token='first',kind=('request','key-b')))
                fresh = inputs._new(owner,token='second',kind=('request','key-b'))
            self.assertEqual(2, rejected.call_count)
            self.assertEqual(1, first['ordinal'])
            self.assertEqual(1, fresh['ordinal'])
            self.assertEqual(1, len(owner._market_input_sequences[1]))
            self.assertEqual(before, owner._market_input_sequences[2])
