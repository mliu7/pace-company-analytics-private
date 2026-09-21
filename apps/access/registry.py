"""The single source of truth for capabilities, roles and per-URL access (Access Spec v1 §5-§6).

Rules enforced elsewhere but defined here:
- Every named dashboard/access URL MUST appear in URL_ACCESS (middleware default-denies; meta-test T1 fails).
- Capabilities with tier="superadmin" are CONCEALED: never listed in the admin console, never named to
  non-superadmins, and their pages 404 (not 403) for non-holders.
- Roles are code; assignments are data (access.models.RoleAssignment). There is no assignable superadmin role —
  only Account.is_superadmin and person-by-person ExtraGrants issued by a superadmin.
"""

NORMAL, SUPERADMIN = "normal", "superadmin"

CAPABILITIES = {
    "projects.view":        {"tier": NORMAL, "desc": "Projects list & detail, forecast, field page (with redactions)"},
    "margins.view":         {"tier": NORMAL, "desc": "GP / sold GP / EAC / budget & cost dollars on projects, people, customers and bids (010 order margins ride with sales010.view instead)"},
    "rates.field.view":     {"tier": NORMAL, "desc": "$/h and wages — field-hourly employees only, never salaried pay; and never held by a viewer who is themselves field-hourly (§7.2, Owner 2026-09-11)"},
    "customers.view":       {"tier": NORMAL, "desc": "Customers & sectors pages"},
    "people.view":          {"tier": NORMAL, "desc": "People / person pages"},
    "finance.view":         {"tier": NORMAL, "desc": "Financial Reports section (daily snapshot, WIP, bank, payments, allocations)"},
    "finance.write":        {"tier": NORMAL, "desc": "Finance data entry (bank figures, reconcile, finance refresh)"},
    "sales010.view":        {"tier": NORMAL, "desc": "010 Hardware Sales section (CNET orders/quotes, margins, drift, serials)"},
    "bids.view":            {"tier": NORMAL, "desc": "Bid pipeline (Bids pages, bid detail, estimator roster) — every role (SharePoint spec §11)"},
    "bids.notes":           {"tier": NORMAL, "desc": "Write notes, follow-ups and risks on bids"},
    "bids.refresh":         {"tier": NORMAL, "desc": "Bid-data maintenance: re-pull the Project Portal now, fix a bidder / client alias by hand"},
    "estimators.view":      {"tier": NORMAL, "desc": "Estimator pages and metrics (ratings stay superadmin-tier)"},
    "planning.view":        {"tier": NORMAL, "desc": "Production planning: status board, active tasks, punch lists, approvals, schedule, Planner boards"},
    "planning.write":       {"tier": NORMAL, "desc": "Edit status rows, punch items, the schedule, approval requests"},
    "planning.approve":     {"tier": NORMAL, "desc": "Approve / reopen BOM and labor requests"},
    "documents.view":       {"tier": NORMAL, "desc": "Project documents (SharePoint + share index, previews, search group)"},
    "documents.findings":   {"tier": NORMAL, "desc": "Proposal checks and document findings"},
    "estimating.view":      {"tier": NORMAL, "desc": "Estimating workbench: catalog, compare, estimates"},
    "estimating.write":     {"tier": NORMAL, "desc": "Create and edit estimates, rate card"},
    "console.view":         {"tier": NORMAL, "desc": "Access console"},
    "accounts.manage":      {"tier": NORMAL, "desc": "Create / deactivate accounts"},
    "roles.assign":         {"tier": NORMAL, "desc": "Assign / revoke system-defined roles"},
    "audit.view_delegated": {"tier": NORMAL, "desc": "Permission-change log (non-superadmin events only)"},
    # ---- Superadmin tier: concealed everywhere below superadmin ----
    "ratings.view":     {"tier": SUPERADMIN, "desc": "Ratings page and every rating fragment (field, PM, salesperson, customer…)"},
    "insights.view":    {"tier": SUPERADMIN, "desc": "Weekly insight pages and special reports (/insights/*)"},
    "insights.notes":   {"tier": SUPERADMIN, "desc": "Annotate insight items; meeting notes, topics & commitments"},
    "ops.view":         {"tier": SUPERADMIN, "desc": "Data Quality, refresh, Django admin"},
    "audit.view_all":   {"tier": SUPERADMIN, "desc": "Full audit log including superadmin-tier events"},
    "usage.view":       {"tier": SUPERADMIN, "desc": "Usage dashboard (who views what)"},
    "impersonate.use":  {"tier": SUPERADMIN, "desc": "User switcher (view as any account)"},
    "grants.manage":    {"tier": SUPERADMIN, "desc": "Extra grants (incl. superadmin tier) and superadmin flag"},
    "salestax.view":    {"tier": SUPERADMIN, "desc": "Sales Tax page (liability by state, remittances, rates, nexus) — Owner only"},
    # Superadmin tier until Owner builds the Command Center out (his call, 2026-09-12); to reopen it, move these two back to
    # NORMAL, drop conceal on the two URLs and give command_center.view (+ all_divisions) back to executive / division_manager.
    "command_center.view":  {"tier": SUPERADMIN, "desc": "Command Center (division-scoped unless all-divisions) — Owner only for now"},
    "command_center.all_divisions": {"tier": SUPERADMIN, "desc": "Command Center across every division"},
    "permissions.design": {"tier": SUPERADMIN, "desc": "Permissions page: decide what each capability unlocks, what each role holds, and confirm every rule before deployment"},
}

