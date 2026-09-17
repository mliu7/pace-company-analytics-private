"""Accounts Receivable page (/finance/ar/): every open AR document, grouped by customer / age / due date / project /
salesperson / division / book, with the aging matrix per group, per-customer payment behaviour, and an on-demand
detail panel per document (what was billed, payments so far, job / order / contact context). Plan + reviews:
docs/ar_page_plan.md. Reconciles to the daily snapshot's AR tile (same table, same rules — docs/06 §AR)."""

import csv
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from apps.core import related_parties as rp
from apps.ingestion.bulk import fetch_dict
from .views import _ctx

D0 = Decimal(0)
GROUPS = ("customer", "age", "due", "project", "salesperson", "division", "book")
SHOWS = ("all", "pastdue", "over90", "credits", "pending")
BUCKET_LABEL = {"current": "Current", "d30": "1–30 days", "d60": "31–60 days", "d90": "61–90 days", "over90": "Over 90 days"}
BUCKET_ORDER = {"over90": 0, "d90": 1, "d60": 2, "d30": 3, "current": 4}
BOOK_LABEL = {"project": "Project billings", "so2": "SO2 — project material orders", "so1": "SO1 — 010 hardware orders", "rma": "RMA",
              "credit": "Credits / unapplied", "pending": "Pending (unreleased)", "other": "Other (no job, no order)"}
BIG = Decimal(100000)


def _f(v):
    return float(v) if v is not None else None


def _docs(where="", params=()):
    """Every open document with its customer, job, order and salesperson context (one query); `where` narrows the set
    (the customer page passes its customer id)."""
    return fetch_dict("""
        SELECT d.id, d.ref_nbr, d.doc_type, d.customer_id_raw cust_id, COALESCE(NULLIF(c.canonical_name, ''), d.customer_name) customer,
               c.sl_customer_id cust_link, COALESCE(c.market_sector, '') sector, COALESCE(c.is_general_contractor, FALSE) gc,
               COALESCE(c.city, '') city, COALESCE(c.state, '') state,
               d.project_id_raw, p.canonical_project_number cpn, p.display_number pdisp, p.title ptitle, dv.code pdiv, p.lifecycle_state pstate,
               p.contract_value cv, p.billed_revenue pbilled, p.pm_percent_complete ppct, pm.canonical_name pm_name, pm.employee_key pm_key,
               d.order_nbr, d.so_type, o.cnet_number, d.book, d.doc_date, d.due_date, d.days_past_due, d.bucket, d.sign, d.doc_bal,
               d.sign * d.doc_bal bal, d.orig_amt, d.released, d.doc_desc, d.slsper_id, COALESCE(sp.name, '') sp_name, spe.employee_key sp_key,
               d.terms, d.cust_po
        FROM finance_aropendocument d
        LEFT JOIN core_customer c ON c.id = d.customer_id
        LEFT JOIN core_project p ON p.id = d.project_id
        LEFT JOIN core_division dv ON dv.id = p.division_id
        LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
        LEFT JOIN sales_slcnetorder o ON o.ord_nbr = d.order_nbr AND d.order_nbr <> ''
        LEFT JOIN core_salesperson sp ON sp.code = d.slsper_id AND d.slsper_id <> ''
        LEFT JOIN core_employee spe ON spe.id = sp.employee_id
        %s
        ORDER BY d.sign * d.doc_bal DESC""" % (("WHERE " + where) if where else ""), list(params))


def _apps_by_doc(cid=None):
    """(invoice ref, type) -> number of payment applications on it (430-day window)."""
    cond, params = (" AND customer_id_raw = %s", [cid]) if cid else ("", [])
    return {(r["ref"], r["t"]): r["n"] for r in fetch_dict(
        "SELECT invoice_ref ref, invoice_type t, COUNT(*) n FROM finance_arpaymentapplication WHERE applied > 0%s GROUP BY 1, 2" % cond, params)}


