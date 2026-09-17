"""T4 — superadmin-tier concealment in the console and delegation limits."""

from apps.access.models import ExtraGrant, RoleAssignment

from .base import AccessTestCase


class ConcealTests(AccessTestCase):
    def test_console_shows_no_superadmin_traces_to_hr(self):
        c = self.client_for("hradmin")
        html = c.get("/access/console/people/").content.decode().lower()
        for word in ("superadmin", "ratings.view", "insights.view", "ops.view", "impersonate", "grants.manage", "extra grant"):
            self.assertNotIn(word, html, word)
        pk = self.fx["accounts"]["pm"].pk
        html2 = c.get("/access/console/people/%d/" % pk).content.decode().lower()
        for word in ("superadmin", "extra grant", "impersonate", "ratings.view"):
            self.assertNotIn(word, html2, word)

    def test_hr_cannot_grant_hidden_caps_or_admin_role(self):
        c = self.client_for("hradmin")
        pk = self.fx["accounts"]["pm"].pk
        r = c.post("/access/console/people/%d/" % pk, {"action": "grant_extra", "capability": "ratings.view"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(ExtraGrant.objects.filter(account_id=pk).exists())
        r2 = c.post("/access/console/people/%d/" % pk, {"action": "grant_role", "role": "permission_admin"})
        self.assertEqual(r2.status_code, 404)
        self.assertFalse(RoleAssignment.objects.filter(account_id=pk, role="permission_admin").exists())
        r3 = c.post("/access/console/people/%d/" % pk, {"action": "toggle_superadmin"})
        self.assertEqual(r3.status_code, 404)
        self.fx["accounts"]["pm"].refresh_from_db()
        self.assertFalse(self.fx["accounts"]["pm"].is_superadmin)

    def test_superadmin_can_make_several_permission_admins(self):
        """Owner, 2026-09-11: Permission Admin is an ordinary role he may give to as many people as he likes."""
        sup = self.client_for("superadmin")
        for principal in ("pm", "finance"):
            pk = self.fx["accounts"][principal].pk
            sup.post("/access/console/people/%d/" % pk, {"action": "grant_role", "role": "permission_admin"})
            self.assertTrue(RoleAssignment.objects.filter(account_id=pk, role="permission_admin").exists(), principal)
        self.assertEqual(RoleAssignment.objects.filter(role="permission_admin").count(), 3)   # + the HR fixture
        self.assertEqual(self.client_for("pm").get("/access/console/people/").status_code, 200)

    def test_hr_audit_view_excludes_superadmin_events(self):
        sup = self.client_for("superadmin")
        pk = self.fx["accounts"]["pm"].pk
        sup.post("/access/console/people/%d/" % pk, {"action": "grant_extra", "capability": "ops.view"})
        sup.post("/access/console/people/%d/" % pk, {"action": "revoke_extra", "capability": "ops.view"})
        html = self.client_for("hradmin").get("/access/console/audit/").content.decode()
        self.assertNotIn("ops.view", html)
        self.assertNotIn("extra", html.lower())
        self.assertIn("ops.view", sup.get("/access/console/audit/all/").content.decode())

    def test_private_capability_cannot_be_granted_on_shared_app(self):
        sup = self.client_for("superadmin")
        pk = self.fx["accounts"]["pm"].pk
        response = sup.post("/access/console/people/%d/" % pk, {"action": "grant_extra", "capability": "ratings.view"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ExtraGrant.objects.filter(account_id=pk, capability="ratings.view").exists())
        self.assertEqual(self.client_for("pm").get("/ratings/").status_code, 404)

    def test_disabled_account_locked_out_everywhere(self):
        c = self.client_for("disabled")
        for url in ["/", "/projects/", "/access/console/people/"]:
            self.assertEqual(c.get(url).status_code, 403, url)
