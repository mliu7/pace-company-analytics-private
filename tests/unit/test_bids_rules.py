"""Bid pipeline rules (apps/bids/rules.py) — pure, no database."""

import os
import sys
import unittest
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from apps.bids import rules as R  # noqa: E402


class Statuses(unittest.TestCase):
    def test_portal_statuses(self):
        self.assertEqual(R.normalize_status("Quoting"), ("quoting", ""))
        self.assertEqual(R.normalize_status("Submitted"), ("submitted", ""))
        self.assertEqual(R.normalize_status("Awarded"), ("awarded", ""))
        self.assertEqual(R.normalize_status("Lost"), ("lost", ""))
        self.assertEqual(R.normalize_status("Did Not Bid"), ("did_not_bid", ""))

    def test_legacy_statuses(self):
        self.assertEqual(R.normalize_status("Completed"), ("awarded", "completed"))
        self.assertEqual(R.normalize_status("In Progress"), ("awarded", "in_progress"))
        self.assertEqual(R.normalize_status("Budget"), ("quoting", "budgetary"))
        self.assertEqual(R.normalize_status("Re-bid"), ("submitted", "rebid"))
        self.assertEqual(R.normalize_status("On Hold"), ("on_hold", ""))
        self.assertEqual(R.normalize_status("No Decision"), ("no_decision", ""))
        self.assertEqual(R.normalize_status("Needs Assessment"), ("quoting", "needs_assessment"))
        self.assertEqual(R.normalize_status(""), ("unknown", ""))
        self.assertEqual(R.normalize_status(None), ("unknown", ""))
        self.assertEqual(R.normalize_status("  awarded "), ("awarded", ""))


class JobNumbers(unittest.TestCase):
    known = {"260071", "260071000000", "265210", "INT21413", "240045000000"}

    def test_six_digits_tries_bare_then_suffix(self):
        self.assertEqual(R.job_number_candidates("260071"), ["260071", "260071000000"])
        self.assertEqual(R.resolve_job_number("260071", self.known), "260071")           # the bare job exists: never assume 000000
        self.assertEqual(R.resolve_job_number("240045", self.known), "240045000000")     # only the 000000 form exists

    def test_twelve_digits_and_hyphen(self):
        self.assertEqual(R.resolve_job_number("260071-000000", self.known), "260071000000")
        self.assertEqual(R.resolve_job_number("260071000000", self.known), "260071000000")

    def test_text_keys_and_junk(self):
        self.assertEqual(R.resolve_job_number("INT21413", self.known), "INT21413")
        self.assertIsNone(R.resolve_job_number("TBD", self.known))
        self.assertIsNone(R.resolve_job_number("", self.known))
        self.assertIsNone(R.resolve_job_number("25-4476", self.known))                    # AV quote number, not a job
        self.assertEqual(R.quote_reference("25-4476 Timeline Theatre"), "25-4476")
        self.assertIsNone(R.quote_reference("265210"))


class People(unittest.TestCase):
    pool = [
        {"name": "Mike Birchfield", "active": True, "role": "pm", "n_pm": 4},
        {"name": "Nathan Birchfield", "active": False, "role": "regular", "n_pm": 0},
        {"name": "Herb Cedarwell", "active": True, "role": "pm", "n_pm": 1029},
        {"name": "Catherine Cedarwell", "active": False, "role": "", "n_pm": 0},
        {"name": "Seph Pebbleford", "active": True, "role": "pm", "n_pm": 24},
        {"name": "Quillford Oakridge", "active": True, "role": "regular", "n_pm": 0},
        {"name": "Mike Oakridge", "active": True, "role": "regular", "n_pm": 961},
        {"name": "Thomas Amberstone", "active": True, "role": "regular", "n_pm": 0},
        {"name": "Donald Amberstone", "active": True, "role": "regular", "n_pm": 0},
    ]

    def test_norm(self):
        self.assertEqual(R.norm_name_key(" Oakridge "), "OAKRIDGE")
        self.assertEqual(R.norm_name_key("OT "), "OT")
        self.assertEqual(R.norm_name_key("Van Mapleford, Jim"), "VAN MAPLEFORD JIM")

    def test_fixed_alias_birchfield_is_mike(self):
        self.assertEqual(R.alias_candidates("BIRCHFIELD", self.pool)[0], ("Mike Birchfield", 0.95, "fixed_alias"))
        self.assertEqual(R.alias_candidates("Birchfield", self.pool)[0][0], "Mike Birchfield")

    def test_unique_surname_and_exact(self):
        self.assertEqual(R.alias_candidates("PEBBLEFORD", self.pool)[0], ("Seph Pebbleford", 0.9, "surname"))
        self.assertEqual(R.alias_candidates("Seph Pebbleford", self.pool)[0], ("Seph Pebbleford", 1.0, "exact"))
        self.assertEqual(R.alias_candidates("Pebbleford, Seph", self.pool)[0][2], "name_words")

    def test_fixed_beats_ambiguous(self):
        self.assertEqual(R.alias_candidates("CEDARWELL", self.pool)[0][0], "Herb Cedarwell")
        self.assertEqual(R.alias_candidates("OAKRIDGE", self.pool)[0][0], "Mike Oakridge")

    def test_ambiguous_surname_is_low_confidence(self):
        c = R.alias_candidates("AMBERSTONE", [e for e in self.pool if "Amberstone" in e["name"]])
        self.assertEqual(c[0][2], "surname_ambiguous")
        self.assertLess(c[0][1], 0.9)

    def test_not_a_person(self):
        self.assertEqual(R.alias_candidates("TBD", self.pool), [])
        self.assertEqual(R.alias_candidates("", self.pool), [])
        self.assertTrue(R.is_house_rep("OT "))
        self.assertTrue(R.is_house_rep("Other"))
        self.assertFalse(R.is_house_rep("BIRCHFIELD"))


