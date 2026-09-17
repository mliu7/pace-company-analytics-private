"""Stored WIP baselines restated on today's contract-value basis (finance_wip.restate, pure)."""

import os
import unittest

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.analytics.finance_wip import restate  # noqa: E402


class RestateTests(unittest.TestCase):
    def test_correction_after_baseline_is_not_movement(self):
        # 250038: baseline stored earned = SL's double-counted $1,060,019 × 100 %; today the app uses $564,685
        jobs = {"250038000000": [496922.0, 1060019.0, 563097.0]}
        now = {"250038000000": (564685, 1060019, "components_billed")}
        then = {"250038000000": (1060019, "sl")}
        out = restate(jobs, now, then)
        self.assertAlmostEqual(out["250038000000"][1], 564685.0, places=2)
        self.assertAlmostEqual(out["250038000000"][0], 1588.0, places=2)
        self.assertEqual(out["250038000000"][2], 563097.0)

    def test_missing_snapshot_falls_back_to_sl_raw(self):
        jobs = {"A": [500.0, 1000.0, 500.0]}
        out = restate(jobs, {"A": (800, 1000, "duplicate_tasks")}, {})
        self.assertEqual(out["A"], [300.0, 800.0, 500.0])

    def test_plain_sl_edit_is_real_movement(self):
        jobs = {"A": [500.0, 1000.0, 500.0]}
        self.assertEqual(restate(jobs, {"A": (1200, 1200, "sl")}, {"A": (1000, "sl")}), jobs)   # untouched

    def test_reverted_correction_restates_to_sl(self):
        jobs = {"A": [0.0, 800.0, 800.0]}
        out = restate(jobs, {"A": (1000, 1000, "sl")}, {"A": (800, "billing_final")})
        self.assertEqual(out["A"], [200.0, 1000.0, 800.0])

    def test_sparse_and_unknown_rows_untouched(self):
        jobs = {"A": [250.0, 0, 0], "B": [1.0, 2.0, 1.0]}
        out = restate(jobs, {"A": (500, 1000, "duplicate_tasks")}, {"A": (1000, "sl")})
        self.assertEqual(out, jobs)


if __name__ == "__main__":
    unittest.main()
