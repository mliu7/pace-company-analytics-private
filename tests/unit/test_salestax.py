"""Sales Tax page — the posting classifier (apps/analytics/salestax_parse.py) against the ledger's real vocabulary, and the
state rules reference's integrity. No DB."""
import unittest
from datetime import date


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        from apps.analytics import salestax_parse as sp
        self.c, self.sp = sp.classify, sp

    def test_collected_state_from_tax_id_first(self):
        r = self.c("AR", "AZ GILA RIVER TRIBAL AREA SP", 0, 12616, date(2026, 3, 1), tax_id="AZ85226", ship_state="AZ")
        self.assertEqual((r["kind"], r["state"], r["state_source"]), ("collected", "AZ", "tax id"))
        r = self.c("AR", "ILLINOIS SALES TAX", 0, 14.88, date(2026, 9, 2), tax_id="TAX2")
        self.assertEqual((r["state"], r["state_source"]), ("IL", "tax id (Illinois flat)"))
        r = self.c("AR", "WA OLYMPIA", 0, 3135, date(2025, 5, 1))
        self.assertEqual((r["state"], r["state_source"]), ("WA", "description"))
        r = self.c("AR", "ILLINOIS SALES TAX", 0, 500, date(2019, 5, 1))
        self.assertEqual((r["state"], r["state_source"]), ("IL", "description"))

    def test_collected_fallbacks_and_hand_keyed(self):
        r = self.c("AR", "BEC012 - ADJ SALES TAX 70505", 8, 0, date(2026, 4, 1), ship_state="TN")
        self.assertEqual((r["kind"], r["state"], r["state_source"], r["note"]), ("collected", "TN", "ship-to", "hand-keyed on the invoice"))
        r = self.c("AR", "ACO001-PAYROLL DED-SALES TAX", 10, 0, date(2019, 4, 1), cust_state="IL")
        self.assertEqual((r["state"], r["state_source"], r["note"]), ("IL", "customer", "hand-keyed on the invoice"))
        r = self.c("AR", "JLL001 - CANCEL TAX - 79899", 22, 0, date(2026, 4, 1))
        self.assertEqual((r["state"], r["state_source"]), ("", ""))
        self.assertEqual(self.c("AR", "PROJECT IS EXEMPT", 0, 0, date(2026, 9, 4))["kind"], "zero")

    def test_remittances(self):
        r = self.c("GL", "TN SALES TAX MAR", 6762.08, 0, date(2026, 4, 20))
        self.assertEqual((r["kind"], r["state"], r["period_covered"], r["period_inferred"]), ("remitted", "TN", "202603", False))
        r = self.c("GL", "IL DEPT OF REV SALES TAX DEC", 2286, 0, date(2024, 1, 23))
        self.assertEqual((r["state"], r["period_covered"]), ("IL", "202312"), "December paid in January is the prior year")
        r = self.c("GL", "MARYLAND SALES TAX MAY", 4834, 0, date(2025, 6, 24))
        self.assertEqual((r["kind"], r["state"], r["period_covered"]), ("remitted", "MD", "202505"))
        r = self.c("GL", "LA SALES TAX", 4554.1, 0, date(2026, 8, 12))
        self.assertEqual((r["state"], r["period_covered"], r["period_inferred"]), ("LA", "202607", True))
        r = self.c("GL", "NJ SALES TAX 2ND QRT", 943, 0, date(2025, 12, 17))
        self.assertEqual(r["period_covered"], "202506")
        r = self.c("GL", "SC SALES TAX 2024", 1540, 0, date(2025, 4, 22))
        self.assertEqual(r["period_covered"], "202412")
        r = self.c("GL", "RECORD SALES TAX PAYMENT", 7793, 0, date(2013, 4, 5))
        self.assertEqual((r["kind"], r["state"], r["state_source"]), ("remitted", "IL", "assumed: Illinois-only"))
        r = self.c("GL", "IL SALES TAX PAYMNET", 20000, 0, date(2021, 10, 20))
        self.assertEqual((r["kind"], r["state"]), ("remitted", "IL"))
        r = self.c("GL", "REV NY SALES TAX", 0, 138, date(2026, 1, 16))
        self.assertEqual((r["kind"], r["state"], r["note"]), ("remitted", "NY", "reversal of a remittance"))
        r = self.c("GL", "COR OH SALES TAX NOV", 1289, 0, date(2024, 12, 26))
        self.assertEqual((r["kind"], r["state"], r["period_covered"], r["note"]), ("remitted", "OH", "202411", "correction of a remittance"))
        r = self.c("AP", "COM015 COMPTROLLER OF MARYLAND", 19388, 0, date(2025, 4, 8))
        self.assertEqual((r["kind"], r["state"], r["state_source"]), ("remitted", "MD", "vendor"))
        r = self.c("AP", "ILL001 2013 TAX AUDIT", 87195, 0, date(2014, 1, 15))
        self.assertEqual((r["kind"], r["state"], r["note"]), ("remitted", "IL", "audit assessment"))
        self.assertEqual(self.c("AP", "EXEMPT", 0, 0, date(2026, 8, 21))["kind"], "zero")

    def test_fees_adjustments_reclasses(self):
        r = self.c("GL", "TN REVENU SALTX RETURN FEE", 2269, 0, date(2023, 9, 29))
        self.assertEqual((r["kind"], r["state"]), ("fee", "TN"))
        r = self.c("GL", "Accrual Adj", 270193.45, 0, date(2024, 12, 31))
        self.assertEqual((r["kind"], r["note"]), ("adjustment", "accrual"))
        r = self.c("GL", "LOUISIANA 45330 SALES TAX", 0, 687, date(2022, 12, 31))
        self.assertEqual((r["kind"], r["state"]), ("adjustment", "LA"))
        r = self.c("GL", "RECLASS MISSED SALES TAX SEPT", 0, 13352, date(2022, 11, 1))
        self.assertEqual(r["kind"], "reclass?")
        self.assertEqual(self.c("GL", "PROJECT SUBACCT RECLASS MAY", 2583.6, 0, date(2026, 7, 31))["kind"], "reclass")
        rows = [
            {"batch_nbr": "A", "kind": "reclass?", "dr_amt": 100, "cr_amt": 0, "state": "", "note": ""},
            {"batch_nbr": "A", "kind": "reclass?", "dr_amt": 0, "cr_amt": 100, "state": "", "note": ""},
            {"batch_nbr": "B", "kind": "reclass?", "dr_amt": 0, "cr_amt": 13352, "state": "", "note": "", "period_covered": "", "period_inferred": False},
            {"batch_nbr": "C", "kind": "reclass?", "dr_amt": 3107, "cr_amt": 0, "state": "IL", "note": "payment recorded as a reclass",
             "period_covered": "202204", "period_inferred": False},
        ]
        out = self.sp.settle_reclasses(rows)
        self.assertEqual([r["kind"] for r in out], ["reclass", "reclass", "adjustment", "remitted"])
        self.assertEqual(out[2]["note"], "reclass that did not net to zero")
        self.assertEqual(out[3]["period_covered"], "202204")

    def test_state_and_period_helpers(self):
        self.assertEqual(self.sp.state_from_text("IN00045844 MD SALES TAX RECLAS"), "MD")
        self.assertEqual(self.sp.state_from_text("S.CAROLINA SALES TAX"), "SC")
        self.assertEqual(self.sp.state_from_text("NORTH CAROLINA SALES TAX"), "NC")
        self.assertIsNone(self.sp.state_from_text("PROJECT SUBACCOUNT RECLASS"))
        self.assertEqual(self.sp.period_from_text("WA SALES TAX MARCH", date(2026, 5, 4)), ("202603", False))
        self.assertEqual(self.sp.period_from_text("IL SALES TAX", date(2026, 1, 20)), ("202512", True))


class RulesTests(unittest.TestCase):
    def test_every_state_present_and_complete(self):
        from apps.analytics.salestax_rules import STATES, NO_SALES_TAX
        from apps.analytics.salestax_parse import STATE_CODES
        self.assertEqual(set(STATES), STATE_CODES)
        for code, s in STATES.items():
            for k in ("name", "base_rate", "local", "nexus_sales", "nexus_txns", "sourcing", "install_labor", "contractor", "due", "discount", "url"):
                self.assertIn(k, s, "%s lacks %s" % (code, k))
            self.assertTrue(s["url"].startswith("https://"), code)
        self.assertEqual(NO_SALES_TAX, {"AK", "DE", "MT", "NH", "OR"})
        self.assertEqual(STATES["IL"]["nexus_txns"], None, "Illinois dropped its 200-transaction test on 2026-01-01")
        self.assertEqual(STATES["CA"]["nexus_sales"], 500000)
        self.assertEqual(STATES["NY"]["nexus_and"], True)


if __name__ == "__main__":
    unittest.main()