_CONTENT_BASE = ("projects.view", "margins.view", "rates.field.view", "customers.view", "people.view",
                 "bids.view", "bids.notes", "estimators.view", "planning.view", "documents.view", "documents.findings", "estimating.view")

ROLES = {
    "executive":        {"label": "Executive", "admin_visible": True, "scoped": False,
                         "caps": _CONTENT_BASE + ("sales010.view", "planning.write", "planning.approve", "bids.refresh", "estimating.write")},
    "division_manager": {"label": "Division Manager", "admin_visible": True, "scoped": True,
                         "caps": _CONTENT_BASE + ("planning.write", "planning.approve", "bids.refresh", "estimating.write")},
    "finance":          {"label": "Finance", "admin_visible": True, "scoped": False,
                         "caps": _CONTENT_BASE + ("finance.view", "finance.write", "sales010.view", "planning.write")},
    "project_manager":  {"label": "Project Manager", "admin_visible": True, "scoped": False,
                         "caps": _CONTENT_BASE + ("planning.write", "bids.refresh", "estimating.write")},
    # Estimators price the work, so they hold the money capabilities too — margins and field $/h
    # (Owner on the Permissions page, 2026-09-11: "Add Estimator to this list too" on margins.view and rates.field.view).
    "estimator":        {"label": "Estimator", "admin_visible": True, "scoped": False,
                         "caps": ("projects.view", "margins.view", "rates.field.view", "customers.view", "people.view", "bids.view",
                                  "bids.notes", "bids.refresh", "estimators.view", "planning.view", "planning.write", "documents.view",
                                  "documents.findings", "estimating.view", "estimating.write")},
    # margins.view: Owner, 2026-09-12 — "the sales role should hold the margins views. This is very important."
    "sales":            {"label": "Sales — 010 Hardware", "admin_visible": True, "scoped": False,
                         "caps": ("sales010.view", "margins.view", "customers.view", "bids.view", "bids.notes")},
    # An ordinary role, assignable to as many people as Owner likes (his note, 2026-09-11) — but only a superadmin may
    # grant or revoke it (SUPERADMIN_ASSIGNED below): a permission admin still cannot mint peers (Access Spec §17.5).
    "permission_admin": {"label": "Permission Admin", "admin_visible": True, "scoped": False,
                         "caps": ("console.view", "accounts.manage", "roles.assign", "audit.view_delegated")},
}

# Roles a permission admin may see but never hand out; only a superadmin can grant or revoke them.
SUPERADMIN_ASSIGNED = {"permission_admin"}

PUBLIC, AUTHENTICATED = {"public": True}, {"auth": True}


def _need(*caps, conceal=False):
    return {"caps": caps, "conceal": conceal}


