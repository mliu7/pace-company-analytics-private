"""Planner boards — permission on the JSON endpoint and the page, the payload shape, the combined AV board, and the
sync contract other apps read (apps/planner/sync.py). Uses the access-suite fixtures (one account per principal)."""

from datetime import date, timedelta

from django.utils import timezone

from apps.planner import sync
from apps.planner.models import PlannerBucket, PlannerGroup, PlannerPlan, PlannerTask, PlannerTaskHistory
from tests.access.base import AccessTestCase

URL, DATA = "/planning/planner/", "/planning/planner/data/"


def seed(fx):
    grp = PlannerGroup.objects.create(group_id="g1", name="AV Division", mail="TEST@example.invalid", tenant_domain="pace-systems.com", site_path="/sites/TEST")
    fab = PlannerPlan.objects.create(plan_id="p-fab", group=grp, title="1. Fabrication", board_kind="workflow", board_kind_auto="workflow", av_stage="fabrication",
                                     category_descriptions={"category1": "Rush"}, task_count=2, open_count=1, last_synced=timezone.now())
    prog = PlannerPlan.objects.create(plan_id="p-prog", group=grp, title="AV Programming List", board_kind="workflow", board_kind_auto="workflow", av_stage="programming",
                                      task_count=1, open_count=1, last_synced=timezone.now())
    b1 = PlannerBucket.objects.create(plan=fab, bucket_id="b1", name="Fabrication stage", order_hint="a")
    b2 = PlannerBucket.objects.create(plan=fab, bucket_id="b2", name="Shipped", order_hint="b")
    b3 = PlannerBucket.objects.create(plan=prog, bucket_id="b3", name="Executing", order_hint="a")
    t1 = PlannerTask.objects.create(plan=fab, bucket=b1, task_id="t1", title="990001 Sentinel test project racks", percent=50, priority=1,
                                    due=date.today() - timedelta(days=3), labels=["Rush"], label_keys=["category1"], assignee_ids=["u1"], assignee_names=["Sally Salaried"],
                                    checklist_done=1, checklist_total=3, project=fx["project"], project_rule="title:digits6", last_change=timezone.now())
    t2 = PlannerTask.objects.create(plan=fab, bucket=b2, task_id="t2", title="Old shipped job", percent=100, completed_at=timezone.now(), project=None)
    t3 = PlannerTask.objects.create(plan=prog, bucket=b3, task_id="t3", title="990001 programming", percent=0, project=fx["project"], project_rule="title:digits6")
    PlannerTaskHistory.objects.create(task=t1, changed_at=timezone.now(), field="percent", old="0", new="50")
    return {"group": grp, "fab": fab, "prog": prog, "t1": t1, "t2": t2, "t3": t3}


class PermissionTests(AccessTestCase):
    """planning.view holders (executive, DM, finance, PM) get the JSON; everyone else is refused (403, not concealed)."""

    def test_json_permissions(self):
        for principal, code in (("superadmin", 200), ("executive", 200), ("dm070", 200), ("finance", 200), ("pm", 200),
                                ("hradmin", 403), ("norole", 403), ("sales", 403), ("disabled", 403)):
            with self.subTest(principal=principal):
                self.assertEqual(self.client_for(principal).get(DATA).status_code, code)

    def test_page_permissions(self):
        for principal, code in (("pm", 200), ("norole", 403), ("sales", 403)):
            with self.subTest(principal=principal):
                self.assertEqual(self.client_for(principal).get(URL).status_code, code)

    def test_page_says_not_connected_when_empty(self):
        html = self.client_for("pm").get(URL).content.decode()
        self.assertIn("Planner not connected", html)


