"""WIP by job — reporting-period resolution (finance_wip.resolve_period / period_options), pure."""
import unittest
from datetime import date


class ResolvePeriodTests(unittest.TestCase):
    def setUp(self):
        from apps.analytics.finance_wip import resolve_period, period_options
        self.fn, self.opts = resolve_period, period_options
        self.today = date(2026, 8, 29)

    def test_default_is_current_month_live(self):
        for p in ("", None, "junk", "2027-01", "2018-05", "2026-13", "12345"):
            r = self.fn(p, self.today)
            self.assertEqual((r["key"], r["live"], r["end"], r["start"]), ("2026-08", "mtd", self.today, None), p)
        self.assertEqual(self.fn("", self.today)["label"], "August 2026 to date")

    def test_past_month(self):
        r = self.fn("2026-07", self.today)
        self.assertEqual((r["live"], r["label"], r["end"], r["start"]), (None, "July 2026", date(2026, 7, 31), date(2026, 6, 30)))
        r = self.fn("2024-2", self.today)   # single-digit month tolerated, normalised key
        self.assertEqual((r["key"], r["end"], r["start"]), ("2024-02", date(2024, 2, 29), date(2024, 1, 31)))
        r = self.fn("2026-01", self.today)
        self.assertEqual(r["start"], date(2025, 12, 31))

    def test_years(self):
        r = self.fn("2025", self.today)
        self.assertEqual((r["live"], r["label"], r["end"], r["start"]), (None, "2025", date(2025, 12, 31), date(2024, 12, 31)))
        r = self.fn("2026", self.today)
        self.assertEqual((r["live"], r["label"], r["end"]), ("ytd", "2026 to date", self.today))
        self.assertEqual(self.fn("2019", self.today)["start"], date(2018, 12, 31))   # first full year with a Dec-31 baseline
        self.assertEqual(self.fn("2018", self.today)["key"], "2026-08")             # before the baselines: default

    def test_live_windows_and_legacy_window_param(self):
        self.assertEqual(self.fn("day", self.today)["live"], "day")
        self.assertEqual(self.fn("wtd", self.today)["label"], "Week to date")
        self.assertEqual(self.fn("", self.today, "mtd")["live"], "mtd")
        self.assertEqual(self.fn("", self.today, "ytd")["key"], "2026")
        self.assertEqual(self.fn("", self.today, "day")["live"], "day")
        self.assertEqual(self.fn("2025-03", self.today, "day")["label"], "March 2025")   # explicit period wins

    def test_options(self):
        groups = dict(self.opts(self.today, months_back=14))
        self.assertEqual([k for k, _ in groups["Live"]], ["day", "wtd"])
        months = groups["Month"]
        self.assertEqual(months[0], ("2026-08", "August 2026 (to date)"))
        self.assertEqual(months[1], ("2026-07", "July 2026"))
        self.assertEqual(len(months), 14)
        self.assertEqual(months[-1][0], "2025-07")
        years = groups["Year"]
        self.assertEqual(years[0], ("2026", "2026 (to date)"))
        self.assertEqual(years[-1], ("2019", "2019"))
        # every option resolves to itself
        for grp, opts in groups.items():
            for k, _ in opts:
                self.assertEqual(self.fn(k, self.today)["key"], k, k)


if __name__ == "__main__":
    unittest.main()


class PeriodMonthsTests(unittest.TestCase):
    """The fiscal periods a WIP report's ledger result (GP for the period) is built from."""

    def test_months_years_and_live_windows(self):
        from apps.analytics.finance_wip import resolve_period, period_months
        today = date(2026, 9, 3)
        self.assertEqual(period_months(resolve_period("2026-07", today)), ["202607"])
        self.assertEqual(period_months(resolve_period("2026-09", today)), ["202609"])            # month to date
        self.assertEqual(period_months(resolve_period("2025", today)), ["2025%02d" % m for m in range(1, 13)])
        self.assertEqual(period_months(resolve_period("2026", today)), ["2026%02d" % m for m in range(1, 10)])   # year to date
        self.assertIsNone(period_months(resolve_period("day", today)))
        self.assertIsNone(period_months(resolve_period("wtd", today)))


class RebaseJobsTests(unittest.TestCase):
    """Per-job baselines are re-anchored on the ledger's billed-to-date (pure when `billed` is passed)."""

    def test_billed_replaced_and_wip_recomputed(self):
        from apps.analytics.finance_wip import rebase_jobs, month_end_period
        jobs = {"A": [227.53, 10599.53, 10372.0], "B": [1000.0, 1000.0, 0.0]}
        out = rebase_jobs(jobs, date(2026, 8, 31), billed={"A": 15622.0})      # A billed a back-dated 5,250; B nothing
        self.assertEqual(out["A"], [10599.53 - 15622.0, 10599.53, 15622.0])
        self.assertEqual(out["B"], [1000.0, 1000.0, 0.0])
        self.assertEqual(month_end_period(date(2026, 8, 31)), "202608")
        self.assertIsNone(month_end_period(date(2026, 8, 30)))
        self.assertEqual(month_end_period(date(2024, 2, 29)), "202402")

    def test_sparse_rows_get_earned_derived_wip_kept(self):
        from apps.analytics.finance_wip import rebase_jobs
        sparse = {"A": [500.0, 0.0, 0.0], "B": [-20.0, 0.0, 0.0]}                # reconstructed year-end shape
        out = rebase_jobs(sparse, date(2024, 12, 31), billed={"A": 99.0})
        self.assertEqual(out, {"A": [500.0, 599.0, 99.0], "B": [-20.0, -20.0, 0.0]})
        self.assertEqual(rebase_jobs({}, date(2024, 12, 31), billed={}), {})

    def test_movement_keeps_jobs_that_earned_and_billed_alike(self):
        from decimal import Decimal
        from apps.analytics.finance_wip import wip_movement
        now = {"A": {"wip": Decimal("0"), "earned": Decimal("1596"), "billed": Decimal("1596")},
               "B": {"wip": Decimal("10"), "earned": Decimal("10"), "billed": Decimal("0")}}
        mv = wip_movement(now, {"B": [10.0, 10.0, 0.0]})
        self.assertEqual(mv["A"], {"d": 0.0, "earned": 1596.0, "billed": 1596.0})   # WIP flat, but it moved
        self.assertNotIn("B", mv)                                                  # nothing moved at all
