"""Unit tests for the bank statement parser and the reconciliation matcher (no database, no SL)."""

import os
import unittest
from datetime import date

import django  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.analytics.bank_reconciliation import finalize_bridge, run_matching, sl_ref_candidates, subset_sum  # noqa: E402
from apps.ingestion.bank_statements import bucket_for, integrity_check, parse_statement_lines  # noqa: E402


def L(text, amount_x1=None):
    """Positional line: words get increasing x; the last money token is placed at amount_x1."""
    words, x = [], 20.0
    toks = text.split()
    for i, t in enumerate(toks):
        w = {"text": t, "x0": x, "x1": x + 6 * len(t)}
        if amount_x1 is not None and i == len(toks) - 1:
            w = {"text": t, "x0": amount_x1 - 6 * len(t), "x1": amount_x1}
        words.append(w)
        x += 6 * len(t) + 6
    return {"text": text, "words": words}


def statement_lines():
    hdr = [L("BMO Bank N.A."), L("ACCOUNT NUMBER: 000-000-000-0"), L("STATEMENT PERIOD"), L("07/01/26 TO 07/31/26"),
           L("PACE SYSTEMS INC"), L("OPERATING ACCOUNT"), L("YOUR PREVIOUS BALANCE WAS 1,000.00"),
           L("3 DEPOSITS 1,300.00"), L("5 WITHDRAWALS 1,150.00"), L("YOUR ENDING BALANCE WAS 1,150.00"),
           {"text": "DATE WITHDRAWALS DEPOSITS", "words": [{"text": "DATE", "x0": 20, "x1": 40}, {"text": "WITHDRAWALS", "x0": 400, "x1": 460}, {"text": "DEPOSITS", "x0": 540, "x1": 590}]}]
    tx = [L("JUL 02 CCD CUSTOMER A PAYMENT 500.00", 590), L("JUL 03 Reve Check# 62001 100.00", 590), L("JUL 06 REMOTE DEPOSIT 700.00", 590),
          L("JUL 07 CCD 92358 PACE SYSTE DIR DEP 92358 400.00", 450), L("JUL 08 PPD PACE SYSTEMS INC ACH OFFSET -SETT-IBANK 300.00", 450),
          L("JUL 09 MAX BALANCE TRANSFER TO 9902800000STL00200105 250.00", 450)]
    chk = [L("THE FOLLOWING CHECKS ARE INCLUDED IN THIS STATEMENT"), L("62001 100.00 07/02 62002* 100.00 07/10"), L("SUBTOTAL 200.00"),
           L("CLOSING DAILY BALANCES AND DEBIT TOTALS"), L("JUL 17 .00 7", 450)]  # daily-balance table must be ignored
    return hdr + tx + chk


class ParserTests(unittest.TestCase):
    def test_parse_and_integrity(self):
        p = parse_statement_lines(statement_lines())
        self.assertEqual((p["period_start"], p["period_end"]), (date(2026, 7, 1), date(2026, 7, 31)))
        self.assertEqual(p["gl_account"], "10250")
        self.assertEqual(p["header"]["title"], "OPERATING ACCOUNT")
        self.assertEqual(p["totals"]["dep_n"], 3)          # 2 deposits + the check reversal credit
        self.assertEqual(p["totals"]["wd_n"], 5)           # 2 checks + payroll + ACH settlement + sweep
        self.assertEqual(p["totals"]["chk_n"], 2)
        self.assertEqual(p["integrity"], [])
        rev = [t for t in p["tx"] if t["kind"] == "check_reversal"][0]
        self.assertEqual(rev["check_number"], "62001")
        self.assertEqual([c["num"] for c in p["checks"]], ["62001", "62002"])
        self.assertTrue(p["checks"][1]["gap"])

    def test_integrity_catches_bad_totals(self):
        p = parse_statement_lines(statement_lines())
        p["summary"]["ending_balance"] = 999.0
        self.assertTrue(any("ending" in x for x in integrity_check(p)))

    def test_buckets(self):
        self.assertEqual(bucket_for("wd", "MAX BALANCE TRANSFER TO 99028"), "sweep")
        self.assertEqual(bucket_for("wd", "CCD 92358 PACE SYSTE DIR DEP"), "payroll")
        self.assertEqual(bucket_for("wd", "PPD PACE SYSTEMS INC ACH OFFSET -SETT-IBANK"), "ach_batch")
        self.assertEqual(bucket_for("dep", "TRANSFER FROM 9902800000STL0020010533"), "loan_draw")
        self.assertEqual(bucket_for("dep", "REMOTE DEPOSIT"), "deposit")
        self.assertEqual(bucket_for("wd", "ACCT ANALYSIS SERV CHG"), "fee")


