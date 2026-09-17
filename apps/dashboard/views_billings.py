"""Billings page (/finance/billings/): what was billed, day by day — every invoice / credit memo / debit memo issued in
a period and the journal revenue posted on jobs, with who keyed each one, when, the date on the document, the fiscal
period, the job behind it and that job's WIP-by-Job columns; grouped by day / project / person / customer / division;
click a row for what it billed and what has been paid. Ties to the Daily Snapshot's revenue (GL 3I) inside the posting
window. Plan + SL findings: docs/billings_page_plan.md; shipped page: docs/06."""

import csv
import re
from collections import defaultdict
from decimal import Decimal

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from apps.analytics.billings import (BASES, GROUPS, SHOWS, active_days, billing_rows, gl_tie, job_context, login_names,
                                     on_ledger, period_options, resolve_basis, resolve_period, snapshot_revenue_mtd)
from apps.ingestion.bulk import fetch_dict
from .views import _ctx

D0 = Decimal(0)
BASIS_LABEL = {"entered": "day keyed", "dated": "invoice date", "posted": "fiscal period"}
SHOW_LABEL = [("all", "All"), ("project", "Project billings"), ("hardware", "010 hardware"), ("other", "No job"),
              ("credits", "Credits"), ("backdated", "Back-dated"), ("journals", "Journals")]
SIGNED = {"amount", "total", "wip", "eac_gp"}


def _f(v):
    return float(v) if v is not None else None


def _sort_value(r, key):
    j = r.get("job") or {}
    if key == "ref":
        return r["ref"]
    if key == "entered":
        return r["entered"]
    if key == "by":
        return (r["by_name"] or "").lower()
    if key == "date":
        return r["doc_date"]
    if key == "period":
        return r["per_post"]
    if key == "customer":
        return (r["customer"] or "").lower()
    if key == "project":
        return r["cpn"] or ""
    if key == "amount":
        return float(r["revenue"])
    if key == "total":
        return float(r["total"])
    if key == "division":
        return r["division"] or ""
    if key == "pm":
        return (j.get("pm_name") or "").lower() if j else None
    if key == "state":
        return j.get("lifecycle_state") if j else None
    if key == "pct":
        return _f(j.get("pm_pct")) if j else None
    if key == "hours_pct":
        return _f(j.get("hours_pct")) if j else None
    if key == "cv":
        return _f(j.get("cv")) if j else None
    if key == "billed_td":
        return _f(j.get("billed")) if j else None
    if key == "earned":
        return _f(j.get("earned")) if j else None
    if key == "wip":
        return j.get("wip_f") if j else None
    if key == "cost":
        return _f(j.get("cost")) if j else None
    if key == "gp":
        return _f(j.get("eac_pct")) if j else None
    if key == "eac_gp":
        return _f(j.get("eac_gp")) if j else None
    return float(r["revenue"])


def _sorted(rows, sort):
    desc = sort.startswith("-")
    key = sort.lstrip("-")
    present = [r for r in rows if _sort_value(r, key) is not None]
    missing = [r for r in rows if _sort_value(r, key) is None]
    if key in SIGNED and desc:
        present.sort(key=lambda r: abs(_sort_value(r, key)), reverse=True)
    else:
        present.sort(key=lambda r: _sort_value(r, key), reverse=desc)
    return present + missing


def _group_key(r, by):
    if by == "day":
        d = r["day"]
        return (d.isoformat() if d else "", d.strftime("%a, %b %-d, %Y") if d else "No date")
    if by == "project":
        if r["cpn"]:
            return ("0" + r["cpn"], "%s %s" % (r["pdisp"], r["ptitle"] or ""))
        if r["hardware"]:
            return ("1hw", "No job — 010 hardware orders")
        return ("2none", "No job")
    if by == "person":
        return (r["by"] or "~", r["by_name"] or "(no login)")
    if by == "customer":
        return (r["cust_id"] or "~", r["customer"] or r["cust_id"] or "(no customer)")
    return (r["division"] or "~", r["division"] or "No division")


