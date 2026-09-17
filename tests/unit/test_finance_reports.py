"""Unit tests for the daily finance snapshot: aging buckets, AR book classification,
period helpers. No database (spec: tests/unit runs without settings)."""

import os
import unittest
from datetime import date
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.analytics.finance_snapshot import period_of, prior_month_period  # noqa: E402
from apps.ingestion.finance_loaders import aging_bucket, classify_ar_book  # noqa: E402


class AgingBucketTests(unittest.TestCase):
    AS_OF = date(2026, 7, 6)

    def test_not_yet_due_is_current(self):
        self.assertEqual(aging_bucket(date(2026, 7, 7), self.AS_OF, False)[0], "current")
        self.assertEqual(aging_bucket(date(2026, 7, 6), self.AS_OF, False)[0], "current")  # due today = current

    def test_bucket_boundaries(self):
        self.assertEqual(aging_bucket(date(2026, 7, 5), self.AS_OF, False), ("d30", 1))
        self.assertEqual(aging_bucket(date(2026, 6, 6), self.AS_OF, False), ("d30", 30))
        self.assertEqual(aging_bucket(date(2026, 6, 5), self.AS_OF, False), ("d60", 31))
        self.assertEqual(aging_bucket(date(2026, 5, 7), self.AS_OF, False), ("d60", 60))
        self.assertEqual(aging_bucket(date(2026, 5, 6), self.AS_OF, False), ("d90", 61))
        self.assertEqual(aging_bucket(date(2026, 4, 7), self.AS_OF, False), ("d90", 90))
        self.assertEqual(aging_bucket(date(2026, 4, 6), self.AS_OF, False), ("over90", 91))

    def test_credits_never_aged(self):
        # matches the finance office's report: CM/PA net in Current regardless of age
        self.assertEqual(aging_bucket(date(2020, 1, 1), self.AS_OF, True), ("current", None))

    def test_missing_due_date_is_current(self):
        self.assertEqual(aging_bucket(None, self.AS_OF, False), ("current", None))


class ARBookTests(unittest.TestCase):
    def test_pending_beats_everything(self):
        self.assertEqual(classify_ar_book("IN", False, "SO1", "241517000000"), "pending")

    def test_credit_docs(self):
        self.assertEqual(classify_ar_book("CM", True, "RM1", ""), "credit")
        self.assertEqual(classify_ar_book("PA", True, "", ""), "credit")

    def test_order_books(self):
        self.assertEqual(classify_ar_book("IN", True, "SO1", ""), "so1")
        self.assertEqual(classify_ar_book("IN", True, "SO2", "241517000000"), "so2")  # order wins over project
        self.assertEqual(classify_ar_book("IN", True, "RM1", ""), "rma")
        self.assertEqual(classify_ar_book("IN", True, "RMS2", ""), "rma")

    def test_project_and_other(self):
        self.assertEqual(classify_ar_book("IN", True, "", "241517000000"), "project")
        self.assertEqual(classify_ar_book("IN", True, "", ""), "other")


class PeriodTests(unittest.TestCase):
    def test_period_of(self):
        self.assertEqual(period_of(date(2026, 8, 26)), "202608")

    def test_prior_month(self):
        self.assertEqual(prior_month_period(date(2026, 8, 26))[0], "202607")
        self.assertEqual(prior_month_period(date(2026, 1, 5))[0], "202512")


if __name__ == "__main__":
    unittest.main()