class PayloadTests(AccessTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.seed = seed(cls.fx)

    def test_picker_json_and_page(self):
        j = self.client_for("pm").get(DATA).json()
        self.assertEqual(j["picker"]["n_plans"], 2)
        self.assertEqual(j["picker"]["av_stages"], 2)
        self.assertEqual([p["av_stage"] for p in j["picker"]["av"]], ["fabrication", "programming"])
        html = self.client_for("pm").get(URL).content.decode()
        self.assertIn("1. Fabrication", html)
        self.assertIn("AV production", html)
        self.assertNotIn("Planner not connected", html)

    def test_plan_payload(self):
        j = self.client_for("pm").get(DATA + "?plan=p-fab&since=30").json()
        self.assertEqual(j["plan"]["title"], "1. Fabrication")
        self.assertEqual(j["plan"]["labels"]["category1"]["label"], "Rush")
        self.assertEqual([b["name"] for b in j["buckets"]], ["Fabrication stage", "Shipped"])
        self.assertEqual(len(j["tasks"]), 2)
        t1 = next(t for t in j["tasks"] if t["id"] == "t1")
        self.assertEqual(t1["state"], "in_progress")
        self.assertEqual(t1["priority_label"], "Urgent")
        self.assertEqual(t1["label_colors"][0]["bg"], "#fbddf0")           # Pink = category1
        self.assertEqual(t1["project"]["cpn"], "990001")
        self.assertEqual(t1["project"]["division"], "070")
        self.assertEqual(t1["project"]["cv"], 9000000.0)                   # projects.view -> money shown
        self.assertTrue(t1["url"].startswith("https://tasks.office.com/pace-systems.com/Home/Task/t1"))
        self.assertEqual(j["history"][0]["field"], "percent")
        self.assertEqual(j["since"], 30)

    def test_gp_never_in_payload(self):
        """The job figures on a card are CV / billed / hours — never GP (a margins figure)."""
        body = self.client_for("pm").get(DATA + "?plan=p-fab").content.decode()
        self.assertNotIn("7,654,321", body)
        self.assertNotIn("7654321", body)

    def test_combined_av_board(self):
        j = self.client_for("pm").get(DATA + "?plan=av").json()
        self.assertTrue(j["plan"]["combined"])
        self.assertEqual([b["name"] for b in j["buckets"]], ["Fabrication / Shipping · 1. Fabrication", "Programming / Commissioning · AV Programming List"])
        self.assertEqual({t["bucket_id"] for t in j["tasks"]}, {"p-fab", "p-prog"})   # columns are plans
        self.assertEqual(next(t for t in j["tasks"] if t["id"] == "t3")["bucket"], "Executing")

    def test_unknown_plan_is_404(self):
        self.assertEqual(self.client_for("pm").get(DATA + "?plan=nope").status_code, 404)

    def test_bad_since_falls_back(self):
        self.assertEqual(self.client_for("pm").get(DATA + "?plan=p-fab&since=abc").json()["since"], 14)


class SyncContractTests(AccessTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.seed = seed(cls.fx)

    def test_statuses_for(self):
        out = sync.planner_statuses_for(["990001", "990002"])
        self.assertEqual(list(out), ["990001"])                            # no tasks -> absent, never []
        rows = out["990001"]
        self.assertEqual([r["task_id"] for r in rows], ["t1", "t3"])
        r = rows[0]
        for key in ("plan", "plan_id", "board_kind", "group", "bucket", "task_id", "task_title", "task_url", "labels", "label_keys", "label_colors",
                    "percent", "state", "priority", "priority_label", "start", "due", "completed_at", "assignees", "checklist", "quote_ref", "match_rule", "last_change"):
            self.assertIn(key, r)
        self.assertEqual(r["plan"], "AV Division › 1. Fabrication")
        self.assertEqual(r["labels"], ["Rush"])
        self.assertEqual(r["label_colors"][0]["name"], "Pink")
        self.assertEqual(r["checklist"], {"done": 1, "total": 3})

    def test_open_tasks_exclude_complete(self):
        self.seed["t3"].percent = 100
        self.seed["t3"].save()
        self.assertEqual([r["task_id"] for r in sync.open_tasks_for_project("990001")], ["t1"])
        self.assertEqual(sync.open_tasks_for_project("990002"), [])