# url_name -> access rule. Namespaces: "admin" and "oidc" are handled by namespace rules in the middleware.
URL_ACCESS = {
    # dashboard
    "command_center":   _need("command_center.view", conceal=True),
    "command_center_jobs": _need("command_center.view", conceal=True),   # JSON: closed jobs behind a Command Center chart element (division-scoped in the view)
    "project_list":     _need("projects.view"),
    "project_detail":   _need("projects.view"),
    "project_account_drill": _need("projects.view", "margins.view"),   # JSON: postings behind a Budget-vs-actual amount
    "project_contract_value": _need("projects.view", "finance.view", "finance.write"),   # POST: override / confirm the contract value the app uses
    "project_entries":  _need("projects.view"),   # JSON: PTT job-report entries behind the hour figures / one crew member / the rest of the work log (no $)
    "project_transactions": _need("projects.view", "margins.view"),   # JSON: the project's full SL transaction ledger (per-row labor redaction inside)
    "forecast":         _need("projects.view", "margins.view"),
    "project_snapshot": _need("projects.view", "margins.view"),
    "people":           _need("people.view"),
    "person_detail":    _need("people.view"),
    "field":            _need("projects.view"),
    "customers":        _need("customers.view"),
    "customer_detail":  _need("customers.view"),
    "customer_payments": _need("customers.view", "finance.view"),   # JSON: the customer page's payment-history table (docs/06)
    "ratings":          _need("ratings.view", conceal=True),
    "insights":         _need("insights.view", conceal=True),
    "insight_topics":   _need("insights.notes", conceal=True),
    "insight_notes":    _need("insights.notes", conceal=True),
    "insight_annotate": _need("insights.notes", conceal=True),
    "insight_meeting_note": _need("insights.notes", conceal=True),
    "insight_commitment":   _need("insights.notes", conceal=True),
    "finance_daily":    _need("finance.view"),
    "finance_wip":      _need("finance.view"),
    "finance_payments": _need("finance.view"),
    "finance_vendors":  _need("finance.view"),
    "finance_vendor":   _need("finance.view"),
    "finance_bank":     _need("finance.view"),
    "finance_bank_detail": _need("finance.view"),
    "finance_allocations": _need("finance.view"),
    "finance_pnl":      _need("finance.view"),
    "finance_pnl_detail": _need("finance.view"),
    "finance_pnl_lines": _need("finance.view"),
    "finance_drill":    _need("finance.view"),
    "finance_liabilities": _need("finance.view"),
    "finance_assets":   _need("finance.view"),
    "finance_ar":       _need("finance.view"),   # Accounts Receivable page (docs/ar_page_plan.md)
    "finance_ar_doc":   _need("finance.view"),   # JSON: one open document's lines / payments / context
    "finance_billings": _need("finance.view"),   # Billings page — what was billed, day by day (docs/billings_page_plan.md)
    "finance_billings_doc": _need("finance.view"),   # JSON: one billing's lines / payments / job context
    "finance_salestax": _need("salestax.view", conceal=True),   # Sales Tax page — superadmin tier, 404 to everyone else (docs/sales_tax_page_plan.md)
    "project_map":      _need("projects.view"),
    "project_map_data": _need("projects.view"),
    "project_map_locate": _need("projects.view"),
    "finance_bank_figures":   _need("finance.view", "finance.write"),
    "finance_bank_reconcile": _need("finance.view", "finance.write"),
    "finance_refresh":  _need("finance.view", "finance.write"),
    "data_quality":     _need("ops.view", conceal=True),
    "refresh":          _need("ops.view", conceal=True),
    "refresh_status":   _need("ops.view", conceal=True),
    "sales010_overview":     _need("sales010.view"),
    "sales010_snapshot":     _need("sales010.view"),   # the 010 body of the Daily/Weekly Snapshot
    "sales010_pipeline":     _need("sales010.view"),
    "sales010_quote_json":   _need("sales010.view"),   # JSON: quote header + lines for the pipeline drilldown
    "sales010_orders":       _need("sales010.view"),
    "sales010_order_detail": _need("sales010.view"),
    "sales010_products":     _need("sales010.view"),
    "sales010_serials":      _need("sales010.view"),
    "sales010_hygiene":      _need("sales010.view"),
    "about":            AUTHENTICATED,
    # ---- SharePoint spec §11: bids & estimators (apps/bids/urls.py)
    "bids_overview":    _need("bids.view"),
    "bids_snapshot":      _need("bids.view"),   # Pipeline Snapshot (day / week submissions, open book, quoting)
    "bids_snapshot_json": _need("bids.view"),
    "bids_board":       _need("bids.view"),
    "bids_calendar":    _need("bids.view"),
    "bids_calendar_json": _need("bids.view"),
    "bids_list":        _need("bids.view"),
    "bids_list_json":   _need("bids.view"),
    "bids_export":      _need("bids.view"),
    "bids_analytics":   _need("bids.view"),
    "bids_analytics_json": _need("bids.view"),
    "bid_detail":       _need("bids.view"),
    "bid_note_add":     _need("bids.view", "bids.notes"),
    "bid_followup":     _need("bids.view", "bids.notes"),
    "bid_risk":         _need("bids.view", "bids.notes"),
    "bids_refresh":     _need("bids.view", "bids.refresh"),
    "bids_alias_set":   _need("bids.view", "bids.refresh"),   # Data Quality: fix a bidder / client alias by hand (bid-data maintenance, not planning)
    "estimators":       _need("estimators.view"),
    "estimator_detail": _need("estimators.view"),
    # ---- production planning (apps/planning/urls.py)
    "planning_today":   _need("planning.view"),
    "planning_status":  _need("planning.view"),
    "planning_status_json": _need("planning.view"),
    "planning_status_edit": _need("planning.view", "planning.write"),
    "planning_status_import": _need("planning.view", "planning.write"),
    "planning_status_send": _need("planning.view", "planning.write"),
    "planning_tasks":   _need("planning.view"),
    "planning_tasks_report": _need("planning.view"),
    "planning_tasks_export": _need("planning.view"),
    "planning_punch":   _need("planning.view"),
    "planning_punch_project": _need("planning.view"),
    "planning_punch_edit": _need("planning.view", "planning.write"),
    "planning_punch_export": _need("planning.view"),
    "planning_approvals": _need("planning.view"),
    "planning_approval_edit": _need("planning.view", "planning.write"),
    "planning_approval_decide": _need("planning.view", "planning.approve"),
    "planning_approval_export": _need("planning.view"),
    "planning_approval_attachment": _need("planning.view"),
    "planning_schedule": _need("planning.view"),
    "planning_schedule_json": _need("planning.view"),
    "planning_schedule_edit": _need("planning.view", "planning.write"),
    "planning_schedule_export": _need("planning.view"),
    "planning_planner": _need("planning.view"),
    "planning_planner_json": _need("planning.view"),
    # ---- documents (apps/documents/urls.py)
    "documents_page":   _need("documents.view"),
    "documents_json":   _need("documents.view"),
    "document_preview": _need("documents.view"),
    "document_content": _need("documents.view"),
    "document_html":    _need("documents.view"),   # Word / Excel / CSV rendered for the sandboxed preview frame
    "document_pdf":     _need("documents.view"),   # Converted Office preview
    "document_page":    _need("documents.view"),   # QuickLook first page (pptx, xls, vsdx …)
    "documents_findings": _need("documents.view", "documents.findings"),
    "document_link_state": _need("documents.view", "documents.findings"),   # confirm / reject a document↔project link (document stewardship, not planning)
    # ---- estimating workbench (apps/estimating/urls.py)
    "estimating_home":  _need("estimating.view"),
    "estimating_search": _need("estimating.view"),
    "estimating_item":  _need("estimating.view"),
    "estimating_estimates": _need("estimating.view"),
    "estimate_detail":  _need("estimating.view"),
    "estimate_save":    _need("estimating.view", "estimating.write"),
    "estimate_export":  _need("estimating.view"),
    "estimating_rates": _need("estimating.view"),
    "estimating_rates_save": _need("estimating.view", "estimating.write"),
    "estimating_sources": _need("estimating.view"),
    "estimating_import": _need("estimating.view", "estimating.write"),
    "search":           AUTHENTICATED,   # results page — each group is gated by its own capability inside dashboard.search
    "search_suggest":   AUTHENTICATED,   # JSON for the sidebar search box (same per-group gating)
    # access app
    "login":            PUBLIC,
    "denied":           PUBLIC,
    "logout":           PUBLIC,   # disabled/denied users must also be able to clear their session
    "console_people":   _need("console.view"),
    "console_person":   _need("console.view"),
    "console_create":   _need("console.view", "accounts.manage"),
    "console_audit":    _need("console.view", "audit.view_delegated"),
    "console_usage":    _need("usage.view", conceal=True),
    "console_audit_all": _need("audit.view_all", conceal=True),
    "view_as_start":    _need("impersonate.use", conceal=True),
    "view_as_stop":     AUTHENTICATED,   # anyone impersonating (only superadmins can be) may always return
    "permissions":        _need("permissions.design", conceal=True),   # the permission-design page (docs/07 "Permissions page")
    "permissions_update": _need("permissions.design", conceal=True),   # POST: confirm / note / change a rule (audited)
}