class WIPFormulaTests(unittest.TestCase):
    """PTT WIP 'accounting' formulas (pacescheduler reports/wip.py), ported exactly."""

    def _uo(self, cost, billed, cv, bud, eac=None):
        from decimal import Decimal as D
        from apps.analytics.finance_wip import wip_under_over
        u, o = wip_under_over(D(str(cost)), D(str(billed)), D(str(cv)), D(str(bud)), D(str(eac)) if eac is not None else None)
        return float(u), float(o)

    def test_underbilled_plain(self):
        self.assertEqual(self._uo(100, 40, 500, 300), (60.0, 0.0))

    def test_underbilled_capped_at_contract(self):
        # cost blew past the contract: claim only contract - billed (Mannheim 265302 case)
        self.assertEqual(self._uo(464095, 0, 52508, 40000), (52508.0, 0.0))

    def test_underbilled_zero_when_contract_already_billed(self):
        self.assertEqual(self._uo(600, 500, 500, 300), (0.0, 0.0))

    def test_overbilled_plain(self):
        self.assertEqual(self._uo(40, 100, 500, 300), (0.0, 60.0))

    def test_overbilled_capped_at_remaining_budget(self):
        # billed beyond budgeted cost: recognize only budget - cost
        self.assertEqual(self._uo(100, 400, 500, 300), (0.0, 200.0))

    def test_overbilled_zero_when_budget_spent(self):
        self.assertEqual(self._uo(350, 400, 500, 300), (0.0, 0.0))

    def test_no_budget_falls_back_to_eac(self):
        self.assertEqual(self._uo(100, 250, 500, 0, 300), (0.0, 150.0))

    def test_balanced_job(self):
        self.assertEqual(self._uo(200, 200, 500, 300), (0.0, 0.0))


class EarnedWIPTests(unittest.TestCase):
    """The company WIP workbook formula (Project Update WIP tab): CV x PTT % - billed,
    positive = underbilled. Cases below are real rows from the August 2026 workbook."""

    def _w(self, cv, pct, billed):
        from decimal import Decimal as D
        from apps.analytics.finance_wip import earned_wip
        return float(earned_wip(D(str(cv)), D(str(pct)) if pct is not None else None, D(str(billed))))

    def test_crystal_lake_250294(self):
        self.assertAlmostEqual(self._w(161713.32, 0.74, 87185.22), 32482.6368, places=3)

    def test_cps_genetec_265292_overbilled(self):
        self.assertAlmostEqual(self._w(198000, 0.08, 16500), -660.0, places=3)

    def test_neiu_265099_unbilled(self):
        self.assertAlmostEqual(self._w(86756, 0.92, 0), 79815.52, places=3)

    def test_no_pct_means_zero_earned(self):
        self.assertEqual(self._w(52508, None, 0), 0.0)
        self.assertEqual(self._w(52508, None, 10000), -10000.0)


class GLPnlBucketTests(unittest.TestCase):
    """Official P&L account classification — matches the accountants' division workbooks
    (070 FY2024 revenue and COGS reproduced to the penny with these buckets)."""

    def _b(self, acct, t):
        from apps.analytics.finance_snapshot import gl_pnl_bucket
        return gl_pnl_bucket(acct, t)

    def test_job_sales(self):
        self.assertEqual(self._b("40000", "3I"), "revenue")
        self.assertEqual(self._b("40001", "3I"), "revenue")

    def test_other_income(self):
        for a in ("40100", "40250", "40300", "40400"):
            self.assertEqual(self._b(a, "3I"), "other_income")

    def test_cogs_includes_salary_accounts(self):
        self.assertEqual(self._b("50700", "4E"), "cogs")
        self.assertEqual(self._b("60000", "4E"), "cogs")   # SALARIES & WAGES — in cost of sales per the workbooks
        self.assertEqual(self._b("60005", "4E"), "cogs")   # UNION BENEFITS PACE ELECTRIC

    def test_overhead(self):
        for a in ("60100", "61800", "63600", "70000"):
            self.assertEqual(self._b(a, "4E"), "overhead")

    def test_balance_sheet_accounts_ignored(self):
        self.assertIsNone(self._b("10250", "1A"))
        self.assertIsNone(self._b("20000", "2L"))


