"""EAC — the PM remaining-hours burn-down and the risk heuristic (apps/analytics/eac.py). No DB.

The burn-down is the fix for the 2026-09-14 Palmer House defect: a PM estimate is a statement made on a date, so hours
worked after it was saved have already consumed it. Adding the raw estimate to today's actual hours invented $245k of
future labor across 79 open jobs.
"""
import unittest
from decimal import Decimal as D


class BurnDownTests(unittest.TestCase):
    def setUp(self):
        from apps.analytics.eac_rules import burn_down_remaining
        self.fn = burn_down_remaining

    def test_no_work_since_leaves_the_estimate_alone(self):
        rem, src, warn = self.fn(D(120), D(0), D(805), D(687))
        self.assertEqual((rem, src, warn), (D(120), "pm_estimate", None))
        self.assertEqual(self.fn(D(120), None, D(805), D(687))[0], D(120))

    def test_palmer_house(self):
        """440 h estimated on Aug 30, 319 h worked before the PM revised it: 121 h may be carried forward, not 440."""
        rem, src, warn = self.fn(D(440), D(319), D(805), D(687))
        self.assertEqual((rem, src), (D(121), "pm_estimate_burned"))
        self.assertIn("319 h worked since the PM's 440 h estimate", warn)

    def test_exhausted_estimate_falls_back_to_budget_but_never_above_the_estimate(self):
        rem, src, warn = self.fn(D(100), D(150), D(805), D(687))
        self.assertEqual((rem, src), (D(100), "estimate_exhausted"), "budget leaves 118 h, but the PM only ever claimed 100")
        self.assertIn("used up", warn)
        self.assertEqual(self.fn(D(100), D(150), D(730), D(690))[0], D(40), "budget says less than the PM: take the budget")

    def test_exhausted_and_over_budget_is_zero_not_negative(self):
        rem, src, _ = self.fn(D(100), D(150), D(500), D(700))
        self.assertEqual((rem, src), (D(0), "estimate_exhausted"))

    def test_correction_is_monotone(self):
        """Whatever the inputs, the burn-down never returns MORE remaining hours than the estimate it started from."""
        for rem in (D(0), D(40), D(440), D(9200)):
            for worked in (D(0), D(1), D(500), D(20000)):
                for bud, ptt in ((D(0), D(0)), (D(805), D(687)), (D(25000), D(3000))):
                    self.assertLessEqual(self.fn(rem, worked, bud, ptt)[0], rem)

    def test_none_estimate_is_zero(self):
        self.assertEqual(self.fn(None, D(10), D(0), D(0))[0], D(0))


class RiskTests(unittest.TestCase):
    def setUp(self):
        from apps.analytics.eac_rules import risk_assessment
        self.fn = risk_assessment

    def _call(self, **kw):
        base = dict(eac_rev=D(150000), eac_gp=D(38000), eac_pct=D("0.25"), change_pts=D("0.02"), overrun=D("1.00"),
                    lifecycle_state="in_progress", unposted_h=D(0), no_estimate=False)
        base.update(kw)
        return self.fn(base["eac_rev"], base["eac_gp"], base["eac_pct"], base["change_pts"], base["overrun"],
                       base["lifecycle_state"], base["unposted_h"], base["no_estimate"], base.get("estimate_exhausted", False))

    def test_healthy_job(self):
        score, level, reasons = self._call()
        self.assertEqual((score, level, reasons), (0, "low", []))

    def test_thin_margin_below_sold(self):
        score, level, reasons = self._call(eac_gp=D(7000), eac_pct=D("0.047"), change_pts=D("-0.19"))
        self.assertEqual(score, 60)
        self.assertEqual(level, "high")
        self.assertIn("projected margin under 10%", reasons)
        self.assertIn("margin more than 15 pts below sold", reasons)

    def test_projected_loss_and_big_loss_override(self):
        self.assertEqual(self._call(eac_gp=D(-1000), eac_pct=D("-0.01"))[1], "moderate")
        score, level, reasons = self._call(eac_gp=D(-60000), eac_pct=D("-0.40"), change_pts=D("-0.60"))
        self.assertEqual(level, "critical")
        self.assertIn("projected loss over $50k", reasons)

    def test_hours_overrun_and_flags(self):
        score, level, reasons = self._call(overrun=D("1.35"))
        self.assertEqual(score, 25)
        self.assertIn("labor hours EAC exceeds budget by >30%", reasons)
        reasons = self._call(unposted_h=D(128), estimate_exhausted=True)[2]
        self.assertIn("128 PTT hours not yet posted in SL", reasons)
        self.assertIn("PM remaining-hours estimate used up by work since it was made", reasons)
        self.assertEqual(self._call(no_estimate=True)[0], 5)
        self.assertEqual(self._call(lifecycle_state="dormant")[0], 10)


if __name__ == "__main__":
    unittest.main()
