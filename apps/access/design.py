"""Builds the Permissions page (docs/07 "Permissions page"): everything that needs a decision before deployment —
capabilities, roles, every page / endpoint, every in-page redaction, and the sign-in / accounts settings — each with its
effective rule (code default + overrides), the roles it opens for, Owner's decision (confirmed / open / stale) and note,
plus the readiness checks. Pure assembly; the writes live in views_permissions / overrides."""

import os
import re
from collections import defaultdict

from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver

from . import overrides, registry

# ---------------------------------------------------------------- areas (how the pages are grouped on the page)
AREAS = [
    ("reports", "Ad-hoc Reports", "Explicit individual and group audiences; creation grants are separate from viewing."),
    ("home", "Home & search", "The sidebar search and the About page. \"/\" is the Command Center, so everyone else is redirected from it to their first section."),
    ("projects", "Projects", "Project list, project page and its drill-downs, forecast, field crew, map, the Project Snapshot."),
    ("people", "People & customers", "Project managers, people pages, customers and sectors."),
    ("finance", "Finance", "Financial Reports: daily snapshot, WIP, P&L, AR, billings, payments, vendors, bank, allocations — and the finance write actions."),
    ("sales", "010 Hardware Sales", "CNET quotes and orders, the 010 snapshot, products, serials, hygiene."),
    ("bids", "Bids & estimators", "Bid pipeline, bid detail, notes, estimator pages."),
    ("planning", "Production planning", "Status board, tasks, punch lists, approvals, schedule, Planner boards."),
    ("documents", "Documents", "Project documents, previews and findings."),
    ("estimating", "Estimating", "Catalog, estimates, rate card, imports."),
    ("superadmin", "Superadmin-only analytics & tools", "Command Center, Data Quality / refresh, Sales Tax — concealed (404) from everyone else."),
    ("console", "Access console & sign-in", "Login, logout, denied page, the People & Access console, audit, usage, the user switcher, this page."),
]
AREA_LABEL = dict((k, l) for k, l, _ in AREAS)

_DASH_AREA = [
    (re.compile(r"^command_center"), "superadmin"),     # Owner-only until he builds it out (2026-09-12)
    (re.compile(r"^(search|about)"), "home"),
    (re.compile(r"^(project_|forecast$|field$|project_map)"), "projects"),
    (re.compile(r"^(people$|person_|customers$|customer_)"), "people"),
    (re.compile(r"^finance_salestax$"), "superadmin"),
    (re.compile(r"^finance_"), "finance"),
    (re.compile(r"^sales010_"), "sales"),
    (re.compile(r"^(ratings$|insight|data_quality$|refresh)"), "superadmin"),
]
_NS_AREA = {"bids": "bids", "planning": "planning", "scheduling": "planning", "planner": "planning", "documents": "documents",
            "estimating": "estimating", "access": "console"}

# ---------------------------------------------------------------- what each area's capabilities mean (role × area matrix)
# Every NORMAL capability belongs to exactly one area (tests/access/test_permissions asserts it); the short label is what a
# matrix cell shows. Superadmin-tier capabilities all sit in the "superadmin" area; "home" has none (its pages are fixed).
AREA_CAPS = {
    "projects":   [("projects.view", "view"), ("margins.view", "margins"), ("rates.field.view", "field $/h")],
    "people":     [("people.view", "people"), ("customers.view", "customers")],
    "finance":    [("finance.view", "view"), ("finance.write", "write")],
    "sales":      [("sales010.view", "view")],
    "bids":       [("bids.view", "view"), ("bids.notes", "notes"), ("bids.refresh", "maintain"), ("estimators.view", "estimators")],
    "planning":   [("planning.view", "view"), ("planning.write", "write"), ("planning.approve", "approve")],
    "documents":  [("documents.view", "view"), ("documents.findings", "findings")],
    "estimating": [("estimating.view", "view"), ("estimating.write", "write")],
    "reports":    [("reports.create", "create reports")],
    "console":    [("console.view", "console"), ("accounts.manage", "accounts"), ("roles.assign", "roles"), ("audit.view_delegated", "change log")],
}
CAP_AREA = {c: area for area, caps in AREA_CAPS.items() for c, _l in caps}
CAP_SHORT = {c: l for caps in AREA_CAPS.values() for c, l in caps}

