"""Unit tests for source-interpretation rules and the read-only guard (spec v3 §13.2).

Run:  .venv/bin/python -m unittest discover -s tests/unit -v
No database or source connection is touched.
"""

import os
import unittest
from datetime import date
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.core import rules  # noqa: E402
from apps.core.models import ProjectMode  # noqa: E402
from apps.ingestion.sources import guard  # noqa: E402


class IdentityTests(unittest.TestCase):
    def test_trim_and_upper(self):
        self.assertEqual(rules.canonical_project_number(" 265330 "), "265330")
        self.assertEqual(rules.canonical_project_number("269999sec"), "269999SEC")

    def test_suffix_never_stripped(self):
        a = rules.canonical_project_number("241517")
        b = rules.canonical_project_number("241517000000")
        self.assertNotEqual(a, b)
        self.assertEqual(b, "241517000000")

    def test_display_number_only_for_display(self):
        self.assertEqual(rules.display_number("241517000000"), "241517-000000")
        self.assertEqual(rules.display_number("241517"), "241517")
        self.assertEqual(rules.display_number("269999SEC"), "269999SEC")

    def test_rejects_placeholder(self):
        with self.assertRaises(ValueError):
            rules.canonical_project_number("0000")
        with self.assertRaises(ValueError):
            rules.canonical_project_number("   ")

    def test_numbering_style(self):
        self.assertEqual(rules.numbering_style("265330"), "SO3")
        self.assertEqual(rules.numbering_style("260120000000"), "SO2")
        self.assertEqual(rules.numbering_style("249999SESALE"), "internal")
        self.assertEqual(rules.numbering_style("260000000000"), "internal")
        self.assertEqual(rules.numbering_style("1159221628"), "legacy")

    def test_internal_and_template(self):
        self.assertTrue(rules.is_internal_bucket("249999SESALE"))
        self.assertTrue(rules.is_internal_bucket("248888SEWNTY"))
        self.assertTrue(rules.is_internal_bucket("260000", "G"))
        self.assertFalse(rules.is_internal_bucket("265330", "A"))
        self.assertTrue(rules.is_template_or_void("220331000000", "M"))