def _groups(rows, by, show, sort):
    groups = {}
    for r in rows:
        gid, label = _group_key(r, by)
        g = groups.setdefault(gid, {"id": gid, "label": label, "rows": [], "hw": [], "first": r, "revenue": D0, "total": D0, "n": 0,
                                    "by_counts": defaultdict(int), "by_amt": defaultdict(lambda: D0), "backdated": 0, "jobs": set(), "customers": set(),
                                    "hw_by": defaultdict(int), "credits": 0, "jrnl": 0})
        fold = r["hardware"] and show != "hardware"
        (g["hw"] if fold else g["rows"]).append(r)
        g["revenue"] += r["revenue"]
        g["total"] += r["total"]
        g["n"] += 1
        g["by_counts"][r["by_name"] or "?"] += 1
        g["by_amt"][r["by_name"] or "?"] += r["revenue"]
        if fold:
            g["hw_by"][r["by_name"] or "?"] += 1
        if r["backdated"]:
            g["backdated"] += 1
        if r["cpn"]:
            g["jobs"].add(r["cpn"])
        if r["cust_id"]:
            g["customers"].add(r["cust_id"])
        if r["credit"]:
            g["credits"] += 1
        if r["kind"] == "jrnl":
            g["jrnl"] += 1
    out = []
    for g in groups.values():
        f = g["first"]
        g["rows"] = _sorted(g["rows"], sort)
        g["hw"] = _sorted(g["hw"], sort)
        g["hw_n"] = len(g["hw"])
        g["hw_revenue"] = sum((r["revenue"] for r in g["hw"]), D0)
        g["hw_total"] = sum((r["total"] for r in g["hw"]), D0)
        g["hw_by_label"] = " · ".join("%s %d" % (k, v) for k, v in sorted(g["hw_by"].items(), key=lambda kv: -kv[1])[:3])
        g["by_label"] = " · ".join("%s %d" % (k, v) for k, v in sorted(g["by_counts"].items(), key=lambda kv: -kv[1])[:4])
        g["jobs_n"], g["customers_n"] = len(g["jobs"]), len(g["customers"])
        g["link"] = ""
        g["job"] = None
        if by == "project" and f["cpn"]:
            g["link"] = "/projects/%s/" % f["cpn"]
            g["job"] = f.get("job")
        elif by == "customer" and f.get("cust_link"):
            g["link"] = "/customers/%s/" % f["cust_link"]
        elif by == "person" and f.get("by_key") and not f["by_key"].startswith("PTT-"):
            g["link"] = "/people/%s/" % f["by_key"]
        out.append(g)
    if by == "day":
        out.sort(key=lambda g: g["id"], reverse=True)
    else:
        out.sort(key=lambda g: -abs(g["revenue"]))
    return out