class WIPMovementTests(unittest.TestCase):
    """wip_movement: per-job change between a stored baseline (detail["wip_jobs"]) and live rows."""

    def setUp(self):
        from apps.analytics.finance_wip import wip_movement
        self.fn = wip_movement
        D = Decimal
        self.now = {"A": {"wip": D("1500"), "earned": D("5000"), "billed": D("3500")},   # earned +1000, billed +500
                    "B": {"wip": D("-200"), "earned": D("800"), "billed": D("1000")},   # unchanged
                    "N": {"wip": D("700"), "earned": D("700"), "billed": D("0")}}       # new since baseline
        self.base = {"A": [1000.0, 4000.0, 3000.0], "B": [-200.0, 800.0, 1000.0], "L": [900.0, 2900.0, 2000.0]}  # L has left

    def test_change_splits_into_earned_and_billed(self):
        mv = self.fn(self.now, self.base)
        self.assertEqual(mv["A"], {"d": 500.0, "earned": 1000.0, "billed": 500.0})
        self.assertAlmostEqual(mv["A"]["d"], mv["A"]["earned"] - mv["A"]["billed"])

    def test_unchanged_job_is_omitted(self):
        self.assertNotIn("B", self.fn(self.now, self.base))

    def test_job_that_left_population_goes_to_zero(self):
        mv = self.fn(self.now, self.base)
        self.assertEqual(mv["L"], {"d": -900.0, "earned": -2900.0, "billed": -2000.0})

    def test_new_job_counts_from_zero(self):
        self.assertEqual(self.fn(self.now, self.base)["N"], {"d": 700.0, "earned": 700.0, "billed": 0.0})

    def test_min_abs_threshold(self):
        now = {"A": {"wip": Decimal("1000.4"), "earned": Decimal("4000.4"), "billed": Decimal("3000")}}
        self.assertEqual(self.fn(now, {"A": [1000.0, 4000.0, 3000.0]}), {})


class PTTEntryAgeTests(unittest.TestCase):
    """age_css: colour band for how long ago a PTT % / remaining-hours entry was made."""

    def test_bands(self):
        from apps.analytics.finance_wip import age_css, FRESH_DAYS, WARN_DAYS, STALE_DAYS
        self.assertEqual(age_css(0), "pos")
        self.assertEqual(age_css(FRESH_DAYS), "pos")
        self.assertEqual(age_css(FRESH_DAYS + 1), "")
        self.assertEqual(age_css(WARN_DAYS), "")
        self.assertEqual(age_css(WARN_DAYS + 1), "warn-ink")
        self.assertEqual(age_css(STALE_DAYS), "warn-ink")
        self.assertEqual(age_css(STALE_DAYS + 1), "neg")
        self.assertEqual(age_css(None), "neg")

    def test_stale_threshold_matches_data_quality_rule(self):
        from apps.analytics.finance_wip import STALE_DAYS
        self.assertEqual(STALE_DAYS, 45)  # analytics/services.py flags pm_percent_complete_stale after 45 days


