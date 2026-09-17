"""Unit tests for the customer page's payment history (apps.analytics.customer_payments): days to pay, timing buckets,
retainage detection, reversals, dollar-weighted summaries and the payer grade. No database."""

import os
import unittest
from datetime import date
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.analytics import customer_payments as CP  # noqa: E402

D = Decimal
TODAY = date(2026, 9, 8)


def app(id_, pref, inv, applied, appl_date, **kw):
    r = {"id": id_, "payment_ref": pref, "customer_id_raw": "BUL001", "date_appl": appl_date, "applied": D(applied), "discount": D(0),
         "batch_nbr": "", "invoice_ref": inv, "invoice_type": "IN", "invoice_date": None, "due_date": None, "terms": "", "invoice_amt": None,
         "invoice_balance": None, "project_id": None}
    r.update(kw)
    return r


def inv(ref, amount, balance, doc_date, due, terms="30", **kw):
    r = {"ref_nbr": ref, "doc_type": "IN", "doc_date": doc_date, "due_date": due, "terms": terms, "amount": D(amount), "balance": D(balance)}
    r.update(kw)
    return r


class HelperTests(unittest.TestCase):
    def test_terms_days(self):
        self.assertEqual(CP.terms_days("30"), 30)
        self.assertEqual(CP.terms_days("45 "), 45)
        self.assertEqual(CP.terms_days("DU"), 1)
        self.assertIsNone(CP.terms_days("PW"))
        self.assertIsNone(CP.terms_days(""))

    def test_payment_method(self):
        self.assertEqual(CP.payment_method("WT08262026"), "wire")
        self.assertEqual(CP.payment_method("CC1234"), "card")
        self.assertEqual(CP.payment_method("071324"), "check")
        self.assertEqual(CP.payment_method("ACH-1"), "other")

    def test_retention_share(self):
        self.assertEqual(CP.retention_share(D("3649.38"), D("36493.82")), D("0.10"))    # BUL001 217249
        self.assertEqual(CP.retention_share(D("500"), D("10000")), D("0.05"))
        self.assertIsNone(CP.retention_share(D("2500"), D("10000")))
        self.assertIsNone(CP.retention_share(D("0"), D("10000")))
        self.assertIsNone(CP.retention_share(D("100"), D("0")))

    def test_timing_bucket(self):
        self.assertEqual(CP.timing_bucket(-3), "on_time")
        self.assertEqual(CP.timing_bucket(0), "on_time")
        self.assertEqual(CP.timing_bucket(1), "late30")
        self.assertEqual(CP.timing_bucket(30), "late30")
        self.assertEqual(CP.timing_bucket(31), "late60")
        self.assertEqual(CP.timing_bucket(90), "late90")
        self.assertEqual(CP.timing_bucket(91), "late90p")
        self.assertIsNone(CP.timing_bucket(None))

    def test_weighted_days_and_median(self):
        self.assertEqual(CP.weighted_days([(10, D(100)), (40, D(300))]), D("32.5"))
        self.assertIsNone(CP.weighted_days([(10, D(0)), (None, D(5))]))
        self.assertEqual(CP.median([5, 1, 9]), 5)
        self.assertEqual(CP.median([1, 2, 3, 4]), 2.5)
        self.assertIsNone(CP.median([]))

    def test_grade(self):
        self.assertEqual(CP.grade(D("38"), 30)["key"], "prompt")
        self.assertEqual(CP.grade(D("41"), 30)["key"], "fair")
        self.assertEqual(CP.grade(D("70"), 30)["key"], "slow")
        self.assertEqual(CP.grade(D("95"), 30)["key"], "poor")
        self.assertEqual(CP.grade(D("35"), None)["over"], 5)      # unknown terms default to 30
        self.assertIsNone(CP.grade(None, 30))