def role_caps(role):
    """The role's capabilities as CODE defines them (the default / recommendation)."""
    return set(ROLES[role]["caps"])


# ---- effective rules: code defaults, adjusted by the Permissions page's audited overrides (access.overrides).
# The middleware, the per-request context and the console read THESE; tests with no overrides see the code defaults.
def rule_for(url_name):
    """Effective access rule for a URL name (None when unregistered)."""
    base = URL_ACCESS.get(url_name)
    if base is None:
        return None
    from . import overrides
    return overrides.view_rule(url_name, base)


def role_caps_effective(role):
    meta = ROLES.get(role)
    if meta is None:
        return set()
    from . import overrides
    return overrides.role_caps(role, set(meta["caps"]))


def role_meta_effective(role):
    """ROLES[role] with `caps` replaced by the effective set (a copy)."""
    meta = dict(ROLES[role])
    meta["caps"] = tuple(sorted(role_caps_effective(role)))
    return meta


def visible_roles():
    """Roles the console may name and tag (every role that is not a hidden implementation detail)."""
    return {k: role_meta_effective(k) for k, v in ROLES.items() if v["admin_visible"]}


def assignable_role_slugs(is_superadmin):
    """Roles this actor may grant or revoke — every visible role, minus the superadmin-assigned ones (no DB)."""
    return {k for k, v in ROLES.items() if v["admin_visible"] and (is_superadmin or k not in SUPERADMIN_ASSIGNED)}


