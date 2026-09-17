"""Resource Scheduler — Django tests for the edit endpoint, the capacity rule at the API boundary, permissions and the
Phase D receiver (SharePoint spec §6.3, §11; parity RS-09/10/11/14). Run: manage.py test tests.scheduling"""

import json
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.scheduling import api, maths as M, services as S
from apps.scheduling.models import Assignment, PlanPhase, PlanProject, Resource, ResourcePto
from tests.access import fixtures
from tests.access.base import AccessTestCase

MON = date(2026, 9, 21)
EDIT = "/planning/schedule/edit/"
PAGE = "/planning/schedule/"


def _post(client, payload):
    return client.post(EDIT, data=json.dumps(payload), content_type="application/json")


def _seed():
    r1 = Resource.objects.create(display_name="Ann Union", sort_name="Union, Ann", trade="Union", divisions=["070"])
    r2 = Resource.objects.create(display_name="Nat Tech", sort_name="Tech, Nat", trade="Non-Union")
    p = PlanProject.objects.create(name="990001 SENTINEL TEST PROJECT", division="070", start=MON, end=date(2026, 9, 25), days=[1, 1, 1, 1, 1, 0, 0],
                                   hours_union=40, hours_nonunion=8, colour="#152dc3")
    pull = PlanPhase.objects.create(plan_project=p, name="Pull", trade="Union", start=MON, end=date(2026, 9, 23), hours=Decimal("40"), order=0)
    prog = PlanPhase.objects.create(plan_project=p, name="Programming", trade="Non-Union", start=date(2026, 9, 24), end=date(2026, 9, 25), hours=Decimal("16"), order=1)
    return r1, r2, p, pull, prog


