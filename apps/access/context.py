"""Per-request AccessContext (Access Spec v1 §12): who is viewing, with exactly what capabilities.

Impersonation (§10): when a superadmin has session["pca_view_as"], the context is computed PURELY from the
target account's assignments — never a union with the superadmin's own — so pages render exactly as they
would for the target ("natively and accurately").
"""

from django.conf import settings

from . import registry

SESSION_VIEW_AS = "pca_view_as"


class AccessContext:
    def __init__(self, real_account, effective_account, caps, allowed_divisions):
        self.account = effective_account            # whose permissions the page renders with
        self.real_account = real_account            # who is actually signed in
        self.caps = caps
        self.allowed_divisions = allowed_divisions  # None = all; else list of division codes (Command Center scope)
        self.viewing_as = effective_account if effective_account is not None and real_account is not None and effective_account.pk != real_account.pk else None

    def can(self, cap):
        return cap in self.caps

    def can_view(self, url_name):
        """Use the effective page rule for a card backed by that page's endpoint.

        A separate hard-coded capability check can contradict an owner override:
        the middleware admits the request while the card or endpoint still denies
        it. Resolve the same rule here, using the effective (possibly viewed-as)
        account, and fail closed for an unregistered page.
        """
        rule = registry.rule_for(url_name)
        if rule is None:
            return False
        if rule.get("public"):
            return True
        if self.account is None or self.account.status != "active":
            return False
        return bool(rule.get("auth")) or all(self.can(cap) for cap in rule.get("caps", ()))

    # -- template-friendly flags (server-side redaction reads these; spec §12) --
    @property
    def projects(self): return self.can("projects.view")
    @property
    def margins(self): return self.can("margins.view")
    @property
    def rates_field(self): return self.can("rates.field.view")
    @property
    def customers(self): return self.can("customers.view")
    @property
    def people(self): return self.can("people.view")
    @property
    def command_center(self): return self.can("command_center.view")
    @property
    def cc_all(self): return self.can("command_center.all_divisions")
    @property
    def finance(self): return self.can("finance.view")
    @property
    def finance_write(self): return self.can("finance.write")
    @property
    def sales010(self): return self.can("sales010.view")
    @property
    def console(self): return self.can("console.view")
    @property
    def ratings(self): return self.can("ratings.view")
    @property
    def insights(self): return self.can("insights.view")
    @property
    def notes(self): return self.can("insights.notes")
    @property
    def ops(self): return self.can("ops.view")
    @property
    def salestax(self): return self.can("salestax.view")
    # SharePoint spec §11
    @property
    def bids(self): return self.can("bids.view")
    @property
    def bids_notes(self): return self.can("bids.notes")
    @property
    def bids_refresh(self): return self.can("bids.refresh")
    @property
    def estimators(self): return self.can("estimators.view")
    @property
    def planning(self): return self.can("planning.view")
    @property
    def planning_write(self): return self.can("planning.write")
    @property
    def planning_approve(self): return self.can("planning.approve")
    @property
    def documents(self): return self.can("documents.view")
    @property
    def documents_findings(self): return self.can("documents.findings")
    @property
    def estimating(self): return self.can("estimating.view")
    @property
    def estimating_write(self): return self.can("estimating.write")
    @property
    def permissions_design(self): return self.can("permissions.design")
    @property
    def is_superadmin(self):
        return bool(self.account and self.account.is_superadmin)
    @property
    def switchable_accounts(self):
        if settings.PRIVATE_MODE or not self.real_is_superadmin:
            return []
        from .models import Account
        return Account.objects.filter(status="active").exclude(pk=self.real_account.pk).order_by("display_name")

    @property
    def real_is_superadmin(self):
        return bool(self.real_account and self.real_account.is_superadmin)


def _caps_for(account):
    if account.is_superadmin:
        return set(registry.CAPABILITIES), None
    caps, divisions, unscoped_role = set(), set(), False
    for ra in account.role_assignments.all():
        meta = registry.ROLES.get(ra.role)
        if not meta:
            continue
        caps |= registry.role_caps_effective(ra.role)       # code default, adjusted by the Permissions page's overrides
        # Division scope comes from holding a SCOPED role (Division Manager), not from any one capability — so the
        # scope survives the Command Center being superadmin-only (2026-09-12) and is ready when it reopens.
        if meta["scoped"]:
            divisions.update(ra.division_codes or [])
        else:
            unscoped_role = True
    caps |= {g.capability for g in account.extra_grants.all() if g.capability in registry.CAPABILITIES}
    if account.report_groups.filter(can_create_reports=True).exists():
        caps.add("reports.create")
    # Compensation rule, viewer half (Access Spec §7.2 "field employees see no rates at all"; restated by Owner on the
    # Permissions page 2026-09-11): a viewer who is themselves field-hourly never holds rates.field.view, whatever role
    # or extra grant would otherwise give it. rate_visible() covers the subject half (whose wage may be shown).
    if account.employee_id and getattr(account.employee, "is_field_hourly", False):
        caps.discard("rates.field.view")
    # None = every division. Only someone whose roles are ALL scoped is restricted; an account with no scoped role is
    # unrestricted (before 2026-09-12 it got [] — "no divisions at all" — which silently emptied the project map for
    # PMs, Finance, Estimators and Sales).
    if divisions and not unscoped_role and "command_center.all_divisions" not in caps:
        allowed = sorted(divisions)
    else:
        allowed = None
    return caps, allowed


def build(request, account):
    """account = the signed-in Account (already validated active)."""
    effective = account
    if account.is_superadmin:
        target_id = request.session.get(SESSION_VIEW_AS)
        if target_id:
            from .models import Account
            target = Account.objects.filter(pk=target_id, status="active").select_related("employee").first()
            if target and target.pk != account.pk:
                effective = target
            else:
                request.session.pop(SESSION_VIEW_AS, None)
    caps, allowed = _caps_for(effective)
    return AccessContext(account, effective, caps, allowed)


def anonymous_context():
    return AccessContext(None, None, set(), [])