def _decorate(d, apps_by_doc):
    """Per-document derived fields shared by the AR page and the customer page's Outstanding-invoices card."""
    d["paid"] = ((d["orig_amt"] or D0) - d["doc_bal"]) if (d["sign"] > 0 and d["orig_amt"] is not None) else D0
    d["partial"] = d["sign"] > 0 and d["paid"] > Decimal("0.005")
    d["apps_n"] = apps_by_doc.get((d["ref_nbr"], d["doc_type"]), 0)
    d["div"] = _doc_div(d)
    d["big"] = abs(d["bal"]) >= BIG
    d["family"] = rp.is_related_customer(d["cust_id"])    # owner-family account (apps/core/related_parties)
    d["billed_pct"] = (d["pbilled"] / d["cv"]) if (d["cv"] and d["pbilled"] is not None) else None
    if d["project_id_raw"] == "ZZ" and not d["cpn"]:     # SL's placeholder job on non-project billings
        d["project_id_raw"] = ""


def _customer_behaviour(today, cid=None):
    """Per customer (or one customer), from the 430-day payment-application window: last payment (date, amount),
    collected and billed in the last 12 months, $-weighted days from invoice to payment."""
    cond, extra = (" AND customer_id_raw = %s", [cid]) if cid else ("", [])
    pay = {}
    for r in fetch_dict("""SELECT customer_id_raw cid, date_appl d, SUM(applied) amt FROM finance_arpaymentapplication
                           WHERE applied > 0%s GROUP BY 1, 2 ORDER BY 1, 2""" % cond, extra):
        pay[r["cid"]] = r          # ordered by date -> the last row per customer wins
    beh = {}
    for r in fetch_dict("""SELECT customer_id_raw cid, SUM(applied) paid_12, SUM(applied * (date_appl - invoice_date)) num,
                                  SUM(CASE WHEN invoice_date IS NOT NULL THEN applied END) den
                           FROM finance_arpaymentapplication WHERE applied > 0 AND date_appl >= %s%s GROUP BY 1""" % ("%s", cond), [today - timedelta(days=365)] + extra):
        beh[r["cid"]] = {"paid_12": r["paid_12"] or D0, "days": (r["num"] / r["den"]) if r["den"] else None}
    for r in fetch_dict("""SELECT customer_id_raw cid, SUM(CASE WHEN doc_type IN ('IN','DM','FC') THEN amount WHEN doc_type = 'CM' THEN -amount ELSE 0 END) billed_12
                           FROM finance_arinvoice WHERE doc_date >= %s%s GROUP BY 1""" % ("%s", cond), [today - timedelta(days=365)] + extra):
        beh.setdefault(r["cid"], {"paid_12": D0, "days": None})["billed_12"] = r["billed_12"] or D0
    for cid, r in pay.items():
        beh.setdefault(cid, {"paid_12": D0, "days": None})["last"] = (r["d"], r["amt"])
    return beh


def _doc_div(d):
    return d["pdiv"] or ("010" if d["book"] == "so1" else "")


