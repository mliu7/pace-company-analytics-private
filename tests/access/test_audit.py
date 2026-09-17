"""T9 — audit trail correctness."""

from apps.access.models import AuditEvent

from .base import AccessTestCase


class AuditTests(AccessTestCase):
    def _count(self, kind):
        return AuditEvent.objects.filter(kind=kind).count()

    def test_page_views_recorded_with_actor(self):
        before = self._count("page_view")
        self.client_for("pm").get("/projects/")
        self.assertEqual(self._count("page_view"), before + 1)
        e = AuditEvent.objects.filter(kind="page_view").latest("at")
        self.assertEqual(e.actor, self.fx["accounts"]["pm"])
        self.assertEqual(e.view_name, "project_list")

    def test_denied_recorded(self):
        before = self._count("denied")
        self.client_for("pm").get("/access/console/usage/")
        self.assertEqual(self._count("denied"), before + 1)

    def test_role_changes_audited(self):
        sup = self.client_for("superadmin")
        pk = self.fx["accounts"]["norole"].pk
        sup.post("/access/console/people/%d/" % pk, {"action": "grant_role", "role": "executive"})
        self.assertEqual(AuditEvent.objects.filter(kind="role_granted", target_id=pk, capability_or_role="executive").count(), 1)
        sup.post("/access/console/people/%d/" % pk, {"action": "revoke_role", "role": "executive"})
        self.assertEqual(AuditEvent.objects.filter(kind="role_revoked", target_id=pk).count(), 1)

    def test_impersonation_audited_with_acting_as(self):
        sup = self.client_for("superadmin")
        sup.post("/access/view-as/", {"account_id": self.fx["accounts"]["pm"].pk})
        sup.get("/projects/")
        e = AuditEvent.objects.filter(kind="page_view").latest("at")
        self.assertEqual(e.actor, self.fx["accounts"]["superadmin"])
        self.assertEqual(e.acting_as, self.fx["accounts"]["pm"])
        self.assertEqual(self._count("impersonation_start"), 1)

    def test_account_lifecycle_audited(self):
        sup = self.client_for("superadmin")
        r = sup.post("/access/console/people/new/", {"email": "newbie@t.local", "display_name": "New Person"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self._count("account_created"), 1)
