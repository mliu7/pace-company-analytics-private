"""Accounts, role assignments, extra grants and the audit trail (Access Spec v1 §4)."""

from django.contrib.auth.models import User
from django.db import models

from apps.core.models import Employee, TimeStampedModel


class Account(TimeStampedModel):
    email = models.EmailField(unique=True)                       # matched case-insensitively against Entra claims
    entra_object_id = models.UUIDField(null=True, blank=True, unique=True)  # immutable identity in the configured tenant
    display_name = models.CharField(max_length=120)
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="accounts")
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="pca_account")
    status = models.CharField(max_length=12, choices=[("active", "Active"), ("disabled", "Disabled")], default="active")
    is_superadmin = models.BooleanField(default=False)           # set only by access_bootstrap or a superadmin (§9)
    created_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="created_accounts")
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["display_name"]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return "%s <%s>" % (self.display_name, self.email)


class RoleAssignment(TimeStampedModel):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="role_assignments")
    role = models.SlugField(max_length=32)                        # must exist in registry.ROLES
    division_codes = models.JSONField(null=True, blank=True)      # required iff registry role is scoped
    granted_by = models.ForeignKey(Account, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("account", "role")]


class ExtraGrant(TimeStampedModel):
    """Person-by-person capability grants — the only path (besides is_superadmin) to superadmin-tier caps."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="extra_grants")
    capability = models.SlugField(max_length=48)
    granted_by = models.ForeignKey(Account, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("account", "capability")]


class AuditEvent(models.Model):
    KINDS = [(k, k) for k in (
        "login", "login_denied", "logout", "page_view", "denied", "account_created", "account_disabled",
        "account_enabled", "role_granted", "role_revoked", "extra_granted", "extra_revoked",
        "superadmin_changed", "impersonation_start", "impersonation_stop", "write_action")]
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(Account, null=True, on_delete=models.SET_NULL, related_name="audit_events")
    acting_as = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=32, choices=KINDS, db_index=True)
    target = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    capability_or_role = models.SlugField(max_length=48, blank=True)
    view_name = models.CharField(max_length=64, blank=True)
    path = models.CharField(max_length=300, blank=True)
    division = models.CharField(max_length=8, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["actor", "at"]), models.Index(fields=["kind", "at"])]
        ordering = ["-at"]

    @property
    def is_superadmin_tier(self):
        from . import registry
        return (self.kind in ("extra_granted", "extra_revoked", "superadmin_changed", "impersonation_start", "impersonation_stop")
                or self.capability_or_role in registry.superadmin_caps())


class AccessDecision(TimeStampedModel):
    """One line of the Permissions page (docs/07 "Permissions page"): Owner's verdict on a rule — a capability, a role, a
    page, an in-page redaction or a sign-in / accounts item. `confirmed` = he agrees with the rule as it stands (the
    effective rule at the time, kept in `snapshot` so a later change re-opens the question). Notes are his context."""

    key = models.CharField(max_length=96, unique=True)            # "view:<url name>" | "cap:<capability>" | "role:<slug>" | "frag:<prop>" | "item:<slug>"
    status = models.CharField(max_length=12, choices=[("open", "Open"), ("confirmed", "Confirmed")], default="open")
    note = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)           # the effective rule when confirmed
    decided_by = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["key"]


class AccessOverride(TimeStampedModel):
    """A rule changed on the Permissions page, layered over the code default in registry.py (which stays the reset
    point). kind "view": value = {"caps": [...], "conceal": bool} for a URL name; kind "role": value = {"caps": [...]}.
    Read on every request (tiny table); every change is an audit event."""

    kind = models.CharField(max_length=8, choices=[("view", "Page / endpoint"), ("role", "Role")])
    key = models.CharField(max_length=96)
    value = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    set_by = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("kind", "key")]
        ordering = ["kind", "key"]
