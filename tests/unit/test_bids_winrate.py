"""The estimated win rate (apps/bids/winrate.py): pure maths and the count model on synthetic rows — no database."""

import os
import sys
import unittest
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.bids import winrate as W  # noqa: E402


def row(won, estimator_id=1, client_id=10, division="070", value=50000, sector="K-12", house=False, name="Camera add", rep_id=None):
    return {"won": won, "estimator_id": estimator_id, "estimator": "Est %d" % estimator_id, "client_id": client_id, "client_name": "C%d" % client_id,
            "client_label": "C%d" % client_id, "division": division, "value": Decimal(value), "size_band": "$25k–100k", "sector": sector,
            "house_account": house, "salesperson_id": rep_id, "rep": None, "project_name": name, "work_type": None}


class Maths(unittest.TestCase):
    def test_logit_roundtrip(self):
        for p in (0.1, 0.5, 0.9):
            self.assertAlmostEqual(W.inv_logit(W.logit(p)), p, places=6)

    def test_smoothing_pulls_small_samples_to_base(self):
        self.assertAlmostEqual(W.smoothed_rate(0, 0, 0.7), 0.7)
        self.assertGreater(W.smoothed_rate(2, 2, 0.7), 0.7)          # 2 of 2 won, nudged up
        self.assertLess(W.smoothed_rate(2, 2, 0.7), 1.0)
        self.assertAlmostEqual(W.smoothed_rate(700, 1000, 0.7), 0.7, places=3)

    def test_combine_without_evidence_is_the_base(self):
        p, bd = W.combine(0.6, [("estimator", "x", 0, 0), ("client", "y", 0, 0)])
        self.assertAlmostEqual(p, 0.6)
        self.assertTrue(all(b["n"] == 0 and b["shift"] == 0 for b in bd))

    def test_strong_factors_move_the_estimate(self):
        p_hi, _ = W.combine(0.6, [("estimator", "a", 95, 100)])
        p_lo, _ = W.combine(0.6, [("estimator", "b", 5, 100)])
        self.assertGreater(p_hi, 0.85)
        self.assertLess(p_lo, 0.2)
        p_small, _ = W.combine(0.6, [("estimator", "c", 3, 3)])
        self.assertLess(p_small, p_hi)                                  # 3 of 3 is weaker evidence than 95 of 100


class CountModel(unittest.TestCase):
    def test_predicts_from_history_with_breakdown(self):
        rows = [row(True, estimator_id=1) for _ in range(40)] + [row(False, estimator_id=1) for _ in range(10)] \
             + [row(True, estimator_id=2) for _ in range(5)] + [row(False, estimator_id=2) for _ in range(25)]
        m = W.Model(rows=rows, today=date(2026, 9, 10))
        self.assertEqual(m.n, 80)
        self.assertAlmostEqual(m.base, 45 / 80)
        p1, bd1 = m.predict(row(None, estimator_id=1))
        p2, bd2 = m.predict(row(None, estimator_id=2))
        self.assertGreater(p1, p2)
        est = [b for b in bd1 if b["factor"] == "estimator"][0]
        self.assertEqual((est["level"], est["n"]), ("Est 1", 50))
        self.assertGreater(est["shift"], 0)
        self.assertEqual(len(bd1), len(W.FACTORS))

    def test_work_type_is_derived_when_missing(self):
        lv = W.factor_levels({"project_name": "Boardroom AV refresh", "division": "040"})
        self.assertEqual(lv["work_type"], "AV / conferencing")
        self.assertIsNone(lv["estimator"])


if __name__ == "__main__":
    unittest.main()