class Values(unittest.TestCase):
    def test_decimal_and_dates(self):
        self.assertEqual(R.to_decimal("$12,506.35"), Decimal("12506.35"))
        self.assertIsNone(R.to_decimal(""))
        self.assertEqual(R.to_date("2026-01-04T06:00:00Z"), date(2026, 1, 4))
        self.assertEqual(R.to_date(datetime(2026, 1, 4, 6)), date(2026, 1, 4))
        self.assertIsNone(R.to_date("garbage"))

    def test_probability_never_zero_for_blank(self):
        self.assertEqual(R.probability_from("25%"), 25)
        self.assertEqual(R.probability_from(0.5), 50)
        self.assertEqual(R.probability_from("50"), 50)
        self.assertEqual(R.probability_from(75), 75)
        self.assertIsNone(R.probability_from(""))
        self.assertIsNone(R.probability_from("Quoting"))
        self.assertIsNone(R.probability_from(None))

    def test_bid_margin(self):
        self.assertEqual(R.bid_margin(Decimal("22219"), Decimal("17731")), (Decimal("22219") - Decimal("17731")) / Decimal("22219"))
        self.assertIsNone(R.bid_margin(None, Decimal(1)))
        self.assertIsNone(R.bid_margin(Decimal(0), Decimal(1)))


class DivisionAndTiming(unittest.TestCase):
    sub2div = {"0700": "070", "0701": "070", "0400": "040", "0800": "080"}

    def test_division_order(self):
        self.assertEqual(R.division_for("070", "040", "0800", self.sub2div), "070")
        self.assertEqual(R.division_for("", "070", "0800", self.sub2div), "070")        # Snowcrest: jobs beat home subaccount
        self.assertEqual(R.division_for("", "", "0400", self.sub2div), "040")
        self.assertEqual(R.division_for("", "", "", self.sub2div), "")

    def test_stage_entries(self):
        v = [(datetime(2026, 5, 8), "submitted"), (datetime(2026, 5, 4), "quoting"), (datetime(2026, 6, 15), "awarded"), (datetime(2026, 5, 7), "submitted")]
        e = R.stage_entries(v)
        self.assertEqual(e["quoting"], datetime(2026, 5, 4))
        self.assertEqual(e["submitted"], datetime(2026, 5, 7))
        self.assertEqual(e["awarded"], datetime(2026, 6, 15))

    def test_won_by_sl(self):
        self.assertTrue(R.won("lost", 1500))
        self.assertTrue(R.won("awarded", 0))
        self.assertFalse(R.won("submitted", 0))

    def test_expected_decision_date(self):
        self.assertEqual(R.expected_decision_date(date(2026, 9, 1), None, 22), date(2026, 9, 23))
        self.assertEqual(R.expected_decision_date(None, date(2026, 9, 1), 0), date(2026, 9, 1))
        self.assertIsNone(R.expected_decision_date(None, None, 10))


if __name__ == "__main__":
    unittest.main()


class SmarterAliases(unittest.TestCase):
    pool = [
        {"name": "Mike Oakridge", "active": True, "role": "regular", "n_pm": 961},
        {"name": "Quillford Oakridge", "active": True, "role": "regular", "n_pm": 0},
        {"name": "Chris Snowcrest", "active": True, "role": "pm", "n_pm": 565},
        {"name": "Jim Redwood", "active": True, "role": "pm", "n_pm": 1364},
        {"name": "Darby Redwood", "active": True, "role": "regular", "n_pm": 0},
        {"name": "Todd Overbrook", "active": True, "role": "pm", "n_pm": 40},
        {"name": "Avery Overbrook", "active": False, "role": "regular", "n_pm": 0},
        {"name": "Herb Cedarwell", "active": True, "role": "pm", "n_pm": 1029},
        {"name": "Daniel Glenbrook", "active": True, "role": "pm", "n_pm": 30},
        {"name": "Michael Glenbrook", "active": False, "role": "regular", "n_pm": 0},
    ]

    def test_choice_cleanup(self):
        self.assertEqual(R.clean_choice(";#In Progress;#"), "In Progress")
        self.assertEqual(R.normalize_status(";#In Progress;#"), ("awarded", "in_progress"))

    def test_joint_entries_take_the_first_name(self):
        self.assertEqual(R.split_joint("MIKE O CHRIS S"), ["MIKE O", "CHRIS S"])
        self.assertEqual(R.split_joint("CEDARWELL / NORTHWOOD"), ["CEDARWELL", "NORTHWOOD"])
        c = R.alias_candidates("MIKE O CHRIS S", self.pool)
        self.assertEqual((c[0][0], c[0][2]), ("Mike Oakridge", "first_initial+joint"))

    def test_first_name_initial(self):
        self.assertEqual(R.alias_candidates("CHRIS S", self.pool)[0][:2], ("Chris Snowcrest", 0.9))

    def test_typo_within_two_edits(self):
        self.assertEqual(R.alias_candidates("REDWODO", self.pool)[0][:3], ("Jim Redwood", 0.8, "surname_fuzzy"))
        self.assertEqual(R.alias_candidates("CEDARWEL", self.pool)[0][0], "Herb Cedarwell")

    def test_dominant_surname(self):
        self.assertEqual(R.alias_candidates("OVERBROOK", self.pool)[0][:3], ("Todd Overbrook", 0.85, "surname_dominant"))

    def test_nickname_to_surname(self):
        self.assertEqual(R.alias_candidates("GLEN", self.pool)[0][0], "Daniel Glenbrook")


