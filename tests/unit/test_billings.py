"""Billings page — the pure rules (apps/finance/billing_rules.py) and the engine's pure helpers (analytics/billings.py):
line classification and signing, the division rule, period resolution, login -> employee matching. No DB."""
import unittest
from datetime import date


class BillingRulesTests(unittest.TestCase):
    def setUp(self):
        from apps.finance import billing_rules as br
        self.br = br

    def test_line_kinds(self):
        c = self.br.classify_ar_line
        self.assertEqual(c("40000", "3I"), "rev")
        self.assertEqual(c("40001", "3I"), "rev")
        self.assertEqual(c("40300", "3I"), "rev")
        self.assertEqual(c("20500", "2L"), "tax")
        self.assertEqual(c("20500", None), "tax")
        self.assertEqual(c("24000", "2L"), "dep")
        self.assertEqual(c("21000", "2L"), "dep")
        self.assertEqual(c("61200", "4E"), "oth")
        self.assertEqual(c("50730", "4E"), "oth")
        self.assertEqual(c("40000", None), "rev", "unknown account type: a 4xxxx account counts as income")
        self.assertEqual(c("12000", None), "oth")

    def test_signing_and_rollup(self):
        br = self.br
        self.assertEqual(br.ar_line_sign("IN"), 1)
        self.assertEqual(br.ar_line_sign("DM"), 1)
        self.assertEqual(br.ar_line_sign("CM"), -1)
        self.assertEqual(br.ar_line_sign("cm "), -1)
        lines = [("rev", 1000, "0700"), ("rev", 250, "0100"), ("tax", 87.5, "0700"), ("dep", 500, "0700"), ("oth", 12, "0000")]
        r = br.rollup_lines(lines, "IN")
        self.assertEqual((r["revenue"], r["tax"], r["deposits"], r["other"], r["top_sub"]), (1250, 87.5, 500, 12, "0700"))
        r = br.rollup_lines(lines, "CM")   # SL stores CM lines positive: everything flips
        self.assertEqual((r["revenue"], r["tax"], r["deposits"], r["other"], r["top_sub"]), (-1250, -87.5, -500, -12, "0700"))
        self.assertEqual(br.rollup_lines([], "IN")["top_sub"], "")

    def test_division_rule(self):
        br = self.br
        self.assertEqual(br.invoice_division("070", "SO2", "0100"), "070", "the job's division wins")
        self.assertEqual(br.invoice_division("", "SO1", "0700"), "010", "job-less SO1 = hardware = 010")
        self.assertEqual(br.invoice_division("", "", "0701"), "070")
        self.assertEqual(br.invoice_division("", "", ""), "")
        self.assertEqual(br.division_from_subaccount("ab"), "")
        self.assertTrue(br.is_hardware_order("SO1", False))
        self.assertFalse(br.is_hardware_order("SO1", True))
        self.assertFalse(br.is_hardware_order("SO2", False))

    def test_backdated_and_period(self):
        br = self.br
        self.assertEqual(br.backdated_days(date(2026, 9, 4), date(2026, 8, 31)), 4)
        self.assertEqual(br.backdated_days(date(2026, 9, 4), date(2026, 9, 4)), 0)
        self.assertEqual(br.backdated_days(date(2026, 9, 1), date(2026, 9, 4)), 0, "keyed ahead of its date is not back-dated")
        self.assertEqual(br.backdated_days(None, date(2026, 9, 4)), 0)
        self.assertEqual(br.period_of(date(2026, 8, 31)), "202608")
        self.assertEqual(br.period_of(None), "")


