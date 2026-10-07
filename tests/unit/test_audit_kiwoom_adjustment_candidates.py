import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_kiwoom_adjustment_candidates.py"
spec = importlib.util.spec_from_file_location("audit_kiwoom_adjustment_candidates", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AdjustmentCandidateAuditTests(unittest.TestCase):
    def test_upper_bound_is_exclusive_when_two_intervals_touch(self):
        # First observation permits [0.5, 1.5); second permits [1.5, 2.5).
        self.assertEqual(module.assess_closes([(1, 1), (1, 2)])["status"], "exclude_conflict")

    def test_half_even_can_include_a_shared_boundary(self):
        self.assertEqual(module.assess_closes([(1, 2), (3, 4)])["status"], "exclude_conflict")
        self.assertEqual(module.assess_closes([(1, 2), (3, 4)], rounding="half_even")["status"], "candidate_unique_4dp")
        self.assertEqual(module.assess_closes([(1, 2), (3, 4)], rounding="half_even")["four_decimal_factor"], "1.5000")

    def test_unique_four_decimal_factor_is_only_daily_candidate(self):
        result = module.assess_closes([(240_000, 60_072), (320_000, 80_096)])
        self.assertEqual(result["status"], "candidate_unique_4dp")
        self.assertEqual(result["four_decimal_factor"], "0.2503")

    def test_single_observation_cannot_be_promoted(self):
        self.assertEqual(module.assess_closes([(100, 50)])["status"], "insufficient_single_day")

    def test_multiple_four_decimal_factors_remain_ambiguous(self):
        self.assertEqual(module.assess_closes([(100, 50), (101, 50)])["status"], "ambiguous_factor")

    def test_exact_factor_can_exist_outside_four_decimal_grid(self):
        self.assertEqual(
            module.assess_closes([(20_000, 1), (20_001, 1)])["status"],
            "unresolved_precision",
        )


if __name__ == "__main__":
    unittest.main()
