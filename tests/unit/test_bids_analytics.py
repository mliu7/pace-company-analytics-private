"""Pure helpers in apps/bids/analytics.py — size bands, hit rates, calibration, expected bookings. No database."""

import os
import sys
import unittest
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.bids import analytics as A  # noqa: E402


class SizeBands(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(A.size_band(None), "(no value)")
        self.assertEqual(A.size_band(Decimal("0")), "(no value)")
        self.assertEqual(A.size_band(Decimal("4999")), "< $5k")
        self.assertEqual(A.size_band(Decimal("5000")), "$5k–25k")
        self.assertEqual(A.size_band(Decimal("99999.99")), "$25k–100k")
        self.assertEqual(A.size_band(Decimal("100000")), "$100k–500k")
        self.assertEqual(A.size_band(Decimal("5000000")), "$500k+")


class Rates(unittest.TestCase):
    def test_hit_rate(self):
        self.assertIsNone(A.hit_rate(0, 0))
        self.assertEqual(A.hit_rate(3, 1), 0.75)

    def test_weighted(self):
        rows = [{"won": True, "value": Decimal(300)}, {"won": False, "value": Decimal(100)}, {"won": True, "value": None}]
        self.assertEqual(A.weighted(rows, "won", "value"), 0.75)
        self.assertIsNone(A.weighted([], "won", "value"))

    def test_median(self):
        self.assertIsNone(A.median([]))
        self.assertEqual(A.median([3, 1, 2]), 2)
        self.assertEqual(A.median([1, 2, 3, 4]), 2.5)


class Calibration(unittest.TestCase):
    def test_buckets_and_gap(self):
        out = A.calibration([(75, True), (75, True), (75, False), (25, False), (None, True)])
        self.assertEqual([b["bucket"] for b in out], [25, 75])
        b75 = out[1]
        self.assertEqual(b75["n"], 3)
        self.assertAlmostEqual(b75["stated"], 0.75)
        self.assertAlmostEqual(b75["actual"], 2 / 3)
        self.assertAlmostEqual(b75["gap"], 2 / 3 - 0.75)


class ExpectedBookings(unittest.TestCase):
    def bid(self, **kw):
        base = {"stage": "submitted", "source": "list", "value": Decimal(1000), "probability": 50, "submitted_on": date(2026, 9, 1), "bid_due": None, "division": "070", "estimator_id": 7}
        base.update(kw)
        return base

    def test_windows_and_overdue(self):
        today = date(2026, 9, 10)
        cycles = {"070": 20, "": 30}
        # decides 2026-09-21 -> inside 30d; a 60-day-old one is overdue; a quoting row with a far due date lands in 90d only
        bids = [self.bid(), self.bid(submitted_on=date(2026, 7, 1)), self.bid(stage="quoting", submitted_on=None, bid_due=date(2026, 11, 1), probability=None)]
        out = A.expected_bookings(bids, today, cycles, hit_rates={7: 0.6})
        self.assertEqual(out[30]["count"], 2)
        self.assertEqual(out[30]["stated"], Decimal(1000))              # 2 × 1000 × 50 %
        self.assertEqual(out[30]["historical"], Decimal("1200.0"))        # 2 × 1000 × 0.6
        self.assertEqual(out[90]["count"], 3)
        self.assertEqual(out[90]["stated"], Decimal(1000))              # the unscored quoting row adds nothing at stated probability
        self.assertEqual(out["overdue"]["count"], 1)
        self.assertEqual(out["overdue"]["value"], Decimal(1000))

    def test_archive_and_closed_rows_are_ignored(self):
        today = date(2026, 9, 10)
        bids = [self.bid(source="archive"), self.bid(stage="awarded"), self.bid(stage="lost")]
        out = A.expected_bookings(bids, today, {"": 20})
        self.assertEqual(out[90]["count"], 0)


if __name__ == "__main__":
    unittest.main()


class AttentionReasons(unittest.TestCase):
    def row(self, **kw):
        base = {"stage": "submitted", "estimator_id": 7, "bid_due": date(2026, 9, 30), "job_number_raw": "", "value": Decimal(1000), "budget": Decimal(700),
                "submitted_on": date(2026, 9, 1), "bom_status": "", "probability": 50, "portal_modified": None, "source": "list"}
        base.update(kw)
        return base

    def test_clean_row_has_no_reasons(self):
        self.assertEqual(A.attention_reasons(self.row(), date(2026, 9, 10)), [])

    def test_each_rule(self):
        today = date(2026, 9, 10)
        self.assertIn("Missing bidder", A.attention_reasons(self.row(estimator_id=None), today))
        self.assertIn("Missing due date", A.attention_reasons(self.row(bid_due=None), today))
        self.assertIn("Submitted, no value", A.attention_reasons(self.row(value=None), today))
        self.assertIn("No budget", A.attention_reasons(self.row(budget=None), today))
        self.assertNotIn("No submitted date", A.attention_reasons(self.row(submitted_on=None), today))   # dropped 2026-09-11: a submitted row is submitted
        self.assertIn("BOM needed", A.attention_reasons(self.row(bom_status="Needed"), today))
        self.assertIn("Past due", A.attention_reasons(self.row(stage="quoting", bid_due=date(2026, 9, 1)), today))
        self.assertNotIn("Past due", A.attention_reasons(self.row(stage="submitted", bid_due=date(2026, 9, 1)), today))   # submitted = past its due date by definition
        self.assertIn("Unscored", A.attention_reasons(self.row(probability=None), today))
        self.assertIn("Awarded, no job #", A.attention_reasons(self.row(stage="awarded"), today))
        self.assertNotIn("Past due", A.attention_reasons(self.row(stage="lost", bid_due=date(2026, 9, 1)), today))

    def test_stale_ladder_for_quotes(self):
        from django.utils import timezone
        today = date(2026, 9, 10)
        old = lambda days: timezone.now() - timezone.timedelta(days=days)
        self.assertIn("Stale 60d+", A.attention_reasons(self.row(stage="quoting", portal_modified=old(70)), today))
        r = A.attention_reasons(self.row(stage="quoting", portal_modified=old(130)), today)
        self.assertIn("Stale 100d+", r); self.assertNotIn("Stale 60d+", r)
        self.assertNotIn("Stale 60d+", A.attention_reasons(self.row(stage="quoting", portal_modified=old(20)), today))
        self.assertIn("No update 120d+", A.attention_reasons(self.row(stage="submitted", portal_modified=old(130)), today))
