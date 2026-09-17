"""Pure Planner rules (apps/planner/rules.py): board kinds, AV sequence, project matching (000000-aware), label
colours, history diff, dates, links. No database."""

import unittest
from datetime import date, datetime, timezone

from apps.planner import rules


class BoardKindTests(unittest.TestCase):
    def test_punch(self):
        self.assertEqual(rules.board_kind("PUNCH LIST JOBS", "Pending Punch List items", ["265092 Skokie"], ["fix door"]), rules.PUNCH)

    def test_per_job_plan_by_title_number(self):
        self.assertEqual(rules.board_kind("265092 - Skokie Paging", "070 Master Schedule"), rules.JOB)
        self.assertEqual(rules.board_kind("25-4451 Leopardo Sankofa Village", "SS PM"), rules.JOB)

    def test_per_job_plan_by_group_number(self):
        self.assertEqual(rules.board_kind("Tasks", "25-4479 NORTHLIGHT THEATER"), rules.JOB)

    def test_workflow_when_tasks_are_jobs(self):
        tasks = ["26-5166 WEB Dubois A26 Server Add", "ORR", "26-5210 Fernwood Keyless", "Deans Workstation"]
        self.assertEqual(rules.board_kind("Project Tracking", "CPS Genetec Upgrades", ["QC Accepted", "Executing"], tasks), rules.WORKFLOW)

    def test_workflow_when_buckets_are_jobs(self):
        self.assertEqual(rules.board_kind("Weekly Schedule", "B&G", ["265001 Job A", "265002 Job B", "Misc"], ["a", "b", "c", "d"]), rules.WORKFLOW)

    def test_workflow_by_av_stage_word(self):
        self.assertEqual(rules.board_kind("1. Fabrication", "AV Division"), rules.WORKFLOW)

    def test_admin_otherwise(self):
        self.assertEqual(rules.board_kind("Bart Tasks", "A & B ACCOUNTING TASKS", ["To do", "Done"], ["Pay invoices", "File taxes"]), rules.ADMIN)
        self.assertEqual(rules.board_kind("Onboarding Steps", "Pace Scheduler Onboarding"), rules.ADMIN)

    def test_few_job_titles_do_not_make_a_workflow(self):
        tasks = ["Pay invoices", "File taxes", "Order paper", "265092 follow-up", "Meeting", "Renew cert", "Backup", "Audit", "Lunch", "Report"]
        self.assertEqual(rules.board_kind("Bart Tasks", "Accounting", ["To do"], tasks), rules.ADMIN)


class AvSequenceTests(unittest.TestCase):
    def test_stages_from_titles(self):
        self.assertEqual(rules.av_stage("1. Fabrication", "AV Division"), "fabrication")
        self.assertEqual(rules.av_stage("3 Fabrication.Shipping Tracker", "AV Division"), "fabrication")
        self.assertEqual(rules.av_stage("AV Programming List", "AV Division"), "programming")
        self.assertEqual(rules.av_stage("6 Programming.Commissioning", "AV Division"), "programming")
        self.assertEqual(rules.av_stage("A-Card Schedule", "Pace AV"), "a_card")
        self.assertEqual(rules.av_stage("AV A-Card Schedule", "Pace Systems"), "a_card")
        self.assertEqual(rules.av_stage("7 Signal Flow Queue", "Anything"), "signal_flow")
        self.assertEqual(rules.av_stage("AV Signoff Sheet", "AV"), "sign_off")
        self.assertEqual(rules.av_stage("1.1 Install Line-UP", "AV Division"), "line_up")
        self.assertEqual(rules.av_stage("1. Installation Schedule", "AV Division"), "install_schedule")
        self.assertEqual(rules.av_stage("2 Project Tracker", "AV Division"), "project_tracker")

    def test_not_av(self):
        self.assertIsNone(rules.av_stage("Project Tracking", "CPS Genetec Upgrades"))   # a tracker, but not AV
        self.assertIsNone(rules.av_stage("Bart Tasks", "Accounting"))

    def test_sequence_order(self):
        self.assertLess(rules.AV_STAGE_INDEX["install_schedule"], rules.AV_STAGE_INDEX["line_up"])
        self.assertLess(rules.AV_STAGE_INDEX["fabrication"], rules.AV_STAGE_INDEX["programming"])
        self.assertLess(rules.AV_STAGE_INDEX["a_card"], rules.AV_STAGE_INDEX["sign_off"])
        self.assertEqual(len(rules.AV_SEQUENCE), 8)