_ACTION_CAP_SUFFIX = (".write", ".notes", ".approve", ".refresh", ".manage", ".assign", "impersonate.use")
_ACTION_NAMES = {"logout", "view_as_stop", "view_as_start", "refresh", "permissions_update", "console_create", "insight_annotate",
                 "insight_meeting_note", "insight_commitment", "bids_alias_set", "document_link_state"}
_DATA_RE = re.compile(r"(_json$|_data$|_doc$|_status$|_suggest$|_export$|_locate$|_content$|_preview$|_page$|_html$|_attachment$|_drill$|_entries$|_transactions$|_lines$|_jobs$|_figures$)")
_WORDS = {"wip": "WIP", "pnl": "P&L", "ar": "AR", "cnet": "CNET", "json": "JSON", "ptt": "PTT", "sl": "SL", "sales010": "010 Sales", "bom": "BOM",
          "csv": "CSV", "html": "HTML", "salestax": "Sales Tax", "cc": "Command Center", "id": "ID", "po": "PO", "so1": "SO1"}


def humanize(name):
    parts = []
    for w in name.split("_"):
        parts.append(_WORDS.get(w, w.capitalize()))
    return " ".join(parts)


def _first_sentence(doc):
    doc = " ".join((doc or "").split())
    if not doc:
        return ""
    m = re.match(r"(.{20,220}?[.!?])(\s|$)", doc)
    return (m.group(1) if m else doc[:220]).strip()


def _walk(patterns, prefix="", ns=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern), p.namespace or ns)
        elif isinstance(p, URLPattern) and p.name:
            yield ns, p.name, "/" + prefix + str(p.pattern), p.callback


def kind_of(name, rule, path):
    caps = rule.get("caps", ())
    if name in _ACTION_NAMES or any(c.endswith(sfx) or c == sfx for c in caps for sfx in _ACTION_CAP_SUFFIX):
        return "action"
    if _DATA_RE.search(name):
        return "data"
    return "page"


def url_catalog():
    """Every registered URL with its path, description, kind, area and effective rule."""
    rows = []
    for ns, name, path, cb in _walk(get_resolver().url_patterns):
        if ns in ("admin", "oidc") or name not in registry.URL_ACCESS:
            continue
        base = registry.URL_ACCESS[name]
        rule = registry.rule_for(name)
        area = _NS_AREA.get(ns)
        if area is None:
            area = next((a for rx, a in _DASH_AREA if rx.search(name)), "home")
        rows.append({"name": name, "ns": ns, "path": path.replace("^", "").replace("$", ""), "label": humanize(name),
                     "desc": _first_sentence(getattr(cb, "__doc__", "")), "kind": kind_of(name, base, path), "area": area,
                     "rule": rule, "base": base, "fixed": name in overrides.FIXED_VIEWS})
    rows.sort(key=lambda r: (r["area"], r["kind"] != "page", r["label"]))
    return rows


def rule_text(rule):
    if rule.get("public"):
        return "Public — no sign-in needed"
    if rule.get("auth"):
        return "Any signed-in account"
    return " + ".join(rule.get("caps", ()))


def roles_with(caps):
    """Roles whose effective capability set covers `caps` (superadmin always does)."""
    caps = set(caps)
    return [r for r in registry.ROLES if caps <= registry.role_caps_effective(r)]


