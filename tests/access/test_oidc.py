"""Browser flow with real signed test tokens; only Entra network calls are mocked."""
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import resolve
from requests import ConnectionError

from apps.access.models import Account, AuditEvent
from apps.access.oidc import PCAOIDCBackend

TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
OWNER = "33333333-3333-4333-8333-333333333333"
OTHER = "44444444-4444-4444-8444-444444444444"
ISSUER = "https://login.microsoftonline.com/%s/v2.0" % TENANT


@override_settings(
    PCA_AUTH_MODE="sso", ROOT_URLCONF="tests.access.oidc_urls",
    AUTHENTICATION_BACKENDS=["apps.access.oidc.PCAOIDCBackend"],
    OIDC_RP_CLIENT_ID=CLIENT, OIDC_RP_CLIENT_SECRET="synthetic-test-secret",
    OIDC_TENANT_ID=TENANT, OIDC_OP_ISSUER=ISSUER,
    OIDC_OP_TOKEN_ENDPOINT=ISSUER + "/token", OIDC_OP_USER_ENDPOINT=ISSUER + "/userinfo",
    OIDC_OP_JWKS_ENDPOINT=ISSUER + "/keys", OIDC_OP_AUTHORIZATION_ENDPOINT=ISSUER + "/authorize",
    OIDC_RP_SIGN_ALGO="RS256", OIDC_RP_SCOPES="openid email profile", OIDC_CREATE_USER=False,
    OIDC_USE_PKCE=True, OIDC_AUTHENTICATION_CALLBACK_URL="oidc:oidc_authentication_callback",
    OIDC_CALLBACK_CLASS="apps.access.oidc.PCAOIDCCallbackView",
    SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
)
class OIDCFlowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.owner = Account.objects.create(
            email="owner@example.invalid", display_name="Test Owner", is_superadmin=True,
            entra_object_id=OWNER,
        )

    def begin(self, next_url="/"):
        response = self.client.get("/oidc/authenticate/", {"next": next_url}, secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(ISSUER + "/authorize?"))
        params = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(params["redirect_uri"], ["https://testserver/oidc/callback/"])
        self.assertEqual(params["client_id"], [CLIENT])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["response_type"], ["code"])
        return params

    def callback(self, params, changes=None, remove=(), key=None):
        now = int(time.time())
        claims = dict(iss=ISSUER, aud=CLIENT, tid=TENANT, sub="test-subject", oid=OWNER,
                      preferred_username=self.owner.email, nonce=params["nonce"][0],
                      iat=now, nbf=now-1, exp=now+300)
        claims.update(changes or {})
        for name in remove:
            claims.pop(name)
        token = jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": "test-key"})
        with patch.object(PCAOIDCBackend, "get_token", return_value={"id_token": token, "access_token": "test-access"}) as exchange, patch.object(PCAOIDCBackend, "retrieve_matching_jwk", return_value=self.key.public_key()):
            response = self.client.get("/oidc/callback/", {"code": "synthetic-auth-code", "state": params["state"][0]}, secure=True)
        self.assertIn("code_verifier", exchange.call_args.args[0])
        return response

    def test_login_page_and_oidc_namespace_are_reachable_anonymously(self):
        self.assertEqual(resolve("/oidc/callback/").namespace, "oidc")
        response = self.client.get("/", secure=True)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response["Location"], secure=True)
        self.assertContains(response, "Sign in with Microsoft")
        self.assertContains(response, "/oidc/authenticate/")
        self.begin()

    def test_owner_signs_in_and_can_manage_access(self):
        response = self.callback(self.begin("/access/console/people/"))
        self.assertEqual(response["Location"], "/access/console/people/")
        self.assertEqual(self.client.get(response["Location"], secure=True).status_code, 200)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.user.is_superuser)
        self.assertTrue(self.owner.user.is_staff)
        self.assertFalse(self.owner.user.has_usable_password())
        self.assertEqual(Account.objects.count(), 1)
        self.assertTrue(AuditEvent.objects.filter(kind="login", actor=self.owner).exists())
        self.assertTrue(self.client.cookies["sessionid"]["secure"])
        self.assertTrue(self.client.cookies["sessionid"]["httponly"])

    def test_unapproved_same_tenant_user_cannot_sign_in_or_create_account(self):
        response = self.callback(self.begin(), {"oid": OTHER, "preferred_username": "unapproved@example.invalid"})
        self.assertEqual(response["Location"], "/access/denied/?why=unknown")
        self.assertEqual(self.client.get(response["Location"], secure=True).status_code, 403)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(Account.objects.count(), 1)
        self.assertEqual(User.objects.count(), 0)
        self.assertTrue(AuditEvent.objects.filter(kind="login_denied").exists())

    def test_owner_email_with_different_object_id_is_denied(self):
        response = self.callback(self.begin(), {"oid": OTHER})
        self.assertEqual(response["Location"], "/access/denied/?why=unknown")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_disabled_account_is_denied(self):
        self.owner.status = "disabled"
        self.owner.save()
        response = self.callback(self.begin())
        self.assertEqual(response["Location"], "/access/denied/?why=disabled")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_django_user_is_denied_and_not_logged_as_success(self):
        self.owner.user = User.objects.create_user(self.owner.email, is_active=False)
        self.owner.save()
        response = self.callback(self.begin())
        self.assertEqual(response["Location"], "/access/denied/?why=disabled")
        self.assertFalse(AuditEvent.objects.filter(kind="login").exists())

    def test_new_preapproved_user_binds_identity_without_admin_rights(self):
        approved = Account.objects.create(email="approved@example.invalid", display_name="Approved User")
        response = self.callback(self.begin(), {"oid": OTHER, "preferred_username": "APPROVED@example.invalid"})
        self.assertEqual(response["Location"], "/")
        approved.refresh_from_db()
        self.assertEqual(approved.entra_object_id, UUID(OTHER))
        self.assertFalse(approved.is_superadmin)
        self.assertFalse(approved.user.is_superuser)
        self.assertEqual(self.client.get("/access/console/people/", secure=True).status_code, 403)
        self.assertEqual(self.client.get("/", secure=True)["Location"], "/about/")

    def test_guest_mail_alias_cannot_match_preapproved_signin_name(self):
        Account.objects.create(email="approved@example.invalid", display_name="Approved User")
        response = self.callback(self.begin(), {"oid": OTHER, "preferred_username": "guest_example.invalid#EXT#@tenant.invalid", "email": "approved@example.invalid"})
        self.assertEqual(response["Location"], "/access/denied/?why=unknown")

    def test_bound_identity_survives_email_change(self):
        response = self.callback(self.begin(), {"preferred_username": "renamed@example.invalid"})
        self.assertEqual(response["Location"], "/")
        self.assertIn("_auth_user_id", self.client.session)

    def test_wrong_audience_issuer_tenant_nonce_expiry_and_required_claims_are_denied(self):
        for changes in ({"aud": OTHER}, {"iss": "https://other.invalid"}, {"tid": OTHER},
                        {"nonce": "wrong"}, {"exp": int(time.time())-60}, {"nbf": int(time.time())+300}):
            with self.subTest(changes=changes):
                response = self.callback(self.begin(), changes)
                self.assertEqual(response["Location"], "/access/denied/?why=signin")
                self.assertNotIn("_auth_user_id", self.client.session)
        for name in ("exp", "sub", "oid", "nonce", "aud", "tid", "iss"):
            with self.subTest(missing=name):
                response = self.callback(self.begin(), remove=(name,))
                self.assertEqual(response["Location"], "/access/denied/?why=signin")
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_bad_signature_is_denied(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        response = self.callback(self.begin(), key=other_key)
        self.assertEqual(response["Location"], "/access/denied/?why=signin")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_missing_state_cannot_reach_token_exchange(self):
        with patch.object(PCAOIDCBackend, "get_token") as exchange:
            response = self.client.get("/oidc/callback/", {"code": "test", "state": "forged"}, secure=True)
        exchange.assert_not_called()
        self.assertEqual(response["Location"], "/access/denied/?why=signin")

    def test_used_state_cannot_be_replayed(self):
        params = self.begin()
        self.callback(params)
        with patch.object(PCAOIDCBackend, "get_token") as exchange:
            response = self.client.get("/oidc/callback/", {"code": "test", "state": params["state"][0]}, secure=True)
        self.assertEqual(response.status_code, 400)
        exchange.assert_not_called()

    def test_provider_error_and_network_failure_show_retry_page(self):
        params = self.begin()
        response = self.client.get("/oidc/callback/", {"error": "access_denied", "state": params["state"][0]}, secure=True)
        self.assertEqual(response["Location"], "/access/denied/?why=signin")
        params = self.begin()
        with patch.object(PCAOIDCBackend, "get_token", side_effect=ConnectionError):
            response = self.client.get("/oidc/callback/", {"code": "test", "state": params["state"][0]}, secure=True)
        self.assertEqual(response["Location"], "/access/denied/?why=signin")

    def test_disabling_account_revokes_existing_session_and_logout_is_reachable(self):
        self.callback(self.begin())
        self.owner.refresh_from_db()
        self.owner.status = "disabled"
        self.owner.save()
        self.assertTrue(self.client.get("/projects/", secure=True)["Location"].startswith("/access/login/"))
        self.assertEqual(self.client.get("/access/logout/", secure=True).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_external_next_url_is_rejected(self):
        response = self.callback(self.begin("https://outside.invalid/"))
        self.assertEqual(response["Location"], "/")
        response = Client().get("/access/login/", {"next": "https://outside.invalid/"}, secure=True)
        self.assertNotContains(response, "outside.invalid")

    def test_local_password_cannot_authenticate(self):
        self.owner.user = User.objects.create_user(self.owner.email, password="synthetic-password")
        self.owner.save()
        self.assertIsNone(authenticate(username=self.owner.email, password="synthetic-password"))

    def test_audit_never_contains_authorization_codes_or_state(self):
        params = self.begin()
        self.callback(params)
        for event in AuditEvent.objects.all():
            text = str(event.meta) + event.path
            self.assertNotIn("synthetic-auth-code", text)
            self.assertNotIn(params["state"][0], text)