def finance_billings(request):
    today = timezone.localdate()
    g = request.GET
    per = resolve_period(g, today)
    basis = resolve_basis(g, per)
    per_filter = g.get("per", "") if re.fullmatch(r"\d{6}", g.get("per", "") or "") else ""
    by = g.get("by") if g.get("by") in GROUPS else "day"
    show = g.get("show") if g.get("show") in SHOWS else "all"
    sort = g.get("sort") or "-amount"
    rows = billing_rows(per, basis, per_filter)
    names = login_names([r["by"] for r in rows])
    for r in rows:
        r["by_name"], r["by_key"] = names.get(r["by"], (r["by"], ""))
        r["ref"] = r["ref_nbr"] if r["kind"] == "inv" else r["ref"]
    ledger = on_ledger([r["ref_nbr"] for r in rows if r["kind"] == "inv" and r["cpn"]])
    jobs = job_context([r["cpn"] for r in rows], today)
    job_billed = defaultdict(lambda: D0)
    for r in rows:
        r["job"] = jobs.get(r["cpn"]) if r["cpn"] else None
        r["on_ledger"] = (r["ref_nbr"] in ledger) if (r["kind"] == "inv" and r["cpn"]) else None
        if r["cpn"]:
            job_billed[r["cpn"]] += r["revenue"]
    for r in rows:
        r["job_billed"] = job_billed.get(r["cpn"]) if r["cpn"] else None
    # ---- KPIs and the GL tie: the whole period, before any filter ----
    inv = [r for r in rows if r["kind"] == "inv"]
    jrnl = [r for r in rows if r["kind"] == "jrnl"]
    kp = {"billed": sum((r["revenue"] for r in rows), D0), "n": len(inv), "n_jrnl": len(jrnl), "jrnl": sum((r["revenue"] for r in jrnl), D0),
          "credits_n": sum(1 for r in inv if r["credit"]), "credits": sum((r["revenue"] for r in inv if r["credit"]), D0),
          "total": sum((r["total"] for r in inv), D0), "tax": sum((r["tax"] for r in inv), D0), "deposits": sum((r["deposits"] for r in inv), D0),
          "project": sum((r["revenue"] for r in rows if r["cpn"]), D0), "jobs": len({r["cpn"] for r in rows if r["cpn"]}),
          "customers": len({r["cust_id"] for r in inv if r["cust_id"]}),
          "hw": sum((r["revenue"] for r in inv if r["hardware"]), D0), "hw_n": sum(1 for r in inv if r["hardware"]),
          "backdated_n": sum(1 for r in inv if r["backdated"]), "backdated": sum((r["revenue"] for r in inv if r["backdated"]), D0),
          "backdated_max": max((r["backdated"] for r in inv), default=0), "per_mismatch_n": sum(1 for r in inv if r["per_mismatch"]),
          "not_on_ledger": sum(1 for r in inv if r["on_ledger"] is False), "no_rollup": sum(1 for r in inv if not r["rollup"])}
    people = defaultdict(lambda: {"n": 0, "amt": D0, "key": ""})
    for r in rows:
        p = people[r["by_name"] or "?"]
        p["n"] += 1
        p["amt"] += r["revenue"]
        p["key"] = r["by_key"]
    kp["people"] = sorted(({"name": k, **v} for k, v in people.items()), key=lambda p: -p["n"])
    tie = gl_tie(per, basis, sum((r["revenue"] for r in inv), D0), kp["jrnl"], today, refs=[r["ref_nbr"] for r in inv]) if rows or per["end"] >= today - timezone.timedelta(days=95) else None
    snap = snapshot_revenue_mtd() if (basis == "posted" and per["kind"] == "mtd") else None
    # ---- filters ----
    div, pm, user, cust, proj_f = g.get("division", ""), g.get("pm", ""), g.get("user", ""), g.get("cust", ""), g.get("project", "")
    q = (g.get("q") or "").strip().lower()
    try:
        min_abs = float(g.get("min") or 0)
    except ValueError:
        min_abs = 0.0
    shown = rows
    if show == "project":
        shown = [r for r in shown if r["cpn"]]
    elif show == "hardware":
        shown = [r for r in shown if r["hardware"]]
    elif show == "other":
        shown = [r for r in shown if not r["cpn"] and not r["hardware"] and r["kind"] == "inv"]
    elif show == "credits":
        shown = [r for r in shown if r["credit"]]
    elif show == "backdated":
        shown = [r for r in shown if r["backdated"] or r["per_mismatch"]]
    elif show == "journals":
        shown = [r for r in shown if r["kind"] == "jrnl"]
    if div:
        shown = [r for r in shown if r["division"] == div]
    if pm:
        shown = [r for r in shown if r["job"] and r["job"].get("pm_key") == pm]
    if user:
        shown = [r for r in shown if r["by"] == user]
    if cust:
        shown = [r for r in shown if r["cust_id"] == cust]
    if proj_f:
        shown = [r for r in shown if r["cpn"] == proj_f]
    if min_abs > 0:
        shown = [r for r in shown if abs(float(r["revenue"])) >= min_abs]
    if q:
        shown = [r for r in shown if q in " ".join(str(r.get(k) or "") for k in ("ref", "customer", "cust_id", "cpn", "pdisp", "ptitle", "desc", "cust_po", "order_nbr", "cnet_number", "by", "by_name")).lower()]
    groups = _groups(shown, by, show, sort)
    if g.get("hwgroup"):
        # the folded 010 hardware rows are fetched on demand (they are ~80% of all rows): one group, or every group at once
        want = None if g["hwgroup"] == "all" else g["hwgroup"]
        parts = [(grp, grp["hw"]) for grp in groups if grp["hw"] and (want is None or grp["id"] == want)]
        return render(request, "dashboard/_billing_rows.html", _ctx(request, "finance-billings", parts=parts))
    if g.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="billings-%s-%s.csv"' % (per["key"].replace("..", "_"), basis)
        w = csv.writer(resp)
        w.writerow(["group", "kind", "ref", "type", "screen", "entered", "entered_by", "invoice_date", "days_backdated", "fiscal_period", "customer_id", "customer",
                    "project", "project_title", "division", "pm", "state", "order", "cnet_order", "description", "cust_po", "revenue", "tax", "deposits", "other",
                    "total", "balance", "on_project_ledger", "contract", "billed_to_date", "earned", "wip", "ptt_pct", "cost_to_date", "eac_gp_pct", "eac_gp"])
        for grp in groups:
            for r in grp["rows"] + grp["hw"]:
                j = r.get("job") or {}
                w.writerow([grp["label"], r["kind"], r["ref"], r["doc_type"], r["crtd_prog"], timezone.localtime(r["entered"]).strftime("%Y-%m-%d %H:%M") if r["entered"] else "", r["by_name"],
                            r["doc_date"], r["backdated"], r["per_post"], r["cust_id"], r["customer"], r["pdisp"] or r["project_id_raw"], r["ptitle"] or "", r["division"],
                            j.get("pm_name", ""), j.get("lifecycle_state", ""), r["order_nbr"], r["cnet_number"] or "", r["desc"], r["cust_po"], r["revenue"], r["tax"],
                            r["deposits"], r["other"], r["total"], r["balance_signed"], r["on_ledger"], j.get("cv"), j.get("billed"), j.get("earned"), j.get("wip"),
                            j.get("pm_pct"), j.get("cost"), j.get("eac_pct"), j.get("eac_gp")])
        return resp
    tot = {"n": len(shown), "revenue": sum((r["revenue"] for r in shown), D0), "total": sum((r["total"] for r in shown), D0),
           "hw_n": sum(1 for r in shown if r["hardware"]), "jobs": len({r["cpn"] for r in shown if r["cpn"]}), "groups": len(groups)}
    filtered = bool(show != "all" or div or pm or user or cust or proj_f or q or min_abs)
    # ---- day arrows: nearest days with a billing on this basis ----
    if per["kind"] == "day":
        days = active_days(basis)
        d = per["start"]
        prev_d = next((x for x in days if x < d), None)
        next_d = next((x for x in reversed(days) if x > d), None)
        per["prev"] = ("day=%s" % prev_d.isoformat()) if prev_d else None
        per["next"] = ("day=%s" % next_d.isoformat()) if next_d else None
    keep = "&".join("%s=%s" % (k, v) for k, v in g.items() if k in ("by", "show", "basis", "division", "pm", "user", "cust", "project", "q", "min", "sort", "per") and v)
    # ---- filter options from the unfiltered population ----
    divisions = sorted({r["division"] for r in rows if r["division"]})
    pms = sorted({(r["job"]["pm_key"], r["job"]["pm_name"]) for r in rows if r["job"] and r["job"].get("pm_key")}, key=lambda t: t[1])
    users = sorted({(r["by"], r["by_name"]) for r in rows if r["by"]}, key=lambda t: t[1])
    as_of = fetch_dict("SELECT MAX(as_of) t FROM finance_aropendocument")[0]["t"]
    when = "as of the last SL pull"
    inv_band, entry, jobb, prog, wipb, profb = "Invoice", "Entry", "Job", "Progress (PTT)", "WIP = earned − billed", "Profitability"
    cols = [
        ("ref", "Invoice", "SL document number (CM = credit memo, DM = debit memo, GJ = a journal posted to the job's revenue) with the customer and the description on the document. Frozen while you scroll. Click the row for what it billed and what has been paid.", False, "fz fz1", inv_band, "inv"),
        ("project", "Project", "The job the document bills (SL project on the invoice header). 010 hardware orders carry no job — they show their sales order, linked to the CNET order. Frozen while you scroll.", False, "fz fz2", inv_band, "proj"),
        ("entered", "Entered", "The day and time the document was keyed into SL (Central time) — the day the billing occurred. Screen on hover: 40690 = sales-order invoicing (shipper release), 08010 = AR Invoice & Memo entry.", False, "gs", entry, "entry"),
        ("by", "By", "Who keyed the document (the SL login, resolved to the employee by initial + surname; the login itself on hover). Edited later by someone else = noted on hover.", False, "", entry, "entry"),
        ("date", "Inv date", "The date on the invoice. Red with a day count when the document was keyed after its date (a progress billing dated the last day of the month and entered days later, for example).", False, "", entry, "entry"),
        ("period", "Period", "The fiscal period the document posted into (the month on the Daily Snapshot and the Divisional P&L). Amber when it is not the invoice date's month.", False, "", entry, "entry"),
        ("amount", "Billed", "Revenue on the document: the sum of its lines on income accounts (40000 SALES, 40001 SALES-MBE, 40300 FREIGHT), negative on a credit memo — exactly what it posted to GL revenue. Sales tax and customer deposits are excluded (see Total). Sorts by size.", True, "", entry, "entry"),
        ("total", "Total", "The document total as the customer sees it: revenue + sales tax + customer deposits / deferred billings (24000 / 21000 — money billed ahead of the service, not revenue). Hover for the split and the open balance.", True, "", entry, "entry"),
        ("division", "Div", "The job's division (SL subaccount prefix); 010 for hardware orders; otherwise the subaccount the revenue line posted to.", False, "gs", jobb, "job"),
        ("pm", "PM", "Project manager from SL.", False, "", jobb, "job"),
        ("state", "State", "Derived lifecycle state of the job today. Dormant = open but no field work for 45+ days.", False, "", jobb, "job"),
        ("pct", "% complete", "PTT estimated %% complete %s — the number that drives the job's WIP. The small figure is how many days ago the PM last touched the job in PTT (green ≤ 14 · amber 31–45 · red over 45 or never); hover for the date and who set it." % when, True, "gs", prog, "prog"),
        ("hours_pct", "Hrs %", "Hours-based % complete: PTT hours to date ÷ (hours to date + the PM's remaining hours). A big gap against the PTT % says the PM % is out of date.", True, "", prog, "prog"),
        ("cv", "Contract", "Contract value — the CONTRACT VALUE budget in SL (current value only).", True, "gs", wipb, "wip"),
        ("billed_td", "Billed to date", "Revenue posted to the job to date (every invoice, less credits), with the share of the contract billed. This row's document is inside it once it has reached the project ledger.", True, "", wipb, "wip"),
        ("earned", "Earned", "Contract × PTT % — revenue earned on the percentage-of-completion basis today.", True, "", wipb, "wip"),
        ("wip", "WIP", "Earned − billed today. Positive (green) = underbilled, money still to invoice; negative (red) = billed ahead of progress. A billing moves this number down by its amount. Sorts by size.", True, "", wipb, "wip"),
        ("cost", "Cost to date", "Direct cost to date (labor + burden + material + subcontract + other direct). The small figure is cost ÷ projected total cost at completion — a cost-based % complete.", True, "gs", profb, "prof"),
        ("gp", "GP% sold → EAC", "Sold GP% (current SL budget) → projected GP% at completion (latest EAC forecast), bold; green above the sold margin, red below.", True, "", profb, "prof"),
        ("eac_gp", "EAC GP", "Projected gross profit at completion in dollars from the latest forecast. Sorts by size.", True, "", profb, "prof"),
    ]
    band_cells = [{"label": inv_band, "span": 1, "cls": "fz fz1", "key": "inv"}, {"label": "", "span": 1, "cls": "fz fz2", "key": "proj"}]
    for key, label, help_, num, cls, band, bkey in cols[2:]:
        if band_cells[-1]["label"] == band and "fz" not in band_cells[-1]["cls"]:
            band_cells[-1]["span"] += 1
        else:
            band_cells.append({"label": band, "span": 1, "cls": "gs", "key": bkey})
    rest_span = len(cols) - 8   # the job bands after the Entry band, spanned by group rows
    return render(request, "dashboard/finance_billings.html",
                  _ctx(request, "finance-billings", per=per, basis=basis, bases=[(b, BASIS_LABEL[b]) for b in BASES], by=by, show=show, shows=SHOW_LABEL,
                       groups=groups, kp=kp, tie=tie, snap=snap, tot=tot, filtered=filtered, sort=sort, sort_key=sort.lstrip("-"), cols=cols, band_cells=band_cells,
                       rest_span=rest_span, per_filter=per_filter, keep=keep, period_groups=period_options(today), divisions=divisions, pms=pms, users=users,
                       g=g, q=q, min_abs=min_abs, as_of=as_of, today=today, groups_labels=GROUPS, posted_ok=bool(per["months"])))


