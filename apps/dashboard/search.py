"""Global search — the sidebar box (base.html #gsearch, search.js) and the /search/ results page.

One query, every kind of thing the app knows by name or number: projects (number, title, customer PO, quote
reference), people (SL employees / PTT people), customers, vendors, 010 orders & quotes (CNET), divisions and the
app's own pages, and — for an identifier-shaped query — every document number the local copy carries: shippers,
SL sales orders, AR invoices / credit memos, AR payments, AP vouchers, vendor invoice numbers, checks, POs and serial
numbers, each opening the page that shows it with ?hl=<number> (base.html reveals and flashes the row). Everything is
read from the local tables fed by SL / PTT / CNET; nothing external.

Matching: every word of the query has to appear (prefix of a word beats a substring, a whole-phrase prefix beats
both); a numeric query is a project-number prefix; when a name search finds almost nothing and pg_trgm is
installed (core migration 0007), trigram word similarity catches typos ("hosptal"). Results are ranked in
Python (`score_text` / `score_number`, pure, unit-tested) with small boosts for what is live now: open jobs,
recent activity, active people / customers / vendors.

Permissions: each group is only searched when the viewer holds the capability its pages need (projects.view,
people.view, customers.view, finance.view, sales010.view); pages follow their own flags. The endpoints themselves
are AUTHENTICATED in access.registry — the gating happens here, per group.
"""

import re
from datetime import date, timedelta
from functools import lru_cache

from django.urls import reverse

from apps.ingestion.bulk import fetch_dict

STATE_LABEL = {
    "in_progress": "In progress", "awarded_not_started": "Awarded, not started", "field_complete": "Field complete",
    "dormant": "Dormant", "closed_stabilized": "Closed", "closed_stabilizing": "Closing", "canceled": "Canceled",
    "template": "Template", "unknown": "Unknown",
}
OPEN_STATES = ("awarded_not_started", "in_progress", "field_complete", "dormant")
ROLE_LABEL = {"head_pm": "Division head", "pm": "Project manager"}
GROUPS = [("project", "Projects"), ("bid", "Bids"), ("person", "People"), ("customer", "Customers"), ("vendor", "Vendors"),
          ("order", "010 orders & quotes"), ("document", "Documents"), ("file", "Files"), ("division", "Divisions"), ("page", "Pages")]
GROUP_LABEL = dict(GROUPS)
GROUP_LABEL["private_note"] = "Private notes · this Mac"

# The app's pages: label, URL name, sidebar group, capability flags (all required, AccessContext attribute names),
# extra words the page answers to. Keep in sidebar order (base.html).
PAGES = [
    ("Project Snapshot", "dashboard:project_snapshot", "Pinned", ("projects", "margins"), "daily weekly window activity hours crews delta earned"),
    ("Daily Financial Snapshot", "dashboard:finance_daily", "Pinned", ("finance",), "cash balance sheet net income ar ap wip ratios bank"),
    ("Command Center", "dashboard:command_center", "Pinned", ("command_center",), "home overview yearly pnl margins histogram scatter"),
    ("All Projects", "dashboard:project_list", "Projects", ("projects",), "jobs list filter lifecycle"),
    ("Active Book & Forecast", "dashboard:forecast", "Projects", ("projects", "margins"), "eac risk open jobs prediction remaining hours"),
    ("Project Map", "dashboard:project_map", "Projects", ("projects",), "map towers locations sites geography"),
    ("Project Managers", "dashboard:people", "People & Customers", ("people",), "pm pms people managers"),
    ("Field Crew", "dashboard:field", "People & Customers", ("projects",), "technicians foremen apprentices crew hours field"),
    ("Customers & Sectors", "dashboard:customers", "People & Customers", ("customers",), "market sector clients"),
    ("Ratings", "dashboard:ratings", "People & Customers", ("ratings",), "sabermetrics adjusted context"),
    ("Divisional P&L", "dashboard:finance_pnl", "Finance", ("finance",), "income statement profit loss month quarter year division overhead"),
    ("WIP by Job", "dashboard:finance_wip", "Finance", ("finance",), "wip over under billing earned unbilled work in process"),
    ("Billings", "dashboard:finance_billings", "Finance", ("finance",), "invoices billed revenue day"),
    ("Receivables", "dashboard:finance_ar", "Finance", ("finance",), "ar aging receivables over 90 collections invoices open"),
    ("Payments Received", "dashboard:finance_payments", "Finance", ("finance",), "cash receipts payment applications checks"),
    ("Vendors", "dashboard:finance_vendors", "Finance", ("finance",), "ap suppliers paid purchase orders"),
    ("Bank Reconciliation", "dashboard:finance_bank", "Finance", ("finance",), "bmo statement cash reconcile"),
    ("Sales Tax", "dashboard:finance_salestax", "Finance", ("salestax",), "tax liability state remittance nexus"),
    ("010 Overview", "dashboard:sales010_overview", "010 Hardware Sales", ("sales010",), "hardware sales cnet channelonline"),
    ("010 Daily Snapshot", "dashboard:sales010_snapshot", "010 Hardware Sales", ("sales010",), "hardware sales daily"),
    ("010 Quote Pipeline", "dashboard:sales010_pipeline", "010 Hardware Sales", ("sales010",), "quotes pipeline backlog"),
    ("010 Orders", "dashboard:sales010_orders", "010 Hardware Sales", ("sales010",), "sales orders hardware"),
    ("010 Products & Drift", "dashboard:sales010_products", "010 Hardware Sales", ("sales010",), "products margin drift skus"),
    ("010 Serial Lookup", "dashboard:sales010_serials", "010 Hardware Sales", ("sales010",), "serial numbers shipped"),
    ("010 Data Hygiene", "dashboard:sales010_hygiene", "010 Hardware Sales", ("sales010",), "hygiene mismatches"),
    ("Topics & Commitments", "dashboard:insight_topics", "Insights & Reports", ("notes",), "meeting notes commitments insights"),
    ("Overhead Allocation", "dashboard:finance_allocations", "System", ("finance",), "corporate overhead shares divisions pool"),
    ("People & Access", "access:console_people", "System", ("console",), "accounts roles permissions users"),
    ("Data Quality & Refresh", "dashboard:data_quality", "System", ("ops",), "refresh ingestion issues checksum"),
    ("Definitions", "dashboard:about", "System", (), "about metric dictionary help glossary"),
]

_WORD = re.compile(r"[a-z0-9]+")
CAP = 400   # candidates pulled per group before ranking; a group that hits it reports its count as "400+"
_NON_DIGIT = re.compile(r"\D")


