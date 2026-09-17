"""Billings page engine (/finance/billings/, docs/billings_page_plan.md): every AR invoice / credit memo / debit memo
issued in a period plus the journal revenue posted on jobs, keyed by the day the billing was KEYED (SL Crtd_DateTime,
Central time) — or by the invoice date, or by the fiscal period it posted into — with who entered it, the date on the
document, the job behind it and that job's WIP-by-Job columns, and the tie to GL revenue (the Daily Snapshot's figure).

Pure helpers (period resolution, login -> name matching) sit at the top and are unit-tested; the rest reads local tables.
"""

import calendar
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.finance.billing_rules import backdated_days, is_hardware_order, period_of
from apps.ingestion.bulk import fetch_dict

D0 = Decimal(0)
TZ = "America/Chicago"      # explicit Central casts (the DB session is Central too since 2026-09-08 — settings.DATABASES TIME_ZONE)
BASES = ("entered", "dated", "posted")
GROUPS = ("day", "project", "person", "customer", "division")
SHOWS = ("all", "project", "hardware", "other", "credits", "backdated", "journals")
PERIOD_MIN_DAYS = 430       # finance_arinvoice lines / roll-up window (finance_loaders.BILLING_LINES_WINDOW_DAYS)


# ---------------------------------------------------------------- period (pure)
def _month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def _range_label(a, b):
    if a == b:
        return a.strftime("%a %b %-d, %Y")
    if a.year == b.year:
        return "%s – %s" % (a.strftime("%b %-d"), b.strftime("%b %-d, %Y"))
    return "%s – %s" % (a.strftime("%b %-d, %Y"), b.strftime("%b %-d, %Y"))


