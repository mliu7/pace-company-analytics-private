"""Proposal checks (apps/documents/checks.py) on the fixture texts under tests/fixtures/documents/ — pure."""

import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from apps.documents import checks as C  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "documents"
GOOD = (FIX / "proposal_good.txt").read_text()
MISSING = (FIX / "proposal_missing.txt").read_text()
NOTP = (FIX / "not_proposal.txt").read_text()
STANDARD_MD = """
## Template phrases
- Pace Systems, Inc.
- PROPOSAL
- Scope of Work

## Exclusions
- Conduit, raceway and 120 V power by others
- Permits and fees
- Patching and painting
- PLACEHOLDER: ignored line

## Clarifications
- Pricing assumes work during normal business hours
- Owner to provide network drops at each device location

## Terms
- Proposal valid for 30 days
- Change orders priced separately

## Payment terms
- Net 30 from invoice date; progress billing monthly

## Warranty
- One year parts and labor from substantial completion
"""
STANDARD = C.load_standard_clauses(STANDARD_MD)
BID = {"project_name": "Timeline Theatre AV", "client_name": "Bulley & Andrews", "value": Decimal("128053"), "bid_due": date(2025, 5, 1)}


def ids(findings):
    return [f["check_id"] for f in findings]


class Recognition(unittest.TestCase):
    def test_is_proposal(self):
        self.assertTrue(C.is_proposal(GOOD)[0])
        self.assertTrue(C.is_proposal(MISSING)[0])
        self.assertFalse(C.is_proposal(NOTP)[0])
        self.assertTrue(C.is_proposal("we propose the following", "Proposal.pdf")[0])   # the name helps

    def test_fingerprint_and_version(self):
        fp = C.template_fingerprint(GOOD)
        self.assertEqual(fp["template"], "pace_proposal")
        self.assertEqual(fp["version"], "2")
        self.assertEqual(C.template_fingerprint(NOTP)["template"], "")


class Sections(unittest.TestCase):
    def test_all_sections_found_in_the_good_proposal(self):
        found = C.find_sections(GOOD)
        for key in C.SECTIONS:
            self.assertIn(key, found, key)

    def test_missing_sections_reported(self):
        f = C.run_checks(MISSING)
        missing = {x["evidence"]["section"] for x in f if x["check_id"] == "missing_section"}
        self.assertEqual(missing, {"exclusions", "clarifications", "schedule", "signature", "revision_date"})

    def test_no_missing_sections_in_good(self):
        self.assertNotIn("missing_section", ids(C.run_checks(GOOD)))


class Money(unittest.TestCase):
    def test_price_is_the_total_line(self):
        self.assertEqual(C.proposal_price(GOOD)[0], Decimal("128053.00"))
        self.assertEqual(C.proposal_price(MISSING)[0], Decimal("41900.00"))
        self.assertIsNone(C.proposal_price(NOTP))

    def test_dates(self):
        self.assertEqual(C.proposal_date(GOOD), date(2025, 4, 24))
        self.assertEqual(C.dates_in("due 5/1/25 and May 3, 2025")[0][0], date(2025, 5, 1))
        self.assertIsNone(C.proposal_date(MISSING))


class BidConsistency(unittest.TestCase):
    def test_good_proposal_agrees_with_the_bid(self):
        f = ids(C.run_checks(GOOD, bid=BID))
        for bad in ("bid_project_name", "bid_client", "bid_price", "bid_price_missing", "bid_date"):
            self.assertNotIn(bad, f)

    def test_price_outside_two_percent(self):
        f = C.run_checks(GOOD, bid=dict(BID, value=Decimal("150000")))
        hit = [x for x in f if x["check_id"] == "bid_price"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["severity"], "error")
        self.assertEqual(hit[0]["evidence"]["found"], "128053.00")
        self.assertIn("quote", hit[0]["evidence"])

    def test_price_inside_two_percent_passes(self):
        self.assertNotIn("bid_price", ids(C.run_checks(GOOD, bid=dict(BID, value=Decimal("129000")))))

    def test_client_and_project_name_missing(self):
        f = ids(C.run_checks(GOOD, bid=dict(BID, client_name="Northwestern Memorial", project_name="Lake Forest Pavilion")))
        self.assertIn("bid_client", f)
        self.assertIn("bid_project_name", f)

    def test_dated_after_due(self):
        self.assertIn("bid_date", ids(C.run_checks(GOOD, bid=dict(BID, bid_due=date(2025, 4, 1)))))


class Clauses(unittest.TestCase):
    def test_extracted_clauses(self):
        c = C.extract_clauses(GOOD)
        self.assertEqual(c["exclusions"], ["Conduit, raceway and 120 V power by others", "Permits and fees", "Patching and painting"])
        self.assertEqual(len(c["clarifications"]), 2)
        self.assertTrue(any("30 days" in t for t in c["terms"]))

    def test_standard_loader_skips_placeholders(self):
        self.assertEqual(STANDARD["exclusions"], ["Conduit, raceway and 120 V power by others", "Permits and fees", "Patching and painting"])
        self.assertEqual(STANDARD["template_phrases"], ["Pace Systems, Inc.", "PROPOSAL", "Scope of Work"])
        real = C.load_standard_clauses((Path(__file__).resolve().parents[2] / "docs" / "proposal_standard_clauses.md").read_text())
        self.assertTrue(all(not v for v in real.values()), "the shipped file must be placeholders only")

    def test_compare(self):
        cmp = C.compare_clauses(["Permits and fees by others", "Patching and painting", "Crane rental"], STANDARD["exclusions"])
        self.assertEqual(cmp["missing"], ["Conduit, raceway and 120 V power by others"])
        self.assertEqual(cmp["deviations"][0][0], "Permits and fees")
        self.assertEqual(cmp["extra"], ["Crane rental"])

    def test_good_proposal_has_no_clause_findings(self):
        f = ids(C.run_checks(GOOD, bid=BID, standard=STANDARD))
        for bad in ("clause_missing", "clause_deviation", "clause_extra", "payment_terms", "warranty"):
            self.assertNotIn(bad, f)
        self.assertIn("template_fingerprint", f)

    def test_deviating_proposal(self):
        text = GOOD.replace("- Permits and fees\n", "").replace("Net 30 from invoice date; progress billing monthly", "50% deposit, balance on completion") \
                   .replace("One year parts and labor from substantial completion", "90 days on labor only")
        f = C.run_checks(text, bid=BID, standard=STANDARD)
        self.assertIn("clause_missing", ids(f))
        self.assertIn("payment_terms", ids(f))
        self.assertIn("warranty", ids(f))

    def test_not_a_proposal_short_circuits(self):
        f = C.run_checks(NOTP, bid=BID, standard=STANDARD)
        self.assertEqual(ids(f), ["not_a_proposal"])


class ModelReview(unittest.TestCase):
    def test_hook_is_off(self):
        self.assertFalse(C.MODEL_REVIEW_ENABLED)
        with self.assertRaises(RuntimeError):
            C.model_review("anything")


if __name__ == "__main__":
    unittest.main()
