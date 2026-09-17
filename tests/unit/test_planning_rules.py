"""Production-planning rules (apps/planning/rules.py) — pure, no database. Parity basis: docs/sharepoint_dashboards_inventory.md."""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from apps.planning import rules as R  # noqa: E402

T = date(2026, 9, 10)


class StatusVocabulary(unittest.TestCase):
    def test_twenty_one_values_plus_blank_in_workflow_order(self):
        self.assertEqual(len(R.STATUSES), 21)      # blank + 20 named (the inventory counts the blank)
        self.assertEqual(R.STATUSES[0], "")
        self.assertEqual(R.STATUSES[1], "WAITING ON CLIENT")
        self.assertEqual(R.STATUSES[-1], "PROJECT COMPLETE")
        self.assertLess(R.STATUSES.index("NEED TO SCHEDULE"), R.STATUSES.index("WORK SCHEDULED"))

    def test_every_status_has_a_colour(self):
        for s in R.STATUSES:
            self.assertIn(s, R.STATUS_COLORS)
        self.assertEqual(R.STATUS_COLORS["NEED TO SCHEDULE"], ("#c50f1f", "#ffffff"))
        self.assertEqual(R.STATUS_COLORS["PROJECT COMPLETE"], ("#0b6a0b", "#ffffff"))
        self.assertEqual(R.status_color("NOT A STATUS"), R.STATUS_COLORS[""])

    def test_misspelling_aliases(self):
        self.assertEqual(R.canonical_status("PROCURMENT ONGOING"), "PROCUREMENT ONGOING")
        self.assertEqual(R.canonical_status("Field Install In Process"), "FIELD INSTALL IN PROGRESS")
        self.assertEqual(R.canonical_status("TESING COMMISSIONING ONGOING"), "TESTING COMMISSIONING ONGOING")
        self.assertEqual(R.canonical_status("closeout docs needed"), "CLOSEOUT DOCS REQUIRED")
        self.assertEqual(R.canonical_status("FABRICATION IN PROGRESS"), "FABRICATION IN PROCESS")
        self.assertEqual(R.canonical_status("ENGINEERING IN PROGESS"), "ENGINEERING IN PROGRESS")

    def test_unknown_values_kept_upper_cased(self):
        self.assertEqual(R.canonical_status(" some new  status "), "SOME NEW STATUS")

    def test_blank_values(self):
        for v in ("", None, "-", "—", "n/a", "None", "no phase selected", "260071"):
            self.assertEqual(R.canonical_status(v), "", v)
        self.assertTrue(R.is_blank_status("no status"))

    def test_custom_alias_table(self):
        self.assertEqual(R.canonical_status("WIP", {"WIP": "WORK SCHEDULED"}), "WORK SCHEDULED")