def _resolve(g, today):
    """Turn the page's querystring into a date window. Precedence: ?day=YYYY-MM-DD (one day) > ?from=&to= (a range;
    ?after=&to= = the same with an exclusive start) > ?period= (wtd · mtd · ytd · 7d · 30d · YYYY-MM · YYYY). Default = the current month to date.
    Returns {"kind", "key", "label", "start", "end", "months": [YYYYMM…] or None (fiscal-period basis only for months
    and years), "prev", "next": querystrings for the neighbouring window (None at the current edge)}."""
    def qs(**kw):
        return "&".join("%s=%s" % (k, v) for k, v in kw.items())

    day = (g.get("day") or "").strip()
    if day:
        try:
            d = date.fromisoformat(day)
        except ValueError:
            d = None
        if d and d <= today:
            return {"kind": "day", "key": d.isoformat(), "label": d.strftime("%a %b %-d, %Y"), "start": d, "end": d, "months": None,
                    "prev": qs(day=(d - timedelta(days=1)).isoformat()), "next": qs(day=(d + timedelta(days=1)).isoformat()) if d < today else None}
    frm, to = (g.get("from") or "").strip(), (g.get("to") or "").strip()
    if not frm and g.get("after") and to:      # ?after= = an exclusive start (the WIP page's live windows run (baseline, end])
        try:
            frm = (date.fromisoformat(g["after"].strip()) + timedelta(days=1)).isoformat()
        except ValueError:
            frm = ""
    if frm and to:
        try:
            a, b = date.fromisoformat(frm), date.fromisoformat(to)
        except ValueError:
            a = b = None
        if a and b:
            if b < a:
                a, b = b, a
            b = min(b, today)
            n = (b - a).days + 1
            return {"kind": "range", "key": "%s..%s" % (a.isoformat(), b.isoformat()), "label": _range_label(a, b), "start": a, "end": b, "months": None,
                    "prev": qs(**{"from": (a - timedelta(days=n)).isoformat(), "to": (a - timedelta(days=1)).isoformat()}),
                    "next": qs(**{"from": (b + timedelta(days=1)).isoformat(), "to": min(b + timedelta(days=n), today).isoformat()}) if b < today else None}
    p = (g.get("period") or "").strip().lower()
    cur_ym = "%04d%02d" % (today.year, today.month)
    if p == "wtd":
        mon = today - timedelta(days=today.weekday())
        return {"kind": "wtd", "key": "wtd", "label": "Week to date · " + _range_label(mon, today), "start": mon, "end": today, "months": None,
                "prev": qs(**{"from": (mon - timedelta(days=7)).isoformat(), "to": (mon - timedelta(days=1)).isoformat()}), "next": None}
    if p in ("7d", "30d"):
        n = int(p[:-1])
        a = today - timedelta(days=n - 1)
        return {"kind": "last", "key": p, "label": "Last %d days · %s" % (n, _range_label(a, today)), "start": a, "end": today, "months": None,
                "prev": qs(**{"from": (a - timedelta(days=n)).isoformat(), "to": (a - timedelta(days=1)).isoformat()}), "next": None}
    if p == "ytd" or (re.fullmatch(r"\d{4}", p) and int(p) >= today.year):
        return {"kind": "ytd", "key": str(today.year), "label": "%d to date" % today.year, "start": date(today.year, 1, 1), "end": today,
                "months": ["%04d%02d" % (today.year, m) for m in range(1, today.month + 1)], "prev": qs(period=str(today.year - 1)), "next": None}
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", p)
    if m and 1 <= int(m.group(2)) <= 12 and (int(m.group(1)), int(m.group(2))) < (today.year, today.month) and date(int(m.group(1)), int(m.group(2)), 1) >= today - timedelta(days=PERIOD_MIN_DAYS + 31):
        y, mo = int(m.group(1)), int(m.group(2))
        nxt = (y + 1, 1) if mo == 12 else (y, mo + 1)
        prv = (y - 1, 12) if mo == 1 else (y, mo - 1)
        return {"kind": "month", "key": "%04d-%02d" % (y, mo), "label": date(y, mo, 1).strftime("%B %Y"), "start": date(y, mo, 1), "end": _month_end(y, mo),
                "months": ["%04d%02d" % (y, mo)], "prev": qs(period="%04d-%02d" % prv), "next": qs(period="%04d-%02d" % nxt)}
    if re.fullmatch(r"\d{4}", p) and int(p) == today.year - 1:
        y = int(p)
        return {"kind": "year", "key": p, "label": p, "start": date(y, 1, 1), "end": date(y, 12, 31), "months": ["%04d%02d" % (y, mm) for mm in range(1, 13)],
                "prev": None, "next": qs(period=str(today.year))}
    # default: the current month to date
    first = today.replace(day=1)
    prv = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return {"kind": "mtd", "key": today.strftime("%Y-%m"), "label": today.strftime("%B %Y") + " to date", "start": first, "end": today,
            "months": [cur_ym], "prev": qs(period="%04d-%02d" % prv), "next": None}


def resolve_period(g, today):
    """_resolve + the window's own querystring (`qs`), so the page can swap basis / grouping while keeping the window."""
    per = _resolve(g, today)
    k = per["kind"]
    if k == "day":
        per["qs"] = "day=%s" % per["key"]
    elif k == "range":
        a, b = per["key"].split("..")
        per["qs"] = "from=%s&to=%s" % (a, b)
    elif k == "ytd":
        per["qs"] = "period=ytd"
    else:
        per["qs"] = "period=%s" % per["key"]
    return per


def period_options(today):
    """[(group, [(querystring, label), …])] for the period picker: live windows, then the months and years the
    invoice lines cover (the roll-up window, ~14 months)."""
    live = [("period=wtd", "Week to date"), ("period=7d", "Last 7 days"), ("period=30d", "Last 30 days"),
            ("period=%s" % today.strftime("%Y-%m"), today.strftime("%B %Y") + " to date"), ("period=ytd", "%d to date" % today.year)]
    months, y, m = [], today.year, today.month
    floor = today - timedelta(days=PERIOD_MIN_DAYS)
    for _ in range(18):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        if date(y, m, 1) < floor.replace(day=1):
            break
        months.append(("period=%04d-%02d" % (y, m), date(y, m, 1).strftime("%B %Y")))
    years = [("period=%d" % (today.year - 1), str(today.year - 1))]
    return [("Live", live), ("Month", months), ("Year", years)]