def _kpis(docs, today):
    live = [d for d in docs if d["released"]]
    pos = [d for d in live if d["sign"] > 0]
    total = sum((d["bal"] for d in live), D0)
    pos_total = sum((d["bal"] for d in pos), D0)
    past = sum((d["bal"] for d in pos if (d["days_past_due"] or 0) > 0), D0)
    over90 = sum((d["bal"] for d in pos if d["bucket"] == "over90"), D0)
    credits = sum((d["bal"] for d in live if d["sign"] < 0), D0)
    pending = [d for d in docs if not d["released"]]
    pending_amt = sum((d["orig_amt"] or D0 for d in pending), D0)
    by_cust = defaultdict(lambda: D0)
    for d in live:
        by_cust[d["cust_id"]] += d["bal"]
    tops = sorted(by_cust.values(), reverse=True)
    billed_90 = fetch_dict("""SELECT COALESCE(SUM(CASE WHEN doc_type IN ('IN','DM','FC') THEN amount WHEN doc_type = 'CM' THEN -amount ELSE 0 END), 0) s
                              FROM finance_arinvoice WHERE doc_date > %s""", [today - timedelta(days=90)])[0]["s"]
    billed_30 = fetch_dict("""SELECT COALESCE(SUM(CASE WHEN doc_type IN ('IN','DM','FC') THEN amount WHEN doc_type = 'CM' THEN -amount ELSE 0 END), 0) s
                              FROM finance_arinvoice WHERE doc_date > %s""", [today - timedelta(days=30)])[0]["s"]
    collected_30 = fetch_dict("SELECT COALESCE(SUM(applied), 0) s FROM finance_arpaymentapplication WHERE applied > 0 AND date_appl > %s", [today - timedelta(days=30)])[0]["s"]
    snaps = fetch_dict("""SELECT snapshot_date, ar_total, ar_current, ar_d30, ar_d60, ar_d90, ar_over90, ar_credits, ar_pending, reconstructed
                          FROM finance_dailyfinancesnapshot ORDER BY snapshot_date DESC LIMIT 2""")
    latest, prev = (snaps[0] if snaps else None), (snaps[1] if len(snaps) > 1 else None)
    return {"total": total, "pos_total": pos_total, "past": past, "past_pct": (past / pos_total) if pos_total else None,
            "over90": over90, "over90_pct": (over90 / pos_total) if pos_total else None,
            "over90_delta": (over90 - prev["ar_over90"]) if (prev and prev["ar_over90"] is not None) else None, "prev_date": prev["snapshot_date"] if prev else None,
            "credits": credits, "credits_n": sum(1 for d in live if d["sign"] < 0), "pending": pending_amt, "pending_n": len(pending),
            "dso": (total / (billed_90 / Decimal(90))) if billed_90 else None, "billed_90": billed_90, "billed_30": billed_30, "collected_30": collected_30,
            "docs": len(live), "customers": len(by_cust),
            "top3": (sum(tops[:3], D0) / total) if total else None, "top10": (sum(tops[:10], D0) / total) if total else None,
            "snap": latest, "snap_match": (latest is not None and latest["ar_total"] is not None and abs(latest["ar_total"] - total) < Decimal("0.01")),
            "snap_diff": (total - latest["ar_total"]) if (latest and latest["ar_total"] is not None) else None}


def _week_bounds(today):
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6), monday + timedelta(days=13)


def _due_key(d, today):
    if d["sign"] < 0:
        return ("6", "Credits / unapplied")
    if not d["released"]:
        return ("7", "Pending (unreleased)")
    due = d["due_date"]
    if due is None:
        return ("5", "No due date")
    if (d["days_past_due"] or 0) > 0:
        return ("0", "Overdue")
    _, sun, next_sun = _week_bounds(today)
    if due <= sun:
        return ("1", "Due this week")
    if due <= next_sun:
        return ("2", "Due next week")
    if due <= today + timedelta(days=30):
        return ("3", "Due within 30 days")
    return ("4", "Due later")


def _group_key(d, by, today):
    """(sort key, id, label) for a document under the chosen grouping."""
    if by == "age":
        if not d["released"]:
            return ("7", "pending", "Pending (unreleased)")
        if d["sign"] < 0:
            return ("6", "credit", "Credits / unapplied")
        return (str(BUCKET_ORDER.get(d["bucket"], 5)), d["bucket"], BUCKET_LABEL.get(d["bucket"], d["bucket"]))
    if by == "due":
        k, lbl = _due_key(d, today)
        return (k, k, lbl)
    if by == "project":
        if d["cpn"]:
            return ("0" + d["cpn"], d["cpn"], "%s %s" % (d["pdisp"], d["ptitle"] or ""))
        if d["book"] == "so1":
            return ("1", "so1", "No job — 010 hardware orders (SO1)")
        return ("2", "none", "No job")
    if by == "salesperson":
        code = d["slsper_id"] or ""
        return ("0" + (d["sp_name"] or code) if code else "1", code or "none", ("%s %s" % (d["sp_name"], code)).strip() if code else "No salesperson on the invoice")
    if by == "division":
        dv = _doc_div(d)
        return ("0" + dv if dv else "1", dv or "none", dv or "No division (no job, not an SO1 order)")
    if by == "book":
        return (str(list(BOOK_LABEL).index(d["book"]) if d["book"] in BOOK_LABEL else 9), d["book"], BOOK_LABEL.get(d["book"], d["book"]))
    return (d["customer"].lower(), d["cust_id"], d["customer"] or d["cust_id"])


