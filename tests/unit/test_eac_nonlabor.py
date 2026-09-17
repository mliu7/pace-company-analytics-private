"""Unit tests for the pooled non-labor EAC floor and voucher-matched PO netting (2026-08-31,
found via 264932: subcontract budget bought as material + an open PO whose voucher already posted)."""

import os
import unittest
from datetime import date
from decimal import Decimal as D

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.analytics.eac import nonlabor_eac, plausible_rate, remaining_is_unset  # noqa: E402
from apps.core.rules import match_vouchered_pos  # noqa: E402


class NonLaborEacTests(unittest.TestCase):
    def test_264932_shape_cross_bucket(self):
        # budget all in subcontract (171,215); the sub work was vouchered as material (73,919 incl. 72,675)
        mat, sub, odc = nonlabor_eac(D("73919.20"), D(0), D(0), D(0), D(0), D(0), D("171215"), D("29800"), D(0))
        self.assertAlmostEqual(float(mat + sub + odc), 171215.0 + 0, delta=0.01)  # total floored at pooled budget
        self.assertAlmostEqual(float(mat), 73919.20, delta=0.01)                  # spent money never shrinks
        # old per-category logic would have said 73,919 + 171,215 + 29,800 = 274,934

    def test_rokwire_shape_reverse_buckets(self):
        # budget in material (95,000), spend posted as subcontract (95,500)
        mat, sub, odc = nonlabor_eac(D(0), D(0), D(0), D("95000"), D("95500"), D(0), D(0), D(0), D(0))
        self.assertEqual((mat, sub, odc), (D(0), D("95500"), D(0)))               # no double floor

    def test_budget_floor_still_applies(self):
        mat, sub, odc = nonlabor_eac(D("10000"), D(0), D("5000"), D("40000"), D(0), D(0), D("20000"), D(0), D(0))
        self.assertAlmostEqual(float(mat + sub + odc), 60000.0, delta=0.01)       # total = pooled budget
        self.assertGreaterEqual(float(mat), 15000.0)
        self.assertGreater(float(sub), 0)                                          # pool distributed by unspent budget

    def test_overspend_beats_budget(self):
        mat, sub, odc = nonlabor_eac(D("50000"), D("1000"), D("10000"), D("30000"), D(0), D(0), D(0), D("5000"), D("2000"))
        self.assertEqual((mat, sub, odc), (D("61000"), D(0), D("5000")))          # base > pooled budget -> no pool


class VoucherMatchTests(unittest.TestCase):
    def test_exact_match(self):
        pos = [(1, 10, "OME001", D("72675.00"), date(2026, 5, 27))]
        vs = [(10, "OME001", D("72675.00"), date(2026, 5, 27))]
        self.assertEqual(match_vouchered_pos(pos, vs), {1})

    def test_no_cross_project_or_vendor_or_amount(self):
        pos = [(1, 10, "OME001", D("72675.00"), date(2026, 5, 27))]
        self.assertEqual(match_vouchered_pos(pos, [(11, "OME001", D("72675.00"), date(2026, 6, 1))]), set())
        self.assertEqual(match_vouchered_pos(pos, [(10, "AMP001", D("72675.00"), date(2026, 6, 1))]), set())
        self.assertEqual(match_vouchered_pos(pos, [(10, "OME001", D("72600.00"), date(2026, 6, 1))]), set())

    def test_one_voucher_matches_only_one_line(self):
        pos = [(1, 10, "V", D("500"), date(2026, 1, 1)), (2, 10, "V", D("500"), date(2026, 1, 2))]
        vs = [(10, "V", D("500"), date(2026, 1, 5))]
        self.assertEqual(len(match_vouchered_pos(pos, vs)), 1)

    def test_voucher_much_older_than_po_does_not_match(self):
        pos = [(1, 10, "V", D("500"), date(2026, 6, 1))]
        self.assertEqual(match_vouchered_pos(pos, [(10, "V", D("500"), date(2025, 1, 1))]), set())


if __name__ == "__main__":
    unittest.main()


class RemainingHoursAndRateGuards(unittest.TestCase):
    """2026-09-01, found via the 070 forecast page (forecast GP > sold GP): 265312 carried PTT's default 0
    remaining hours with no estimate ever entered, and 229425 priced remaining hours at a $416/h posted rate."""

    def test_never_entered_zero_is_no_estimate(self):
        self.assertTrue(remaining_is_unset(None, None))
        self.assertTrue(remaining_is_unset(D(0), None))                       # PTT default, PM never touched it
        self.assertFalse(remaining_is_unset(D(0), date(2026, 8, 1)))          # a PM deliberately said zero
        self.assertFalse(remaining_is_unset(D(120), None))

    def test_posted_rate_must_be_a_rate(self):
        self.assertTrue(plausible_rate(D("91.92"), D("104.56")))
        self.assertTrue(plausible_rate(D("29"), D("32.21")))                 # cheap IT staffing is real
        self.assertFalse(plausible_rate(D("415.59"), D("104.56")))           # 229425
        self.assertTrue(plausible_rate(D("240"), None))                       # no division rate: $250 absolute cap
        self.assertFalse(plausible_rate(D("260"), None))
        self.assertFalse(plausible_rate(D(0), D("100")))