def resolve_basis(g, per):
    """entered (default) · dated · posted — posted needs fiscal periods, so it falls back to dated for day / week / range."""
    b = (g.get("basis") or "").strip().lower()
    if b not in BASES:
        b = "entered"
    if b == "posted" and not per["months"]:
        b = "dated"
    return b


# ---------------------------------------------------------------- who keyed it (pure + a small query)
def match_logins(logins, employees):
    """{login: (display name, employee_key)} for SL logins of the form first-initial + surname (MTHORNFIELD -> Megan Thornfield).
    employees: iterable of (employee_key, canonical_name, active). A hyphenated surname matches on either part
    (MUMBERTON -> Martha Umberton-Valewood). Ambiguous logins (two different people) and unknown ones are left out —
    the page then shows the login itself. Among duplicate rows for one person, a non-PTT key wins."""
    by_last = defaultdict(list)
    for key, name, active in employees:
        parts = [p for p in re.split(r"[\s,]+", (name or "").strip()) if p]
        if len(parts) < 2:
            continue
        first, last = parts[0], parts[-1]
        tokens = {last.upper()} | {t.upper() for t in last.split("-") if t}
        for t in tokens:
            by_last[t].append((first[0].upper(), name, key or "", bool(active)))
    out = {}
    for lg in logins:
        u = (lg or "").strip().upper()
        if len(u) < 3:
            continue
        cands = [c for c in by_last.get(u[1:], []) if c[0] == u[0]]
        names = {c[1] for c in cands}
        if len(names) != 1:
            continue
        keys = sorted((k for _, _, k, _ in cands), key=lambda k: (k.startswith("PTT-"), k))
        out[lg] = (names.pop(), keys[0] if keys else "")
    return out


def login_names(logins):
    logins = [l for l in set(logins) if l]
    if not logins:
        return {}
    emps = [(r["employee_key"], r["canonical_name"], r["active"]) for r in fetch_dict("SELECT employee_key, canonical_name, active FROM core_employee")]
    return match_logins(logins, emps)


# ---------------------------------------------------------------- the rows
def _basis_where(basis, per, col_entered, col_dated, col_period, per_filter):
    if basis == "posted":
        where, params = ["%s = ANY(%%s)" % col_period], [per["months"]]
    elif basis == "dated":
        where, params = ["%s BETWEEN %%s AND %%s" % col_dated], [per["start"], per["end"]]
    else:
        where, params = ["(%s AT TIME ZONE %%s)::date BETWEEN %%s AND %%s" % col_entered], [TZ, per["start"], per["end"]]
    if per_filter:
        where.append("%s = %%s" % col_period)
        params.append(per_filter)
    return " AND ".join(where), params