class EvidenceRules(unittest.TestCase):
    def test_sales_history_counts_and_inactive_can_win(self):
        pool = [{"name": "Todd Overbrook", "active": False, "role": "regular", "n_pm": 1, "n_sales": 447},
                {"name": "Avery Overbrook", "active": False, "role": "regular", "n_pm": 0, "n_sales": 0}]
        self.assertEqual(R.alias_candidates("OVERBROOK", pool)[0][:3], ("Todd Overbrook", 0.85, "surname_dominant"))

    def test_same_person_two_rows(self):
        pool = [{"name": "Dave Hazelton", "active": True, "role": "regular", "n_pm": 0}, {"name": "David Hazelton", "active": True, "role": "", "n_pm": 0}]
        self.assertEqual(R.alias_candidates("HAZELTON", pool)[0][2], "surname_dominant")

    def test_only_pm_among_never_ran_a_job(self):
        pool = [{"name": "Zach Marshwell", "active": False, "role": "pm", "n_pm": 1}, {"name": "Jermaine Marshwell", "active": False, "role": "regular", "n_pm": 0}]
        self.assertEqual(R.alias_candidates("MARSHWELL", pool)[0][0], "Zach Marshwell")

    def test_compound_surname(self):
        pool = [{"name": "Jim Van Mapleford", "active": False, "role": "pm", "n_pm": 71, "n_sales": 103}]
        self.assertEqual(R.alias_candidates("VANMAPLEFORD", pool)[0][0], "Jim Van Mapleford")
        self.assertEqual(R.alias_candidates("VAN MAPLEFOD", pool)[0][:3], ("Jim Van Mapleford", 0.8, "surname_fuzzy"))
        self.assertEqual(R.alias_candidates("Van Mapleford, Jim", pool)[0][2], "name_words")


class ArchiveStage(unittest.TestCase):
    def test_archived_open_rows_are_not_pipeline(self):
        self.assertEqual(R.archive_stage(R.SUBMITTED, ""), (R.NO_DECISION, "archived_open"))
        self.assertEqual(R.archive_stage(R.QUOTING, "in_progress"), (R.NO_DECISION, "archived_open"))
        self.assertEqual(R.archive_stage(R.AWARDED, ""), (R.AWARDED, ""))
        self.assertEqual(R.archive_stage(R.LOST, ""), (R.LOST, ""))


class PortalIdText(unittest.TestCase):
    def test_trailing_zeros_survive(self):
        self.assertEqual(R.portal_id_text(9990.0), "9990")
        self.assertEqual(R.portal_id_text("9990.0"), "9990")
        self.assertEqual(R.portal_id_text(9900), "9900")
        self.assertEqual(R.portal_id_text("9955"), "9955")
        self.assertEqual(R.portal_id_text(""), "")
        self.assertEqual(R.portal_id_text(None), "")
        self.assertEqual(R.portal_id_text("ABC-12"), "ABC-12")


class WorkTypes(unittest.TestCase):
    def test_keywords(self):
        self.assertEqual(R.work_type("NEIU EL Centro WAP Install"), "Network / cabling")
        self.assertEqual(R.work_type("Naperville Municipal IT Room Access Control"), "Access control")
        self.assertEqual(R.work_type("KKC W149 Access Center ADA"), "Access control")
        self.assertEqual(R.work_type("Boardroom Upgrades"), "AV / conferencing")
        self.assertEqual(R.work_type("Genetec Advantage renewal"), "Cameras / CCTV")
        self.assertEqual(R.work_type("Managed IT Services"), "IT / managed services")
        self.assertEqual(R.work_type("MSP RFP"), "IT / managed services")
        self.assertEqual(R.work_type("Box Sales 3-2026"), "Hardware / box sale")
        self.assertEqual(R.work_type("", "070"), "Security (other)")
        self.assertEqual(R.work_type("Something else", ""), "Other")