class EnrichTests(unittest.TestCase):
    """BUL001-shaped history: a progress bill paid to 10 % retention, then the retention released; a payment covering
    two invoices; a reversal."""

    def setUp(self):
        self.invoices = {
            ("217249", "IN"): inv("217249", "36493.82", "3649.38", date(2025, 10, 23), date(2025, 11, 22), cpn="254627", disp="254627", title="NORTHBROOK PARK DIST"),
            ("213294", "IN"): inv("213294", "13194.93", "0", date(2023, 1, 31), date(2023, 3, 2)),
            ("IN00045563", "IN"): inv("IN00045563", "27261.61", "0", date(2022, 9, 30), date(2022, 10, 30), order_nbr="ORD0031162"),
            ("500", "IN"): inv("500", "1000", "0", date(2024, 1, 1), date(2024, 1, 31)),
            ("600", "IN"): inv("600", "10000", "0", date(2024, 3, 1), date(2024, 3, 31)),
        }
        self.payments = {
            ("70256", "BUL001"): {"doc_date": date(2026, 2, 9), "orig_amt": D("32844.44"), "balance": D(0), "batch_nbr": "031666"},
            ("055938", "BUL001"): {"doc_date": date(2023, 4, 5), "orig_amt": D("62215.03"), "balance": D(0), "batch_nbr": "023546"},
            ("999", "BUL001"): {"doc_date": date(2035, 9, 3), "orig_amt": D("3649.38"), "balance": D(0), "batch_nbr": "x"},   # fat-fingered date
            ("CC77", "BUL001"): {"doc_date": date(2024, 1, 20), "orig_amt": D("1000"), "balance": D(0), "batch_nbr": ""},
            ("700", "BUL001"): {"doc_date": date(2024, 4, 1), "orig_amt": D("10000"), "balance": D(0), "batch_nbr": ""},
        }
        self.apps = [
            app(1, "70256", "217249", "32844.44", date(2026, 2, 9)),
            app(2, "055938", "213294", "13194.93", date(2023, 4, 5)),
            app(3, "055938", "IN00045563", "27261.61", date(2023, 4, 5)),
            app(4, "999", "217249", "3649.38", date(2026, 9, 3)),         # retention release; payment dated 2035 -> falls back to date_appl
            app(5, "CC77", "500", "1000", date(2024, 1, 20)),
            app(6, "700", "600", "10000", date(2024, 4, 1)),
            app(7, "700", "600", "-10000", date(2024, 4, 15)),           # bounced -> reversal
        ]
        self.rows = CP.enrich(self.apps, self.invoices, self.payments, TODAY)
        self.by_id = {r["id"]: r for r in self.rows}

    def test_days_and_vs_due(self):
        r = self.by_id[1]
        self.assertEqual(r["pay_date"], date(2026, 2, 9))
        self.assertEqual(r["days"], 109)
        self.assertEqual(r["vs_due"], 79)
        self.assertEqual(r["timing"], "late90")
        self.assertEqual(r["method"], "check")
        r2 = self.by_id[2]
        self.assertEqual(r2["days"], 64)
        self.assertEqual(r2["vs_due"], 34)
        self.assertEqual(r2["timing"], "late60")

    def test_retention_detected_and_release_excluded(self):
        first, release = self.by_id[1], self.by_id[4]
        self.assertEqual(first["open_after"], D("3649.38"))
        self.assertEqual(first["retention"], D("0.10"))
        self.assertFalse(first["release"])
        self.assertTrue(first["counts"])
        self.assertEqual(release["seq"], 2)
        self.assertEqual(release["n_apps"], 2)
        self.assertEqual(release["open_after"], D("0.00"))
        self.assertTrue(release["release"])
        self.assertIsNone(release["timing"])
        self.assertFalse(release["counts"])
        self.assertEqual(release["pay_date"], date(2026, 9, 3))     # the 2035 document date was ignored

    def test_reversal(self):
        paid, rev = self.by_id[6], self.by_id[7]
        self.assertFalse(paid["reversal"])
        self.assertEqual(paid["open_after"], D("0.00"))
        self.assertTrue(rev["reversal"])
        self.assertEqual(rev["open_after"], D("10000.00"))
        self.assertIsNone(rev["timing"])
        self.assertFalse(rev["counts"])

    def test_default_order_is_newest_payment_first(self):
        self.assertEqual([r["id"] for r in self.rows][:2], [4, 1])
        # the two invoices of payment 055938 sit together, larger application first
        ids = [r["id"] for r in self.rows]
        self.assertEqual(ids[ids.index(3) + 1], 2)

    def test_summary(self):
        s = CP.summarize(self.rows, self.invoices, TODAY)
        self.assertEqual(s["n_apps"], 7)
        self.assertEqual(s["n_payments"], 5)
        self.assertEqual(s["collected"], D("32844.44") + D("13194.93") + D("27261.61") + D("3649.38") + D("1000") + D("10000"))
        self.assertEqual(s["reversed"], D("10000"))
        self.assertEqual(s["terms"], "30")
        self.assertEqual(s["terms_d"], 30)
        # weighted days over the counting rows: 1 (109d), 2 (64d), 3 (187d), 5 (19d), 6 (31d)
        num = 109 * D("32844.44") + 64 * D("13194.93") + 187 * D("27261.61") + 19 * D("1000") + 31 * D("10000")
        den = D("32844.44") + D("13194.93") + D("27261.61") + D("1000") + D("10000")
        self.assertEqual(s["days"].quantize(D("0.01")), (num / den).quantize(D("0.01")))
        # only the card payment beat its due date; invoice 600 was paid the day after it was due
        self.assertEqual(s["on_time_share"].quantize(D("0.0001")), (D("1000") / den).quantize(D("0.0001")))
        self.assertEqual(s["late_n"], 4)
        self.assertEqual(s["max_late"], 157)
        self.assertEqual(s["max_late_row"]["invoice_ref"], "IN00045563")
        self.assertEqual(s["invoices_n"], 5)
        self.assertEqual(s["open_n"], 1)
        self.assertEqual(s["retention_open"], D("3649.38"))
        self.assertTrue(s["holds_retention"])
        self.assertEqual(s["grade"]["key"], "poor")
        years = {y["year"]: y for y in s["by_year"]}
        self.assertEqual(set(years), {2022, 2023, 2024, 2025, 2026})
        self.assertEqual(years[2023]["collected"], D("13194.93") + D("27261.61"))
        self.assertEqual(years[2024]["collected"], D("11000"))      # the reversal never counts as collected
        self.assertEqual(years[2024]["on_time_share"].quantize(D("0.0001")), (D("1000") / D("11000")).quantize(D("0.0001")))
        self.assertEqual(years[2026]["collected"], D("32844.44") + D("3649.38"))
        self.assertEqual(years[2026]["n"], 2)
        self.assertEqual(years[2026]["days"], D(109))              # the release is not in pays-in

    def test_facets(self):
        f = CP.facets(self.rows)
        self.assertEqual([y["v"] for y in f["year"]], [2026, 2024, 2023])
        labels = {t["label"] for t in f["timing"]}
        self.assertIn("Retention release", labels)
        self.assertIn("Reversal", labels)
        self.assertIn("Within terms", labels)
        self.assertEqual({m["v"] for m in f["method"]}, {"check", "card"})
        self.assertEqual(f["proj"][0]["v"], "254627")


if __name__ == "__main__":
    unittest.main()
