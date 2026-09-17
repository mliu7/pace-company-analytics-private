"""Divisional P&L helpers — periods, account classification, estimates and row math. Pure
functions, no database (docs/10_divisional_pnl.md)."""
import os
import unittest
from datetime import date
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.analytics.divisional_pnl import (  # noqa: E402
    COST_GROUPS, OVERHEAD_GROUPS, Period, allocate, cost_group, lag_estimate, month_end, months_back,
    open_month_set, overhead_group, parse_period, period_for_month, periods_in_year, pjtran_group, pnl_line,
    prior_month, row_math,
)

D = Decimal


class PeriodTests(unittest.TestCase):
    def test_parse_month(self):
        p = parse_period("2026-08")
        self.assertEqual((p.kind, p.year, p.idx, p.key, p.label), ("month", 2026, 8, "2026-08", "Aug 2026"))
        self.assertEqual(p.months, ["202608"])
        self.assertEqual((p.start, p.end), (date(2026, 8, 1), date(2026, 8, 31)))

    def test_parse_quarter_and_year(self):
        q = parse_period("2026-q3")
        self.assertEqual((q.kind, q.idx, q.label), ("quarter", 3, "Q3 2026"))
        self.assertEqual(q.months, ["202607", "202608", "202609"])
        y = parse_period("2025")
        self.assertEqual((y.kind, y.key, len(y.months)), ("year", "2025", 12))
        self.assertEqual((y.start, y.end), (date(2025, 1, 1), date(2025, 12, 31)))

    def test_bad_keys(self):
        for bad in ("", "2026-13", "2026-Q5", "26-08", "2026/08", "abcd"):
            with self.assertRaises(ValueError):
                parse_period(bad)

    def test_prev_next_cross_year(self):
        self.assertEqual(Period("month", 2026, 1).prev().key, "2025-12")
        self.assertEqual(Period("month", 2025, 12).next().key, "2026-01")
        self.assertEqual(Period("quarter", 2026, 1).prev().key, "2025-Q4")
        self.assertEqual(Period("quarter", 2025, 4).next().key, "2026-Q1")
        self.assertEqual(Period("year", 2026).prev().key, "2025")

    def test_periods_in_year_and_for_month(self):
        self.assertEqual([p.key for p in periods_in_year("quarter", 2026)], ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"])
        self.assertEqual(len(periods_in_year("month", 2026)), 12)
        self.assertEqual(period_for_month("quarter", "202608").key, "2026-Q3")
        self.assertEqual(period_for_month("year", "202602").key, "2026")

    def test_month_helpers(self):
        self.assertEqual(month_end("202402"), date(2024, 2, 29))
        self.assertEqual(prior_month("202601"), "202512")
        self.assertEqual(months_back("202602", 3), ["202601", "202512", "202511"])


class ClassificationTests(unittest.TestCase):
    def test_pnl_lines(self):
        self.assertEqual(pnl_line("40000", "3I"), "revenue")
        self.assertEqual(pnl_line("40001", "3I"), "revenue")
        self.assertEqual(pnl_line("40100", "3I"), "other_income")
        self.assertEqual(pnl_line("50700", "4E"), "costs")
        self.assertEqual(pnl_line("60000", "4E"), "costs")     # division salaries are cost of sales
        self.assertEqual(pnl_line("60005", "4E"), "costs")
        self.assertEqual(pnl_line("50701", "4E"), "wip")       # booked WIP is its own line, never inside costs
        self.assertEqual(pnl_line("60100", "4E"), "overhead")
        self.assertEqual(pnl_line("70000", "4E"), "overhead")
        self.assertIsNone(pnl_line("12000", "1A"))
        self.assertIsNone(pnl_line("20000", "2L"))

    def test_groups(self):
        self.assertEqual(cost_group("50700"), "Materials & goods")
        self.assertEqual(cost_group("50714"), "Direct labor")
        self.assertEqual(cost_group("60000"), "Direct labor")
        self.assertEqual(cost_group("50711"), "Labor burden & union")
        self.assertEqual(cost_group("50716"), "Subcontractors")
        self.assertEqual(cost_group("50300"), "Other direct costs")
        self.assertEqual(cost_group("59999"), COST_GROUPS[-1])
        self.assertEqual(overhead_group("60100"), "G&A salaries & benefits")
        self.assertEqual(overhead_group("63600"), "Facilities, insurance & depreciation")
        self.assertEqual(overhead_group("65440"), "Travel, auto & entertainment")
        self.assertEqual(overhead_group("70000"), OVERHEAD_GROUPS[-1])

    def test_pjtran_group(self):
        self.assertEqual(pjtran_group("revenue", "40000"), "Revenue")
        self.assertEqual(pjtran_group("material", "50700"), "Materials & goods")
        self.assertEqual(pjtran_group("labor_wage", None), "Direct labor")        # payroll allocation: no GL account
        self.assertEqual(pjtran_group("labor_burden", ""), "Labor burden & union")
        self.assertEqual(pjtran_group("labor_wage", "60000"), "Direct labor")
        self.assertEqual(pjtran_group("other_direct", "50300"), "Other direct costs")


class EstimateTests(unittest.TestCase):
    def test_lag_estimate(self):
        self.assertEqual(lag_estimate([100, 120, 140], 30), D("90"))
        self.assertEqual(lag_estimate([100, 100], 150), D("0"))       # posted more than the baseline: nothing missing
        self.assertEqual(lag_estimate([], 50), D("0"))
        self.assertEqual(lag_estimate([None, 200], 50), D("150"))

    def test_open_months(self):
        today = date(2026, 8, 31)
        booked = {"202601", "202602", "202603", "202604", "202605", "202606", "202607"}
        self.assertEqual(open_month_set(booked, today), {"202608"})
        # July not closed either -> both open
        self.assertEqual(open_month_set(booked - {"202607"}, today), {"202607", "202608"})
        # the current month already booked (closed on the 31st) -> nothing open
        self.assertEqual(open_month_set(booked | {"202608"}, today), set())
        # an old month with no entry is history, not an open month
        self.assertEqual(open_month_set(booked | {"202608"} - {"202601"}, today), set())
        # January: lookback crosses the year boundary
        self.assertEqual(open_month_set({"202509", "202510", "202511", "202512"}, date(2026, 1, 15)), {"202601"})
        # a month inside the lookback with no entry is open too (close not done yet)
        self.assertEqual(open_month_set({"202509", "202510", "202511"}, date(2026, 1, 15)), {"202512", "202601"})


class MathTests(unittest.TestCase):
    def test_allocate(self):
        alloc, rest = allocate(D("1000"), {"040": D("0.25"), "070": D("0.25")})
        self.assertEqual(alloc, {"040": D("250"), "070": D("250")})
        self.assertEqual(rest, D("500"))
        alloc, rest = allocate(D("1000"), {"a": D("0.5"), "b": D("0.5")})
        self.assertEqual(rest, D("0"))
        alloc, rest = allocate(D("1000"), {})
        self.assertEqual((alloc, rest), ({}, D("1000")))

    def test_row_math(self):
        r = row_math({"revenue": D("1000"), "other_income": D("10"), "costs": D("600"), "wip_booked": D("100"),
                      "wip_pca": D("0"), "overhead": D("150"), "payroll_lag": D("40"), "corp_alloc": D("60")})
        self.assertEqual(r["wip"], D("100"))
        self.assertEqual(r["gross"], D("500"))                     # 1000 - 600 + 100
        self.assertEqual(r["earned"], D("1100"))
        self.assertEqual(r["gross_pct"], D("500") / D("1100"))
        self.assertEqual(r["op_income"], D("260"))                 # 500 + 10 - 150 - 40 - 60
        self.assertEqual(r["op_pct"], D("260") / D("1100"))

    def test_row_math_no_revenue(self):
        r = row_math({"costs": D("5")})
        self.assertEqual(r["gross"], D("-5"))
        self.assertIsNone(r["gross_pct"])
        self.assertIsNone(r["op_pct"])

    def test_pct_blank_on_negative_earned_base(self):
        # revenue 100, WIP -300 -> earned -200: a ratio on a negative base is meaningless
        r = row_math({"revenue": D("100"), "costs": D("50"), "wip_booked": D("-300")})
        self.assertEqual(r["earned"], D("-200"))
        self.assertIsNone(r["gross_pct"])
        self.assertIsNone(r["op_pct"])


if __name__ == "__main__":
    unittest.main()


class SnapshotJobEntryTests(unittest.TestCase):
    """Per-job snapshot entries are [wip, earned, billed]; reconstructed year-ends store WIP only."""

    def test_snap_val_reads_position_or_zero(self):
        from apps.analytics.divisional_pnl import _snap_val
        self.assertEqual(_snap_val([12.5, 100.0, 87.5], 0), D("12.5"))
        self.assertEqual(_snap_val([12.5, 100.0, 87.5], 2), D("87.5"))
        self.assertEqual(_snap_val([12.5], 1), D(0))          # sparse entry: no earned stored
        self.assertEqual(_snap_val([12.5, None, 3], 1), D(0))
        self.assertEqual(_snap_val(None, 0), D(0))            # job absent from the snapshot

    def test_has_drivers_only_when_earned_or_billed_present(self):
        from apps.analytics.divisional_pnl import _has_drivers
        self.assertTrue(_has_drivers({"A": [10.0, 50.0, 40.0], "B": [0.0, 0.0, 0.0]}))
        self.assertFalse(_has_drivers({"A": [10.0, 0.0, 0.0], "B": [-5.0, 0.0, 0.0]}))   # reconstructed year-end shape
        self.assertFalse(_has_drivers({"A": [10.0]}))
        self.assertFalse(_has_drivers({}))
