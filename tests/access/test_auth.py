"""T8 — authentication edges: pre-provisioning, unknown/disabled logins, dev-bypass interlock."""

from django.contrib.auth.models import User
from django.test import Client, override_settings

from apps.access.models import Account, AuditEvent

from .base import AccessTestCase


class AuthTests(AccessTestCase):
    def test_authenticated_user_without_account_gets_denied_page(self):
        u = User.objects.create_user("stranger@t.local")
        c = Client(); c.force_login(u)
        r = c.get("/projects/")
        self.assertEqual(r.status_code, 403)
        self.assertIn(b"no Pace Company Analytics account", r.content)

    def test_disabled_account_denied_and_audited(self):
        before = AuditEvent.objects.filter(kind="login_denied").count()
        self.client_for("disabled").get("/projects/")
        self.assertGreater(AuditEvent.objects.filter(kind="login_denied").count(), before)

    @override_settings(PCA_AUTH_MODE="dev", DEBUG=False)
    def test_dev_bypass_requires_debug(self):
        c = Client()
        r = c.get("/projects/")
        self.assertEqual(r.status_code, 302)   # no auto-login without DEBUG: must bounce to login
        self.assertTrue(r.headers["Location"].startswith("/access/login/"))
        self.assertFalse(Account.objects.filter(email="dev@pca.local").exists())

    @override_settings(PCA_AUTH_MODE="dev", DEBUG=True)
    def test_dev_bypass_works_on_localhost_debug(self):
        c = Client()
        self.assertEqual(c.get("/projects/").status_code, 200)
        self.assertTrue(Account.objects.get(email="dev@pca.local").is_superadmin)

    def test_oidc_backend_matches_preprovisioned_only(self):
        from apps.access.oidc import PCAOIDCBackend
        b = PCAOIDCBackend.__new__(PCAOIDCBackend)   # no OIDC config needed for these methods
        claims = {"email": "T-EXEC@t.local", "oid": "11111111-1111-4111-8111-111111111111"}
        users = b.filter_users_by_claims(claims)
        self.assertEqual(users.count(), 1)
        self.assertEqual(b.filter_users_by_claims({"email": "nobody@t.local", "oid": "22222222-2222-4222-8222-222222222222"}).count(), 0)
        self.assertEqual(b.filter_users_by_claims({"email": "t-dis@t.local", "oid": "33333333-3333-4333-8333-333333333333"}).count(), 0)  # disabled
        self.assertIsNone(b.create_user({"email": "x@t.local"}))