def billing_rows(per, basis, per_filter=""):
    """Invoices (finance_arinvoice) and journal revenue on jobs (finance_projectfinancialtransaction, GL system) inside the
    window on the chosen basis. Every row: kind (inv | jrnl), ref, doc_type, customer, job, the three dates, who keyed it,
    signed revenue / tax / deposits / other / total, balance, flags (hardware, backdated_days, per_mismatch)."""
    w, params = _basis_where(basis, per, "i.sl_created_at", "i.doc_date", "i.per_post", per_filter)
    rows = fetch_dict("""
        SELECT i.id, i.ref_nbr, i.doc_type, i.customer_id_raw cust_id, COALESCE(NULLIF(c.canonical_name, ''), i.customer_name) customer,
               c.sl_customer_id cust_link, i.project_id pid, p.canonical_project_number cpn, p.display_number pdisp, p.title ptitle,
               i.project_id_raw, i.task_id, i.order_nbr, i.so_type, o.cnet_number, i.doc_date, i.due_date, i.per_post, i.batch_nbr,
               i.amount, i.balance, i.doc_desc, i.cust_po, i.terms, i.slsper_id, COALESCE(sp.name, '') sp_name,
               i.sl_created_at entered, (i.sl_created_at AT TIME ZONE %%s)::date entered_day, i.crtd_user, i.crtd_prog, i.lupd_user, i.lupd_at,
               i.revenue, i.tax, i.deposits, i.other, i.division
        FROM finance_arinvoice i
        LEFT JOIN core_customer c ON c.id = i.customer_id
        LEFT JOIN core_project p ON p.id = i.project_id
        LEFT JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND i.order_nbr <> ''
        LEFT JOIN core_salesperson sp ON sp.code = i.slsper_id AND i.slsper_id <> ''
        WHERE %s""" % w, [TZ] + params)
    out = []
    for r in rows:
        sign = -1 if r["doc_type"] == "CM" else 1
        total = sign * (r["amount"] or D0)
        rollup = r["revenue"] is not None
        rev = r["revenue"] if rollup else total          # outside the lines window the document total stands in for revenue
        d = dict(r, kind="inv", sign=sign, total=total, revenue=rev, rollup=rollup,
                 tax=r["tax"] or D0, deposits=r["deposits"] or D0, other=r["other"] or D0,
                 balance_signed=sign * (r["balance"] or D0), entered_day=r["entered_day"],
                 hardware=is_hardware_order(r["so_type"], bool(r["cpn"])),
                 backdated=backdated_days(r["entered_day"], r["doc_date"]),
                 per_mismatch=bool(r["per_post"] and r["doc_date"] and r["per_post"] != period_of(r["doc_date"])),
                 credit=(r["doc_type"] == "CM"), by=r["crtd_user"] or "", desc=r["doc_desc"] or "")
        d["paid"] = total - d["balance_signed"]
        d["day"] = d["doc_date"] if basis == "dated" else d["entered_day"]
        out.append(d)
    # journal revenue on jobs (GL journals to a project's REVENUE account: deferred-revenue releases, reclasses, corrections)
    w, params = _basis_where(basis, per, "t.source_created_at", "t.transaction_date", "t.fiscal_period", per_filter)
    for r in fetch_dict("""
        SELECT t.id, t.batch_id, t.detail_num, t.project_id pid, p.canonical_project_number cpn, p.display_number pdisp, p.title ptitle,
               COALESCE(NULLIF(c.canonical_name, ''), '') customer, c.sl_customer_id cust_link, c.sl_customer_id cust_id,
               t.transaction_date doc_date, t.fiscal_period per_post, t.source_created_at entered,
               (t.source_created_at AT TIME ZONE %%s)::date entered_day, t.source_created_by crtd_user, t.amount, t.comment,
               t.gl_account, t.gl_subaccount, t.batch_type, d.code division
        FROM finance_projectfinancialtransaction t
        JOIN core_project p ON p.id = t.project_id
        JOIN core_division d ON d.id = p.division_id
        LEFT JOIN core_customer c ON c.id = p.customer_id
        WHERE t.category = 'revenue' AND t.system_cd = 'GL' AND %s""" % w, [TZ] + params):
        amt = r["amount"] or D0
        out.append(dict(r, kind="jrnl", ref="GJ %s" % r["batch_id"], doc_type="GJ", sign=1, total=amt, revenue=amt, rollup=True,
                        tax=D0, deposits=D0, other=D0, balance_signed=D0, paid=amt, hardware=False,
                        backdated=backdated_days(r["entered_day"], r["doc_date"]),
                        per_mismatch=bool(r["per_post"] and r["doc_date"] and r["per_post"] != period_of(r["doc_date"])),
                        credit=amt < 0, by=r["crtd_user"] or "", desc=r["comment"] or "", order_nbr="", so_type="", cnet_number=None,
                        crtd_prog="GJ", lupd_user="", lupd_at=None, cust_po="", terms="", slsper_id="", sp_name="", due_date=None,
                        project_id_raw=r["cpn"], task_id="", batch_nbr=r["batch_id"]))
        out[-1]["day"] = out[-1]["doc_date"] if basis == "dated" else out[-1]["entered_day"]
    return out


