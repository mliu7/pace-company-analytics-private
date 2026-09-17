"""Document linking rules (apps/documents/linking.py) — pure, no database."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from apps.documents import linking as L  # noqa: E402

KNOWN = {"265092", "254476", "254479", "241649000000", "260071", "260071000000", "INT21413", "188169"}


class NumberHits(unittest.TestCase):
    def test_folder_with_leading_number(self):
        hits = L.number_hits("265092 - Village of Skokie PW Paging System")
        self.assertEqual(len(hits), 1)
        cands, text, leading, kind = hits[0]
        self.assertEqual(cands, ["265092", "265092000000"])       # both tried, typed number first (000000 rule)
        self.assertTrue(leading)
        self.assertEqual(kind, "six")

    def test_twelve_digit_is_exact_and_never_stripped(self):
        hits = L.number_hits("260071-000000 Rush Hospital")
        self.assertEqual(hits[0][0], ["260071000000"])
        hits = L.number_hits("260071000000 Rush Hospital")
        self.assertEqual(hits[0][0], ["260071000000"])

    def test_quote_number_is_the_sl_number_with_a_hyphen(self):
        hits = L.number_hits("25-4476 Bulley-Andrews Timeline Theatre")
        self.assertEqual(hits[0][0], ["254476", "254476000000"])
        self.assertEqual(hits[0][3], "quote")
        self.assertTrue(hits[0][2])

    def test_quote_number_needs_a_plausible_year(self):
        self.assertEqual([h for h in L.number_hits("09-2024 budget") if h[3] == "quote"], [])
        self.assertEqual(L.number_hits("Phone 312-555-1234"), [])

    def test_file_name_glued_to_letters(self):
        hits = L.number_hits("254479NORTHLIGHT.xlsx")
        self.assertEqual(hits[0][0][0], "254479")
        self.assertTrue(hits[0][2])

    def test_dates_and_long_runs_are_not_numbers(self):
        self.assertEqual(L.number_hits("Photos 20240915.zip"), [])
        self.assertEqual(L.number_hits("invoice 1234567.pdf"), [])
        self.assertEqual(L.number_hits("v1.265092 notes"), [])        # a decimal tail is not a job number

    def test_text_keys(self):
        hits = L.number_hits("INT21413 internal.pdf")
        self.assertEqual(hits[0][0], ["INT21413"])

    def test_unrelated_names_yield_nothing(self):
        self.assertEqual(L.number_hits("Templates"), [])
        self.assertEqual(L.number_hits("00 general"), [])
        self.assertEqual(L.number_hits("25-xxxx Bulley-Andrews Northbrook"), [])


class Rules(unittest.TestCase):
    def test_folder_rule_confidence(self):
        r = L.folder_rules("265092 - Village of Skokie PW Paging System")
        self.assertEqual(r[0][1], "folder_number"); self.assertEqual(r[0][2], 0.95)
        r = L.folder_rules("Skokie PW Paging 265092")
        self.assertEqual(r[0][1], "folder_number_in"); self.assertEqual(r[0][2], 0.85)
        r = L.folder_rules("25-4476 Bulley-Andrews Timeline Theatre")
        self.assertEqual(r[0][1], "quote_number"); self.assertEqual(r[0][2], 0.92)

    def test_file_rule_confidence(self):
        self.assertEqual(L.file_rules("241649 Scope.pdf")[0][1:3], ("file_number", 0.9))
        self.assertEqual(L.file_rules("Scope for 241649 rev3.pdf")[0][1:3], ("file_number_in", 0.75))
        self.assertEqual(L.file_rules("25-4476 Proposal.docx")[0][1:3], ("quote_number", 0.92))
        self.assertEqual(L.file_rules("Proposal 25-4476.docx")[0][1:3], ("quote_number", 0.77))

    def test_resolve_prefers_the_typed_number_then_000000(self):
        self.assertEqual(L.resolve(["241649", "241649000000"], KNOWN), "241649000000")
        self.assertEqual(L.resolve(["260071", "260071000000"], KNOWN), "260071")     # both exist: the typed one wins
        self.assertIsNone(L.resolve(["999999", "999999000000"], KNOWN))

    def test_evidence_records_the_match(self):
        r = L.folder_rules("265092 - Village of Skokie")
        self.assertEqual(r[0][3]["matched"], "265092")


class QuoteReferences(unittest.TestCase):
    def test_sl_refs_in_names(self):
        self.assertEqual(L.sl_quote_refs("SP 6199 Daley College proposal.pdf"), ["SP6199"])
        self.assertEqual(L.sl_quote_refs("HD#1234 - rack.pdf"), ["HD1234"])
        self.assertEqual(L.sl_quote_refs("SPRING 1234"), [])
        self.assertEqual(L.sl_quote_refs("ESP 1234"), [])

    def test_normalize_core_project_quote_reference(self):
        self.assertEqual(L.normalize_quote_reference("SP  4476"), "SP4476")
        self.assertEqual(L.normalize_quote_reference("HD#1234"), "HD1234")
        self.assertEqual(L.normalize_quote_reference("MULTIPLE HD#'S"), "")
        self.assertEqual(L.normalize_quote_reference("SP"), "")


class Planner(unittest.TestCase):
    def test_plan_name_from_folder(self):
        self.assertEqual(L.planner_plan_name("Microsoft Planner/AV Fabrication_bC9xQ2mT8kLp/rack.pdf"), "AV Fabrication")
        self.assertEqual(L.planner_plan_name("Microsoft Planner/AV-Shipping Line-up_abcdefgh123/"), "AV-Shipping Line-up")
        self.assertEqual(L.planner_plan_name("Microsoft Planner/Plain_x/file.pdf"), "Plain_x")   # tail too short to be an id
        self.assertIsNone(L.planner_plan_name("Active Jobs/265092 - Skokie/file.pdf"))


class BidNames(unittest.TestCase):
    def test_score_and_threshold(self):
        s = L.bid_name_score("Northlight Theatre Bulley Andrews", "Bulley & Andrews", "Northlight Theatre AV")
        self.assertGreaterEqual(s, 0.6)
        self.assertEqual(L.bid_name_score("Templates", "Bulley & Andrews", "Northlight Theatre AV"), 0.0)
        self.assertEqual(L.bid_name_score("Theatre bid", "Someone", "Theatre renovation"), 0.0)   # one shared word is never a match
        self.assertEqual(L.bid_name_score("bid documents", "Tripp Lite", "2477 Tripp Lite Documents"), 0.0)
        self.assertEqual(L.bid_name_score("69 W Washington 30th floor", "Washington Housing", "Managed IT Services"), 0.0)

    def test_matches_best_first(self):
        bids = [(1, "Bulley & Andrews", "Northlight Theatre AV"), (2, "Bulley & Andrews", "Timeline Theatre AV"), (3, "Village of Skokie", "PW Paging")]
        m = L.bid_name_matches("Northlight Theatre Bulley Andrews", bids)
        self.assertEqual(m[0][0], 1)
        self.assertTrue(all(b != 3 for b, _ in m))


class Hygiene(unittest.TestCase):
    def test_styles(self):
        self.assertEqual(L.folder_style("265092 - Village of Skokie PW Paging System"), "number_dash_name")
        self.assertEqual(L.folder_style("265138 Martell Cary"), "number_name")
        self.assertEqual(L.folder_style("254479NORTHLIGHT"), "number_name")
        self.assertEqual(L.folder_style("265092"), "number_only")
        self.assertEqual(L.folder_style("260071-000000"), "number_only")
        self.assertEqual(L.folder_style("25-4476 Bulley-Andrews Timeline Theatre"), "quote_number")
        self.assertEqual(L.folder_style("Skokie paging 265092"), "name_number")
        self.assertEqual(L.folder_style("Templates"), "name_only")

    def test_confidence_labels(self):
        self.assertEqual(L.confidence_label(0.95), "high")
        self.assertEqual(L.confidence_label(0.85), "good")
        self.assertEqual(L.confidence_label(0.5), "check")


if __name__ == "__main__":
    unittest.main()


class PortalIds(unittest.TestCase):
    """P: drive job folders (2022 →) are '<year> Projects/<Client>/<Portal ID> - <name>' — the Project List's Project ID
    leads the name (2026-09-10: 764 of 826 job folders under 2026 Projects)."""

    def test_leading_portal_id(self):
        self.assertEqual(L.portal_id_hit("9955 - NEIU EL Centro WAP Install"), "9955")
        self.assertEqual(L.portal_id_hit("7946 - Kingsley ES Summer 2025 Reno  25-4457"), "7946")
        self.assertEqual(L.portal_id_hit("9245 - Boardroom Upgrades  26-0044-000000"), "9245")
        self.assertEqual(L.portal_id_hit("  0455 – Old style"), "455")
        self.assertEqual(L.portal_id_hit("6580_FACIT Annual Software License"), "6580")

    def test_not_a_portal_id(self):
        for n in ("2026 Projects", "01. Pictures", "00.BLANK FOLDERS FOR SP", "265092 - Village of Skokie", "SP 2433 Lifequotes", "Northeastern Illinois University", "", None, "9955"):
            self.assertIsNone(L.portal_id_hit(n), n)

    def test_rule_registered(self):
        self.assertIn("portal_id", L.RULES)
        self.assertGreaterEqual(L.RULES["portal_id"][1], L.LOW)