class EditEndpoint(AccessTestCase):
    def setUp(self):
        self.r1, self.r2, self.p, self.pull, self.prog = _seed()

    def test_page_and_json_render_for_planning_view(self):
        c = self.client_for("pm")
        self.assertEqual(c.get(PAGE).status_code, 200)
        j = c.get(PAGE + "data/?week=2026-09-21").json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["board"]["week"]["monday"], "2026-09-21")
        self.assertEqual(len(j["board"]["rows"]), 2)
        self.assertEqual(j["board"]["lane"][0]["name"], self.p.name)
        self.assertEqual(j["board"]["lane"][0]["blocks"]["2026-09-21"]["tags"][0]["state"], "none")

    def test_edit_requires_planning_write(self):
        for principal in ("sales", "hradmin", "norole"):
            r = _post(self.client_for(principal), {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-21", "hours": 8})
            self.assertIn(r.status_code, (403, 404), principal)
        self.assertEqual(Assignment.objects.count(), 0)
        self.assertEqual(self.client_for("sales").get(PAGE).status_code, 403)

    def test_assign_then_cap_rejection(self):
        c = self.client_for("pm")
        r = _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-21", "hours": 8, "phase_id": self.pull.id, "week": "2026-09-21"})
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertTrue(j["ok"])
        self.assertEqual(next(r for r in j["board"]["rows"] if r["id"] == self.r1.id)["util"]["used"], 8)
        # dashboard rule: a day whose crew consumes a slot (1 person × 8h fills the 1-resource slot of [2, 2, 1]) shows green
        self.assertEqual(j["board"]["lane"][0]["blocks"]["2026-09-21"]["tags"][0]["state"], "ok")
        self.assertEqual(j["board"]["lane"][0]["blocks"]["2026-09-22"]["tags"][0]["state"], "none")
        a = Assignment.objects.get()
        self.assertEqual(a.created_by.email, "t-pm@t.local")
        # daily cap: another 4 h the same day → 400 with reason 'daily'
        r = _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-21", "hours": 4, "phase_id": self.pull.id})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["reason"], "daily")
        # weekly cap: fill Tue–Fri, then Saturday is over 40
        for d in ("2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"):
            self.assertEqual(_post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": d, "hours": 8}).status_code, 200)
        r = _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-26", "hours": 8})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["reason"], "weekly")
        self.assertIn("48h this week", r.json()["error"])
        # Approved Overtime lifts it
        self.assertEqual(_post(c, {"action": "resource_ot", "id": self.r1.id, "ot": True}).status_code, 200)
        self.assertEqual(_post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-26", "hours": 8}).status_code, 200)
        self.assertEqual(Assignment.objects.filter(resource=self.r1).count(), 6)

    def test_pto_blocks_and_hours_zero_deletes(self):
        c = self.client_for("pm")
        ResourcePto.objects.create(resource=self.r1, start=MON, end=MON)
        r = _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-21", "hours": 8})
        self.assertEqual((r.status_code, r.json()["reason"]), (400, "pto"))
        j = _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-22", "hours": 8}).json()
        self.assertEqual(_post(c, {"action": "assignment_save", "id": j["id"], "hours": 0}).status_code, 200)
        self.assertEqual(Assignment.objects.count(), 0)

    def test_move_checks_phase_coverage_and_caps(self):
        c = self.client_for("pm")
        j = _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-21", "hours": 8, "phase_id": self.pull.id}).json()
        r = _post(c, {"action": "assignment_move", "resource_id": self.r1.id, "project_id": self.p.id, "from": "2026-09-21", "date": "2026-09-24"})
        self.assertEqual((r.status_code, r.json()["reason"]), (400, "phase"))       # Pull ends Sep 23
        r = _post(c, {"action": "assignment_move", "resource_id": self.r1.id, "project_id": self.p.id, "from": "2026-09-21", "date": "2026-09-22"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Assignment.objects.get(pk=j["id"]).date, date(2026, 9, 22))

    def test_copy_allocation_preview_and_apply(self):
        c = self.client_for("pm")
        _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-21", "hours": 8, "phase_id": self.pull.id})
        ResourcePto.objects.create(resource=self.r1, start=date(2026, 9, 23), end=date(2026, 9, 23))
        pv = _post(c, {"action": "copy_preview", "project_id": self.p.id, "phase_id": self.pull.id, "source_date": "2026-09-21", "dates": ["2026-09-22", "2026-09-23"]}).json()
        self.assertEqual([x["name"] for x in pv["source"]], ["Ann Union"])
        self.assertEqual([(x["date"], x["reason"]) for x in pv["conflicts"]], [("2026-09-23", "On PTO / vacation")])
        ap = _post(c, {"action": "copy_apply", "project_id": self.p.id, "phase_id": self.pull.id, "source_date": "2026-09-21", "dates": ["2026-09-22", "2026-09-23"]}).json()
        self.assertEqual((ap["copied"], ap["skipped"]), (1, 1))
        self.assertEqual(sorted(str(a.date) for a in Assignment.objects.all()), ["2026-09-21", "2026-09-22"])

    def test_completed_early_releases_later_allocations_only(self):
        c = self.client_for("pm")
        for d in ("2026-09-21", "2026-09-22", "2026-09-23"):
            _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": d, "hours": 8, "phase_id": self.pull.id})
        _post(c, {"action": "assignment_save", "resource_id": self.r2.id, "project_id": self.p.id, "date": "2026-09-24", "hours": 8, "phase_id": self.prog.id})
        pv = _post(c, {"action": "ce_preview", "project_id": self.p.id, "date": "2026-09-22", "scope": "Pull"}).json()
        self.assertEqual((pv["count"], pv["hours"], pv["people"]), (1, 8, 1))
        self.assertEqual([o["value"] for o in pv["options"]], ["Pull", "Programming", "Union", "Non-Union", "all"])
        r = _post(c, {"action": "completed_early", "project_id": self.p.id, "date": "2026-09-22", "scope": "Pull", "week": "2026-09-21"}).json()
        self.assertEqual(r["count"], 1)
        self.assertEqual(Assignment.objects.count(), 3)                                    # Sep 23 Pull released, Programming untouched
        p = PlanProject.objects.get(pk=self.p.id)
        self.assertEqual(p.completed_early["scope"], "Pull")
        card = next(cd for cd in r["board"]["cards"] if cd["id"] == self.p.id)
        self.assertTrue(next(ph for ph in card["phases"] if ph["name"] == "Pull")["staffed"])   # gated → staffed
        self.assertFalse(next(ph for ph in card["phases"] if ph["name"] == "Programming")["staffed"])
        self.assertEqual(_post(c, {"action": "completed_early_clear", "project_id": self.p.id}).status_code, 200)
        self.assertIsNone(PlanProject.objects.get(pk=self.p.id).completed_early)

    def test_project_save_builds_phases_and_version_conflict(self):
        c = self.client_for("pm")
        payload = {"action": "project_save", "name": "260099 NEW JOB", "division": "070", "start": "2026-10-05", "end": "2026-10-16", "days": [1, 1, 1, 1, 1, 0, 0],
                   "hours_union": 0, "hours_nonunion": 0, "short_project": False,
                   "phases": [{"name": "Pull", "enabled": True, "ranges": [{"start": "2026-10-05", "end": "2026-10-07", "weekend": "none", "hours": 48}]},
                              {"name": "Test", "enabled": True, "ranges": [{"start": "2026-10-08", "end": "2026-10-08", "weekend": "none", "hours": 8, "trade": "Union"},
                                                                          {"start": "2026-10-09", "end": "2026-10-09", "weekend": "none", "hours": 4, "trade": "Non-Union"}]},
                              {"name": "Trim", "enabled": False, "ranges": [{"start": "2026-10-01", "end": "2026-10-02", "weekend": "none", "hours": 8}]},
                              {"name": "Programming", "enabled": True, "ranges": [{"start": "2026-09-30", "end": "2026-10-20", "weekend": "sat", "hours": 16}]}]}
        j = _post(c, payload).json()
        self.assertTrue(j["ok"], j)
        p = PlanProject.objects.get(pk=j["id"])
        names = sorted((ph.name, ph.trade, str(ph.start), str(ph.end), float(ph.hours)) for ph in p.phases.all())
        self.assertEqual(names, [("Programming", "Non-Union", "2026-10-05", "2026-10-16", 16.0),      # clamped to the project window
                                 ("Pull", "Union", "2026-10-05", "2026-10-07", 48.0),
                                 ("Test", "Non-Union", "2026-10-09", "2026-10-09", 4.0), ("Test", "Union", "2026-10-08", "2026-10-08", 8.0)])
        self.assertEqual(p.version, 1)
        # stale version → 409
        stale = dict(payload, id=p.id, version=1, name="260099 NEW JOB v2")
        self.assertEqual(_post(c, stale).status_code, 200)
        self.assertEqual(PlanProject.objects.get(pk=p.id).version, 2)
        r = _post(c, stale)
        self.assertEqual(r.status_code, 409)
        self.assertIn("reload", r.json()["error"])
        # short project → synthetic Union block only when hours > 0
        short = {"action": "project_save", "id": p.id, "version": 2, "name": p.name, "division": "070", "start": "2026-10-05", "end": "2026-10-09", "days": [1, 1, 1, 1, 1, 0, 0],
                 "hours_union": 24, "hours_nonunion": 0, "short_project": True, "short_union": {"ranges": [{"start": "2026-10-05", "end": "2026-10-07", "weekend": "none"}]}, "phases": []}
        self.assertEqual(_post(c, short).status_code, 200)
        phs = list(p.phases.all())
        self.assertEqual([(ph.name, ph.is_short, ph.dates) for ph in phs], [("Union", True, ["2026-10-05", "2026-10-06", "2026-10-07"])])

    def test_assist_and_exports(self):
        c = self.client_for("pm")
        j = _post(c, {"action": "assist", "project_id": self.p.id, "groups": [{"key": "Pull", "label": "Pull", "trade": "Union", "hours": 16, "ranges": [{"start": "2026-09-21", "end": "2026-09-22", "weekend": "none"}]}]}).json()
        g = j["groups"][0]
        self.assertEqual((g["verdict"], g["free"], g["plan"]), ("Covered · 16h", 16, "2 days of 1 resource"))
        _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-21", "hours": 8, "phase_id": self.pull.id})
        csv = c.get(PAGE + "export/?kind=week_csv&week=2026-09-21").content.decode("utf-8-sig")
        self.assertIn("PROJECTS,990001 SENTINEL TEST PROJECT", csv)
        self.assertIn("Ann Union,990001 SENTINEL TEST PROJECT • Pull 8h", csv)
        self.assertIn("Hours by project", csv)
        x = c.get(PAGE + "export/?kind=xlsx&week=2026-09-21")
        self.assertEqual(x.status_code, 200)
        self.assertTrue(x["Content-Type"].startswith("application/vnd.openxmlformats"))
        html = c.get(PAGE + "export/?kind=resources_html&week=2026-09-21&resources=%d" % self.r1.id).content.decode()
        self.assertIn("Ann Union", html)
        self.assertIn("Pull", html)
        self.assertNotIn("Nat Tech", html)

    def test_audit_event_written(self):
        from apps.access.models import AuditEvent
        c = self.client_for("pm")
        _post(c, {"action": "assignment_save", "resource_id": self.r1.id, "project_id": self.p.id, "date": "2026-09-21", "hours": 8})
        ev = AuditEvent.objects.filter(kind="write_action").latest("at")
        self.assertEqual(ev.meta["action"], "assignment_save")
        self.assertEqual(ev.view_name, "planning_schedule_edit")


class Receiver(TestCase):
    """RS-14: the status board's Send to Scheduler lands here."""

    def test_create_plan_project_unscheduled_then_dated(self):
        p = api.create_plan_project({"source": "Project Status", "status_row_id": 77, "name": "260078 RUSH DAY SCHOOL", "division": "040", "pm_raw": "PEBBLEFORD",
                                     "hours_union": 40, "hours_per_day": 8, "days": [1, 1, 1, 1, 1, 0, 0], "start": "", "end": "", "engineer": "Bob", "notes": "rack first"})
        self.assertTrue(p.unscheduled)
        self.assertEqual((p.source, p.status_row_id, float(p.hours_union), p.short_project), ("Project Status", 77, 40.0, True))
        self.assertIn("Engineer: Bob", p.notes)
        self.assertEqual(p.phases.count(), 0)
        # a re-send with dates updates the same row and gives it a synthetic Union block
        p2 = api.create_plan_project({"source": "Project Status", "status_row_id": 77, "name": "260078 RUSH DAY SCHOOL", "division": "040", "pm_raw": "PEBBLEFORD",
                                      "hours_union": 40, "start": "2026-10-05", "end": "2026-10-09"})
        self.assertEqual(p2.pk, p.pk)
        self.assertFalse(p2.unscheduled)
        self.assertEqual([(ph.name, float(ph.hours), ph.is_short) for ph in p2.phases.all()], [("Union", 40.0, True)])
        self.assertEqual(p2.version, 2)
        board = S.board_payload(date(2026, 10, 5))
        self.assertEqual(board["lane"][0]["name"], "260078 RUSH DAY SCHOOL")
        self.assertIsNotNone(M.staffing_need(S.load_state()["projects"][0], []))       # 40 union hours still open

    def test_kind_nonunion(self):
        p = api.create_plan_project({"status_row_id": 78, "name": "X", "kind": "nonunion", "hours_union": 16})
        self.assertEqual((float(p.hours_union), float(p.hours_nonunion)), (0.0, 16.0))


class RosterSeed(TestCase):
    def test_seed_uses_employee_flags(self):
        from apps.core.models import Employee
        Employee.objects.create(employee_key="F1", canonical_name="Frank Field", ptt_employee_type="union", union_code="134A", is_field_hourly=True, active=True, home_subaccount="0700")
        Employee.objects.create(employee_key="T1", canonical_name="Tina Tech", ptt_employee_type="non_union", classification_code="tech", ptt_active=True, is_field_hourly=True, active=True, home_subaccount="0400")
        Employee.objects.create(employee_key="P1", canonical_name="Pat Pm", ptt_employee_type="non_union", ptt_employee_role="pm", ptt_active=True, active=True, home_subaccount="0000")
        Employee.objects.create(employee_key="O1", canonical_name="Olive Office", ptt_employee_type="non_union", classification_code="admin", active=True)
        Employee.objects.create(employee_key="X1", canonical_name="Ex Employee", is_field_hourly=True, active=False)
        rows = S.seed_resources(apply=True)
        self.assertEqual(sorted((r["name"], r["trade"], r["division"]) for r in rows), [("Frank Field", "Union", "070"), ("Pat Pm", "Non-Union", ""), ("Tina Tech", "Non-Union", "040")])
        self.assertEqual(Resource.objects.get(display_name="Frank Field").sort_name, "Field, Frank")
        self.assertEqual(Resource.objects.get(display_name="Frank Field").divisions, ["070"])
        self.assertEqual(S.seed_resources(apply=True), [])                                       # idempotent