class Priority(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(R.priority_bucket(date(2026, 9, 9), T), "red")
        self.assertEqual(R.priority_bucket(date(2026, 9, 10), T), "yellow")      # today counts as due
        self.assertEqual(R.priority_bucket(date(2026, 9, 24), T), "yellow")      # +14
        self.assertEqual(R.priority_bucket(date(2026, 9, 25), T), "green")       # +15
        self.assertEqual(R.priority_bucket(None, T), "none")
        self.assertEqual(R.priority_bucket("2026-09-01", T), "red")
        self.assertEqual(R.priority_bucket("garbage", T), "none")

    def test_starting_soon(self):
        self.assertTrue(R.starting_soon(date(2026, 9, 10), T))
        self.assertTrue(R.starting_soon(date(2026, 9, 24), T))
        self.assertFalse(R.starting_soon(date(2026, 9, 25), T))
        self.assertFalse(R.starting_soon(date(2026, 9, 9), T))
        self.assertFalse(R.starting_soon(None, T))

    def test_active_task(self):
        self.assertTrue(R.is_active_task(False, 0))
        self.assertTrue(R.is_active_task(False, "99.9"))
        self.assertFalse(R.is_active_task(False, 100))
        self.assertFalse(R.is_active_task(True, 0))

    def test_summary(self):
        tasks = [{"end": date(2026, 9, 1), "start": None, "hours_left": 10, "percent": 50},
                 {"end": date(2026, 9, 20), "start": date(2026, 9, 12), "hours_left": 5, "percent": 0},
                 {"end": None, "start": None, "hours_left": 100, "percent": 10},
                 {"end": date(2026, 12, 1), "start": None, "hours_left": 1, "percent": 100}]
        s = R.task_summary(tasks, T)
        self.assertEqual((s["active"], s["union_hours"], s["starting_soon"], s["past_due"], s["due_soon"], s["missing"], s["on_track"]), (4, 116, 1, 1, 1, 1, 1))
        self.assertEqual(s["urgent_hours"], 15)
        self.assertEqual(s["avg_pct"], 40)


class PmValues(unittest.TestCase):
    def test_split(self):
        self.assertEqual(R.split_pm("FERN/CARLSON"), ("FERN", "CARLSON"))
        self.assertEqual(R.split_pm("FERN-KAMIN"), ("FERN", "KAMIN"))
        self.assertEqual(R.split_pm("MUTH & OAKRIDGE"), ("MUTH", "OAKRIDGE"))
        self.assertEqual(R.split_pm("muth, oakridge"), ("MUTH", "OAKRIDGE"))
        self.assertEqual(R.split_pm("A & B"), ("", ""))          # single letters are never names
        self.assertEqual(R.split_pm("Cedarwell AND Birchfield"), ("CEDARWELL", "BIRCHFIELD"))
        self.assertEqual(R.split_pm("HERRERA/ n/a"), ("HERRERA", ""))
        self.assertEqual(R.split_pm("/WALDEN"), ("WALDEN", ""))
        self.assertEqual(R.split_pm("HERRERA/HERRERA"), ("HERRERA", ""))
        self.assertEqual(R.split_pm("HERRERA/?"), ("HERRERA", ""))
        self.assertEqual(R.split_pm(""), ("", ""))
        self.assertEqual(R.pm_first_value("Cedarwell / Northwood"), "CEDARWELL")


class JobNumbers(unittest.TestCase):
    def test_job_key(self):
        self.assertEqual(R.job_key("250216 HARVARD CUSD VALCOM UPGRADE 598255"), "250216")
        self.assertEqual(R.job_key("Rush signage", "26-5219"), "265219")
        self.assertEqual(R.job_key("26-5219 Rush"), "265219")
        self.assertEqual(R.job_key("Rush 265219 signage"), "265219")
        self.assertEqual(R.job_key("no number here"), "")

    def test_candidates_never_strip_000000(self):
        self.assertEqual(R.project_candidates("241517"), ["241517", "241517000000"])
        self.assertEqual(R.project_candidates("241517000000"), ["241517000000"])
        self.assertEqual(R.resolve_project("241517", {"241517000000"}), "241517000000")
        self.assertEqual(R.resolve_project("241517", {"241517", "241517000000"}), "241517")
        self.assertIsNone(R.resolve_project("999999", {"241517"}))

    def test_punch_code(self):
        self.assertEqual(R.punch_code("260200"), "26-0200")
        self.assertEqual(R.punch_code("26-0200"), "26-0200")
        self.assertEqual(R.punch_code("25464210020"), "25464210020")
        self.assertEqual(R.code_digits("22-0280"), "220280")

    def test_division_from(self):
        self.assertEqual(R.division_from("x", "070 Master Schedule.xlsx"), "070")
        self.assertEqual(R.division_from("Division 040"), "040")
        self.assertEqual(R.division_from("0400"), "")
        self.assertEqual(R.division_from(None, ""), "")

    def test_merge_key(self):
        self.assertEqual(R.merge_key("070", "25-0216", "x"), "070|PROJECT|250216")
        self.assertEqual(R.merge_key("070", "", "  Rush   Signage "), "070|NAME|RUSH SIGNAGE")


class HeaderMapping(unittest.TestCase):
    def test_claim_order_prevents_pm_inside_equipment(self):
        m = R.map_headers(["Task Name", "Phase Complete Status", "Equipment Complete Status V2", "PM", "Engineer", "Start Date", "Critical Stop Date", "Union Hours Remaining", "Project Number", "Update Notes", "Last Update Date", "% Complete"])
        self.assertEqual(m["status"], 1)
        self.assertEqual(m["status2"], 2)
        self.assertEqual(m["pm"], 3)
        self.assertEqual(m["engineer"], 4)
        self.assertEqual(m["start"], 5)
        self.assertEqual(m["finish"], 6)
        self.assertEqual(m["hours"], 7)
        self.assertEqual(m["project"], 8)
        self.assertEqual(m["notes"], 9)
        self.assertEqual(m["lastUpdate"], 10)
        self.assertEqual(m["pct"], 11)
        self.assertEqual(m["name"], 0)

    def test_name_required(self):
        self.assertEqual(R.map_headers(["PM", "Status", "Start"]), {})

    def test_exact_beats_contains_and_longest_alias_wins(self):
        m = R.map_headers(["Status", "Status V2", "Description"])
        self.assertEqual((m["status"], m["status2"], m["name"]), (0, 1, 2))
        m = R.map_headers(["Project Description", "Project Number", "Project Manager"])
        self.assertEqual((m["name"], m["project"], m["pm"]), (0, 1, 2))

    def test_find_header_row_scans_and_scores(self):
        rows = [["Master Schedule 070", None], ["", ""], ["Task Name", "PM", "Status", "Finish"], ["260001 job", "MUTH", "WORK SCHEDULED", "2026-10-01"]]
        i, m = R.find_header_row(rows)
        self.assertEqual(i, 2)
        self.assertEqual(set(m), {"name", "pm", "status", "finish"})

    def test_cell_parsers(self):
        self.assertEqual(R.parse_pct("45%"), 45.0)
        self.assertEqual(R.parse_pct(0.45), 45.0)
        self.assertEqual(R.parse_pct(45), 45.0)
        self.assertEqual(R.parse_pct("1"), 100.0)
        self.assertEqual(R.parse_pct(250), 100.0)
        self.assertIsNone(R.parse_pct("x"))
        self.assertEqual(R.parse_hours("3 days"), 24)
        self.assertEqual(R.parse_hours("12.5"), 12.5)
        self.assertEqual(R.parse_hours(None), 0.0)
        self.assertEqual(R.parse_date_cell(46000), date(2025, 12, 9))
        self.assertEqual(R.parse_date_cell("2026-09-10T00:00:00"), date(2026, 9, 10))
        self.assertEqual(R.parse_date_cell("9/1/26"), date(2026, 9, 1))
        self.assertEqual(R.parse_date_cell("13/45/26"), None)
        self.assertIsNone(R.parse_date_cell(""))


class PunchRules(unittest.TestCase):
    def test_item_status(self):
        self.assertEqual(R.punch_item_status({"date_completed": "2026-09-01", "due_by": "2026-01-01"}, T), "completed")
        self.assertEqual(R.punch_item_status({"date_completed": None, "due_by": "2026-09-09"}, T), "overdue")
        self.assertEqual(R.punch_item_status({"date_completed": None, "due_by": "2026-09-10"}, T), "open")   # due today is not overdue
        self.assertEqual(R.punch_item_status({"date_completed": None, "due_by": None, "active": True}, T), "open")   # Active tick is visual only

    def test_critical_and_stats(self):
        items = [{"date_completed": None, "due_by": "2026-09-01", "critical": 5}, {"date_completed": None, "due_by": None, "critical": 4},
                 {"date_completed": "2026-09-02", "due_by": "2026-09-01", "critical": 5}, {"date_completed": None, "due_by": "2026-10-01", "critical": 3}]
        self.assertTrue(R.is_critical_open(items[0]))
        self.assertFalse(R.is_critical_open(items[2]))
        s = R.punch_stats(items, T)
        self.assertEqual((s["total"], s["completed"], s["open"], s["overdue"], s["critical"], s["crit5"], s["pct"]), (4, 1, 3, 1, 2, 1, 25))
        self.assertEqual(R.days_overdue(items[0], T), 9)
        self.assertEqual(R.days_overdue(items[2], T), 0)

    def test_bic_and_criticality_tables(self):
        self.assertEqual(len(R.BIC_OPTIONS), 9)
        self.assertEqual(R.BIC_OPTIONS[0], "PM")
        self.assertEqual(R.CRITICAL_LABELS[5], "Highest")
        self.assertEqual(R.CRITICAL_COLORS[1], "#d9ead3")
        self.assertEqual(R.PUNCH_ITEM_DEFAULTS, {"active": False, "bic": "PM", "critical": 3})


class Approvals(unittest.TestCase):
    def test_expected_decision(self):
        self.assertEqual(R.expected_decision("pending", "approve"), "approved")
        self.assertEqual(R.expected_decision("reopened", "approve"), "approved")
        self.assertEqual(R.expected_decision("approved", "reopen"), "reopened")
        with self.assertRaises(ValueError):
            R.expected_decision("approved", "approve")
        with self.assertRaises(ValueError):
            R.expected_decision("pending", "reopen")

    def test_legacy_status_and_overdue(self):
        self.assertEqual(R.normalize_approval_status("Needs Approval"), "pending")
        self.assertEqual(R.normalize_approval_status("Approved"), "approved")
        self.assertEqual(R.normalize_approval_status(None), "pending")
        self.assertTrue(R.approval_overdue(date(2026, 9, 9), "pending", T))
        self.assertFalse(R.approval_overdue(date(2026, 9, 10), "pending", T))
        self.assertFalse(R.approval_overdue(date(2026, 9, 1), "approved", T))
        self.assertFalse(R.approval_overdue(None, "pending", T))


class SchedulerPayload(unittest.TestCase):
    def test_payload_is_complete(self):
        p = R.scheduler_payload({"id": 7, "project_id": 3, "name": "260001 Job", "division": "070", "pm_id": 9, "pm_raw": "MUTH", "hours_left": "24.5",
                                 "start": date(2026, 9, 1), "end": date(2026, 10, 1), "engineer": "E", "notes": "n", "phase_status": "WORK SCHEDULED", "equipment_status": ""})
        self.assertEqual(p["source"], "status_row")
        self.assertEqual(p["status_row_id"], 7)
        self.assertEqual(p["hours_union"], 24.5)
        self.assertEqual((p["hours_per_day"], p["days"]), (8, [1, 1, 1, 1, 1, 0, 0]))
        self.assertEqual((p["start"], p["end"]), ("2026-09-01", "2026-10-01"))
        for k in ("project_id", "name", "division", "pm_id", "pm_raw", "engineer", "notes", "phase_status", "equipment_status"):
            self.assertIn(k, p)


if __name__ == "__main__":
    unittest.main()