class PeriodTests(unittest.TestCase):
    def setUp(self):
        from apps.analytics.billings import resolve_period, resolve_basis, period_options
        self.fn, self.basis, self.opts = resolve_period, resolve_basis, period_options
        self.today = date(2026, 9, 8)

    def test_default_is_month_to_date(self):
        for g in ({}, {"period": "junk"}, {"period": "2026-09"}, {"period": "2027-01"}, {"day": "nonsense"}):
            r = self.fn(g, self.today)
            self.assertEqual((r["kind"], r["start"], r["end"], r["months"], r["qs"]), ("mtd", date(2026, 9, 1), self.today, ["202609"], "period=2026-09"), g)
        self.assertEqual(self.fn({}, self.today)["prev"], "period=2026-08")
        self.assertIsNone(self.fn({}, self.today)["next"])

    def test_day_wins(self):
        r = self.fn({"day": "2026-09-04", "period": "2026-08"}, self.today)
        self.assertEqual((r["kind"], r["start"], r["end"], r["months"], r["qs"]), ("day", date(2026, 9, 4), date(2026, 9, 4), None, "day=2026-09-04"))
        self.assertEqual(r["label"], "Fri Sep 4, 2026")
        self.assertEqual(self.fn({"day": "2026-09-08"}, self.today)["next"], None)
        self.assertEqual(self.fn({"day": "2026-12-01"}, self.today)["kind"], "mtd", "a future day falls back")

    def test_range(self):
        r = self.fn({"from": "2026-08-20", "to": "2026-08-10"}, self.today)
        self.assertEqual((r["kind"], r["start"], r["end"], r["qs"]), ("range", date(2026, 8, 10), date(2026, 8, 20), "from=2026-08-10&to=2026-08-20"))
        self.assertEqual(r["prev"], "from=2026-07-30&to=2026-08-09")
        self.assertEqual(r["next"], "from=2026-08-21&to=2026-08-31")
        r = self.fn({"from": "2026-09-01", "to": "2026-12-31"}, self.today)
        self.assertEqual(r["end"], self.today, "clamped to today")
        self.assertIsNone(r["next"])
        r = self.fn({"after": "2026-09-04", "to": "2026-09-08"}, self.today)
        self.assertEqual((r["start"], r["end"]), (date(2026, 9, 5), date(2026, 9, 8)), "after= is an exclusive start (WIP live windows)")

    def test_month_year_live(self):
        r = self.fn({"period": "2026-08"}, self.today)
        self.assertEqual((r["kind"], r["label"], r["start"], r["end"], r["months"]), ("month", "August 2026", date(2026, 8, 1), date(2026, 8, 31), ["202608"]))
        self.assertEqual((r["prev"], r["next"]), ("period=2026-07", "period=2026-09"))
        self.assertEqual(self.fn({"period": "2026-1"}, self.today)["key"], "2026-01")
        self.assertEqual(self.fn({"period": "2024-06"}, self.today)["kind"], "mtd", "beyond the lines window")
        r = self.fn({"period": "2025"}, self.today)
        self.assertEqual((r["kind"], r["start"], r["end"], len(r["months"])), ("year", date(2025, 1, 1), date(2025, 12, 31), 12))
        r = self.fn({"period": "ytd"}, self.today)
        self.assertEqual((r["kind"], r["start"], r["months"][-1], r["qs"]), ("ytd", date(2026, 1, 1), "202609", "period=ytd"))
        r = self.fn({"period": "wtd"}, self.today)
        self.assertEqual((r["kind"], r["start"], r["end"], r["months"]), ("wtd", date(2026, 9, 7), self.today, None))
        r = self.fn({"period": "7d"}, self.today)
        self.assertEqual((r["kind"], r["start"], r["end"], r["qs"]), ("last", date(2026, 9, 2), self.today, "period=7d"))

    def test_basis(self):
        per_m, per_d = self.fn({"period": "2026-08"}, self.today), self.fn({"day": "2026-09-04"}, self.today)
        self.assertEqual(self.basis({}, per_m), "entered")
        self.assertEqual(self.basis({"basis": "posted"}, per_m), "posted")
        self.assertEqual(self.basis({"basis": "posted"}, per_d), "dated", "no fiscal period for a day: falls back to the invoice date")
        self.assertEqual(self.basis({"basis": "weird"}, per_d), "entered")

    def test_options(self):
        groups = dict(self.opts(self.today))
        self.assertEqual(groups["Live"][0], ("period=wtd", "Week to date"))
        self.assertEqual(groups["Month"][0], ("period=2026-08", "August 2026"))
        self.assertTrue(all(v.startswith("period=") for v, _ in groups["Month"]))
        self.assertGreaterEqual(len(groups["Month"]), 13)
        self.assertEqual(groups["Year"], [("period=2025", "2025")])


class LoginMatchTests(unittest.TestCase):
    def test_initial_plus_surname(self):
        from apps.analytics.billings import match_logins
        emps = [("BUR002", "Megan Thornfield", True), ("HUB002", "Stephanie Amberstone", True), ("HUB001", "Tom Amberstone", True), ("HUB003", "Thomas Amberstone", True),
                ("VIL002", "Martha Umberton-Valewood", True), ("CHA002", "Ulisses Umberton", False), ("THI001", "Lorena Trellis", True), ("THI002", "Brian Timberlake", False),
                ("PTT-882", "Michele Silverlake", True), ("MAG003", "Michele Silverlake", True), ("TAY001", "Nick Quillford", True), ("TAY002", "Dan Quillford", False),
                ("PLA002", "Tyler Poppyfield", False), ("X1", "Tina Amberstone", True)]
        m = match_logins(["MTHORNFIELD", "SAMBERSTONE", "MUMBERTON", "LTRELLIS", "MSILVERLAKE", "NQUILLFORD", "CPOPPYFIELD", "TAMBERSTONE", "", "AB", "SYSTEM"], emps)
        self.assertEqual(m["MTHORNFIELD"], ("Megan Thornfield", "BUR002"))
        self.assertEqual(m["SAMBERSTONE"], ("Stephanie Amberstone", "HUB002"))
        self.assertEqual(m["MUMBERTON"], ("Martha Umberton-Valewood", "VIL002"), "hyphenated surname matches on either part")
        self.assertEqual(m["LTRELLIS"], ("Lorena Trellis", "THI001"), "Timberlake is not Trellis")
        self.assertEqual(m["MSILVERLAKE"], ("Michele Silverlake", "MAG003"), "duplicate rows for one person: the non-PTT key wins")
        self.assertEqual(m["NQUILLFORD"], ("Nick Quillford", "TAY001"))
        self.assertNotIn("CPOPPYFIELD", m, "no employee with that initial: the login stands")
        self.assertNotIn("TAMBERSTONE", m, "Tom / Thomas / Tina Amberstone: ambiguous, the login stands")
        self.assertNotIn("SYSTEM", m)
        self.assertNotIn("", m)


if __name__ == "__main__":
    unittest.main()