def finance_billings_doc(request):
    """JSON detail for one billing: an invoice's lines (what was billed, with the GL account / subaccount each line posted
    to), payments applied and balance, the job's other billings in the window and whether this one has reached the project
    ledger — or, for ?jrnl=<id>, the journal row itself. ?ref=&type=&cust= identify an invoice."""
    if request.GET.get("jrnl"):
        try:
            jid = int(request.GET["jrnl"])
        except ValueError:
            raise Http404
        rows = fetch_dict("""SELECT t.batch_id, t.detail_num, t.fiscal_period, t.transaction_date, t.posting_date, t.source_created_at, t.source_created_by,
                                    t.amount, t.comment, t.gl_account, t.gl_subaccount, t.batch_type, t.task_id, p.canonical_project_number cpn, p.display_number pdisp, p.title
                             FROM finance_projectfinancialtransaction t JOIN core_project p ON p.id = t.project_id WHERE t.id = %s""", [jid])
        if not rows:
            raise Http404
        r = rows[0]
        return JsonResponse({"kind": "jrnl", "ref": "GJ %s" % r["batch_id"], "detail": r["detail_num"], "period": r["fiscal_period"], "date": r["transaction_date"].isoformat() if r["transaction_date"] else "",
                             "posted": r["posting_date"].isoformat() if r["posting_date"] else "", "entered": timezone.localtime(r["source_created_at"]).strftime("%b %-d, %Y %-I:%M %p") if r["source_created_at"] else "",
                             "by": r["source_created_by"], "amt": _f(r["amount"]), "desc": r["comment"], "acct": r["gl_account"], "sub": r["gl_subaccount"], "task": r["task_id"],
                             "project": {"cpn": r["cpn"], "disp": r["pdisp"], "title": r["title"]}})
    ref, typ, cust = request.GET.get("ref", ""), request.GET.get("type", "IN"), request.GET.get("cust", "")
    docs = fetch_dict("""SELECT i.*, p.canonical_project_number cpn, p.display_number pdisp, p.title ptitle, c.sl_customer_id cust_link,
                                COALESCE(NULLIF(c.canonical_name, ''), i.customer_name) cname, o.cnet_number, sp.name sp_name
                         FROM finance_arinvoice i
                         LEFT JOIN core_project p ON p.id = i.project_id
                         LEFT JOIN core_customer c ON c.id = i.customer_id
                         LEFT JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND i.order_nbr <> ''
                         LEFT JOIN core_salesperson sp ON sp.code = i.slsper_id AND i.slsper_id <> ''
                         WHERE i.ref_nbr = %s AND i.doc_type = %s AND (%s = '' OR i.customer_id_raw = %s)""", [ref, typ, cust, cust])
    if not docs:
        raise Http404
    d = docs[0]
    sign = -1 if d["doc_type"] == "CM" else 1
    lines = fetch_dict("""SELECT line_nbr, kind, gl_account, gl_subaccount, tran_desc, project_id_raw, task_id, invt_id, qty, unit_price, amount, shipper_id, order_nbr
                          FROM finance_arinvoiceline WHERE ref_nbr = %s AND doc_type = %s AND customer_id_raw = %s ORDER BY line_nbr""", [ref, typ, d["customer_id_raw"]])
    apps = fetch_dict("""SELECT date_appl, payment_ref, applied, discount, batch_nbr FROM finance_arpaymentapplication
                         WHERE invoice_ref = %s AND invoice_type = %s ORDER BY date_appl, payment_ref""", [ref, typ])
    others, ledger = [], None
    if d["project_id"]:
        others = fetch_dict("""SELECT ref_nbr, doc_type, doc_date, (sl_created_at AT TIME ZONE %s)::date entered_day, crtd_user, amount, revenue, balance, doc_desc
                               FROM finance_arinvoice WHERE project_id = %s AND NOT (ref_nbr = %s AND doc_type = %s) ORDER BY doc_date DESC, sl_created_at DESC LIMIT 15""",
                            ["America/Chicago", d["project_id"], ref, typ])
        led = fetch_dict("""SELECT fiscal_period, MIN(source_created_at) at, MIN(source_created_by) by, SUM(amount) amt FROM finance_projectfinancialtransaction
                            WHERE system_cd = 'AR' AND category = 'revenue' AND voucher_num = %s AND project_id = %s GROUP BY fiscal_period""", [ref, d["project_id"]])
        ledger = ({"period": led[0]["fiscal_period"], "at": timezone.localtime(led[0]["at"]).strftime("%b %-d, %Y %-I:%M %p") if led[0]["at"] else "", "by": led[0]["by"], "amt": _f(led[0]["amt"])}
                  if led else {"missing": True})
    names = login_names([d["crtd_user"], d["lupd_user"]] + [o["crtd_user"] for o in others])
    nm = lambda lg: names.get(lg, (lg, ""))[0]   # noqa: E731
    return JsonResponse({
        "kind": "inv", "ref": ref, "type": typ, "customer": d["cname"], "cust_id": d["customer_id_raw"], "cust_link": d["cust_link"],
        "lines": [{"n": l["line_nbr"], "kind": l["kind"], "acct": l["gl_account"], "sub": l["gl_subaccount"], "desc": l["tran_desc"],
                   "project": "" if l["project_id_raw"] in ("", "ZZ") else l["project_id_raw"], "task": "" if l["project_id_raw"] in ("", "ZZ") else l["task_id"],
                   "item": l["invt_id"], "qty": _f(l["qty"]), "price": _f(l["unit_price"]), "amt": _f(l["amount"]), "shipper": l["shipper_id"], "order": l["order_nbr"]} for l in lines],
        "apps": [{"d": a["date_appl"].isoformat() if a["date_appl"] else "", "ref": a["payment_ref"], "amt": _f(a["applied"]), "disc": _f(a["discount"]), "batch": a["batch_nbr"]} for a in apps],
        "total": _f(sign * (d["amount"] or 0)), "bal": _f(sign * (d["balance"] or 0)), "revenue": _f(d["revenue"]), "tax": _f(d["tax"]), "deposits": _f(d["deposits"]), "other": _f(d["other"]),
        "doc_date": d["doc_date"].isoformat() if d["doc_date"] else "", "due": d["due_date"].isoformat() if d["due_date"] else "",
        "entered": timezone.localtime(d["sl_created_at"]).strftime("%b %-d, %Y %-I:%M %p") if d["sl_created_at"] else "", "by": nm(d["crtd_user"]), "login": d["crtd_user"], "screen": d["crtd_prog"],
        "edited": ({"by": nm(d["lupd_user"]), "at": timezone.localtime(d["lupd_at"]).strftime("%b %-d, %Y %-I:%M %p")} if (d["lupd_at"] and d["sl_created_at"] and d["lupd_at"] > d["sl_created_at"] + timezone.timedelta(minutes=1)) else None),
        "period": d["per_post"], "batch": d["batch_nbr"], "terms": d["terms"], "po": d["cust_po"], "desc": d["doc_desc"], "sp": d["sp_name"] or d["slsper_id"],
        "order": ({"nbr": d["order_nbr"], "so_type": d["so_type"], "cnet": d["cnet_number"]} if d["order_nbr"] else None),
        "project": ({"cpn": d["cpn"], "disp": d["pdisp"], "title": d["ptitle"], "task": d["task_id"], "ledger": ledger,
                     "others": [{"ref": o["ref_nbr"], "type": o["doc_type"], "date": o["doc_date"].isoformat() if o["doc_date"] else "", "entered": o["entered_day"].isoformat() if o["entered_day"] else "",
                                 "by": nm(o["crtd_user"]), "rev": _f(o["revenue"] if o["revenue"] is not None else (-o["amount"] if o["doc_type"] == "CM" else o["amount"])),
                                 "bal": _f((-1 if o["doc_type"] == "CM" else 1) * (o["balance"] or 0)), "desc": o["doc_desc"]} for o in others]} if d["cpn"] else None),
    })