class LedgerBreakdownTests(unittest.TestCase):
    """Pure helpers behind the 'what makes up total liabilities / assets' modals
    (apps.analytics.finance_ledger + the two side specs)."""

    def setUp(self):
        from apps.analytics import finance_assets, finance_ledger, finance_liabilities
        self.L, self.FL, self.FA = finance_ledger, finance_liabilities, finance_assets

    def test_every_named_liability_account_lands_in_a_display_group(self):
        keys = {g[0] for g in self.FL.GROUPS}
        for acct, (group, _explain) in self.FL.ACCOUNTS.items():
            self.assertIn(group, keys, acct)
            self.assertTrue(acct.startswith("2") and acct < self.FL.LIABILITY_MAX_ACCT, acct)

    def test_every_named_asset_account_lands_in_a_display_group(self):
        keys = {g[0] for g in self.FA.GROUPS}
        for acct, (group, _explain) in self.FA.ACCOUNTS.items():
            self.assertIn(group, keys, acct)
            self.assertTrue(acct.startswith("1") or acct == "000000", acct)
        # the current/non-current cut matches the snapshot builder's
        from apps.analytics.finance_snapshot import CURRENT_ASSET_MAX
        self.assertEqual(self.FA.CURRENT_ASSET_MAX, CURRENT_ASSET_MAX)

    def test_specs_disagree_only_on_sign_and_population(self):
        for spec in (self.FL.SPEC, self.FA.SPEC):
            for key in ("key", "where", "inc", "up", "down", "inv", "groups", "accounts", "names", "sub_tab", "snapshot_field"):
                self.assertIn(key, spec)
        self.assertEqual(self.FL.SPEC["inc"], "cr_amt - dr_amt")
        self.assertEqual(self.FA.SPEC["inc"], "dr_amt - cr_amt")
        self.assertTrue(self.FL.SPEC["inv"] and not self.FA.SPEC["inv"])

    def test_unknown_account_is_other_with_gl_description_name(self):
        self.assertEqual(self.FL.classify("29999"), "other")
        self.assertEqual(self.FL.explain("29999"), "")
        self.assertEqual(self.FL.name("29999", "SOME NEW ACCRUAL"), "Some new accrual")
        self.assertEqual(self.FL.name("20001"), "PO clearing — received, not invoiced")
        self.assertEqual(self.FA.classify("19999"), "other")

    def test_month_end_balance_uses_periods_through_month_and_folds_adjustment_into_december(self):
        row = {"beg": Decimal("100"), "p": [Decimal(i) for i in range(1, 14)]}   # Jan=1 … Dec=12, adj=13
        self.assertEqual(self.L.month_end_balance(row, 1), Decimal("101"))
        self.assertEqual(self.L.month_end_balance(row, 3), Decimal("106"))          # 100 + 1+2+3
        self.assertEqual(self.L.month_end_balance(row, 11), Decimal("166"))         # 100 + 1..11
        self.assertEqual(self.L.month_end_balance(row, 12), Decimal("191"))         # 100 + 1..12 + 13

    def test_month_series_spans_fiscal_years_and_marks_missing_years(self):
        fys = {"2025": {"beg": Decimal("10"), "p": [Decimal("1")] * 13},
               "2026": {"beg": Decimal("23"), "p": [Decimal("2")] * 13}}
        series = self.L.month_series(fys, 2026, 2, n=4)
        self.assertEqual([lbl for lbl, _ in series], ["2025-11", "2025-12", "2026-01", "2026-02"])
        self.assertEqual([v for _, v in series], [Decimal("21"), Decimal("23"), Decimal("25"), Decimal("27")])
        self.assertIsNone(self.L.month_series({"2026": fys["2026"]}, 2026, 1, n=2)[0][1])   # Dec 2025 unknown

    def test_months_back_is_oldest_first_and_wraps_the_year(self):
        self.assertEqual(self.L.months_back(2026, 2, 3), [(2025, 12), (2026, 1), (2026, 2)])

    def test_sparkline_fits_the_box_and_skips_gaps(self):
        pts = self.L.sparkline([0, None, 10], width=100, height=20, pad=0).split(" ")
        self.assertEqual(pts, ["0.0,20.0", "100.0,0.0"])          # first point bottom-left, last top-right
        self.assertEqual(self.L.sparkline([5, 5, 5], width=100, height=20, pad=0).split(" ")[1], "50.0,20.0")  # flat = baseline
        self.assertEqual(self.L.sparkline([1]), "")

    def test_bar_segments_scale_positive_groups_and_report_negatives(self):
        groups = [{"total": Decimal("75")}, {"total": Decimal("25")}, {"total": Decimal("-10")}]
        negatives = self.L.bar_segments(groups)
        self.assertEqual([g["bar_pct"] for g in groups], [75.0, 25.0, 0.0])
        self.assertEqual(negatives, [groups[2]])

    def test_fy_balance_reads_the_by_acct_structure(self):
        by_acct = {"11000": {"2026": {"beg": Decimal("5"), "p": [Decimal("1")] * 13}}}
        self.assertEqual(self.L.fy_balance(by_acct, "11000", 2026), Decimal("18"))
        self.assertEqual(self.L.fy_balance(by_acct, "12000", 2026), Decimal("0"))