# ------------------------------------------------------------------------------- pure ranking
def tokenize(q):
    """Lower-cased words of the query, in order (empty list for a blank query)."""
    return [t for t in re.split(r"\s+", (q or "").strip().lower()) if t]


def score_text(text, tokens):
    """0..100 — how well a name / title answers the query words. Whole phrase = 100, phrase prefix = 92,
    phrase inside at a word start = 84, every word a word-prefix = 76, every word somewhere inside = 62,
    part of the words = up to 40, nothing = 0. Words under four letters only count at a word start."""
    t = (text or "").lower().strip()
    if not t or not tokens:
        return 0
    phrase = " ".join(tokens)
    if t == phrase:
        return 100
    if t.startswith(phrase):
        return 92
    words = _WORD.findall(t)
    if all(any(w.startswith(tok) for w in words) for tok in tokens):
        return 84 if (" " + t).find(" " + phrase) >= 0 else 76
    if any(len(tok) < 4 for tok in tokens):   # a short word must start a word: "wip" is not inside "swipe"
        return 0
    if all(tok in t for tok in tokens):
        return 62
    hits = sum(1 for tok in tokens if tok in t)
    part = int(40 * hits / len(tokens)) if hits else 0
    return part if part >= 30 else 0   # fewer than three quarters of the words: not a hit


def is_number_query(q):
    """A project-number style query: digits (a hyphen allowed, as in 260071-000000), at least two of them."""
    s = (q or "").strip().replace("-", "")
    return len(s) >= 2 and s.isdigit()


def score_number(display_number, canonical, q):
    """0..100 — a numeric query against a project number: exact = 100, prefix = 90 + digits typed (max 98),
    inside the number = 55. The trailing 000000 is part of the number (never stripped, CLAUDE.md)."""
    if not is_number_query(q):
        return 0
    qn = (q or "").strip().replace("-", "")
    for cand in (_NON_DIGIT.sub("", display_number or ""), (canonical or "").lower()):
        if not cand:
            continue
        if cand == qn:
            return 100
        if cand.startswith(qn):
            return 90 + min(len(qn), 8)
    if qn in (canonical or ""):
        return 55
    return 0


def page_score(label, keywords, tokens):
    """Pages answer to their label first, then to their keywords."""
    s = score_text(label, tokens)
    if s:
        return s
    k = score_text("%s %s" % (label, keywords), tokens)
    return min(k, 70) if k else 0


def allowed_pages(acc):
    """The PAGES rows this viewer may open (every flag in the row must be truthy on the AccessContext)."""
    return [p for p in PAGES if all(getattr(acc, f, False) for f in p[3])]


