"""T8 — the Permissions page (docs/07 "Permissions page"): superadmin-only, decisions persist, overrides change the live
rule (and only within the guardrails), resets return to the code default."""

from apps.access import overrides, registry
from apps.access.models import AccessDecision, AccessOverride, AuditEvent

from .base import AccessTestCase

URL, UPD = "/access/permissions/", "/access/permissions/update/"


class PermissionsPageTests(AccessTestCase):
    def test_only_superadmin_sees_it_and_it_lists_everything(self):
        html = self.client_for("superadmin").get(URL).content.decode()
        for needle in ("Sign-in &amp; accounts", "Capabilities", "Roles", "Page rules", "In-page redactions", "finance.view", "project_detail", "acc.margins", "Ready to deploy"):
            self.assertIn(needle, html, needle)
        for principal in ("executive", "finance", "pm", "hradmin", "sales", "norole"):
            self.assertEqual(self.client_for(principal).get(URL).status_code, 404, principal)
            self.assertEqual(self.client_for(principal).post(UPD, {"action": "confirm", "key": "view:project_list"}).status_code, 404, principal)
        self.assertEqual(self.client_for("disabled").get(URL).status_code, 403)

    def test_the_decisions_are_items_roles_and_areas_only(self):
        """Owner, 2026-09-17: 220 checkboxes was excessive — capabilities and in-page gates are consequences of the role and
        page-rule decisions, so they are reference. Every normal capability must belong to exactly one area."""
        from apps.access import design
        d = design.build()
        self.assertEqual(d["total"], len(d["items"]) + len(d["roles"]) + len(d["areas"]))
        self.assertLessEqual(d["total"], 40)
        normal = {c for c, m in registry.CAPABILITIES.items() if m["tier"] == registry.NORMAL}
        self.assertEqual(set(design.CAP_AREA), normal, "every normal capability sits in exactly one AREA_CAPS area")
        self.assertEqual(len(design.CAP_AREA), sum(len(v) for v in design.AREA_CAPS.values()), "no capability listed twice")
        finance = next(a for a in d["areas"] if a["key"] == "finance")
        self.assertTrue(any(g["caps"] == ["finance.view"] and g["n"] >= 15 for g in finance["rules"]), "finance pages group into one finance.view rule")
        # an area decision goes stale when any one of its pages changes
        c = self.client_for("superadmin")
        c.post(UPD, {"action": "confirm", "key": "area:finance"})
        self.assertEqual(design.decorate("area:finance", "area", "finance", design.decisions())["status"], "confirmed")
        overrides.set_view_rule("finance_daily", ["projects.view"], False, None)
        self.assertEqual(design.decorate("area:finance", "area", "finance", design.decisions())["status"], "stale")

    def test_confirm_note_and_undo_persist(self):
        c = self.client_for("superadmin")
        r = c.post(UPD, {"action": "confirm", "key": "view:project_list", "anchor": "v-project_list"})
        self.assertEqual(r.status_code, 302)
        d = AccessDecision.objects.get(key="view:project_list")
        self.assertEqual(d.status, "confirmed"); self.assertEqual(d.snapshot["caps"], ["projects.view"])
        c.post(UPD, {"action": "note", "key": "view:project_list", "note": "PMs need this daily"})
        self.assertEqual(AccessDecision.objects.get(key="view:project_list").note, "PMs need this daily")
        c.post(UPD, {"action": "unconfirm", "key": "view:project_list"})
        self.assertEqual(AccessDecision.objects.get(key="view:project_list").status, "open")
        c.post(UPD, {"action": "confirm_many", "keys": ["cap:margins.view", "role:finance", "item:auth_mode"]})
        self.assertEqual(AccessDecision.objects.filter(status="confirmed").count(), 3)
        self.assertTrue(AuditEvent.objects.filter(kind="write_action", meta__meta_action="permissions.confirm").exists())

    def test_view_override_changes_the_live_rule_and_resets(self):
        pm = self.client_for("pm")
        self.assertEqual(pm.get("/finance/daily/").status_code, 403)          # code default: finance.view
        sup = self.client_for("superadmin")
        r = sup.post(UPD, {"action": "set_view", "view": "finance_daily", "caps": ["projects.view"], "reason": "trial"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(registry.rule_for("finance_daily")["caps"], ("projects.view",))
        self.assertEqual(pm.get("/finance/daily/").status_code, 200)          # the override is live
        self.assertEqual(AccessDecision.objects.get(key="view:finance_daily").status, "confirmed")
        self.assertIn(("view", "finance_daily"), overrides.changed_keys())
        sup.post(UPD, {"action": "reset_view", "view": "finance_daily"})
        self.assertFalse(AccessOverride.objects.filter(kind="view", key="finance_daily").exists())
        self.assertEqual(pm.get("/finance/daily/").status_code, 403)
        self.assertEqual(AccessDecision.objects.get(key="view:finance_daily").status, "open")

    def test_superadmin_cap_on_a_page_forces_concealment(self):
        sup = self.client_for("superadmin")
        sup.post(UPD, {"action": "set_view", "view": "finance_daily", "caps": ["finance.view", "ops.view"]})
        rule = registry.rule_for("finance_daily")
        self.assertTrue(rule["conceal"])
        self.assertEqual(self.client_for("finance").get("/finance/daily/").status_code, 404)

    def test_fixed_pages_and_empty_rules_are_refused(self):
        sup = self.client_for("superadmin")
        sup.post(UPD, {"action": "set_view", "view": "login", "caps": ["finance.view"]})
        self.assertFalse(AccessOverride.objects.filter(key="login").exists())
        sup.post(UPD, {"action": "set_view", "view": "finance_daily", "caps": []})
        self.assertFalse(AccessOverride.objects.filter(key="finance_daily").exists())
        self.assertEqual(self.client_for("finance").get("/finance/daily/").status_code, 200)

    def test_role_override_changes_effective_caps_and_never_gains_superadmin_tier(self):
        pm = self.client_for("pm")
        self.assertEqual(pm.get("/finance/daily/").status_code, 403)
        sup = self.client_for("superadmin")
        caps = sorted(registry.role_caps("project_manager") | {"finance.view"})
        sup.post(UPD, {"action": "set_role", "role": "project_manager", "caps": caps})
        self.assertIn("finance.view", registry.role_caps_effective("project_manager"))
        self.assertEqual(pm.get("/finance/daily/").status_code, 200)
        # a superadmin-tier capability is refused outright, the role unchanged
        sup.post(UPD, {"action": "set_role", "role": "project_manager", "caps": caps + ["ops.view"]})
        self.assertNotIn("ops.view", registry.role_caps_effective("project_manager"))
        self.assertEqual(pm.get("/ratings/").status_code, 404)
        sup.post(UPD, {"action": "reset_role", "role": "project_manager"})
        self.assertEqual(registry.role_caps_effective("project_manager"), registry.role_caps("project_manager"))
        self.assertEqual(pm.get("/finance/daily/").status_code, 403)

    def test_confirmation_goes_stale_when_the_rule_changes(self):
        sup = self.client_for("superadmin")
        sup.post(UPD, {"action": "confirm", "key": "role:finance"})
        from apps.access import design
        self.assertEqual(design.decorate("role:finance", "role", "finance", design.decisions())["status"], "confirmed")
        overrides.set_role_caps("finance", sorted(registry.role_caps("finance") - {"sales010.view"}), None)
        self.assertEqual(design.decorate("role:finance", "role", "finance", design.decisions())["status"], "stale")

    def test_writes_blocked_while_impersonating(self):
        sup = self.client_for("superadmin")
        sup.post("/access/view-as/", {"account_id": self.fx["accounts"]["pm"].pk})
        r = sup.post(UPD, {"action": "confirm", "key": "view:project_list"})
        # while viewing as a PM the request carries the PM's capabilities, so the concealed endpoint answers 404 before the
        # write-block's 403 could — either way nothing is written
        self.assertIn(r.status_code, (403, 404))
        self.assertFalse(AccessDecision.objects.filter(key="view:project_list").exists())
