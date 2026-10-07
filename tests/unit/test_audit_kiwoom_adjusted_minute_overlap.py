import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("audit_kiwoom_adjusted_minute_overlap", SCRIPTS / "audit_kiwoom_adjusted_minute_overlap.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AdjustedMinuteOverlapTests(unittest.TestCase):
    def test_integer_round_half_up(self):
        self.assertEqual(module.round_half_up_scaled(320_000, 2503), 80_096)
        self.assertEqual(module.round_half_up_scaled(1, 5000), 1)
        self.assertEqual(module.round_half_even_scaled(15_000, 5003), 7_504)
        self.assertEqual(module.round_half_even_scaled(25_000, 5003), 12_508)

    def test_comparison_keeps_ohlc_and_volume_separate(self):
        kiwoom = {"2025-10-31 09:00:00": (60_072, 60_573, 58_821, 60_197, 100)}
        creon = {"2025-10-31 09:00:00": (240_000, 242_000, 235_000, 240_500, 99)}
        result = module.compare_day(kiwoom, creon, 2503)
        self.assertEqual(result["adjusted_ohlc_match_minutes"], 1)
        self.assertEqual(result["half_even_ohlc_match_minutes"], 0)
        self.assertEqual(result["volume_match_minutes"], 0)
        self.assertEqual(result["direct_evidence"], "all_common_ohlc_match_half_up_candidate")


if __name__ == "__main__":
    unittest.main()