class ProjectMatchTests(unittest.TestCase):
    KNOWN = {"265092", "241608000000", "265166", "254451"}

    def test_six_digit_in_title(self):
        self.assertEqual(rules.match_project("265092 - Skokie Paging", "", self.KNOWN), ("265092", "", "title:digits6"))

    def test_quote_style_number_tries_000000(self):
        """'24-1608' is how PMs write job 241608; only the 000000 form exists in SL."""
        self.assertEqual(rules.match_project("24-1608 Northbrook Library Civic Room", "", self.KNOWN), ("241608000000", "24-1608", "title:quote"))

    def test_bucket_name_after_title(self):
        self.assertEqual(rules.match_project("Rudolph Camera Add", "26-5166 WEB Dubois", self.KNOWN), ("265166", "26-5166", "bucket:quote"))

    def test_quote_reference_lookup(self):
        known = {"990123"}
        self.assertEqual(rules.match_project("25-4451 Leopardo", "", known, {"25-4451": "990123"}), ("990123", "25-4451", "quote_reference"))

    def test_no_match_keeps_quote(self):
        self.assertEqual(rules.match_project("25-9999 Unknown job", "", self.KNOWN), (None, "25-9999", ""))
        self.assertEqual(rules.match_project("ORR", "QC Accepted", self.KNOWN), (None, "", ""))

    def test_twelve_digit_key_is_never_shortened(self):
        known = {"241517", "241517000000"}
        self.assertEqual(rules.match_project("241517000000 paging", "", known)[0], "241517000000")
        self.assertEqual(rules.match_project("241517 paging", "", known)[0], "241517")

    def test_numbers_in_finds_every_token(self):
        toks = rules.numbers_in("25-4322/25-4323 LaSalle II")
        self.assertEqual([t[1] for t in toks], ["25-4322", "25-4323"])
        self.assertEqual(toks[0][2], ["254322", "254322000000"])
        self.assertEqual(rules.numbers_in("Deans Workstation 192.0.2.197"), [])

    def test_quote_ref_in(self):
        self.assertEqual(rules.quote_ref_in("26-5060 Phase 02 Tamayo"), "26-5060")
        self.assertEqual(rules.quote_ref_in("no number here"), "")


class LabelTests(unittest.TestCase):
    def test_colour_map_covers_25_categories(self):
        self.assertEqual(len(rules.CATEGORY_COLORS), 25)
        self.assertEqual(rules.label_color("category1")["name"], "Pink")
        self.assertEqual(rules.label_color("category5"), {"name": "Blue", "bg": "#0078d4", "fg": "#ffffff"})
        self.assertEqual(rules.label_color("category99")["name"], "Gray")   # never loses a label

    def test_plan_labels_keep_only_named_keys_in_order(self):
        labels = rules.plan_labels({"category3": "Cesar Perez", "category1": "Orlando Magana", "category2": None, "category7": ""})
        self.assertEqual(list(labels), ["category1", "category3"])
        self.assertEqual(labels["category1"]["label"], "Orlando Magana")
        self.assertEqual(labels["category3"]["bg"], "#fde300")

    def test_applied_labels(self):
        labels = rules.plan_labels({"category1": "Orlando Magana"})
        names, keys = rules.applied_labels({"category2": True, "category1": True, "category3": False}, labels)
        self.assertEqual(names, ["Orlando Magana", "Red"])     # unnamed key shows as its colour name
        self.assertEqual(keys, ["category1", "category2"])


