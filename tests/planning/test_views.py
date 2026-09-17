"""Production planning — edit endpoints, optimistic locking, approval permissions, project card (needs the DB)."""

import json
from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.planning import api, loaders
from apps.planning.models import ApprovalRequest, PunchItem, PunchProject, StatusHistory, StatusRow
from tests.access.base import AccessTestCase


def _j(r):
    return json.loads(r.content.decode())


class StatusEditTests(AccessTestCase):
    def _row(self, **kw):
        d = dict(division="070", name="990001 SENTINEL TEST PROJECT", project_number_raw="990001", project=self.fx["projects"]["p070"] if "p070" in self.fx.get("projects", {}) else None,
                 phase_status="NEED TO SCHEDULE", hours_left=8)
        d.update(kw)
        return StatusRow.objects.create(**d)

    def test_pages_render_for_pm(self):
        self._row()
        c = self.client_for("pm")
        for url in ("/planning/", "/planning/status/", "/planning/tasks/", "/planning/punch/", "/planning/approvals/", "/planning/tasks/report/", "/planning/status/import/"):
            r = c.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.assertEqual(c.get("/planning/status/data/").status_code, 200)
        self.assertEqual(c.get("/planning/tasks/export/")["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    def test_norole_denied(self):
        self.assertEqual(self.client_for("norole").get("/planning/status/").status_code, 403)
        self.assertEqual(self.client_for("sales").get("/planning/").status_code, 403)

    def test_inline_edit_stamps_history_and_version(self):
        row = self._row()
        c = self.client_for("pm")
        r = c.post("/planning/status/edit/", json.dumps({"op": "update", "id": row.id, "version": 1, "field": "phase_status", "value": "procurment ongoing"}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        d = _j(r)
        self.assertEqual(d["row"]["phase_status"], "PROCUREMENT ONGOING")     # alias folded
        self.assertEqual(d["row"]["version"], 2)
        self.assertEqual(d["row"]["last_update_date"], timezone.localdate().isoformat())
        self.assertTrue(d["row"]["updated_by"])
        self.assertEqual(StatusHistory.objects.filter(row=row, field="phase_status").count(), 1)

    def test_stale_version_conflicts(self):
        row = self._row()
        c = self.client_for("pm")
        c.post("/planning/status/edit/", json.dumps({"op": "update", "id": row.id, "version": 1, "field": "notes", "value": "first"}), content_type="application/json")
        r = c.post("/planning/status/edit/", json.dumps({"op": "update", "id": row.id, "version": 1, "field": "notes", "value": "second"}), content_type="application/json")
        self.assertEqual(r.status_code, 409)
        self.assertIn("changed by", _j(r)["error"])
        self.assertEqual(StatusRow.objects.get(pk=row.pk).notes, "first")

    def test_complete_is_manual_and_reversible(self):
        row = self._row()
        c = self.client_for("pm")
        r = c.post("/planning/status/edit/", json.dumps({"op": "complete", "id": row.id, "version": 1, "completed": True}), content_type="application/json")
        self.assertTrue(_j(r)["row"]["completed"])
        r = c.post("/planning/status/edit/", json.dumps({"op": "complete", "id": row.id, "version": 2, "completed": False}), content_type="application/json")
        self.assertFalse(_j(r)["row"]["completed"])

    def test_create_duplicate_delete(self):
        c = self.client_for("pm")
        r = c.post("/planning/status/edit/", json.dumps({"op": "create", "fields": {"division": "040", "name": "New thing", "pm": "Sally Salaried"}}), content_type="application/json")
        d = _j(r)
        self.assertEqual(d["row"]["division"], "040")
        self.assertEqual(d["row"]["pm"], "Sally Salaried")
        self.assertEqual(d["row"]["source"], "manual")
        rid = d["row"]["id"]
        r = c.post("/planning/status/edit/", json.dumps({"op": "duplicate", "id": rid, "version": 1}), content_type="application/json")
        self.assertTrue(_j(r)["row"]["name"].endswith(" copy"))
        r = c.post("/planning/status/edit/", json.dumps({"op": "delete", "id": rid, "version": 1}), content_type="application/json")
        self.assertTrue(_j(r)["ok"])
        self.assertFalse(StatusRow.objects.filter(pk=rid).exists())

    def test_project_number_links_sl_project(self):
        row = self._row(project=None, project_number_raw="")
        c = self.client_for("pm")
        r = c.post("/planning/status/edit/", json.dumps({"op": "update", "id": row.id, "version": 1, "field": "project_number", "value": "990001"}), content_type="application/json")
        d = _j(r)
        self.assertEqual(d["row"]["project_number"], "990001")
        self.assertIsNotNone(d["row"]["project_id"])
        self.assertIsNotNone(d["row"]["enrich"])
        self.assertEqual(d["row"]["enrich"]["contract_value"], 9000000.0)

    def test_send_to_scheduler(self):
        """PS-07: the receiver exists (Phase E) → a PlanProject is created and the row is marked sent; without the
        receiver the endpoint says so and marks nothing."""
        row = self._row(start=date(2026, 9, 14), end=date(2026, 9, 25))
        r = self.client_for("pm").post("/planning/status/send/", json.dumps({"id": row.id}), content_type="application/json")
        try:
            from apps.scheduling.api import create_plan_project  # noqa: F401
            has_receiver = True
        except ImportError:
            has_receiver = False
        row.refresh_from_db()
        if has_receiver:
            self.assertEqual(r.status_code, 200, r.content)
            self.assertIsNotNone(row.sent_to_scheduler_at)
            self.assertIsNotNone(row.plan_project_id)
            payload = _j(r)["payload"]
            self.assertEqual((payload["hours_union"], payload["days"], payload["start"]), (8.0, [1, 1, 1, 1, 1, 0, 0], "2026-09-14"))
            from apps.scheduling.models import PlanProject
            self.assertEqual(PlanProject.objects.get(pk=row.plan_project_id).status_row_id, row.id)
        else:
            self.assertEqual(r.status_code, 409)
            self.assertIn("not built yet", _j(r)["message"])
            self.assertIsNone(row.sent_to_scheduler_at)

    def test_read_only_role_cannot_edit(self):
        row = self._row()
        r = self.client_for("norole").post("/planning/status/edit/", json.dumps({"op": "update", "id": row.id, "version": 1, "field": "notes", "value": "x"}), content_type="application/json")
        self.assertEqual(r.status_code, 403)

    def test_project_card_renders_and_is_silent_without_data(self):
        c = self.client_for("pm")
        html = c.get("/projects/990001/").content.decode()
        self.assertNotIn('id="production"', html)
        from apps.core.models import Project
        self._row(project=Project.objects.get(canonical_project_number="990001"))
        html = c.get("/projects/990001/").content.decode()
        self.assertIn('id="production"', html)
        self.assertIn("NEED TO SCHEDULE", html)


class PunchTests(AccessTestCase):
    def test_seed_add_edit_delete(self):
        StatusRow.objects.create(division="070", name="990001 SENTINEL TEST PROJECT", project_number_raw="990001", phase_status="WORK SCHEDULED", hours_left=10)
        self.assertEqual(loaders.seed_punch_projects_from_status("070"), 1)
        self.assertEqual(loaders.seed_punch_projects_from_status("070"), 0)     # idempotent, never deletes
        pp = PunchProject.objects.get(division="070")
        self.assertEqual(pp.code, "99-0001")
        c = self.client_for("pm")
        r = c.post("/planning/punch/edit/", json.dumps({"op": "item_add", "project_id": pp.id}), content_type="application/json")
        it = _j(r)["item"]
        self.assertEqual((it["bic"], it["critical"], it["active"]), ("PM", 3, False))
        r = c.post("/planning/punch/edit/", json.dumps({"op": "item_update", "id": it["id"], "version": 1, "fields": {"due_by": (timezone.localdate() - timedelta(days=3)).isoformat(), "critical": "5"}}), content_type="application/json")
        d = _j(r)["item"]
        self.assertEqual(d["critical"], 5)
        self.assertEqual(d["version"], 2)
        r = c.get("/planning/punch/?div=070&format=json")
        p = _j(r)["projects"][0]
        self.assertEqual(len(p["items"]), 1)
        self.assertEqual(c.get("/planning/punch/%d/" % pp.id).status_code, 200)
        self.assertEqual(c.get("/planning/punch/export/?div=070&fmt=csv").status_code, 200)
        self.assertEqual(c.get("/planning/punch/export/?div=070&fmt=xlsx").status_code, 200)
        r = c.post("/planning/punch/edit/", json.dumps({"op": "item_delete", "id": it["id"]}), content_type="application/json")
        self.assertTrue(_j(r)["ok"])
        self.assertEqual(PunchItem.objects.count(), 0)
        r = c.post("/planning/punch/edit/", json.dumps({"op": "project_add", "div": "070", "code": "260200", "title": "", "pm": "Unassigned"}), content_type="application/json")
        self.assertEqual(_j(r)["project"]["code"], "26-0200")
        r = c.post("/planning/punch/edit/", json.dumps({"op": "project_add", "div": "070", "code": "26-0200"}), content_type="application/json")
        self.assertEqual(r.status_code, 400)


class ApprovalTests(AccessTestCase):
    def _create(self, client, **extra):
        data = {"op": "create", "project_name": "Test job", "project_number": "990001", "kind": "BOM", "department": "AV", "notes": "please"}
        data.update(extra)
        return client.post("/planning/approval/edit/".replace("approval/", "approvals/"), data)

    def test_create_records_signed_in_requester_and_links_project(self):
        r = self._create(self.client_for("pm"), files=SimpleUploadedFile("spec.txt", b"hello"))
        self.assertEqual(r.status_code, 200, r.content)
        d = _j(r)["request"]
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["requested_by"], self.fx["accounts"]["pm"].display_name)
        self.assertEqual(d["project_url"], "/projects/990001/")
        self.assertEqual(d["n_files"], 1)
        att = ApprovalRequest.objects.get().attachments.get()
        self.assertEqual(self.client_for("pm").get("/planning/approvals/attachment/%d/" % att.id).status_code, 200)

    def test_non_approver_gets_403_and_approver_identity_is_recorded(self):
        self._create(self.client_for("pm"))
        req = ApprovalRequest.objects.get()
        r = self.client_for("pm").post("/planning/approvals/decide/", json.dumps({"id": req.id, "action": "approve", "version": 1}), content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(ApprovalRequest.objects.get().status, "pending")
        r = self.client_for("executive").post("/planning/approvals/decide/", json.dumps({"id": req.id, "action": "approve", "version": 1}), content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        d = _j(r)["request"]
        self.assertEqual(d["status"], "approved")
        self.assertEqual(d["decided_by"], self.fx["accounts"]["executive"].display_name)
        self.assertTrue(d["decided_at"])
        r = self.client_for("executive").post("/planning/approvals/decide/", json.dumps({"id": req.id, "action": "approve", "version": 2}), content_type="application/json")
        self.assertEqual(r.status_code, 409)      # already approved
        r = self.client_for("dm070").post("/planning/approvals/decide/", json.dumps({"id": req.id, "action": "reopen", "version": 2}), content_type="application/json")
        self.assertEqual(_j(r)["request"]["status"], "reopened")
        self.assertEqual(_j(r)["request"]["decided_by"], "")

    def test_no_approve_button_without_capability(self):
        self._create(self.client_for("pm"))
        html = self.client_for("pm").get("/planning/approvals/").content.decode()
        self.assertIn("canApprove: false", html)
        html = self.client_for("executive").get("/planning/approvals/").content.decode()
        self.assertIn("canApprove: true", html)

    def test_export_and_overdue_filter(self):
        c = self.client_for("pm")
        self._create(c, needed_by=(timezone.localdate() - timedelta(days=1)).isoformat())
        self._create(c, project_name="Other", kind="Labor")
        r = c.get("/planning/approvals/export/?filter=overdue")
        body = r.content.decode()
        self.assertEqual(body.count("\r\n"), 2)     # header + one row
        self.assertIn("Test job", body)
        r = c.get("/planning/approvals/export/?type=Labor")
        self.assertIn("Other", r.content.decode())

    def test_api_create(self):
        from apps.core.models import Project
        p = Project.objects.get(canonical_project_number="990001")
        req = api.create_approval_request(p.id, "labor", None, date(2026, 10, 1), "Field", "n", estimate_id=42)
        self.assertEqual((req.kind, req.status, req.estimate_id, req.project_id), ("Labor", "pending", 42, p.id))
        self.assertIn("990001", req.project_name)
        with self.assertRaises(ValueError):
            api.create_approval_request(None, "BOM", None, None, "", "")


class ImporterTests(AccessTestCase):
    def test_json_rows_merge_rules(self):
        rows = [{"id": "active-070-1-990001", "div": "070", "task_num": 1, "name": "990001 SENTINEL TEST PROJECT", "status": "PROCURMENT ONGOING", "status2": "",
                 "pm": "Salaried/Field", "start": "2026-09-01", "due": "2026-10-01", "hoursLeft": 12, "proj_num": "990001", "lastUpdate": "2026-09-01", "updateNotes": "n", "pct": 0.5},
                {"id": "x", "div": "070", "name": "999999 UNKNOWN JOB", "pm": "NOBODY", "hoursLeft": 1, "proj_num": "999999", "completed": True, "completionTouched": True},
                {"id": "y", "div": "020", "name": "wrong division"}]
        rep = loaders.import_status_rows(rows, "test", apply=False)
        self.assertEqual(rep.counts["new rows"], 2)
        self.assertEqual(rep.counts["new rows imported as completed"], 1)
        self.assertEqual(rep.counts["skipped (no name / bad division)"], 1)
        self.assertIn("999999", rep.unmatched_projects)
        self.assertIn("NOBODY", rep.unknown_pms)
        self.assertEqual(StatusRow.objects.count(), 0)          # dry run
        loaders.import_status_rows(rows, "test", apply=True)
        r = StatusRow.objects.get(project_number_raw="990001")
        self.assertEqual((r.phase_status, r.pm.canonical_name, r.pm2.canonical_name, float(r.percent), float(r.hours_left)), ("PROCUREMENT ONGOING", "Sally Salaried", "Frank Field", 50.0, 12.0))
        self.assertIsNotNone(r.project_id)
        self.assertTrue(StatusRow.objects.get(source_key="x").completed)
        # a re-import refreshes the open imported row but never touches completed / manual rows
        rows[0]["hoursLeft"] = 20
        rows[1]["name"] = "changed"
        rep = loaders.import_status_rows(rows, "test", apply=True)
        self.assertEqual(rep.counts["existing rows refreshed"], 1)
        self.assertEqual(rep.counts["existing completed / manual rows left alone"], 1)
        self.assertEqual(float(StatusRow.objects.get(project_number_raw="990001").hours_left), 20.0)
        self.assertEqual(StatusRow.objects.get(source_key="x").name, "999999 UNKNOWN JOB")

    def test_punch_seed_import(self):
        seed = {"070": [{"code": "99-0001", "title": "99-0001 Sentinel", "pm": "Salaried", "items": [{"active": False, "date_entered": "2026-06-03", "description": "fix", "bic": "PM", "critical": 5, "due_by": None, "date_completed": None, "assigned": "", "engineer_signoff": "", "verified": ""}]}]}
        rep = loaders.import_punch_projects(seed, "seed", apply=True)
        self.assertEqual(rep.counts["new projects"], 1)
        pp = PunchProject.objects.get()
        self.assertEqual((pp.items.count(), pp.pm.canonical_name, pp.project.canonical_project_number), (1, "Sally Salaried", "990001"))
        rep = loaders.import_punch_projects(seed, "seed", apply=True)
        self.assertEqual(rep.counts["existing projects (items kept)"], 1)
        self.assertEqual(PunchItem.objects.count(), 1)

    def test_workbook_rows(self):
        import openpyxl, tempfile, os
        wb = openpyxl.Workbook(); ws = wb.active; ws.title = "070 Schedule"
        ws.append(["Master Schedule"]); ws.append([])
        ws.append(["Task Name", "Phase Complete Status", "Equipment Complete Status V2", "PM", "Critical Stop Date", "Union Hours Remaining", "% Complete", "Update Notes"])
        ws.append(["990001 SENTINEL TEST PROJECT", "PROJECT COMPLETE", "", "SALARIED", date(2026, 10, 1), 3, "100%", "done"])
        ws.append(["", "x", "", "", None, 0, "", ""])
        path = os.path.join(tempfile.mkdtemp(), "070 Master Schedule.xlsx"); wb.save(path)
        rows, notes, mappings = loaders.workbook_to_rows(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["div"], rows[0]["proj_num"], rows[0]["pct"], rows[0]["hoursLeft"], rows[0]["completed"]), ("070", "990001", 100.0, 3.0, False))
        rep = loaders.import_master_schedule(path, apply=True)
        self.assertEqual(rep.counts["new rows"], 1)
        self.assertFalse(StatusRow.objects.get().completed)      # Excel status / percent never complete a row