def assignable_roles(is_superadmin):
    """The same, with each role's effective definition — what the console's role table renders."""
    slugs = assignable_role_slugs(is_superadmin)
    return {k: m for k, m in visible_roles().items() if k in slugs}


def superadmin_caps():
    return {c for c, m in CAPABILITIES.items() if m["tier"] == SUPERADMIN}


def validate():
    for r, meta in ROLES.items():
        unknown = set(meta["caps"]) - set(CAPABILITIES)
        assert not unknown, "role %s references unknown caps %s" % (r, unknown)
        assert not (set(meta["caps"]) & superadmin_caps()), "role %s must not carry superadmin-tier caps" % r
    for name, rule in URL_ACCESS.items():
        for c in rule.get("caps", ()):
            assert c in CAPABILITIES, "URL_ACCESS[%s] references unknown cap %s" % (name, c)

# Private routes and capabilities do not exist in a shared process, even for a
# company superadmin. ExtraGrant cannot reintroduce an unregistered capability.
from django.conf import settings as _settings
_PRIVATE_CAPS = {"ratings.view", "insights.view", "insights.notes"}
if not _settings.PRIVATE_MODE:
    for _name in list(URL_ACCESS):
        if set(URL_ACCESS[_name].get("caps", ())) & _PRIVATE_CAPS:
            del URL_ACCESS[_name]
    for _cap in _PRIVATE_CAPS:
        CAPABILITIES.pop(_cap, None)
CAPABILITIES["reports.create"] = {"tier": NORMAL, "desc": "Create ad-hoc reports and manage their explicit audience (grant to named people first)"}
for _name in ("reports_list", "report_detail", "report_content", "report_audience"):
    URL_ACCESS[_name] = AUTHENTICATED
URL_ACCESS["report_create"] = _need("reports.create")
URL_ACCESS["report_groups"] = _need("grants.manage", conceal=True)
URL_ACCESS["finance_bank_upload"] = _need("finance.view", "finance.write")
URL_ACCESS["finance_bank_file"] = _need("finance.view")

if _settings.PRIVATE_MODE:
    CAPABILITIES["private.workspace"] = {"tier": SUPERADMIN, "desc": "Local private workspace"}
    for _name in ("private_home", "private_notes", "private_document", "private_link_state", "private_entity_note",
                  "private_egress", "private_ask", "private_ask_thread", "private_action"):
        URL_ACCESS[_name] = _need("private.workspace", conceal=True)
