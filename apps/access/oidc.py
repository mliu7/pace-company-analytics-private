"""Single-tenant Entra sign-in, restricted to pre-approved PCA accounts."""

from uuid import UUID

import jwt
from requests import RequestException
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView

from .audit import log
from .models import Account


class PCAOIDCBackend(OIDCAuthenticationBackend):
    def _claim_email(self, claims):
        # Prefer the tenant sign-in name over the optional, mutable mail alias.
        value = claims.get("preferred_username") or claims.get("email") or ""
        return value.strip().lower() if isinstance(value, str) else ""

    def _deny(self, email="", reason="unknown"):
        request = getattr(self, "request", None)
        if request is not None:
            request.pca_login_denial_reason = reason
        # Never put the callback query string (authorization code/state) in audit data.
        log("login_denied", request, path=request.path if request else "",
            meta_email=email, reason=reason)
        return User.objects.none()

    def _verify_jws(self, token, key):
        try:
            return jwt.decode(
                token, key, algorithms=["RS256"],
                audience=self.OIDC_RP_CLIENT_ID, issuer=settings.OIDC_OP_ISSUER,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "tid", "oid", "nonce"]},
            )
        except jwt.InvalidTokenError as exc:
            raise SuspiciousOperation("Invalid Entra ID token") from exc

    def verify_token(self, token, **kwargs):
        payload = super().verify_token(token, **kwargs)
        if payload.get("tid") != settings.OIDC_TENANT_ID:
            raise SuspiciousOperation("Unexpected Entra tenant")
        return payload

    def get_userinfo(self, access_token, id_token, payload):
        # Identity comes from the validated ID token, including its tenant and oid.
        return payload

    def authenticate(self, request, **kwargs):
        try:
            return super().authenticate(request, **kwargs)
        except (SuspiciousOperation, RequestException):
            self._deny(reason="signin")
            return None

    def verify_claims(self, claims):
        try:
            UUID(str(claims.get("oid", "")))
        except (ValueError, TypeError, AttributeError):
            self._deny(reason="signin")
            return False
        return bool(claims.get("sub"))

    @transaction.atomic
    def filter_users_by_claims(self, claims):
        email = self._claim_email(claims)
        try:
            object_id = UUID(str(claims.get("oid", "")))
        except (ValueError, TypeError, AttributeError):
            return self._deny(email, "signin")
        accounts = Account.objects.select_for_update()
        acct = accounts.filter(entra_object_id=object_id).first()
        if acct is None:
            matches = list(accounts.filter(email__iexact=email)[:2]) if email else []
            if len(matches) != 1:
                return self._deny(email)
            acct = matches[0]
            if acct.entra_object_id is not None:  # a recycled email cannot take over an account
                return self._deny(email)
        if acct.status != "active":
            return self._deny(email, "disabled")
        if acct.user is None:                    # account approved before first sign-in
            user, _ = User.objects.get_or_create(username=acct.email, defaults={"email": acct.email})
            if not user.is_active:
                return self._deny(email, "disabled")
            user.set_unusable_password()
            user.is_staff = user.is_superuser = acct.is_superadmin
            user.save()
            acct.user = user
        elif not acct.user.is_active:
            return self._deny(email, "disabled")
        acct.entra_object_id = object_id
        acct.last_login_at = timezone.now()
        acct.save(update_fields=["user", "entra_object_id", "last_login_at"])
        request = getattr(self, "request", None)
        log("login", request, actor=acct, path=request.path if request else "")
        return User.objects.filter(pk=acct.user_id)

    def create_user(self, claims):
        return None                              # never create PCA accounts at sign-in

    def get_user(self, user_id):
        return User.objects.filter(pk=user_id, is_active=True, pca_account__status="active").first()


class PCAOIDCCallbackView(OIDCAuthenticationCallbackView):
    def login_failure(self):
        reason = getattr(self.request, "pca_login_denial_reason", "signin")
        return redirect("/access/denied/?why=" + reason)