def on_ledger(refs):
    """Invoice references that have reached the project ledger (PJTran REVENUE rows are created when the AR batch is
    released to the jobs — sometimes days after the invoice)."""
    if not refs:
        return set()
    return {r["v"] for r in fetch_dict("""SELECT DISTINCT voucher_num v FROM finance_projectfinancialtransaction
                                          WHERE system_cd = 'AR' AND category = 'revenue' AND voucher_num = ANY(%s)""", [list(refs)])}


# ---------------------------------------------------------------- the job behind a billing (WIP by Job columns, today's figures)
def job_context(cpns, today):
    """{cpn: row} with the live WIP-by-Job columns for each job: contract, billed / earned / WIP, PTT % with its age and who
    set it, hours %, remaining hours, cost to date and cost ÷ EAC, sold vs EAC GP%, EAC GP. Same rows as the WIP page
    (finance_wip.wip_job_meta), so a billing reads against the job exactly as that page shows it."""
    from apps.analytics.finance_wip import age_css, in_wip_population, is_service_agreement, ptt_entry_people, wip_job_meta, STALE_DAYS
    cpns = [c for c in set(cpns) if c]
    if not cpns:
        return {}
    meta = wip_job_meta(cpns)
    ids = [m["id"] for m in meta.values()]
    pct_by, rem_by = ptt_entry_people(ids)
    prof = {r["id"]: r for r in fetch_dict("""
        SELECT p.id, p.sold_gp_percent sold_pct, pr.eac_gp_dollars eac_gp, pr.eac_gp_percent eac_pct,
               pr.projected_margin_change_points vs_sold, pr.risk_level risk, pr.as_of_date eac_as_of
        FROM core_project p
        LEFT JOIN LATERAL (SELECT x.eac_gp_dollars, x.eac_gp_percent, x.projected_margin_change_points, x.risk_level, x.as_of_date
                           FROM analytics_projectprediction x WHERE x.project_id = p.id ORDER BY x.as_of_date DESC LIMIT 1) pr ON TRUE
        WHERE p.id = ANY(%s)""", [ids])} if ids else {}

    def age(dt):
        if not dt:
            return None
        if isinstance(dt, datetime):          # aware PTT timestamps -> the Central calendar day
            dt = timezone.localtime(dt).date()
        return (today - dt).days

    for cpn, r in meta.items():
        r["pct_age"], r["rem_age"] = age(r.get("pct_at")), age(r.get("rem_at"))
        ages = [a for a in (r["pct_age"], r["rem_age"]) if a is not None]
        r["touched_age"] = min(ages) if ages else None
        r["touched_css"] = age_css(r["touched_age"])
        r["stale"] = r["touched_age"] is None or r["touched_age"] > STALE_DAYS
        r["pct_by"], r["rem_by"] = pct_by.get(r["id"]), rem_by.get(r["id"])
        # outside the WIP population (service agreements, T&M, closed): WIP is zero by rule, not what the formula says
        r["in_pop"] = in_wip_population(r)
        r["sa"] = is_service_agreement(r)
        if not r["in_pop"]:
            r["wip"], r["earned"] = Decimal(0), None
        r["wip_f"] = float(r["wip"])
        r["billed_pct"] = (float(r["billed"] or 0) / float(r["cv"])) if r.get("cv") else None
        r["cost_eac_pct"] = (float(r["cost"] or 0) / float(r["eac_cost"])) if r.get("eac_cost") else None
        pf = prof.get(r["id"]) or {}
        r["sold_pct"], r["eac_pct"], r["eac_gp"], r["risk"], r["eac_as_of"] = pf.get("sold_pct"), pf.get("eac_pct"), pf.get("eac_gp"), pf.get("risk"), pf.get("eac_as_of")
        r["vs_sold"] = pf.get("vs_sold")
        if r["vs_sold"] is None and r["eac_pct"] is not None and r["sold_pct"] is not None:
            r["vs_sold"] = r["eac_pct"] - r["sold_pct"]
    return meta