def _filter(docs, g, today):
    show = g.get("show") if g.get("show") in SHOWS else "all"
    q = (g.get("q") or "").strip().lower()
    div, sp, book = g.get("div", ""), g.get("sp", ""), g.get("book", "")
    try:
        min_bal = Decimal(g.get("min") or 0)
    except Exception:
        min_bal = D0
    out, family_out = [], []
    # "Past due" and "Over 90" exist to build a collections worklist, so owner-family accounts come off them
    # (apps/core/related_parties). "All", the customer page and the CSV keep every document, and no total moves.
    problem_view = show in ("pastdue", "over90")
    for d in docs:
        if problem_view and d.get("family"):
            family_out.append(d)
            continue
        if show == "all" and not d["released"]:
            continue
        if show == "pastdue" and not (d["sign"] > 0 and (d["days_past_due"] or 0) > 0 and d["released"]):
            continue
        if show == "over90" and not (d["bucket"] == "over90" and d["sign"] > 0 and d["released"]):
            continue
        if show == "credits" and not d["sign"] < 0:
            continue
        if show == "pending" and d["released"]:
            continue
        if div and _doc_div(d) != div:
            continue
        if sp and d["slsper_id"] != sp:
            continue
        if book and d["book"] != book:
            continue
        if min_bal and abs(d["bal"]) < min_bal:
            continue
        if q:
            hay = " ".join(str(x or "") for x in (d["customer"], d["cust_id"], d["ref_nbr"], d["cust_po"], d["doc_desc"], d["project_id_raw"], d["pdisp"], d["ptitle"], d["order_nbr"], d["cnet_number"], d["sp_name"])).lower()
            if q not in hay:
                continue
        out.append(d)
    if problem_view:
        family_out = [d for d in family_out if d["sign"] > 0 and d["released"]
                      and ((d["days_past_due"] or 0) > 0 if show == "pastdue" else d["bucket"] == "over90")]
    return out, show, q, div, sp, book, min_bal, family_out