# ---------------------------------------------------------------- in-page redactions (template fragments)
FRAGMENTS = {   # AccessContext property -> (capability, what it gates)
    "margins": ("margins.view", "Gross profit, sold GP, EAC, budget and cost dollars — columns, KPI cards, chart series"),
    "rates_field": ("rates.field.view", "Wages and $/h — and only for field-hourly employees (Employee.is_field_hourly); salaried pay never shows below superadmin, and a field-hourly viewer never holds the capability at all"),
    "ratings": ("ratings.view", "Every rating fragment: PM, customer, salesperson and field-crew ratings, the Ratings page link"),
    "finance": ("finance.view", "Customer AR card and sidebar Finance group. Customer payment history follows the customer_payments page rule instead."),
    "finance_write": ("finance.write", "Finance data-entry controls (bank figures, reconcile, refresh buttons)"),
    "customers": ("customers.view", "Customer links and the Customers pages"),
    "people": ("people.view", "Links to people pages (PM, updater, crew names)"),
    "projects": ("projects.view", "Project sections and the Projects sidebar group"),
    "command_center": ("command_center.view", "Command Center link"),
    "sales010": ("sales010.view", "010 Hardware Sales group and 010 fragments"),
    "bids": ("bids.view", "Bids pages and links"), "bids_notes": ("bids.notes", "Bid note / follow-up / risk controls"), "bids_refresh": ("bids.refresh", "Re-pull the Project Portal button"),
    "estimators": ("estimators.view", "Estimator pages and links"),
    "planning": ("planning.view", "Production planning group"), "planning_write": ("planning.write", "Planning edit controls"), "planning_approve": ("planning.approve", "Approve / reopen controls"),
    "documents": ("documents.view", "Documents link and previews"), "documents_findings": ("documents.findings", "Document findings"),
    "estimating": ("estimating.view", "Estimating workbench link"), "estimating_write": ("estimating.write", "Estimate / rate-card edit controls"),
    "insights": ("insights.view", "Weekly insights sidebar group"), "notes": ("insights.notes", "Insight annotations, topics, commitments"),
    "ops": ("ops.view", "Data Quality & refresh link"), "salestax": ("salestax.view", "Sales Tax link"), "console": ("console.view", "People & Access link"),
    "permissions_design": ("permissions.design", "Permissions page link"),
    "is_superadmin": (None, "Superadmin flag: superadmin-only controls (extra grants, superadmin toggle, 'visible only to you' blocks)"),
}


def fragment_usage():
    """{prop: {"cap", "what", "count", "templates": [...]}} from a scan of every template for `acc.<prop>`."""
    counts, files = defaultdict(int), defaultdict(set)
    rx = re.compile(r"acc\.([a-z_]+)")
    for app in os.listdir(settings.BASE_DIR / "apps"):
        tdir = settings.BASE_DIR / "apps" / app / "templates"
        if not tdir.is_dir():
            continue
        for root, _dirs, names in os.walk(tdir):
            for fn in names:
                if not fn.endswith(".html"):
                    continue
                try:
                    text = open(os.path.join(root, fn), encoding="utf-8").read()
                except OSError:
                    continue
                for m in rx.finditer(text):
                    prop = m.group(1)
                    if prop in FRAGMENTS:
                        counts[prop] += 1
                        files[prop].add(fn)
    out = {}
    for prop, (cap, what) in FRAGMENTS.items():
        if cap and cap not in registry.CAPABILITIES:
            continue
        out[prop] = {"prop": prop, "cap": cap, "what": what, "count": counts.get(prop, 0), "templates": sorted(files.get(prop, ()))[:8],
                     "tier": (registry.CAPABILITIES[cap]["tier"] if cap else "superadmin")}
    return out