class StateTests(unittest.TestCase):
    def test_state_of(self):
        self.assertEqual(rules.state_of(0), "not_started")
        self.assertEqual(rules.state_of(50), "in_progress")
        self.assertEqual(rules.state_of(100), "complete")
        self.assertEqual(rules.state_of(0, completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc)), "complete")

    def test_priority_words(self):
        self.assertEqual([rules.priority_label(p) for p in (0, 1, 2, 4, 5, 7, 8, 10, None)],
                         ["Urgent", "Urgent", "Important", "Important", "Medium", "Medium", "Low", "Low", "Medium"])

    def test_overdue_uses_the_given_today(self):
        today = date(2026, 9, 10)
        self.assertTrue(rules.is_overdue(date(2026, 9, 9), 50, today))
        self.assertFalse(rules.is_overdue(date(2026, 9, 10), 50, today))     # due today is not overdue
        self.assertFalse(rules.is_overdue(date(2026, 9, 1), 100, today))     # complete
        self.assertFalse(rules.is_overdue(None, 0, today))


class DateTests(unittest.TestCase):
    def test_central_calendar_date(self):
        self.assertEqual(rules.to_central_date("2026-09-12T05:00:00Z"), date(2026, 9, 12))   # midnight CDT
        self.assertEqual(rules.to_central_date("2026-09-12T04:59:00Z"), date(2026, 9, 11))   # 23:59 CDT the day before
        self.assertIsNone(rules.to_central_date(None))

    def test_seven_digit_fraction(self):
        dt = rules.to_datetime("2026-09-09T15:57:22.5825803Z")
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond), (2026, 9, 9, 15, 57, 22, 582580))
        self.assertIsNone(rules.to_datetime("garbage"))


class HistoryDiffTests(unittest.TestCase):
    OLD = {"title": "26-5166 Dubois", "percent": 0, "priority": 5, "start": None, "due": date(2026, 9, 1), "completed": None,
           "labels": ["QC Accepted"], "assignees": ["Sergio Lopez"], "bucket": "Executing"}

    def test_new_task(self):
        self.assertEqual(rules.diff_task(None, {"title": "New thing"}), [("created", "", "New thing")])

    def test_unchanged(self):
        self.assertEqual(rules.diff_task(self.OLD, dict(self.OLD)), [])

    def test_tracked_changes(self):
        new = dict(self.OLD, percent=50, bucket="QC Accepted", due=date(2026, 9, 15), assignees=["Sergio Lopez", "Cesar Perez"])
        changes = rules.diff_task(self.OLD, new)
        self.assertEqual(changes, [("percent", "0", "50"), ("bucket", "Executing", "QC Accepted"), ("due", "2026-09-01", "2026-09-15"),
                                   ("assignees", "Sergio Lopez", "Sergio Lopez, Cesar Perez")])

    def test_completed_datetime_is_central(self):
        new = dict(self.OLD, completed=datetime(2026, 9, 10, 17, 30, tzinfo=timezone.utc))
        self.assertEqual(rules.diff_task(self.OLD, new), [("completed", "", "2026-09-10 12:30")])


class LinkTests(unittest.TestCase):
    def test_urls(self):
        self.assertEqual(rules.task_url("I8H9", "pace-systems.com"), "https://tasks.office.com/pace-systems.com/Home/Task/I8H9?Type=TaskLink&Channel=Link")
        self.assertEqual(rules.plan_url("0j-s", "pace-systems.com"), "https://tasks.office.com/pace-systems.com/Home/PlanViews/0j-s?Type=PlanLink&Channel=Link")

    def test_mail_derivations(self):
        self.assertEqual(rules.tenant_domain_from_mail("CPSSchools@example.invalid"), "example.invalid")
        self.assertEqual(rules.tenant_domain_from_mail(""), "pace-systems.com")
        self.assertEqual(rules.site_path_from_mail("CPSSchools@example.invalid"), "/sites/CPSSchools")
        self.assertEqual(rules.site_path_from_mail(None), "")

    def test_initials(self):
        self.assertEqual(rules.initials("Sergio Lopez"), "SL")
        self.assertEqual(rules.initials("Cher"), "C")
        self.assertEqual(rules.initials(""), "?")


if __name__ == "__main__":
    unittest.main()