def _groups(docs, by, today, beh):
    """Decorated documents -> group rows (aging matrix, chips, behaviour) for the chosen grouping."""
    groups = {}
    for d in docs:
        sk, gid, label = _group_key(d, by, today)
        g = groups.setdefault(gid, {"id": gid, "sort": sk, "label": label, "docs": [], "open": D0, "credits": D0, "n": 0, "max_days": 0, "oldest_due": None,
                                    "b": {k: D0 for k in BUCKET_LABEL}, "first": d})
        g["docs"].append(d)
        g["n"] += 1
        if d["released"]:
            g["open"] += d["bal"]
            if d["sign"] > 0:
                g["b"][d["bucket"]] = g["b"].get(d["bucket"], D0) + d["bal"]
                if (d["days_past_due"] or 0) > g["max_days"]:
                    g["max_days"], g["oldest_due"] = d["days_past_due"], d["due_date"]
            else:
                g["credits"] += d["bal"]
    out = []
    for g in groups.values():
        f = g["first"]
        g["cols"] = [g["b"][k] for k in ("current", "d30", "d60", "d90", "over90")]
        g["past_due"] = sum(g["cols"][1:], D0)
        g["big"] = abs(g["open"]) >= BIG
        # chips: what the group is (customer / project), how it pays (customer mode)
        chips = []
        if by == "customer":
            g["link"] = "/customers/%s/" % f["cust_link"] if f["cust_link"] else ""
            b = beh.get(g["id"], {})
            terms = sorted({d["terms"] for d in g["docs"] if d["terms"]})
            sps = sorted({d["sp_name"] for d in g["docs"] if d["sp_name"]})
            if f["sector"]:
                chips.append(("sector", f["sector"]))
            if f["gc"]:
                chips.append(("gc", "general contractor"))
            if terms:
                chips.append(("terms", "terms " + " / ".join(terms)))
            if sps:
                chips.append(("sp", " / ".join(sps[:2]) + (" +%d" % (len(sps) - 2) if len(sps) > 2 else "")))
            if f["city"]:
                chips.append(("city", ", ".join(x for x in (f["city"], f["state"]) if x)))
            g["last"] = b.get("last")
            g["days"] = b.get("days")
            g["paid_12"], g["billed_12"] = b.get("paid_12", D0), b.get("billed_12", D0)
            t = min((int(x) for x in terms if x.isdigit()), default=None)
            g["terms_days"] = t
            g["days_cls"] = ("neg" if (t is not None and g["days"] is not None and g["days"] > t + 45) else "warn-ink" if (t is not None and g["days"] is not None and g["days"] > t + 15) else "")
            since = (today - g["last"][0]).days if g.get("last") else None
            g["since"] = since
            if since is not None and since <= 30:
                g["beh"] = ("pos", "paid %dd ago" % since, "A payment landed within the last 30 days — a live relationship.")
            elif g["past_due"] and (since is None or since >= 90):
                g["beh"] = ("neg", ("no payment %dd" % since) if since is not None else "no payment on record", "Past due and nothing collected in 90+ days (430-day window).")
            else:
                g["beh"] = None
        elif by == "project":
            g["link"] = "/projects/%s/" % f["cpn"] if f["cpn"] else ""
            if f["cpn"]:
                for k, v in (("pm", f["pm_name"]), ("div", f["pdiv"]), ("state", (f["pstate"] or "").replace("_", " "))):
                    if v:
                        chips.append((k, v))
                if f["cv"]:
                    chips.append(("cv", "billed %s of %s (%s)" % (_money(f["pbilled"]), _money(f["cv"]), _pct(f["billed_pct"] if "billed_pct" in f else None))))
                    if f["ppct"] is not None:
                        chips.append(("pct", "%s complete (PTT)" % _pct(f["ppct"])))
            g["pm_key"] = f["pm_key"] if f["cpn"] else None
        elif by == "salesperson":
            g["link"] = "/people/%s/" % f["sp_key"] if (f["sp_key"] and g["id"] != "none") else ""
        else:
            g["link"] = ""
        g["chips"] = chips
        g["customers"] = len({d["cust_id"] for d in g["docs"]})
        g["docs"].sort(key=lambda d: (-d["bal"]))
        out.append(g)
    if by == "customer":
        out.sort(key=lambda g: -g["open"])
    else:
        out.sort(key=lambda g: g["sort"])
    return out


def _money(v):
    if v is None:
        return "—"
    v = int(round(v))
    return ("-$%s" % format(-v, ",")) if v < 0 else ("$%s" % format(v, ","))


def _pct(v):
    return "—" if v is None else "%d%%" % int(round(float(v) * 100))