# ---------------------------------------------------------------- sign-in & accounts items
def signin_items():
    from .models import Account, RoleAssignment
    mode = settings.PCA_AUTH_MODE
    sups = list(Account.objects.filter(is_superadmin=True).order_by("display_name"))
    admins = list(Account.objects.filter(role_assignments__role="permission_admin").distinct().order_by("display_name"))
    accts = Account.objects.all()
    n_active, n_disabled = accts.filter(status="active").count(), accts.filter(status="disabled").count()
    never = accts.filter(status="active", last_login_at__isnull=True).count()
    unassigned = accts.filter(status="active", is_superadmin=False).exclude(role_assignments__isnull=False).count()
    oidc_ok = all(os.environ.get(k) for k in ("PCA_OIDC_TENANT_ID", "PCA_OIDC_CLIENT_ID", "PCA_OIDC_CLIENT_SECRET"))
    hosts = ", ".join(settings.ALLOWED_HOSTS)
    items = [
        {"key": "auth_mode", "title": "How people sign in",
         "rec": "Production runs PCA_AUTH_MODE=sso: Microsoft Entra single sign-on with MFA enforced by a Conditional Access policy on the app registration. "
                "The dev auto-login exists only for this laptop and physically refuses to run unless DEBUG is on and the host is localhost.",
         "cur": "This machine: %s%s. Entra secrets in the environment: %s." % (mode, " (DEBUG on)" if settings.DEBUG else "", "yes" if oidc_ok else "not set here — normal for the dev laptop; required on the server"),
         "ok": mode == "dev" and settings.DEBUG or mode == "sso"},
        {"key": "provisioning", "title": "Who gets an account",
         "rec": "Accounts are pre-provisioned in People & Access by a permission admin (HR) before someone first signs in — email, name, employee link, roles. "
                "SSO never creates accounts: an unknown Microsoft identity lands on the denied page and is logged.",
         "cur": "%d active account%s, %d disabled, %d never signed in yet, %d active with no role (they see only About)." % (n_active, "" if n_active == 1 else "s", n_disabled, never, unassigned),
         "ok": True},
        {"key": "superadmin", "title": "Superadmin — who holds everything",
         "rec": "Exactly one superadmin: you. The flag is not a role; it grants every capability including the concealed tier. "
                "Grant a single concealed capability to a person with an extra grant instead of making them a superadmin.",
         "cur": "Superadmin now: %s." % (", ".join(a.display_name for a in sups) or "nobody — run access_bootstrap on the server"),
         "ok": len(sups) == 1},
        {"key": "permission_admin", "title": "Delegated administration",
         "rec": "Permission Admin is an ordinary role like Executive or Project Manager, and you can give it to as many people as you like (HR first). "
                "It creates and disables accounts and assigns the other roles. Only a superadmin grants or revokes Permission Admin itself, so an admin "
                "cannot mint peers; admins never see superadmin-tier capabilities, events or people.",
         "cur": "Permission admin now: %s." % (", ".join(a.display_name for a in admins) or "nobody assigned yet — assign HR in the console before go-live"),
         "ok": True},
        {"key": "conceal", "title": "Concealment of the superadmin tier",
         "rec": "Every superadmin-tier page answers 404, not 403, to anyone else — as if it did not exist — and its links, console entries and audit rows never render for them. "
                "The registry test enforces that a page requiring a superadmin capability is concealed; the override layer forces it too.",
         "cur": "%d concealed pages / endpoints; %d superadmin-tier capabilities." % (sum(1 for r in registry.URL_ACCESS.values() if r.get("conceal")), len(registry.superadmin_caps())),
         "ok": True},
        {"key": "denied", "title": "What a refused request looks like",
         "rec": "Unregistered URLs and concealed pages: 404. A registered page the person lacks a capability for: a friendly 403 page. "
                "The home page redirects people without the Command Center to the first section they can open. Every refusal is audited.",
         "cur": "Default-deny middleware; %d registered URLs; anything else is refused." % len(registry.URL_ACCESS), "ok": True},
        {"key": "sessions", "title": "Sessions & cookies",
         "rec": "Sessions expire after 12 hours; cookies are HttpOnly and SameSite=Lax; Secure cookies switch on automatically when DEBUG is off (TLS terminates at nginx). "
                "Disabling an account kills its sessions immediately.",
         "cur": "SESSION_COOKIE_AGE %dh · secure cookies %s · allowed hosts: %s." % (settings.SESSION_COOKIE_AGE // 3600, "on" if not settings.DEBUG else "off (DEBUG)", hosts),
         "ok": True},
        {"key": "impersonation", "title": "Viewing the app as someone else",
         "rec": "Superadmin-only 'View as…' renders any account's exact permissions (verified byte-for-byte by the test suite); every write is blocked while impersonating; start and stop are audited.",
         "cur": "impersonate.use is superadmin tier; %d active accounts are switchable." % max(n_active - 1, 0), "ok": True},
        {"key": "audit", "title": "Audit & usage",
         "rec": "Every login, refusal, permission change, impersonation and page view is an audit event. HR's change log excludes superadmin-tier events; "
                "you have the full audit and the usage dashboard. Every change made on this page is audited as a write action.",
         "cur": "%d audit events on record." % _count("access_auditevent"), "ok": True},
        {"key": "compensation", "title": "The compensation rule",
         "rec": "Two halves. Whose pay: $/h and wages show only for field-hourly employees (PTT form-1 submitters, derived nightly); salaried pay — including labor lines in "
                "project ledgers — is yours alone, never visible to any role. Who may look: Executive, Division Manager, Finance, Project Manager and Estimator (and you) — "
                "and never a field-hourly person themselves, who holds no rate capability at all whatever role they are given. rate_visible() and the context are the gates.",
         "cur": "%d employees flagged field-hourly · roles holding rates.field.view: %s." % (
             _count("core_employee", "is_field_hourly"),
             ", ".join(registry.ROLES[r]["label"] for r in registry.ROLES if "rates.field.view" in registry.role_caps_effective(r)) or "none"),
         "ok": True},
        {"key": "division_scope", "title": "Division scoping",
         "rec": "A Division Manager assignment carries division codes, and any page that scopes by division (the project map today, the Command Center when it reopens) "
                "shows that manager their own divisions only. Everyone else is unrestricted. Dormant while the Command Center is yours alone — the codes are still "
                "collected at assignment time so the scope is ready the day you open it up.",
         "cur": "%d division-manager assignment%s · Command Center: superadmin only." % (
             RoleAssignment.objects.filter(role="division_manager").count(),
             "" if RoleAssignment.objects.filter(role="division_manager").count() == 1 else "s"),
         "ok": True},
        {"key": "changes", "title": "Changing rules later",
         "rec": "Change what a page requires or what a role holds on this page — the change takes effect on the next request and is audited; 'Reset' returns to the code default. "
                "People are never named in rules: adjust roles or capabilities, and assign people to roles in the console.",
         "cur": "%d rule%s currently differ from the code defaults." % (len(overrides.changed_keys()), "" if len(overrides.changed_keys()) == 1 else "s"), "ok": True},
    ]
    return items


def _count(table, where_col=None):
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM %s%s" % (table, (" WHERE %s" % where_col) if where_col else ""))
        return cur.fetchone()[0]


# ---------------------------------------------------------------- decisions
def snapshot_for(kind, key, urls=None):
    """The effective rule as a comparable dict — stored on confirm, compared later to flag stale confirmations."""
    if kind == "view":
        r = registry.rule_for(key) or {}
        return {"caps": sorted(r.get("caps", ())), "conceal": bool(r.get("conceal")), "public": bool(r.get("public")), "auth": bool(r.get("auth"))}
    if kind == "role":
        return {"caps": sorted(registry.role_caps_effective(key))}
    if kind == "cap":
        m = registry.CAPABILITIES.get(key, {})
        return {"tier": m.get("tier"), "roles": sorted(r for r in registry.ROLES if key in registry.role_caps_effective(r))}
    if kind == "area":       # every page in the area with its effective rule — any change to any of them re-opens the area
        return area_snapshot([u for u in (urls if urls is not None else url_catalog()) if u["area"] == key])
    return {}


def area_snapshot(rows):
    return {u["name"]: [sorted(u["rule"].get("caps", ())), bool(u["rule"].get("conceal")), bool(u["rule"].get("public")), bool(u["rule"].get("auth"))]
            for u in rows}


def decisions():
    from .models import AccessDecision
    return {d.key: d for d in AccessDecision.objects.select_related("decided_by")}


def decorate(dkey, kind, key, decs, snap=None):
    """Decision state for one row: status (open / confirmed / stale), note, who / when."""
    d = decs.get(dkey)
    if snap is None:
        snap = snapshot_for(kind, key) if kind in ("view", "role", "cap", "area") else {}
    if d is None:
        return {"status": "open", "note": "", "by": None, "at": None, "snap": snap}
    status = d.status
    if status == "confirmed" and snap and d.snapshot and d.snapshot != snap:
        status = "stale"
    return {"status": status, "note": d.note, "by": d.decided_by.display_name if d.decided_by else None, "at": d.decided_at, "snap": snap}


def build():
    """Everything the template needs."""
    decs = decisions()
    changed = overrides.changed_keys()
    normal_caps = [c for c, m in registry.CAPABILITIES.items() if m["tier"] == registry.NORMAL]
    super_caps = [c for c, m in registry.CAPABILITIES.items() if m["tier"] == registry.SUPERADMIN]
    # pages
    urls = url_catalog()
    for u in urls:
        u["rule_text"] = rule_text(u["rule"])
        u["roles"] = [] if (u["rule"].get("public") or u["rule"].get("auth")) else roles_with(u["rule"].get("caps", ()))
        u["everyone"] = bool(u["rule"].get("public") or u["rule"].get("auth"))
        u["changed"] = ("view", u["name"]) in changed
        u["base_text"] = rule_text(u["base"])
        u["dec"] = decorate("view:" + u["name"], "view", u["name"], decs)
        u["super"] = bool(set(u["rule"].get("caps", ())) & set(super_caps))
        u["caps"] = list(u["rule"].get("caps", ()))
    areas = []
    for key, label, blurb in AREAS:
        rows = [u for u in urls if u["area"] == key]
        if not rows:
            continue
        # one decision per area: its pages grouped by the rule they share ("these 21 pages require finance.view")
        groups = defaultdict(list)
        for u in rows:
            groups[(tuple(u["rule"].get("caps", ())), bool(u["rule"].get("conceal")), bool(u["rule"].get("public")), bool(u["rule"].get("auth")))].append(u)
        rules = []
        for (caps, conceal, public, auth), members in groups.items():
            rules.append({"caps": list(caps), "conceal": conceal, "everyone": public or auth,
                          "text": rule_text({"caps": caps, "public": public, "auth": auth}),
                          "roles": [] if (public or auth) else roles_with(caps),
                          "super": bool(set(caps) & set(super_caps)), "rows": members, "n": len(members),
                          "n_pages": sum(1 for u in members if u["kind"] == "page"), "n_actions": sum(1 for u in members if u["kind"] == "action")})
        rules.sort(key=lambda g: (g["everyone"], len(g["caps"]), -g["n"]))
        areas.append({"key": key, "label": label, "blurb": blurb, "rows": rows, "rules": rules, "n": len(rows),
                      "changed": any(u["changed"] for u in rows),
                      "dec": decorate("area:" + key, "area", key, decs, snap=area_snapshot(rows))})
    # capabilities
    caps = []
    by_cap_pages = defaultdict(list)
    for u in urls:
        for c in u["rule"].get("caps", ()):
            by_cap_pages[c].append(u)
    frags = fragment_usage()
    frag_by_cap = defaultdict(list)
    for f in frags.values():
        if f["cap"]:
            frag_by_cap[f["cap"]].append(f)
    for c, m in registry.CAPABILITIES.items():
        caps.append({"cap": c, "tier": m["tier"], "desc": m["desc"], "pages": by_cap_pages.get(c, []),
                     "frags": sum(f["count"] for f in frag_by_cap.get(c, [])),
                     "roles": [r for r in registry.ROLES if c in registry.role_caps_effective(r)],
                     "dec": decorate("cap:" + c, "cap", c, decs)})
    # roles
    from .models import RoleAssignment
    members = defaultdict(list)
    for ra in RoleAssignment.objects.select_related("account"):
        members[ra.role].append(ra.account.display_name)
    roles = []
    matrix_areas = [(k, l) for k, l, _b in AREAS if k in AREA_CAPS or k == "superadmin"]
    for r, meta in registry.ROLES.items():
        eff = registry.role_caps_effective(r)
        base = set(meta["caps"])
        # what the role can do, area by area — the readable form of its capability list
        by_area, cannot, cells = [], [], []
        for k, l in matrix_areas:
            held = [short for c, short in AREA_CAPS.get(k, []) if c in eff]
            cells.append({"area": k, "labels": held, "caps": [c for c, _s in AREA_CAPS.get(k, []) if c in eff]})
            (by_area if held else cannot).append((l, held))
        roles.append({"role": r, "label": meta["label"], "scoped": meta["scoped"], "admin_visible": meta["admin_visible"],
                      "superadmin_assigned": r in registry.SUPERADMIN_ASSIGNED, "caps": eff,
                      "added": sorted(eff - base), "removed": sorted(base - eff), "changed": ("role", r) in changed,
                      "by_area": by_area, "cannot": [l for l, _h in cannot], "cells": cells,
                      "members": sorted(members.get(r, [])), "pages": sum(1 for u in urls if not u["everyone"] and set(u["rule"].get("caps", ())) <= eff),
                      "dec": decorate("role:" + r, "role", r, decs)})
    # the matrix the template draws: one row per area, one cell per role (the superadmin column is implied — always ✓)
    matrix = [{"key": k, "label": l, "caps": AREA_CAPS.get(k, []) or [(c, c) for c in super_caps],
               "cells": [dict(r["cells"][i], role=r["role"], label=r["label"]) for r in roles]}
              for i, (k, l) in enumerate(matrix_areas)]
    # fragments
    frag_rows = []
    for prop, f in frags.items():
        f = dict(f)
        f["dec"] = decorate("frag:" + prop, "frag", prop, decs)
        f["roles"] = [r for r in registry.ROLES if f["cap"] and f["cap"] in registry.role_caps_effective(r)] if f["cap"] else []
        frag_rows.append(f)
    frag_rows.sort(key=lambda f: -f["count"])
    # sign-in items
    items = signin_items()
    for it in items:
        it["dec"] = decorate("item:" + it["key"], "item", it["key"], decs)
    for c in caps:
        c["area"] = CAP_AREA.get(c["cap"], "superadmin")
    # totals + readiness — the decisions are the sign-in items, the roles and the page rules per area; capabilities and
    # in-page gates are reference (their meaning is code, who holds them is the role decision, where they apply is the area)
    all_decs = [i["dec"] for i in items] + [r["dec"] for r in roles] + [a["dec"] for a in areas]
    total = len(all_decs)
    confirmed = sum(1 for d in all_decs if d["status"] == "confirmed")
    stale = sum(1 for d in all_decs if d["status"] == "stale")
    urlconf_names = {name for _ns, name, _p, _cb in _walk(get_resolver().url_patterns) if _ns not in ("admin", "oidc")}
    unregistered = sorted(urlconf_names - set(registry.URL_ACCESS))
    role_leak = [r["role"] for r in roles if set(r["caps"]) & set(super_caps)]
    unconcealed = [u["name"] for u in urls if u["super"] and not u["rule"].get("conceal")]
    from .models import Account
    sups = Account.objects.filter(is_superadmin=True).count()
    checks = [
        {"label": "Every decision confirmed", "ok": confirmed == total, "detail": "%d of %d confirmed%s" % (confirmed, total, (" · %d changed since confirmation" % stale) if stale else "")},
        {"label": "Every URL is registered (default-deny)", "ok": not unregistered, "detail": "unregistered: %s" % ", ".join(unregistered) if unregistered else "%d URLs, all registered" % len(urls)},
        {"label": "No role carries a superadmin-tier capability", "ok": not role_leak, "detail": ", ".join(role_leak) if role_leak else "checked every role, defaults and overrides"},
        {"label": "Superadmin-tier pages are concealed", "ok": not unconcealed, "detail": ", ".join(unconcealed) if unconcealed else "%d concealed" % sum(1 for u in urls if u["rule"].get("conceal"))},
        {"label": "Exactly one superadmin", "ok": sups == 1, "detail": "%d account%s hold the flag" % (sups, "" if sups == 1 else "s")},
        {"label": "Production sign-in is Entra SSO", "ok": settings.PCA_AUTH_MODE == "sso", "detail": "this machine runs %s — set PCA_AUTH_MODE=sso in the server's .env" % settings.PCA_AUTH_MODE, "soft": True},
        {"label": "Access test suite", "ok": None, "detail": "run  python manage.py test tests.access  before deploying — registry, URL × role sweep, sentinel leakage, concealment, impersonation"},
    ]
    return {"areas": areas, "caps": caps, "normal_caps": normal_caps, "super_caps": super_caps, "roles": roles, "matrix": matrix, "frags": frag_rows, "items": items,
            "total": total, "confirmed": confirmed, "stale": stale, "open": total - confirmed, "changed_n": len(changed), "checks": checks,
            "noted": sum(1 for d in all_decs + [c["dec"] for c in caps] + [f["dec"] for f in frag_rows] if d["note"]),   # reference notes count too
            "ready": all(c["ok"] for c in checks if c["ok"] is not None and not c.get("soft")) and confirmed == total,
            "n_urls": len(urls), "n_caps": len(caps), "n_roles": len(roles), "n_frags": sum(f["count"] for f in frag_rows)}