# ---------------------------------------------------------------- the tie to GL revenue (the Daily Snapshot's figure)
def gl_tie(per, basis, inv_total, jrnl_total, today, refs=None, window_days=95):
    """GL income postings (3I except 40100 interest — the Daily Snapshot's revenue definition) for the same window and basis,
    split by module: AR (invoices) and GL (journals; the page shows the ones on jobs). By fiscal period and by invoice date the
    AR-module figure equals Σ invoice revenue on the page exactly (GLTran carries the document's period and date). By the day
    keyed it need not: the GL rows are created when the AR batch is RELEASED, which can be a later day — so on that basis the
    tie is drawn by document (`refs`): how much of this page's revenue has posted, and what posted in the window for documents
    keyed earlier. Only inside the posting window finance_glrecentposting covers; None otherwise."""
    if basis == "posted":
        floor_ym = "%04d%02d" % ((today.replace(day=1) - timedelta(days=1)).year, (today.replace(day=1) - timedelta(days=1)).month)
        if min(per["months"]) < floor_ym:
            return None
        where, params = "per_post = ANY(%s)", [per["months"]]
    else:
        if per["start"] < today - timedelta(days=window_days):
            return None
        col = "tran_date" if basis == "dated" else "(sl_created_at AT TIME ZONE %s)::date"
        where, params = ("%s BETWEEN %%s AND %%s" % col), ([TZ] if basis != "dated" else []) + [per["start"], per["end"]]
    by_mod = {r["module"]: r["s"] for r in fetch_dict("""
        SELECT module, COALESCE(SUM(cr_amt - dr_amt), 0) s FROM finance_glrecentposting
        WHERE acct_type = '3I' AND acct <> '40100' AND %s GROUP BY module""" % where, params)}
    gl_ar = by_mod.get("AR", D0)
    gl_gl = by_mod.get("GL", D0)
    gl_other = sum((v for k, v in by_mod.items() if k not in ("AR", "GL")), D0)
    total = gl_ar + gl_gl + gl_other
    out = {"total": total, "ar": gl_ar, "gl": gl_gl, "other": gl_other, "inv": inv_total, "jrnl": jrnl_total, "offjob": gl_gl - jrnl_total,
           "by_doc": False, "diff": gl_ar - inv_total, "ok": abs(gl_ar - inv_total) < Decimal("0.01")}
    if basis == "entered":
        out["by_doc"] = True
        posted = fetch_dict("""SELECT COALESCE(SUM(cr_amt - dr_amt), 0) s, COUNT(DISTINCT ref_nbr) n FROM finance_glrecentposting
                               WHERE acct_type = '3I' AND acct <> '40100' AND module = 'AR' AND ref_nbr = ANY(%s)""", [list(refs or [])])[0]
        out["posted"] = posted["s"]                        # this page's documents that have reached the GL (any release day)
        out["unposted"] = inv_total - posted["s"]          # keyed, batch not released yet
        out["ok"] = abs(out["unposted"]) < Decimal("0.01")
        out["earlier_docs"] = gl_ar - fetch_dict("""SELECT COALESCE(SUM(cr_amt - dr_amt), 0) s FROM finance_glrecentposting
                               WHERE acct_type = '3I' AND acct <> '40100' AND module = 'AR' AND ref_nbr = ANY(%s) AND %s""" % ("%s", where),
                                                  [list(refs or [])] + params)[0]["s"]   # released in the window, keyed before it
    return out


def snapshot_revenue_mtd():
    """(snapshot_date, revenue_mtd, as_of) of the latest live Daily Snapshot — what the posted-basis current month ties to."""
    r = fetch_dict("""SELECT snapshot_date, revenue_mtd, as_of FROM finance_dailyfinancesnapshot
                      WHERE NOT reconstructed AND revenue_mtd IS NOT NULL ORDER BY snapshot_date DESC LIMIT 1""")
    return r[0] if r else None


def active_days(basis, limit=120):
    """Recent days with at least one billing on the basis (for the day arrows): newest first."""
    col = "doc_date" if basis == "dated" else "(sl_created_at AT TIME ZONE %s)::date"
    params = [] if basis == "dated" else [TZ]
    return [r["d"] for r in fetch_dict("SELECT %s d FROM finance_arinvoice WHERE sl_created_at IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT %d" % (col, limit), params)]