def finance_ar(request):
    today = timezone.localdate()
    g = request.GET
    by = g.get("by") if g.get("by") in GROUPS else "customer"
    docs = _docs()
    apps_by_doc = _apps_by_doc()
    for d in docs:
        _decorate(d, apps_by_doc)
    kp = _kpis(docs, today)
    as_of = fetch_dict("SELECT MAX(as_of) t FROM finance_aropendocument")[0]["t"]
    shown, show, q, div, sp, book, min_bal, family_out = _filter(docs, g, today)
    beh = _customer_behaviour(today) if by == "customer" else {}
    groups = _groups(shown, by, today, beh)
    if g.get("export") == "csv":
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="open-ar-%s-%s.csv"' % (by, today.isoformat())
        w = csv.writer(resp)
        w.writerow(["group", "customer_id", "customer", "ref", "type", "book", "division", "project", "project_title", "order", "cnet_order", "invoice_date", "due_date",
                    "days_past_due", "bucket", "terms", "salesperson", "cust_po", "description", "original", "paid", "balance", "released"])
        for grp in groups:
            for d in grp["docs"]:
                w.writerow([grp["label"], d["cust_id"], d["customer"], d["ref_nbr"], d["doc_type"], d["book"], d["div"], d["pdisp"] or d["project_id_raw"], d["ptitle"] or "",
                            d["order_nbr"], d["cnet_number"] or "", d["doc_date"], d["due_date"], d["days_past_due"], d["bucket"], d["terms"], d["sp_name"] or d["slsper_id"],
                            d["cust_po"], d["doc_desc"], d["orig_amt"], d["paid"], d["bal"], d["released"]])
        return resp
    tot = {"open": sum((grp["open"] for grp in groups), D0), "credits": sum((grp["credits"] for grp in groups), D0), "n": sum(grp["n"] for grp in groups),
           "cols": [sum((grp["cols"][i] for grp in groups), D0) for i in range(5)], "groups": len(groups)}
    filtered = bool(q or div or sp or book or min_bal or show != "all")
    divisions = sorted({_doc_div(d) for d in docs if _doc_div(d)})
    sps = sorted({(d["slsper_id"], d["sp_name"]) for d in docs if d["slsper_id"]}, key=lambda x: x[1] or x[0])
    books = [b for b in BOOK_LABEL if any(d["book"] == b for d in docs)]
    came_from = g.get("from", "") if g.get("from", "") not in ("", today.isoformat()) else ""   # banner only when the visitor left a past day
    return render(request, "dashboard/finance_ar.html",
                  _ctx(request, "finance-ar", by=by, show=show, q=q, div=div, sp=sp, book=book, min_bal=min_bal, groups=groups, tot=tot, kp=kp, as_of=as_of,
                       today=today, filtered=filtered, divisions=divisions, sps=sps, books=books, book_label=BOOK_LABEL, groups_labels=GROUPS,
                       bucket_label=BUCKET_LABEL, came_from=came_from, big=BIG,
                       family_note=rp.excluded_note([dict(d, amt=float(d["bal"])) for d in family_out], noun="document"),
                       family_chip=rp.CHIP_LABEL, family_title=rp.CHIP_TITLE,
                       shows=[("all", "All"), ("pastdue", "Past due"), ("over90", "Over 90"), ("credits", "Credits"), ("pending", "Pending")]))