# ------------------------------------------------------------------------------- SQL helpers
@lru_cache(maxsize=1)
def trigram_available():
    try:
        return bool(fetch_dict("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))
    except Exception:   # pragma: no cover
        return False


def _like(tok):
    return "%" + tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _all_tokens(col, tokens):
    """SQL fragment + params: every token appears in `col` (ILIKE)."""
    if not tokens:
        return "FALSE", []
    return "(" + " AND ".join("%s ILIKE %%s" % col for _ in tokens) + ")", [_like(t) for t in tokens]


def _fuzzy_clause(col, q):
    """Trigram fallback (typos): the query word is close to a word of the column. Single-word queries only —
    for several words the AND-of-words ILIKE is already forgiving, and word similarity would let one matching word
    through ("Pacific Construction" is not "CONSTRUCTION INC.")."""
    return "word_similarity(%%s, %s) >= 0.55" % col, [q]


def _when(d):
    """Short 'last activity' label for a date."""
    if not d:
        return ""
    today = date.today()
    if d >= today - timedelta(days=200):
        return d.strftime("%b %-d")
    return d.strftime("%b %Y")


# ------------------------------------------------------------------------------- the groups
def _projects(q, tokens, limit):
    numeric = is_number_query(q)
    parts, params = [], []
    if numeric:
        qn = q.strip().replace("-", "")
        parts.append("REPLACE(p.display_number, '-', '') ILIKE %s OR p.canonical_project_number ILIKE %s")
        params += [qn + "%", qn + "%"]
    frag, ps = _all_tokens("p.title", tokens)
    parts.append(frag); params += ps
    if len(q.strip()) >= 3:
        parts.append("p.customer_po ILIKE %s OR p.quote_reference ILIKE %s")
        params += [_like(q.strip()), _like(q.strip())]
    where = " OR ".join("(%s)" % x for x in parts)
    sql = """
        SELECT p.canonical_project_number cpn, p.display_number, p.title, p.lifecycle_state, p.last_transaction_date,
               p.customer_po, p.quote_reference, d.code div, c.canonical_name customer, e.canonical_name pm, 0.0 sim
        FROM core_project p JOIN core_division d ON d.id = p.division_id
        LEFT JOIN core_customer c ON c.id = p.customer_id LEFT JOIN core_employee e ON e.id = p.project_manager_id
        WHERE (%s) AND NOT p.is_template_or_void
        ORDER BY p.last_transaction_date DESC NULLS LAST LIMIT %d""" % (where, CAP)
    rows = fetch_dict(sql, params)
    capped = len(rows) >= CAP
    if len(rows) < 3 and not numeric and len(q.strip()) >= 4 and len(tokens) == 1 and trigram_available():
        fz, fp = _fuzzy_clause("p.title", q.strip())
        rows += fetch_dict("""
            SELECT p.canonical_project_number cpn, p.display_number, p.title, p.lifecycle_state, p.last_transaction_date,
                   p.customer_po, p.quote_reference, d.code div, c.canonical_name customer, e.canonical_name pm, word_similarity(%%s, p.title) sim
            FROM core_project p JOIN core_division d ON d.id = p.division_id
            LEFT JOIN core_customer c ON c.id = p.customer_id LEFT JOIN core_employee e ON e.id = p.project_manager_id
            WHERE %s AND NOT p.is_template_or_void AND NOT (p.canonical_project_number = ANY(%%s))
            ORDER BY word_similarity(%%s, p.title) DESC, p.last_transaction_date DESC NULLS LAST LIMIT 20""" % fz,
            [q.strip()] + fp + [[r["cpn"] for r in rows], q.strip()])
    today = date.today()
    out = []
    for r in rows:
        s = max(score_number(r["display_number"], r["cpn"], q), score_text(r["title"], tokens))
        if not s and len(q.strip()) >= 3 and (q.strip().lower() in (r["customer_po"] or "").lower() or q.strip().lower() in (r["quote_reference"] or "").lower()):
            s = 58
        if not s and r["sim"]:
            s = int(55 * float(r["sim"]))
        if not s:
            continue
        if r["lifecycle_state"] in OPEN_STATES:
            s += 8
        elif r["lifecycle_state"] in ("canceled", "template"):
            s -= 15
        if r["last_transaction_date"] and r["last_transaction_date"] >= today - timedelta(days=120):
            s += 4
        state = STATE_LABEL.get(r["lifecycle_state"], r["lifecycle_state"] or "")
        sub = " · ".join(x for x in (r["customer"] or "", state, ("PM " + r["pm"]) if r["pm"] else "") if x)
        note = ""
        if len(q.strip()) >= 3 and r["customer_po"] and q.strip().lower() in r["customer_po"].lower():
            note = "PO " + r["customer_po"]
        elif len(q.strip()) >= 3 and r["quote_reference"] and q.strip().lower() in r["quote_reference"].lower():
            note = "quote " + r["quote_reference"]
        out.append({"type": "project", "key": r["cpn"], "url": reverse("dashboard:project_detail", args=[r["cpn"]]),
                    "number": r["display_number"], "title": r["title"] or "(untitled)", "sub": sub + ((" · " + note) if note else ""),
                    "ava": r["div"], "tags": [{"t": state, "c": r["lifecycle_state"] or ""}],
                    "meta": _when(r["last_transaction_date"]), "score": s,
                    "customer": r["customer"] or "", "division": r["div"], "state": state, "state_key": r["lifecycle_state"] or "",
                    "pm": r["pm"] or "", "last": r["last_transaction_date"], "note": note,
                    "_sort": (r["last_transaction_date"] or date.min, r["cpn"])})
    out.sort(key=lambda x: (-x["score"], -(x["_sort"][0].toordinal()), x["_sort"][1]))
    return out, len(out), capped


def _people(q, tokens, limit, divmap):
    frag, ps = _all_tokens("e.canonical_name", tokens)
    rows = fetch_dict("""
        SELECT e.employee_key k, e.canonical_name name, e.ptt_employee_role role, e.classification, e.active, e.home_subaccount sub,
               e.is_field_hourly, e.termination_date, 0.0 sim
        FROM core_employee e WHERE (%s OR e.employee_key ILIKE %%s) AND e.canonical_name <> '' LIMIT %d""" % (frag, CAP), ps + [q.strip() + "%"])
    capped = len(rows) >= CAP
    if len(rows) < 3 and len(q.strip()) >= 4 and not is_number_query(q) and len(tokens) == 1 and trigram_available():
        fz, fp = _fuzzy_clause("e.canonical_name", q.strip())
        rows += fetch_dict("""
            SELECT e.employee_key k, e.canonical_name name, e.ptt_employee_role role, e.classification, e.active, e.home_subaccount sub,
                   e.is_field_hourly, e.termination_date, word_similarity(%%s, e.canonical_name) sim
            FROM core_employee e WHERE %s AND e.canonical_name <> '' AND NOT (e.employee_key = ANY(%%s))
            ORDER BY word_similarity(%%s, e.canonical_name) DESC LIMIT 12""" % fz, [q.strip()] + fp + [[r["k"] for r in rows], q.strip()])
    out = []
    for r in rows:
        s = score_text(r["name"], tokens) or (score_text(r["k"], tokens) and 70) or int(55 * float(r["sim"] or 0))
        if not s:
            continue
        if r["active"]:
            s += 8
        if r["role"] in ROLE_LABEL:
            s += 3
        div = divmap.get((r["sub"] or "")[:4]) or divmap.get((r["sub"] or "")[:3]) or ""
        role = ROLE_LABEL.get(r["role"] or "", "")
        cls = (r["classification"] or "").strip()
        sub = " · ".join(x for x in (role, cls if cls and cls != role else "", div, "" if r["active"] else "inactive") if x)
        initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", r["name"])[:2]).upper() or "?"
        tags = ([{"t": div.split(" ")[0], "c": "div"}] if div else []) + ([] if r["active"] else [{"t": "inactive", "c": "inactive"}])
        out.append({"type": "person", "key": r["k"], "url": reverse("dashboard:person_detail", args=[r["k"]]), "number": "",
                    "title": r["name"], "sub": sub, "ava": initials, "tags": tags, "meta": "", "score": s,
                    "role": role, "classification": cls, "division": div, "active": bool(r["active"])})
    out.sort(key=lambda x: (-x["score"], x["title"]))
    return out, len(out), capped


def _customers(q, tokens, limit):
    frag, ps = _all_tokens("c.canonical_name", tokens)
    rows = fetch_dict("""
        SELECT c.sl_customer_id k, c.canonical_name name, c.market_sector sector, c.city, c.state, c.active,
               (SELECT COUNT(*) FROM core_project p WHERE p.customer_id = c.id AND p.lifecycle_state = ANY(%%s)) open_jobs,
               (SELECT COUNT(*) FROM core_project p WHERE p.customer_id = c.id) jobs, 0.0 sim
        FROM core_customer c WHERE (%s OR c.sl_customer_id ILIKE %%s) LIMIT %d""" % (frag, CAP), [list(OPEN_STATES)] + ps + [q.strip() + "%"])
    capped = len(rows) >= CAP
    if len(rows) < 3 and len(q.strip()) >= 4 and not is_number_query(q) and len(tokens) == 1 and trigram_available():
        fz, fp = _fuzzy_clause("c.canonical_name", q.strip())
        rows += fetch_dict("""
            SELECT c.sl_customer_id k, c.canonical_name name, c.market_sector sector, c.city, c.state, c.active,
                   (SELECT COUNT(*) FROM core_project p WHERE p.customer_id = c.id AND p.lifecycle_state = ANY(%%s)) open_jobs,
                   (SELECT COUNT(*) FROM core_project p WHERE p.customer_id = c.id) jobs, word_similarity(%%s, c.canonical_name) sim
            FROM core_customer c WHERE %s AND NOT (c.sl_customer_id = ANY(%%s))
            ORDER BY word_similarity(%%s, c.canonical_name) DESC LIMIT 12""" % fz,
            [list(OPEN_STATES), q.strip()] + fp + [[r["k"] for r in rows], q.strip()])
    out = []
    for r in rows:
        s = score_text(r["name"], tokens) or (score_text(r["k"], tokens) and 70) or int(55 * float(r["sim"] or 0))
        if not s:
            continue
        if r["active"]:
            s += 3
        if r["open_jobs"]:
            s += 6
        elif r["jobs"]:
            s += 2
        place = ", ".join(x for x in ((r["city"] or "").title(), r["state"] or "") if x)
        jobs = ("%d open job%s" % (r["open_jobs"], "" if r["open_jobs"] == 1 else "s")) if r["open_jobs"] else ("%d job%s" % (r["jobs"], "" if r["jobs"] == 1 else "s") if r["jobs"] else "")
        sub = " · ".join(x for x in (r["sector"] or "", place, jobs, "" if r["active"] else "inactive") if x)
        out.append({"type": "customer", "key": r["k"], "url": reverse("dashboard:customer_detail", args=[r["k"]]), "number": "",
                    "title": r["name"], "sub": sub, "ava": "", "tags": ([{"t": r["sector"], "c": ""}] if r["sector"] else []), "meta": "", "score": s,
                    "sector": r["sector"] or "", "place": place, "open_jobs": r["open_jobs"], "jobs": r["jobs"], "active": bool(r["active"])})
    out.sort(key=lambda x: (-x["score"], x["title"]))
    return out, len(out), capped


def _vendors(q, tokens, limit):
    frag, ps = _all_tokens("v.name", tokens)
    rows = fetch_dict("""
        SELECT v.vendor_id k, v.name, v.status, v.city, 0.0 sim FROM finance_slvendor v
        WHERE (%s OR v.vendor_id ILIKE %%s) AND v.name <> '' LIMIT %d""" % (frag, CAP), ps + [q.strip() + "%"])
    capped = len(rows) >= CAP
    if len(rows) < 3 and len(q.strip()) >= 4 and not is_number_query(q) and len(tokens) == 1 and trigram_available():
        fz, fp = _fuzzy_clause("v.name", q.strip())
        rows += fetch_dict("""
            SELECT v.vendor_id k, v.name, v.status, v.city, word_similarity(%%s, v.name) sim FROM finance_slvendor v
            WHERE %s AND v.name <> '' AND NOT (v.vendor_id = ANY(%%s)) ORDER BY word_similarity(%%s, v.name) DESC LIMIT 12""" % fz,
            [q.strip()] + fp + [[r["k"] for r in rows], q.strip()])
    out = []
    for r in rows:
        s = score_text(r["name"], tokens) or (score_text(r["k"], tokens) and 70) or int(55 * float(r["sim"] or 0))
        if not s:
            continue
        active = (r["status"] or "").upper() == "A"
        if active:
            s += 3
        sub = " · ".join(x for x in ((r["city"] or "").title(), "" if active else "inactive") if x)
        out.append({"type": "vendor", "key": r["k"], "url": reverse("dashboard:finance_vendor", args=[r["k"]]), "number": "",
                    "title": r["name"], "sub": sub, "ava": "", "tags": ([] if active else [{"t": "inactive", "c": "inactive"}]), "meta": "", "score": s,
                    "place": (r["city"] or "").title(), "active": active})
    out.sort(key=lambda x: (-x["score"], x["title"]))
    return out, len(out), capped


def _orders(q, tokens, limit):
    frag, ps = _all_tokens("NULLIF(d.description, 'None') || ' ' || COALESCE(d.customer_company, '')", tokens)
    po_ok = len(q.strip()) >= 4
    rows = fetch_dict("""
        SELECT d.document_number num, d.doc_type, d.status, NULLIF(d.description, 'None') description, d.customer_company company, d.customer_po po,
               d.created_at, d.ordered_at, d.salesperson_name rep
        FROM sales_cnetdocument d
        WHERE NOT d.deleted AND (d.document_number ILIKE %%s OR (%%s AND d.customer_po ILIKE %%s) OR %s)
        ORDER BY d.created_at DESC NULLS LAST LIMIT %d""" % (frag, CAP), [q.strip() + "%", po_ok, _like(q.strip())] + ps)
    capped = len(rows) >= CAP
    out, seen = [], set()
    for r in rows:
        if r["num"] in seen:
            continue
        seen.add(r["num"])
        s = 0
        if (r["num"] or "").lower().startswith(q.strip().lower()):
            s = 100 if (r["num"] or "").lower() == q.strip().lower() else 90
        elif po_ok and r["po"] and q.strip().lower() in r["po"].lower():
            s = 70
        else:
            s = max(score_text(r["description"], tokens), score_text(r["company"], tokens))
        if not s:
            continue
        kind = "Sales order" if r["doc_type"] == "sales_order" else "Quote"
        if r["doc_type"] == "sales_order":
            s += 2
        when = r["ordered_at"] or r["created_at"]
        sub = " · ".join(x for x in (kind, r["company"] or "", (r["status"] or "").replace("_", " "), ("PO " + r["po"]) if r["po"] else "") if x)
        out.append({"type": "order", "key": r["num"], "url": reverse("dashboard:sales010_order_detail", args=[r["num"]]),
                    "number": r["num"], "title": r["description"] or (r["company"] or "(no description)"), "sub": sub, "ava": "SO" if r["doc_type"] == "sales_order" else "Q",
                    "tags": [{"t": kind, "c": ""}], "meta": _when(when.date() if when else None), "score": s,
                    "kind": kind, "company": r["company"] or "", "status": (r["status"] or "").replace("_", " "), "po": r["po"] or "",
                    "when": when.date() if when else None, "rep": r["rep"] or ""})
    out.sort(key=lambda x: (-x["score"], x["number"]))
    return out, len(out), capped


# ------------------------------------------------------------------------------- documents (numbers)
DOC_AVA = {"Shipper": "SH", "SL order": "SO", "AR invoice": "INV", "AR credit memo": "CM", "AR debit memo": "DM", "AR payment": "PAY",
           "AP voucher": "VO", "Vendor invoice": "INV", "Check": "CK", "EFT": "EFT", "Hand check": "HC", "Void": "VC", "PO": "PO", "Serial": "S/N"}
AP_KIND = {"CK": "Check", "EP": "EFT", "HC": "Hand check", "VC": "Void", "VO": "AP voucher", "AD": "AP adjustment", "AC": "AP credit"}
AR_KIND = {"IN": "AR invoice", "CM": "AR credit memo", "DM": "AR debit memo"}


def is_identifier_query(q):
    """One word, four or more characters, with a digit in it: SH00083034, ORD0053895, IN00080469, 83034, a serial."""
    q = (q or "").strip()
    return len(q) >= 4 and " " not in q and any(ch.isdigit() for ch in q)


def doc_score(value, q):
    """A document number against the typed text: exact 100, starts with 90, ends with (digits typed without the
    prefix: 83034 for SH00083034) 80, inside 60, no 0."""
    v, ql = (value or "").strip().lower(), (q or "").strip().lower()
    if not v or not ql:
        return 0
    if v == ql:
        return 100
    if v.startswith(ql):
        return 90
    if v.endswith(ql):
        return 80
    if ql in v:
        return 60
    return 0


def _money(v):
    if v is None:
        return ""
    v = float(v)
    return ("-" if v < 0 else "") + "${:,.0f}".format(abs(v))


def _doc(kind, number, title, parts, url, opens, when, amount, score, party=""):
    return {"type": "document", "kind": kind, "key": "%s:%s" % (kind, number), "number": number, "title": title or "",
            "sub": " · ".join(x for x in parts if x), "ava": DOC_AVA.get(kind, "#"), "tags": [{"t": kind, "c": "doc"}],
            "meta": _when(when), "score": score, "url": url, "opens": opens, "when": when, "amount": amount, "party": party}


def _documents(q, acc):
    """Every kind of document number the local copy of SL / CNET carries, each linking to the page that shows it with
    ?hl=<number> so the row is revealed and flashed (base.html)."""
    ql = q.strip()
    like = _like(ql)
    fin, sales, proj = (getattr(acc, f, False) for f in ("finance", "sales010", "projects"))
    out, seen = [], set()

    def add(item):
        k = (item["kind"], item["number"].lower())
        if item["score"] and k not in seen:
            seen.add(k)
            out.append(item)

    # ---- shippers: 010 order page when the order is CNET-linked, else the project the shipment went to
    if sales or proj:
        for r in fetch_dict("""
            SELECT s.shipper_id, s.ord_nbr, s.invc_nbr, s.ship_date, s.tot_invc, o.cnet_number, o.ship_name, c.canonical_name customer
            FROM sales_slcnetshipper s LEFT JOIN sales_slcnetorder o ON o.ord_nbr = s.ord_nbr LEFT JOIN core_customer c ON c.sl_customer_id = o.cust_id
            WHERE s.shipper_id ILIKE %s OR s.invc_nbr ILIKE %s ORDER BY s.ship_date DESC NULLS LAST LIMIT 30""", [like, like]):
            if not (sales and r["cnet_number"]):
                continue
            url = reverse("dashboard:sales010_order_detail", args=[r["cnet_number"]]) + "?hl=" + r["shipper_id"]
            add(_doc("Shipper", r["shipper_id"], r["customer"] or r["ship_name"] or "", ["on %s" % r["ord_nbr"], ("invoice " + r["invc_nbr"]) if r["invc_nbr"] else "", _money(r["tot_invc"]) + " invoiced" if r["tot_invc"] is not None else ""],
                     url, "010 order %s" % r["cnet_number"], r["ship_date"], r["tot_invc"], max(doc_score(r["shipper_id"], ql), doc_score(r["invc_nbr"], ql)), r["customer"] or r["ship_name"] or ""))
    if proj:
        for r in fetch_dict("""
            SELECT DISTINCT ON (l.shipper_id) l.shipper_id, l.so_nbr, l.invc_nbr, l.ship_date, p.canonical_project_number cpn, p.display_number pdisp, p.title,
                   SUM(l.tot_invc) OVER (PARTITION BY l.shipper_id) tot
            FROM finance_projectshipmentline l JOIN core_project p ON p.id = l.project_id
            WHERE l.shipper_id ILIKE %s OR l.invc_nbr ILIKE %s ORDER BY l.shipper_id, l.ship_date DESC LIMIT 30""", [like, like]):
            url = reverse("dashboard:project_detail", args=[r["cpn"]]) + "?hl=" + r["shipper_id"]
            add(_doc("Shipper", r["shipper_id"], "%s %s" % (r["pdisp"], r["title"] or ""), ["on %s" % r["so_nbr"], ("invoice " + r["invc_nbr"]) if r["invc_nbr"] else "", _money(r["tot"]) + " invoiced" if r["tot"] is not None else ""],
                     url, "project %s" % r["pdisp"], r["ship_date"], r["tot"], max(doc_score(r["shipper_id"], ql), doc_score(r["invc_nbr"], ql)), r["title"] or ""))

    # ---- SL sales orders: the CNET-linked ones open on the 010 order page, project-tied ones on the project
    if sales:
        for r in fetch_dict("""
            SELECT o.ord_nbr, o.cnet_number, o.cust_ord_nbr, o.ship_name, o.ord_date, o.tot_ord, o.status, c.canonical_name customer
            FROM sales_slcnetorder o LEFT JOIN core_customer c ON c.sl_customer_id = o.cust_id
            WHERE o.ord_nbr ILIKE %s OR o.cust_ord_nbr ILIKE %s ORDER BY o.ord_date DESC NULLS LAST LIMIT 30""", [like, like]):
            if not r["cnet_number"]:
                continue
            url = reverse("dashboard:sales010_order_detail", args=[r["cnet_number"]]) + "?hl=" + r["ord_nbr"]
            add(_doc("SL order", r["ord_nbr"], r["customer"] or r["ship_name"] or "", [("PO " + r["cust_ord_nbr"]) if r["cust_ord_nbr"] else "", "CNET %s" % r["cnet_number"], _money(r["tot_ord"])],
                     url, "010 order %s" % r["cnet_number"], r["ord_date"], r["tot_ord"], max(doc_score(r["ord_nbr"], ql), doc_score(r["cust_ord_nbr"], ql)), r["customer"] or ""))
    if proj:
        for r in fetch_dict("""
            SELECT DISTINCT ON (l.so_nbr) l.so_nbr, l.so_type, l.ship_name, l.ord_date, l.cust_ord_nbr, p.canonical_project_number cpn, p.display_number pdisp, p.title,
                   SUM(l.tot_ord) OVER (PARTITION BY l.so_nbr) tot
            FROM finance_projectsalesorderline l JOIN core_project p ON p.id = l.project_id
            WHERE l.so_nbr ILIKE %s OR l.cust_ord_nbr ILIKE %s ORDER BY l.so_nbr, l.ord_date DESC LIMIT 30""", [like, like]):
            url = reverse("dashboard:project_detail", args=[r["cpn"]]) + "?hl=" + r["so_nbr"]
            add(_doc("SL order", r["so_nbr"], "%s %s" % (r["pdisp"], r["title"] or ""), [r["so_type"] or "", ("PO " + r["cust_ord_nbr"]) if r["cust_ord_nbr"] else "", _money(r["tot"])],
                     url, "project %s" % r["pdisp"], r["ord_date"], r["tot"], max(doc_score(r["so_nbr"], ql), doc_score(r["cust_ord_nbr"], ql)), r["title"] or ""))

    if fin:
        # ---- AR invoices / credit memos (Billings page, the invoice's month; open ones also sit on Receivables)
        # a 010 (CNET-linked) invoice opens on its order page, where the shipper table names it; any other invoice opens
        # Billings for the month it is dated (basis=dated), filtered to the number so the row is on the page whatever fold it sits in
        for r in fetch_dict("""
            SELECT i.ref_nbr, i.doc_type, i.customer_name, i.customer_id_raw, i.doc_date, i.amount, i.cust_po, i.order_nbr, p.display_number pdisp, o.cnet_number,
                   EXISTS (SELECT 1 FROM finance_aropendocument od WHERE od.ref_nbr = i.ref_nbr AND od.doc_type = i.doc_type) is_open
            FROM finance_arinvoice i LEFT JOIN core_project p ON p.id = i.project_id
            LEFT JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND i.order_nbr <> ''
            WHERE i.ref_nbr ILIKE %s OR i.cust_po ILIKE %s ORDER BY i.doc_date DESC NULLS LAST LIMIT 30""", [like, like]):
            kind = AR_KIND.get(r["doc_type"], "AR " + (r["doc_type"] or ""))
            if r["cnet_number"] and sales:
                url, opens = reverse("dashboard:sales010_order_detail", args=[r["cnet_number"]]) + "?hl=" + r["ref_nbr"], "010 order %s" % r["cnet_number"]
            elif r["doc_date"]:
                url = reverse("dashboard:finance_billings") + "?period=%s&basis=dated&q=%s&hl=%s" % (r["doc_date"].strftime("%Y-%m"), _urlq(r["ref_nbr"]), r["ref_nbr"])
                opens = "Billings, %s" % r["doc_date"].strftime("%b %Y")
            else:
                url, opens = reverse("dashboard:finance_billings") + "?q=%s&hl=%s" % (_urlq(r["ref_nbr"]), r["ref_nbr"]), "Billings"
            parts = [("job " + r["pdisp"]) if r["pdisp"] else "", ("PO " + r["cust_po"]) if r["cust_po"] else "", ("on " + r["order_nbr"]) if r["order_nbr"] else "", _money(r["amount"]), "open" if r["is_open"] else ""]
            add(_doc(kind, r["ref_nbr"], r["customer_name"] or "", parts, url, opens,
                     r["doc_date"], r["amount"], max(doc_score(r["ref_nbr"], ql), doc_score(r["cust_po"], ql)), r["customer_name"] or ""))
        # ---- AR payments (Payments Received, the payment's day)
        for r in fetch_dict("""
            SELECT ref_nbr, customer_name, doc_date, orig_amt FROM finance_arpayment
            WHERE ref_nbr ILIKE %s ORDER BY doc_date DESC NULLS LAST LIMIT 20""", [like]):
            url = reverse("dashboard:finance_payments") + ("?day=%s&hl=%s" % (r["doc_date"].isoformat(), r["ref_nbr"]) if r["doc_date"] else "?hl=" + r["ref_nbr"])
            add(_doc("AR payment", r["ref_nbr"], r["customer_name"] or "", [_money(r["orig_amt"])], url, "Payments Received", r["doc_date"], r["orig_amt"], doc_score(r["ref_nbr"], ql), r["customer_name"] or ""))
        # ---- AP: open vouchers, payments (checks / EFTs), PO vouchers, paid vouchers by the vendor's invoice number
        for r in fetch_dict("""
            SELECT ref_nbr, doc_type, vendor_id, vendor_name, invoice_nbr, po_nbr, doc_date, doc_bal FROM finance_apopendocument
            WHERE ref_nbr ILIKE %s OR invoice_nbr ILIKE %s OR po_nbr ILIKE %s ORDER BY doc_date DESC NULLS LAST LIMIT 20""", [like, like, like]):
            url = reverse("dashboard:finance_vendor", args=[r["vendor_id"]]) + "?hl=" + r["ref_nbr"]
            add(_doc(AP_KIND.get(r["doc_type"], "AP " + (r["doc_type"] or "")), r["ref_nbr"], r["vendor_name"] or r["vendor_id"], [("vendor invoice " + r["invoice_nbr"]) if r["invoice_nbr"] else "", ("PO " + r["po_nbr"]) if r["po_nbr"] else "", _money(r["doc_bal"]) + " open"],
                     url, "vendor %s" % (r["vendor_name"] or r["vendor_id"]), r["doc_date"], r["doc_bal"], max(doc_score(r["ref_nbr"], ql), doc_score(r["invoice_nbr"], ql), doc_score(r["po_nbr"], ql)), r["vendor_name"] or ""))
        for r in fetch_dict("""
            SELECT ref_nbr, doc_type, vendor_id, vendor_name, amount, doc_date FROM finance_apcheck
            WHERE ref_nbr ILIKE %s ORDER BY doc_date DESC NULLS LAST LIMIT 20""", [like]):
            url = reverse("dashboard:finance_vendor", args=[r["vendor_id"]]) + "?hl=" + r["ref_nbr"]
            add(_doc(AP_KIND.get(r["doc_type"], "AP " + (r["doc_type"] or "")), r["ref_nbr"], r["vendor_name"] or r["vendor_id"], [_money(r["amount"])],
                     url, "vendor %s" % (r["vendor_name"] or r["vendor_id"]), r["doc_date"], r["amount"], doc_score(r["ref_nbr"], ql), r["vendor_name"] or ""))
        for r in fetch_dict("""
            SELECT ref_nbr, vendor_id, vendor_name, po_nbr, amount, doc_date FROM finance_povoucher
            WHERE ref_nbr ILIKE %s ORDER BY doc_date DESC NULLS LAST LIMIT 20""", [like]):
            url = reverse("dashboard:finance_vendor", args=[r["vendor_id"]]) + "?hl=" + r["ref_nbr"]
            add(_doc("AP voucher", r["ref_nbr"], r["vendor_name"] or r["vendor_id"], [("PO " + r["po_nbr"]) if r["po_nbr"] else "", _money(r["amount"])],
                     url, "vendor %s" % (r["vendor_name"] or r["vendor_id"]), r["doc_date"], r["amount"], doc_score(r["ref_nbr"], ql), r["vendor_name"] or ""))
        for r in fetch_dict("""
            SELECT DISTINCT ON (a.voucher_ref, a.vendor_id) a.voucher_ref, a.vendor_id, v.name vendor_name, a.invoice_nbr, a.po_nbr, a.check_ref, a.voucher_date, a.voucher_amount
            FROM finance_apcheckapplication a LEFT JOIN finance_slvendor v ON v.vendor_id = a.vendor_id
            WHERE a.invoice_nbr ILIKE %s OR a.voucher_ref ILIKE %s ORDER BY a.voucher_ref, a.vendor_id, a.voucher_date DESC LIMIT 20""", [like, like]):
            by_invoice = doc_score(r["invoice_nbr"], ql) >= doc_score(r["voucher_ref"], ql)   # show and highlight whichever number matched
            number = r["invoice_nbr"] if by_invoice else r["voucher_ref"]
            url = reverse("dashboard:finance_vendor", args=[r["vendor_id"]]) + "?hl=" + number
            parts = [("voucher " + r["voucher_ref"]) if by_invoice else (("vendor invoice " + r["invoice_nbr"]) if r["invoice_nbr"] else ""),
                     ("paid by check " + r["check_ref"]) if r["check_ref"] else "", ("PO " + r["po_nbr"]) if r["po_nbr"] else "", _money(r["voucher_amount"])]
            add(_doc("Vendor invoice" if by_invoice else "AP voucher", number, r["vendor_name"] or r["vendor_id"], parts,
                     url, "vendor %s" % (r["vendor_name"] or r["vendor_id"]), r["voucher_date"], r["voucher_amount"], max(doc_score(r["invoice_nbr"], ql), doc_score(r["voucher_ref"], ql)), r["vendor_name"] or ""))
    # ---- purchase orders: the job they were bought for, else the vendor
    if fin or proj:
        for r in fetch_dict("""
            SELECT DISTINCT ON (l.po_nbr) l.po_nbr, l.vendor_id, l.vendor_name, l.po_date, l.status, p.canonical_project_number cpn, p.display_number pdisp, p.title,
                   SUM(l.ext_cost) OVER (PARTITION BY l.po_nbr) tot
            FROM finance_poline l LEFT JOIN core_project p ON p.id = l.project_id
            WHERE l.po_nbr ILIKE %s ORDER BY l.po_nbr, l.po_date DESC LIMIT 30""", [like]):
            if r["cpn"] and proj:
                url, opens = reverse("dashboard:project_detail", args=[r["cpn"]]) + "?hl=" + r["po_nbr"], "project %s" % r["pdisp"]
            elif fin and r["vendor_id"]:
                url, opens = reverse("dashboard:finance_vendor", args=[r["vendor_id"]]) + "?hl=" + r["po_nbr"], "vendor %s" % (r["vendor_name"] or r["vendor_id"])
            else:
                continue
            add(_doc("PO", r["po_nbr"], r["vendor_name"] or r["vendor_id"] or "", [("job %s %s" % (r["pdisp"], r["title"] or "")).strip() if r["pdisp"] else "", _money(r["tot"]), "open" if (r["status"] or "").upper() == "O" else ""],
                     url, opens, r["po_date"], r["tot"], doc_score(r["po_nbr"], ql), r["vendor_name"] or ""))
    # ---- serial numbers: CNET line serials (Serial Lookup), SL shipper serials (the 010 order)
    if sales:
        for r in fetch_dict("""
            SELECT s.serial, l.part_number, d.document_number, d.customer_company, d.ordered_at
            FROM sales_cnetlineserial s JOIN sales_cnetdocumentline l ON l.id = s.line_id JOIN sales_cnetdocument d ON d.id = l.document_id
            WHERE s.serial ILIKE %s ORDER BY d.ordered_at DESC NULLS LAST LIMIT 20""", [like]):
            url = reverse("dashboard:sales010_serials") + "?q=" + _urlq(r["serial"])
            add(_doc("Serial", r["serial"], r["part_number"] or "", ["010 order " + r["document_number"], r["customer_company"] or ""], url, "Serial Lookup",
                     r["ordered_at"].date() if r["ordered_at"] else None, None, doc_score(r["serial"], ql), r["customer_company"] or ""))
        for r in fetch_dict("""
            SELECT s.serial, s.invt_id, s.shipper_id, s.ord_nbr, o.cnet_number, sh.ship_date
            FROM sales_slcnetshipperserial s LEFT JOIN sales_slcnetorder o ON o.ord_nbr = s.ord_nbr LEFT JOIN sales_slcnetshipper sh ON sh.shipper_id = s.shipper_id
            WHERE s.serial ILIKE %s ORDER BY sh.ship_date DESC NULLS LAST LIMIT 20""", [like]):
            if not r["cnet_number"]:
                continue
            url = reverse("dashboard:sales010_order_detail", args=[r["cnet_number"]]) + "?hl=" + r["shipper_id"]
            add(_doc("Serial", r["serial"], r["invt_id"] or "", ["shipped on " + r["shipper_id"], "SL order " + r["ord_nbr"]], url, "010 order %s" % r["cnet_number"],
                     r["ship_date"], None, doc_score(r["serial"], ql), ""))
    out.sort(key=lambda x: (-x["score"], -(x["when"].toordinal() if x["when"] else 0), x["number"]))
    return out, len(out)


def _bids(q, tokens, limit):
    """Project Portal bids by job number, portal id, name, client or estimator (SharePoint spec §5.3)."""
    frag, ps = _all_tokens("b.project_name || ' ' || b.client_name", tokens)
    numeric = is_number_query(q)
    parts, params = [frag], list(ps)
    if numeric or len(q.strip()) >= 3:
        parts.append("REPLACE(b.job_number_raw, '-', '') ILIKE %s OR b.portal_project_id ILIKE %s")
        params += [q.strip().replace("-", "") + "%", q.strip() + "%"]
    parts.append("e.canonical_name ILIKE %s")
    params.append(_like(q.strip()))
    rows = fetch_dict("""
        SELECT b.id, b.job_number_raw, b.portal_project_id, b.project_name, b.client_name, b.stage, b.value, b.bid_due, b.division, b.source,
               e.canonical_name estimator, c.canonical_name customer, 0.0 sim
        FROM bids_bid b LEFT JOIN core_employee e ON e.id = b.estimator_id LEFT JOIN core_customer c ON c.id = b.client_id
        WHERE (%s) ORDER BY b.bid_due DESC NULLS LAST LIMIT %d""" % (" OR ".join("(%s)" % p for p in parts), CAP), params)
    capped = len(rows) >= CAP
    from apps.bids import rules as _br
    out = []
    for r in rows:
        s = max(score_text(r["project_name"], tokens), score_text(r["client_name"], tokens), score_text(r["estimator"], tokens) and 70)
        if numeric and r["job_number_raw"] and r["job_number_raw"].replace("-", "").startswith(q.strip().replace("-", "")):
            s = max(s, 90)
        if r["portal_project_id"] and r["portal_project_id"] == q.strip():
            s = 100
        if not s:
            continue
        if r["stage"] in ("quoting", "submitted"):
            s += 8
        label = _br.STAGE_LABEL.get(r["stage"], r["stage"])
        sub = " · ".join(x for x in (r["customer"] or r["client_name"], label, ("est. " + r["estimator"]) if r["estimator"] else "", ("$%s" % "{:,.0f}".format(float(r["value"]))) if r["value"] else "") if x)
        out.append({"type": "bid", "key": str(r["id"]), "url": reverse("bids:bid_detail", args=[r["id"]]), "number": r["job_number_raw"] or ("#" + r["portal_project_id"] if r["portal_project_id"] else ""),
                    "title": r["project_name"] or "(unnamed bid)", "sub": sub, "ava": r["division"] or "bid", "tags": [{"t": label, "c": "bid-" + r["stage"]}],
                    "meta": _when(r["bid_due"]), "score": s, "stage": label, "client": r["customer"] or r["client_name"], "estimator": r["estimator"] or "",
                    "value": r["value"], "due": r["bid_due"]})
    out.sort(key=lambda x: (-x["score"], x["title"]))
    return out, len(out), capped

def _divisions(q, tokens, divisions, acc):
    out = []
    for d in divisions:
        s = max(score_text(d["code"], tokens), score_text(d["name"], tokens), score_text("division %s" % d["code"], tokens) and 60)
        if not s:
            continue
        url = reverse("dashboard:project_snapshot") if getattr(acc, "margins", False) else reverse("dashboard:project_list")
        out.append({"type": "division", "key": d["code"], "url": url + "?div=" + d["code"], "number": d["code"], "title": d["name"],
                    "sub": "Division %s" % d["code"], "ava": d["code"], "tags": [], "meta": "", "score": s})
    out.sort(key=lambda x: (-x["score"], x["key"]))
    return out, len(out)


def _pages(tokens, acc):
    out = []
    for label, url_name, group, needs, keywords in allowed_pages(acc):
        s = page_score(label, keywords, tokens)
        if not s:
            continue
        out.append({"type": "page", "key": url_name, "url": reverse(url_name), "number": "", "title": label, "sub": group,
                    "ava": "→", "tags": [], "meta": "", "score": s, "group": group})
    out.sort(key=lambda x: -x["score"])
    return out, len(out)


def quick_pages(acc, n=6):
    """The empty-query panel: the viewer's pinned pages first, then the rest in sidebar order."""
    pages = allowed_pages(acc)
    items = [{"type": "page", "key": p[1], "url": reverse(p[1]), "number": "", "title": p[0], "sub": p[2], "ava": "→", "tags": [], "meta": "", "score": 0, "group": p[2]}
             for p in pages[:n]]
    return {"type": "page", "label": "Jump to", "count": len(pages), "items": items}


# ------------------------------------------------------------------------------- entry point
def search(q, acc, per_type=6, project_limit=8, page_limit=5, doc_limit=8):
    """{'q', 'total', 'groups': [{'type', 'label', 'count', 'items'}], 'more_url'} — groups in GROUPS order, each
    trimmed to its per-type limit (count = every match, for the '+N more' hint and the results page's facets).
    Document numbers (shippers, SL orders, invoices, payments, vouchers, checks, POs, serials) are searched only for
    an identifier-shaped query: one word with a digit in it, four characters or more."""
    q = (q or "").strip()
    tokens = tokenize(q)
    if not tokens:
        return {"q": q, "total": 0, "groups": [quick_pages(acc)], "more_url": reverse("dashboard:search")}
    divisions = fetch_dict("SELECT code, name, sl_subaccounts FROM core_division WHERE active ORDER BY code")
    divmap = {}
    for d in divisions:
        for sub in (d["sl_subaccounts"] or []):
            divmap[sub] = "%s %s" % (d["code"], d["name"])
        divmap.setdefault(d["code"], "%s %s" % (d["code"], d["name"]))
    groups = []
    if getattr(acc, "projects", False):
        groups.append(("project", project_limit) + _projects(q, tokens, project_limit))
    if getattr(acc, "bids", False):
        groups.append(("bid", per_type) + _bids(q, tokens, per_type))
    if getattr(acc, "people", False):
        groups.append(("person", per_type) + _people(q, tokens, per_type, divmap))
    if getattr(acc, "customers", False):
        groups.append(("customer", per_type) + _customers(q, tokens, per_type))
    if getattr(acc, "finance", False):
        groups.append(("vendor", per_type) + _vendors(q, tokens, per_type))
    if getattr(acc, "sales010", False):
        groups.append(("order", per_type) + _orders(q, tokens, per_type))
    if is_identifier_query(q):
        groups.append(("document", doc_limit) + _documents(q, acc) + (False,))
    if getattr(acc, "documents", False):
        from apps.documents import search as _docsearch
        items, n = _docsearch.group(q, tokens, acc, per_type)
        groups.append(("file", per_type, items, n, False))
    if getattr(acc, "projects", False):
        groups.append(("division", per_type) + _divisions(q, tokens, divisions, acc) + (False,))
    groups.append(("page", page_limit) + _pages(tokens, acc) + (False,))
    from django.conf import settings
    if settings.PRIVATE_MODE:
        from apps.private_workspace.search import group as private_group
        private_items, private_count = private_group(q, per_type)
        groups.append(("private_note", per_type, private_items, private_count, False))
    out, total = [], 0
    for kind, lim, items, n, capped in groups:
        if not n:
            continue
        total += n
        for it in items:
            it.pop("_sort", None)
        out.append({"type": kind, "label": GROUP_LABEL[kind], "count": n, "capped": capped, "items": items[:lim]})
    return {"q": q, "total": total, "capped": any(g["capped"] for g in out), "groups": out, "more_url": reverse("dashboard:search") + "?q=" + _urlq(q)}


def _urlq(q):
    from urllib.parse import quote_plus
    return quote_plus(q)
