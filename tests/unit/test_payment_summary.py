"""Payments Received — the payment-row summary (apps/analytics/payment_summary.py). No DB."""
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace as NS


def line(ref, inv, due, applied, amt, bal, project=None, so1=False, typ="IN"):
    return NS(invoice_ref=ref, invoice_type=typ, invoice_date=inv, due_date=due, date_appl=applied,
              days_late=(applied - due).days if (applied and due) else None, invoice_amt=Decimal(amt), invoice_balance=Decimal(bal),
              project_id=project.id if project else None, project=project, is_so1=so1)


JOB = NS(id=7, display_number="264888")
JOB2 = NS(id=8, display_number="250038")


class LateLabelTests(unittest.TestCase):
    def test_boundaries(self):
        from apps.analytics.payment_summary import late_label
        self.assertEqual(late_label(None), "")
        self.assertEqual(late_label(-5), "on time")
        self.assertEqual(late_label(0), "on time")
        self.assertEqual(late_label(1), "1 day late")
        self.assertEqual(late_label(6), "6 days late")
        self.assertEqual(late_label(7), "1 week late")
        self.assertEqual(late_label(29), "4 weeks late")
        self.assertEqual(late_label(30), "1 month late")
        self.assertEqual(late_label(75), "2 months late")
        self.assertEqual(late_label(400), "1 year late")


class SummaryTests(unittest.TestCase):
    def setUp(self):
        from apps.analytics.payment_summary import summarize_payment
        self.fn = summarize_payment

    def test_single_invoice(self):
        s = self.fn([line("1", date(2026, 7, 31), date(2026, 8, 30), date(2026, 9, 3), "1000", "0", project=JOB)])
        self.assertEqual((s["inv_date"], s["inv_multi"], s["due"], s["due_multi"]), (date(2026, 7, 31), False, date(2026, 8, 30), False))
        self.assertEqual((s["paid"]["label"], s["paid"]["cls"]), ("4 days late", "neg"))
        self.assertEqual((s["project_mode"], s["project"]), ("one", JOB))
        self.assertEqual((s["inv_amt"], s["open"]), (Decimal("1000"), Decimal("0")))

    def test_same_timeframe_no_average(self):
        s = self.fn([line("1", date(2026, 6, 30), date(2026, 7, 30), date(2026, 9, 3), "100", "0", project=JOB),
                     line("2", date(2026, 6, 30), date(2026, 7, 28), date(2026, 9, 3), "200", "50", project=JOB)])
        self.assertEqual(s["paid"]["label"], "1 month late", "both invoices are '1 month late': no average")
        self.assertFalse(s["inv_multi"])
        self.assertTrue(s["due_multi"])
        self.assertEqual((s["inv_amt"], s["open"]), (Decimal("300"), Decimal("50")))

    def test_different_timeframes_average(self):
        s = self.fn([line("1", date(2026, 6, 1), date(2026, 7, 1), date(2026, 9, 3), "100", "0", project=JOB),     # 64 days late
                     line("2", date(2026, 8, 1), date(2026, 9, 10), date(2026, 9, 3), "100", "0", project=JOB)])   # early → 0
        self.assertEqual(s["paid"]["label"], "1 month late (avg.)")   # (64 + 0) / 2 = 32 days
        self.assertEqual(s["paid"]["cls"], "neg")
        self.assertIn("1 of 2 invoices paid late", s["paid"]["title"])
        self.assertTrue(s["inv_multi"] and s["due_multi"])
        self.assertIn("2 invoices dated Jun 1, 2026 – Aug 1, 2026", s["inv_range"])

    def test_all_on_time(self):
        s = self.fn([line("1", date(2026, 8, 1), date(2026, 9, 10), date(2026, 9, 3), "10", "0"),
                     line("2", date(2026, 8, 2), date(2026, 9, 12), date(2026, 9, 3), "10", "0")])
        self.assertEqual((s["paid"]["label"], s["paid"]["cls"]), ("on time", "pos"))

    def test_project_modes(self):
        so1 = [line("1", None, None, None, "5", "0", so1=True), line("2", None, None, None, "5", "0", so1=True)]
        self.assertEqual(self.fn(so1)["project_mode"], "so1")
        mixed = [line("1", None, None, None, "5", "0", project=JOB), line("2", None, None, None, "5", "0", so1=True)]
        s = self.fn(mixed)
        self.assertEqual((s["project_mode"], s["project"]), ("multiple", None))
        self.assertEqual(s["projects_title"], "Invoices on: 264888, SO1 order")
        two = self.fn([line("1", None, None, None, "5", "0", project=JOB), line("2", None, None, None, "5", "0", project=JOB2)])
        self.assertEqual(two["project_mode"], "multiple")
        self.assertEqual(self.fn([line("1", None, None, None, "5", "0")])["project_mode"], "none")
        self.assertEqual(self.fn([])["project_mode"], "")

    def test_invoice_counted_once(self):
        s = self.fn([line("9", date(2026, 8, 1), date(2026, 8, 31), date(2026, 9, 3), "1000", "400", project=JOB),
                     line("9", date(2026, 8, 1), date(2026, 8, 31), date(2026, 9, 3), "1000", "400", project=JOB)])
        self.assertEqual((s["inv_amt"], s["open"]), (Decimal("1000"), Decimal("400")))


if __name__ == "__main__":
    unittest.main()