def customer_open_ar(cid, today):
    """The customer page's "Outstanding invoices" card: one customer's open AR documents — the same population, aging
    rules and per-document derivations as the AR page — with totals, the aging matrix and payment behaviour.
    `cid` is the SL customer id (core_customer.sl_customer_id == finance_aropendocument.customer_id_raw)."""
    docs = _docs("d.customer_id_raw = %s", [cid])
    apps_by_doc = _apps_by_doc(cid)
    for d in docs:
        _decorate(d, apps_by_doc)
    live = [d for d in docs if d["released"]]
    pos = [d for d in live if d["sign"] > 0]
    pending = [d for d in docs if not d["released"]]
    b = {k: D0 for k in BUCKET_LABEL}
    for d in pos:
        b[d["bucket"]] = b.get(d["bucket"], D0) + d["bal"]
    cols = [b[k] for k in ("current", "d30", "d60", "d90", "over90")]
    oldest = max(pos, key=lambda d: d["days_past_due"] or 0, default=None)
    beh = _customer_behaviour(today, cid).get(cid, {})
    terms = sorted({d["terms"] for d in docs if d["terms"]})
    terms_days = min((int(x) for x in terms if x.isdigit()), default=None)
    days = beh.get("days")
    last = beh.get("last")
    since = (today - last[0]).days if last else None
    past_due = sum(cols[1:], D0)
    out = {
        "docs": docs, "n": len(live), "pending": pending, "pending_amt": sum((d["orig_amt"] or D0 for d in pending), D0),
        "open": sum((d["bal"] for d in live), D0), "pos_total": sum((d["bal"] for d in pos), D0),
        "past_due": past_due, "over90": b["over90"], "cols": cols, "bucket_cols": list(zip(("current", "d30", "d60", "d90", "over90"), cols)),
        "credits": sum((d["bal"] for d in live if d["sign"] < 0), D0), "credits_n": sum(1 for d in live if d["sign"] < 0),
        "orig": sum((d["orig_amt"] or D0 for d in pos), D0), "paid": sum((d["paid"] for d in pos), D0),
        "max_days": (oldest["days_past_due"] or 0) if oldest else 0, "oldest_due": oldest["due_date"] if oldest else None,
        "last": last, "since": since, "days": days, "terms": terms, "terms_days": terms_days,
        "days_cls": ("neg" if (terms_days is not None and days is not None and days > terms_days + 45)
                     else "warn-ink" if (terms_days is not None and days is not None and days > terms_days + 15) else ""),
        "paid_12": beh.get("paid_12", D0), "billed_12": beh.get("billed_12", D0),
        "as_of": fetch_dict("SELECT MAX(as_of) t FROM finance_aropendocument")[0]["t"],
    }
    out["past_pct"] = (past_due / out["pos_total"]) if out["pos_total"] else None
    if since is not None and since <= 30:
        out["beh"] = ("pos", "paid %dd ago" % since, "A payment landed within the last 30 days — a live relationship.")
    elif past_due and (since is None or since >= 90):
        out["beh"] = ("neg", ("no payment %dd" % since) if since is not None else "no payment on record", "Past due and nothing collected in 90+ days (430-day window).")
    else:
        out["beh"] = None
    return out