class HelperTests(unittest.TestCase):
    def test_check_number_candidates(self):
        self.assertIn("162192", sl_ref_candidates("62192"))
        self.assertIn("062192", sl_ref_candidates("62192"))
        self.assertEqual(sl_ref_candidates("162192")[0], "162192")

    def test_subset_sum(self):
        items = [{"amt": 384167.0, "k": "a"}, {"amt": 32130.0, "k": "b"}, {"amt": 6896.05, "k": "c"}, {"amt": 12.0, "k": "d"}]
        hit = subset_sum(items, 39106305)  # 384167.00 + 6896.05 in cents
        self.assertEqual(sorted(x["k"] for x in hit), ["a", "c"])
        self.assertIsNone(subset_sum(items, 1))


class MatcherTests(unittest.TestCase):
    """A tiny July: 2 checks (one bank-reversed and reissued), an ACH settlement of 2 EFTs, a lump
    deposit of 2 AR receipts, payroll, a sweep, an outstanding check and a payroll entry dated August."""

    def setUp(self):
        P0, P1 = date(2026, 7, 1), date(2026, 7, 31)
        self.P0, self.P1 = P0, P1
        self.lines = [
            {"id": 1, "line_no": 1, "date": date(2026, 7, 2), "kind": "check", "bucket": "check", "desc": "CHECK 62001", "amt": 100.0, "check_number": "62001"},
            {"id": 2, "line_no": 2, "date": date(2026, 7, 3), "kind": "check_reversal", "bucket": "check_reversal", "desc": "Reve Check# 62001", "amt": 100.0, "check_number": "62001"},
            {"id": 3, "line_no": 3, "date": date(2026, 7, 10), "kind": "check", "bucket": "check", "desc": "CHECK 62002", "amt": 100.0, "check_number": "62002"},
            {"id": 4, "line_no": 4, "date": date(2026, 7, 2), "kind": "dep", "bucket": "ach_in", "desc": "CCD CUSTOMER A PAYMENT", "amt": 500.0, "check_number": ""},
            {"id": 5, "line_no": 5, "date": date(2026, 7, 6), "kind": "dep", "bucket": "deposit", "desc": "REMOTE DEPOSIT", "amt": 700.0, "check_number": ""},
            {"id": 6, "line_no": 6, "date": date(2026, 7, 7), "kind": "wd", "bucket": "payroll", "desc": "CCD 92358 PACE SYSTE DIR DEP", "amt": 400.0, "check_number": ""},
            {"id": 7, "line_no": 7, "date": date(2026, 7, 8), "kind": "wd", "bucket": "ach_batch", "desc": "PPD PACE SYSTEMS INC ACH OFFSET -SETT-IBANK", "amt": 300.0, "check_number": ""},
            {"id": 8, "line_no": 8, "date": date(2026, 7, 9), "kind": "wd", "bucket": "sweep", "desc": "MAX BALANCE TRANSFER TO LOAN", "amt": 250.0, "check_number": ""},
        ]
        self.gl = [
            {"dt": date(2026, 7, 1), "per": "202607", "m": "AP", "bat": "1", "ref": "162001", "descr": "VENDOR A", "dr": 0, "cr": 100.0, "u": "AP"},
            {"dt": date(2026, 7, 7), "per": "202607", "m": "AP", "bat": "2", "ref": "162001", "descr": "VENDOR A void", "dr": 100.0, "cr": 0, "u": "AP"},
            {"dt": date(2026, 7, 7), "per": "202607", "m": "AP", "bat": "2", "ref": "162002", "descr": "VENDOR A reissue", "dr": 0, "cr": 100.0, "u": "AP"},
            {"dt": date(2026, 7, 2), "per": "202607", "m": "AR", "bat": "10", "ref": "P1", "descr": "CUSTOMER A", "dr": 500.0, "cr": 0, "u": "AR"},
            {"dt": date(2026, 7, 6), "per": "202607", "m": "AR", "bat": "11", "ref": "P2", "descr": "CUSTOMER B", "dr": 300.0, "cr": 0, "u": "AR"},
            {"dt": date(2026, 7, 6), "per": "202607", "m": "AR", "bat": "11", "ref": "P3", "descr": "CUSTOMER C", "dr": 400.0, "cr": 0, "u": "AR"},
            {"dt": date(2026, 7, 7), "per": "202607", "m": "GL", "bat": "20", "ref": "", "descr": "PR-REG CHECK NET-07/07/26", "dr": 0, "cr": 400.0, "u": "PR"},
            {"dt": date(2026, 8, 4), "per": "202607", "m": "GL", "bat": "21", "ref": "", "descr": "PR-REG CHECK NET-08/04/26", "dr": 0, "cr": 150.0, "u": "PR"},
            {"dt": date(2026, 7, 8), "per": "202607", "m": "AP", "bat": "3", "ref": "013001", "descr": "EFT VENDOR X", "dr": 0, "cr": 200.0, "u": "AP"},
            {"dt": date(2026, 7, 8), "per": "202607", "m": "AP", "bat": "3", "ref": "013002", "descr": "EFT VENDOR Y", "dr": 0, "cr": 100.0, "u": "AP"},
            {"dt": date(2026, 7, 9), "per": "202607", "m": "GL", "bat": "22", "ref": "", "descr": "COMM LOAN PAYMENT", "dr": 0, "cr": 250.0, "u": "GL"},
            {"dt": date(2026, 7, 30), "per": "202607", "m": "AP", "bat": "4", "ref": "162003", "descr": "VENDOR B", "dr": 0, "cr": 75.0, "u": "AP"},
        ]
        self.checks = [
            {"ref": "162001", "doc_type": "CK", "dt": date(2026, 7, 1), "vendor_id": "A", "payee": "VENDOR A", "amt": 100.0, "cleared": None},
            {"ref": "162001", "doc_type": "VC", "dt": date(2026, 7, 7), "vendor_id": "A", "payee": "VENDOR A", "amt": 100.0, "cleared": None},
            {"ref": "162002", "doc_type": "CK", "dt": date(2026, 7, 7), "vendor_id": "A", "payee": "VENDOR A", "amt": 100.0, "cleared": date(2026, 7, 10)},
            {"ref": "162003", "doc_type": "CK", "dt": date(2026, 7, 30), "vendor_id": "B", "payee": "VENDOR B", "amt": 75.0, "cleared": None},
            {"ref": "013001", "doc_type": "CK", "dt": date(2026, 7, 8), "vendor_id": "X", "payee": "EFT VENDOR X", "amt": 200.0, "cleared": None},
            {"ref": "013002", "doc_type": "CK", "dt": date(2026, 7, 8), "vendor_id": "Y", "payee": "EFT VENDOR Y", "amt": 100.0, "cleared": None},
        ]
        # book: start 1,000; July GL debits 500+300+400 (AR) + 100 (void) = 1,300; credits 100+100+400+150+200+100+250+75 = 1,375 -> end 925
        self.book_prev, self.book_end = 1000.0, 925.0
        # bank: start 1,000 + deposits (500+700+100 reversal) - withdrawals (100+100+400+300+250) = 1,150
        self.bank_end = 1150.0

    def test_full_match_and_bridge(self):
        result, lm = run_matching(self.P0, self.P1, self.lines, self.gl, self.checks, self.book_prev, self.book_end)
        finalize_bridge(result, self.bank_end)
        self.assertEqual(result["checks"]["matched"], 2)
        self.assertEqual(result["checks"]["reversed"], ["62001"])
        self.assertEqual(lm[7]["match_kind"], "ACH settlement = 2 SL EFT payments")
        self.assertTrue(lm[5]["match_kind"].startswith("deposit = 2 AR receipts"))
        self.assertEqual(lm[8]["match_kind"], "1:1")
        self.assertEqual(result["bank_only"], [])
        self.assertEqual([o["ref"] for o in result["outstanding"]["items"]], ["162003"])
        self.assertEqual(result["payroll"]["gl_dated_after_period"], 150.0)
        # bridge: bank 1,150 - 75 outstanding - 150 payroll dated Aug but posted to July = 925 = book (void + original check net to zero)
        self.assertAlmostEqual(result["adjusted_bank"], 925.0, places=2)
        self.assertAlmostEqual(result["residual"], 0.0, places=2)
        self.assertEqual(result["flags"][0]["code"], "residual")
        self.assertEqual(result["flags"][0]["level"], "low")
        self.assertTrue(any(f["code"] == "posted_early" for f in result["flags"]))
        self.assertTrue(any(f["code"] == "bank_reversed_checks" for f in result["flags"]))

    def test_book_error_shows_in_residual(self):
        result, _ = run_matching(self.P0, self.P1, self.lines, self.gl, self.checks, self.book_prev, 825.0)
        finalize_bridge(result, self.bank_end)
        self.assertAlmostEqual(result["residual"], 100.0, places=2)
        self.assertEqual(result["flags"][0]["level"], "moderate")

    def test_unknown_check_is_critical(self):
        lines = self.lines + [{"id": 9, "line_no": 9, "date": date(2026, 7, 20), "kind": "check", "bucket": "check", "desc": "CHECK 62099", "amt": 42.0, "check_number": "62099"}]
        result, lm = run_matching(self.P0, self.P1, lines, self.gl, self.checks, self.book_prev, self.book_end)
        self.assertTrue(any(f["code"] == "check_not_in_sl" and f["level"] == "critical" for f in result["flags"]))
        self.assertEqual(lm[9]["match_kind"], "")


if __name__ == "__main__":
    unittest.main()