class ModeTests(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(rules.project_mode("265330", "TM TICKET-U OF C LABS", "A", "TM TICKET"), ProjectMode.TM_TICKET)
        self.assertEqual(rules.project_mode("231228", "TM SERVICE - CCC SECURITY ALL CAMPUSES", "I", ""), ProjectMode.TM_SERVICE)
        self.assertEqual(rules.project_mode("265329", "SA-DALEY COLLEGE 32 AV SUPPORT CREDITS", "A", ""), ProjectMode.SERVICE_AGREEMENT)
        self.assertEqual(rules.project_mode("254612", "SA - SENTINEL SECURITY ITSSMMM IN SCOPE", "A", ""), ProjectMode.SERVICE_AGREEMENT)
        self.assertEqual(rules.project_mode("239987", "CCC JOC KK2306", "I", ""), ProjectMode.JOC)
        self.assertEqual(rules.project_mode("248888SEWNTY", "2024 SECURITY WARRANTY SERVICE", "I", ""), ProjectMode.INTERNAL)
        self.assertEqual(rules.project_mode("249999SESALE", "2024 SECURITY SALE", "I", ""), ProjectMode.INTERNAL)
        self.assertEqual(rules.project_mode("260000000000", "2026 Template", "G", ""), ProjectMode.TEMPLATE)
        self.assertEqual(rules.project_mode("220031000000", "CANCELLED Sales Order ORD0029255", "M", ""), ProjectMode.CANCELED)
        self.assertEqual(rules.project_mode("231080", "CPS URBAN PREP BRONZEVILLE CAMERA PROJECT 2023", "I", "4111937"), ProjectMode.INSTALLATION)

    def test_quote_reference(self):
        self.assertEqual(rules.quote_reference("VIL HANOVER PARK-COUNCIL CHAMBERS BOARDROOM UPGRADE 610205"), "610205")
        self.assertEqual(rules.quote_reference("CPS SHOOP ADA DOOR REPAIR PROJECT", "SP#9936"), "SP#9936")
        self.assertEqual(rules.quote_reference("CPS SHOOP ADA DOOR REPAIR PROJECT"), "")


class DivisionTests(unittest.TestCase):
    def test_subaccounts(self):
        self.assertEqual(rules.division_for_subaccount("0700"), ("070", "Premise Security Systems"))
        self.assertEqual(rules.division_for_subaccount("0701")[0], "070")
        self.assertEqual(rules.division_for_subaccount("0800")[0], "080")
        self.assertEqual(rules.division_for_subaccount("")[0], "000")


class TransactionRuleTests(unittest.TestCase):
    def test_pay_period(self):
        check, start, end = rules.parse_pay_period("CK DT 7/26/2023  7/17/23 -7/23/23")
        self.assertEqual((check, start, end), (date(2023, 7, 26), date(2023, 7, 17), date(2023, 7, 23)))
        self.assertEqual(rules.parse_pay_period("ELE001 ELECTRICAL INSURANCE TR"), (None, None, None))

    def test_sub_tags(self):
        self.assertEqual(rules.transaction_sub_tag("BURDEN", "PA", "CHRG", "BOY001", "", ""), "payroll_tax")
        self.assertEqual(rules.transaction_sub_tag("BURDEN", "AP", "VO", "", "ELE001", "50710"), "union_fringe")
        self.assertEqual(rules.transaction_sub_tag("ODC", "AP", "VO", "", "V1", "50750"), "freight")
        self.assertEqual(rules.transaction_sub_tag("MATERIALS", "AP", "PO", "", "V1", "50700"), "po_receipt")
        self.assertEqual(rules.transaction_sub_tag("MATERIALS", "OM", "IN", "", "", "50700"), "sales_order_cogs")
        self.assertEqual(rules.transaction_sub_tag("REVENUE", "AR", "CM", "", "", "40000"), "credit_memo")
        self.assertEqual(rules.transaction_sub_tag("REVENUE", "AR", "IN", "", "", "20500"), "sales_tax_line")

    def test_names(self):
        self.assertEqual(rules.name_from_sl("WILLOWBY~WILLIAM"), "William Willowby")
        self.assertTrue(rules.name_from_sl("REDWOOD II~JAMES").startswith("James Redwood"))


class PTTValueTests(unittest.TestCase):
    def test_hours(self):
        self.assertEqual(rules.parse_hours("8.0"), (Decimal("8.0"), False))
        self.assertEqual(rules.parse_hours(""), (Decimal("0"), False))
        self.assertEqual(rules.parse_hours(None), (Decimal("0"), False))
        self.assertEqual(rules.parse_hours("abc")[1], True)
        self.assertEqual(rules.parse_hours("99")[1], True)  # implausible

    def test_json_double_encoding(self):
        raw = '"{\\"1\\": 12.0, \\"2\\": 212.0}"'
        self.assertEqual(rules.parse_ptt_json(raw), {"1": 12.0, "2": 212.0})
        self.assertEqual(rules.parse_ptt_json('{"1": 1}'), {"1": 1})
        self.assertEqual(rules.parse_ptt_json("not json"), {})

    def test_utc_tuple(self):
        dt = rules.utc_from_tuple([2026, 8, 14, 18, 10, 59])
        self.assertEqual((dt.year, dt.hour, dt.second), (2026, 18, 59))


class GuardTests(unittest.TestCase):
    def test_rejects_writes(self):
        for sql in ("UPDATE pjproj SET x=1", "SELECT 1; DELETE FROM t", "SELECT * INTO t2 FROM t", "EXEC sp_who", "SELECT 1; SELECT 2", "", "WITH x AS (SELECT 1) UPDATE t SET a=1"):
            with self.assertRaises(guard.WriteAttemptError, msg=sql):
                guard.assert_read_only(sql)

    def test_allows_reads(self):
        guard.assert_read_only("SELECT a FROM b WHERE c = ?;")
        guard.assert_read_only("-- comment\nWITH x AS (SELECT 1) SELECT * FROM x")

    def test_registry_fails_closed(self):
        with self.assertRaises(guard.UnregisteredQueryError):
            guard.load_query("sl.drop_everything")

    def test_all_registered_queries_are_read_only_and_present(self):
        for name in guard.ALLOWED_SOURCE_QUERIES:
            sql, digest = guard.load_query(name)
            self.assertTrue(sql.strip())
            self.assertEqual(len(digest), 64)

    def test_redaction(self):
        self.assertNotIn("secret", guard.redact("DRIVER=x;PWD=secret;APP=y"))


if __name__ == "__main__":
    unittest.main()


class CommitmentNettingTests(unittest.TestCase):
    """SL project-inventory allocations are netted FIFO against quantity already shipped to the same project (docs 02 §3b)."""

    def test_fully_shipped_allocation_is_phantom(self):
        self.assertEqual(rules.fifo_open_units([Decimal("15")], Decimal("15")), [Decimal("0")])

    def test_nothing_shipped_keeps_everything_open(self):
        self.assertEqual(rules.fifo_open_units([Decimal("5"), Decimal("1")], Decimal("0")), [Decimal("5"), Decimal("1")])
        self.assertEqual(rules.fifo_open_units([Decimal("5")], None), [Decimal("5")])

    def test_earliest_receipts_consumed_first(self):
        # 5 received in December, 1 in January; 5 shipped -> the January unit is the one still in the warehouse
        self.assertEqual(rules.fifo_open_units([Decimal("5"), Decimal("1")], Decimal("5")), [Decimal("0"), Decimal("1")])
        self.assertEqual(rules.fifo_open_units([Decimal("5"), Decimal("1")], Decimal("3")), [Decimal("2"), Decimal("1")])

    def test_over_shipment_never_goes_negative(self):
        self.assertEqual(rules.fifo_open_units([Decimal("5"), Decimal("1")], Decimal("40")), [Decimal("0"), Decimal("0")])
        self.assertEqual(rules.fifo_open_units([Decimal("5")], Decimal("-3")), [Decimal("5")])


class PctFractionTests(unittest.TestCase):
    """PTT's derived % complete is unbounded; only 0-100 is a valid update (docs/02 gotcha 16)."""

    def test_in_range(self):
        self.assertEqual(rules.pct_fraction(41), Decimal("0.41"))
        self.assertEqual(rules.pct_fraction("57.5"), Decimal("0.575"))
        self.assertEqual(rules.pct_fraction(0), Decimal("0"))
        self.assertEqual(rules.pct_fraction(100), Decimal("1"))

    def test_out_of_range_is_no_update(self):
        self.assertIsNone(rules.pct_fraction(-538.82))      # 265304, 2026-09-02
        self.assertIsNone(rules.pct_fraction(100.01))
        self.assertIsNone(rules.pct_fraction(-0.5))

    def test_none(self):
        self.assertIsNone(rules.pct_fraction(None))