def finance_ar_doc(request):
    """JSON detail for one open document: lines billed, payments applied, job / order / customer context, sibling
    open documents on the same job. ?ref=&type=&cust= identify the document (ref numbers repeat across doc types)."""
    ref, typ, cust = request.GET.get("ref", ""), request.GET.get("type", "IN"), request.GET.get("cust", "")
    docs = fetch_dict("""SELECT d.*, p.canonical_project_number cpn, p.display_number pdisp, p.title ptitle, dv.code pdiv, p.lifecycle_state pstate,
                                p.contract_value cv, p.billed_revenue pbilled, p.earned_revenue pearned, p.pm_percent_complete ppct, p.close_date pclose,
                                pm.canonical_name pm_name, pm.employee_key pm_key, o.cnet_number, o.ship_name, o.ship_city, o.ship_state, o.cust_ord_nbr,
                                sp.name sp_name
                         FROM finance_aropendocument d
                         LEFT JOIN core_project p ON p.id = d.project_id LEFT JOIN core_division dv ON dv.id = p.division_id
                         LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
                         LEFT JOIN sales_slcnetorder o ON o.ord_nbr = d.order_nbr AND d.order_nbr <> ''
                         LEFT JOIN core_salesperson sp ON sp.code = d.slsper_id
                         WHERE d.ref_nbr = %s AND d.doc_type = %s AND (%s = '' OR d.customer_id_raw = %s)""", [ref, typ, cust, cust])
    if not docs:
        raise Http404
    d = docs[0]
    lines = fetch_dict("""SELECT line_nbr, gl_account, gl_subaccount, tran_desc, project_id_raw, task_id, invt_id, qty, unit_price, amount
                          FROM finance_aropeninvoiceline WHERE ref_nbr = %s AND doc_type = %s AND customer_id_raw = %s ORDER BY line_nbr""", [ref, typ, d["customer_id_raw"]])
    apps = fetch_dict("""SELECT date_appl, payment_ref, applied, discount, batch_nbr FROM finance_arpaymentapplication
                         WHERE invoice_ref = %s AND invoice_type = %s ORDER BY date_appl, payment_ref""", [ref, typ])
    contact = fetch_dict("""SELECT name, attn, phone, email, bill_name, bill_attn, bill_phone, bill_city, bill_state, terms, stmt_cycle, status, credit_limit, setup_date
                            FROM finance_slcustomer WHERE customer_id = %s""", [d["customer_id_raw"]])
    siblings = fetch_dict("""SELECT ref_nbr, doc_type, doc_date, due_date, days_past_due, sign * doc_bal bal FROM finance_aropendocument
                             WHERE project_id IS NOT NULL AND project_id = %s AND NOT (ref_nbr = %s AND doc_type = %s) AND released ORDER BY due_date""",
                          [d["project_id"], ref, typ]) if d["project_id"] else []
    return JsonResponse({
        "ref": ref, "type": typ, "customer": d["customer_name"], "cust_id": d["customer_id_raw"],
        "lines": [{"n": l["line_nbr"], "acct": l["gl_account"], "sub": l["gl_subaccount"], "desc": l["tran_desc"], "project": "" if l["project_id_raw"] in ("", "ZZ") else l["project_id_raw"], "task": "" if l["project_id_raw"] in ("", "ZZ") else l["task_id"],
                   "item": l["invt_id"], "qty": _f(l["qty"]), "price": _f(l["unit_price"]), "amt": _f(l["amount"]), "tax": l["gl_account"].startswith("205")} for l in lines],
        "apps": [{"d": a["date_appl"].isoformat() if a["date_appl"] else "", "ref": a["payment_ref"], "amt": _f(a["applied"]), "disc": _f(a["discount"]), "batch": a["batch_nbr"]} for a in apps],
        "orig": _f(d["orig_amt"]), "bal": _f(d["sign"] * d["doc_bal"]), "doc_date": d["doc_date"].isoformat() if d["doc_date"] else "", "due": d["due_date"].isoformat() if d["due_date"] else "",
        "days": d["days_past_due"], "terms": d["terms"], "po": d["cust_po"], "desc": d["doc_desc"], "per_post": d["per_post"], "sp": d["sp_name"] or d["slsper_id"], "book": d["book"],
        "project": ({"cpn": d["cpn"], "disp": d["pdisp"], "title": d["ptitle"], "div": d["pdiv"], "state": d["pstate"], "cv": _f(d["cv"]), "billed": _f(d["pbilled"]), "earned": _f(d["pearned"]),
                     "pct": _f(d["ppct"]), "close": d["pclose"].isoformat() if d["pclose"] else "", "pm": d["pm_name"], "pm_key": d["pm_key"],
                     "siblings": [{"ref": s["ref_nbr"], "type": s["doc_type"], "date": s["doc_date"].isoformat() if s["doc_date"] else "", "due": s["due_date"].isoformat() if s["due_date"] else "",
                                   "days": s["days_past_due"], "bal": _f(s["bal"])} for s in siblings]} if d["cpn"] else None),
        "order": ({"nbr": d["order_nbr"], "so_type": d["so_type"], "cnet": d["cnet_number"], "ship_name": d["ship_name"], "ship_city": d["ship_city"], "ship_state": d["ship_state"],
                   "cust_po": d["cust_ord_nbr"]} if d["order_nbr"] else None),
        "contact": (contact[0] and {k: (_f(v) if k == "credit_limit" else (v.isoformat() if hasattr(v, "isoformat") else v)) for k, v in contact[0].items()}) if contact else None,
    })
