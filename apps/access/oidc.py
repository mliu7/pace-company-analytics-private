"""Entra OIDC backend: match pre-provisioned accounts only, never auto-create (Access Spec v1 §3)."""

from django.contrib.auth.models import User
from django.utils import timezone

try:
    from mozilla_django_oidc.auth import OIDCAuthenticationBackend
except ImportError:                       # dev mode: module unused
    OIDCAuthenticationBackend = object

from .audit import log
from .models import Account


class PCAOIDCBackend(OIDCAuthenticationBackend):
    def _claim_email(self, claims):
        return (claims.get("email") or claims.get("preferred_username") or "").strip().lower()

    def filter_users_by_claims(self, claims):
        email = self._claim_email(claims)
        acct = Account.objects.filter(email=email, status="active").first()
        if acct is None:
            log("login_denied", meta_email=email)
            return User.objects.none()
        if acct.user is None:             # pre-provisioned before user creation
            user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
            user.set_unusable_password(); user.save()
            acct.user = user
            acct.save(update_fields=["user"])
        acct.last_login_at = timezone.now()
        acct.save(update_fields=["last_login_at"])
        log("login", actor=acct)
        return User.objects.filter(pk=acct.user_id)

    def create_user(self, claims):        # belt & suspenders: never create
        return None

    def verify_claims(self, claims):
        return bool(self._claim_email(claims))
