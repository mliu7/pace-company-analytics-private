"""Customer payment behaviour — every payment a customer made, invoice by invoice, and how long each took.

Powers the "Payment history" card on the customer page (docs/06 "Customer payment history"; metric definitions in
docs/04 "Customer payment behaviour"). Sources are the local copies of SL AR (read-only pulls, full history since
`finance_loaders.AR_HISTORY_FIRST_YEAR` = 2013): `finance_arinvoice` (every IN / DM / CM with due date and terms),
`finance_arpaymentapplication` (every PA → invoice application) joined to `finance_arpayment` (the payment's own
date, total and reference) and `core_project` for the job behind each invoice.

Definitions
- **Paid on** = the payment document's date (ARDoc PA DocDate: the check / wire date, the Payments page's day key).
  The application date (ARAdjust DateAppl) is the fallback when the payment is missing or dated in the future.
- **Days to pay** = paid on − invoice date, per application. Customer-level figures are dollar-weighted by the
  applied amount, so a $180k progress bill counts for more than a $200 service call.
- **vs due** = paid on − due date (negative = before the due date). Timing buckets: within terms (≤ 0 days),
  1–30 late, 31–60, 61–90, over 90.
- **Retainage.** General contractors hold 5 % or 10 % of every progress bill until the job closes. An application that
  leaves exactly that share of the invoice open (± 0.3 pt) is "paid to retention": the customer paid everything it
  owed on time and the balance is the contractual holdback, not a late payment. The later payment of that holdback is a
  "retention release"; its days are shown on the row but kept out of the headline pays-in figure and the timing
  buckets, because it measures the job's closeout, not the customer's promptness.
- **Reversals** (negative applications: NSF, misapplied cash) are listed and re-open the invoice in the running
  balance, but never weight a metric.

The pure functions take plain dicts so tests/unit runs without a database.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

D0 = Decimal("0")
CENT = Decimal("0.01")

# SL Terms.TermsId -> due days (dbo.Terms: DueType 'D' + DueIntrv; 75 / 90 carry DueIntrv 0 in SL, so the code's face
# value is used). PW = pay-when-paid has no fixed due interval.
TERMS_DAYS = {"10": 10, "20": 20, "30": 30, "45": 45, "60": 60, "75": 75, "90": 90,
              "AE": 10, "CC": 10, "MC": 10, "CD": 1, "DU": 1, "PP": 1, "PW": None}
DEFAULT_TERMS_DAYS = 30

RETENTION_RATES = (Decimal("0.05"), Decimal("0.10"))
RETENTION_TOL = Decimal("0.003")

TIMING_ORDER = ("on_time", "late30", "late60", "late90", "late90p")
TIMING_LABEL = {"on_time": "Within terms", "late30": "1–30 days late", "late60": "31–60 days late",
                "late90": "61–90 days late", "late90p": "Over 90 days late"}
METHOD_LABEL = {"wire": "Wire / ACH", "check": "Check", "card": "Card", "other": "Other"}

# Payer grade (PCA's rule, not an SL field): dollar-weighted days to pay minus the terms on the invoices, retention
# releases and reversals excluded. Thresholds are deliberately generous — a week of mail and processing float is normal.
GRADES = (("prompt", 10, "Pays on time", "Dollar-weighted days to pay within 10 days of terms."),
          ("fair", 30, "Pays a little late", "Dollar-weighted days to pay 11–30 days over terms."),
          ("slow", 60, "Pays slowly", "Dollar-weighted days to pay 31–60 days over terms."),
          ("poor", None, "Pays very late", "Dollar-weighted days to pay more than 60 days over terms."))
GRADE_CSS = {"prompt": "good", "fair": "info", "slow": "warn", "poor": "bad"}


def terms_days(code):
    """Due days for an SL terms code; None when the code has no fixed interval (PW) or is unknown."""
    code = (code or "").strip().upper()
    if code in TERMS_DAYS:
        return TERMS_DAYS[code]
    return int(code) if code.isdigit() else None


def payment_method(ref):
    """How the money came, from the payment reference: WT… = wire / ACH batch, CC… = card, digits = check number."""
    r = (ref or "").strip().upper()
    if r.startswith("WT"):
        return "wire"
    if r.startswith("CC"):
        return "card"
    if r.isdigit():
        return "check"
    return "other"


def retention_share(open_after, invoice_amt):
    """0.05 / 0.10 when the balance left open is that share of the invoice (± RETENTION_TOL), else None."""
    if not invoice_amt or invoice_amt <= 0 or open_after is None or open_after <= 0:
        return None
    share = Decimal(open_after) / Decimal(invoice_amt)
    for rate in RETENTION_RATES:
        if abs(share - rate) <= RETENTION_TOL:
            return rate
    return None


def timing_bucket(vs_due):
    """Timing bucket key for days vs the due date (None when unknown)."""
    if vs_due is None:
        return None
    if vs_due <= 0:
        return "on_time"
    if vs_due <= 30:
        return "late30"
    if vs_due <= 60:
        return "late60"
    if vs_due <= 90:
        return "late90"
    return "late90p"


def weighted_days(pairs):
    """Dollar-weighted mean of (days, amount) pairs; None when nothing weighs."""
    num = den = D0
    for days, amt in pairs:
        if days is None or amt is None or amt <= 0:
            continue
        num += Decimal(days) * amt
        den += amt
    return (num / den) if den else None


def median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def grade(days, terms_d):
    """(key, label, css, explanation) for a dollar-weighted days-to-pay figure against the terms; None without data."""
    if days is None:
        return None
    over = int(round(days - (terms_d if terms_d is not None else DEFAULT_TERMS_DAYS)))
    for key, limit, label, why in GRADES:
        if limit is None or over <= limit:
            return {"key": key, "label": label, "css": GRADE_CSS[key], "over": over, "why": why}
    return None


def enrich(apps, invoices, payments, today):
    """Turn raw application rows into the table's rows.

    apps: dicts with id, payment_ref, customer_id_raw, date_appl, applied, discount, invoice_ref, invoice_type,
          invoice_date, due_date, terms, invoice_amt, invoice_balance (+ any project / order context keys, passed through).
    invoices: {(ref, type): {"amount", "balance", "doc_date", "due_date", "terms", ...}} — the invoice master, which
          wins over the application's copy of the same fields when present.
    payments: {(ref, customer_id_raw): {"doc_date", "orig_amt", "balance", "batch_nbr"}}.
    Returns rows sorted by paid-on descending, payment ref, applied descending (the default table order)."""
    by_inv = defaultdict(list)
    rows = []
    for a in apps:
        inv = invoices.get((a["invoice_ref"], a["invoice_type"])) or {}
        pay = payments.get((a["payment_ref"], a["customer_id_raw"])) or {}
        pay_date = pay.get("doc_date")
        if pay_date is None or (today and pay_date > today + timedelta(days=7)):
            pay_date = a.get("date_appl")
        inv_date = inv.get("doc_date") or a.get("invoice_date")
        due = inv.get("due_date") or a.get("due_date")
        terms = inv.get("terms") or a.get("terms") or ""
        amt = inv.get("amount") if inv.get("amount") is not None else a.get("invoice_amt")
        r = dict(a)
        r.update({"pay_date": pay_date, "pay_amt": pay.get("orig_amt"), "pay_bal": pay.get("balance") or D0,
                  "batch": pay.get("batch_nbr") or a.get("batch_nbr") or "", "method": payment_method(a["payment_ref"]),
                  "inv_date": inv_date, "due": due, "terms": terms, "terms_d": terms_days(terms),
                  "inv_amt": amt, "inv_bal": inv.get("balance") if inv.get("balance") is not None else a.get("invoice_balance"),
                  "applied": a.get("applied") or D0, "discount": a.get("discount") or D0,
                  "days": (pay_date - inv_date).days if (pay_date and inv_date) else None,
                  "vs_due": (pay_date - due).days if (pay_date and due) else None,
                  "year": pay_date.year if pay_date else None})
        for k in ("order_nbr", "cust_po", "desc", "cnet", "cpn", "disp", "title", "div", "pm", "pm_key", "state", "cv", "billed", "pct", "close"):
            r.setdefault(k, inv.get(k) if k in inv else None)
        rows.append(r)
        by_inv[(a["invoice_ref"], a["invoice_type"])].append(r)
    for key, group in by_inv.items():
        group.sort(key=lambda r: (r["pay_date"] or date.min, r.get("date_appl") or date.min, r.get("id") or 0))
        cum = D0
        amt = group[0]["inv_amt"]
        for i, r in enumerate(group):
            cum += (r["applied"] or D0) + (r["discount"] or D0)
            r["seq"], r["n_apps"] = i + 1, len(group)
            r["open_after"] = (amt - cum).quantize(CENT) if amt is not None else None
            r["reversal"] = (r["applied"] or D0) < 0
            r["retention"] = retention_share(r["open_after"], amt) if not r["reversal"] else None
            r["release"] = bool(i > 0 and not r["reversal"] and amt and retention_share(r["applied"], amt) is not None
                                and r["open_after"] is not None and abs(r["open_after"]) <= Decimal("0.5"))
            r["timing"] = None if (r["reversal"] or r["release"]) else timing_bucket(r["vs_due"])
            r["counts"] = bool(not r["reversal"] and not r["release"] and r["days"] is not None and (r["applied"] or D0) > 0)
    rows.sort(key=lambda r: (-(r["pay_date"].toordinal() if r["pay_date"] else 0), r["payment_ref"], -(r["applied"] or D0)))
    return rows


def summarize(rows, invoices, today):
    """Customer-level picture from the enriched rows and the invoice master (amount ≠ 0 invoices only)."""
    real = [r for r in rows if r["counts"]]
    year_ago = today - timedelta(days=365)
    recent = [r for r in real if r["pay_date"] and r["pay_date"] >= year_ago]
    collected = sum((r["applied"] for r in rows if (r["applied"] or D0) > 0 and not r["reversal"]), D0)
    reversed_amt = sum((-r["applied"] for r in rows if r["reversal"]), D0)
    payments = {(r["payment_ref"], r["customer_id_raw"]) for r in rows}
    dates = [r["pay_date"] for r in rows if r["pay_date"]]
    inv_list = [i for i in invoices.values() if i.get("doc_type", "IN") in ("IN", "DM") and (i.get("amount") or D0) != 0]
    credits = [i for i in invoices.values() if i.get("doc_type") == "CM"]
    zero_n = sum(1 for i in invoices.values() if i.get("doc_type", "IN") in ("IN", "DM") and (i.get("amount") or D0) == 0)
    open_inv = [i for i in inv_list if (i.get("balance") or D0) > 0]
    retention_open = sum((i["balance"] for i in open_inv if retention_share(i["balance"], i["amount"]) is not None), D0)
    late = [r for r in real if r["vs_due"] is not None and r["vs_due"] > 0]
    buckets = {k: D0 for k in TIMING_ORDER}
    for r in real:
        if r["timing"]:
            buckets[r["timing"]] += r["applied"]
    weighed = sum((r["applied"] for r in real if r["timing"]), D0)
    # terms: the code carrying the most dollars on this customer's invoices
    terms_amt = defaultdict(lambda: D0)
    for i in inv_list:
        if (i.get("terms") or "").strip():
            terms_amt[i["terms"].strip()] += abs(i.get("amount") or D0)
    if not terms_amt:            # invoice master without terms (e.g. only the application rows carry them)
        for r in real:
            if (r.get("terms") or "").strip():
                terms_amt[r["terms"].strip()] += r["applied"]
    terms_code = max(terms_amt, key=terms_amt.get) if terms_amt else ""
    terms_d = terms_days(terms_code)
    days_all = weighted_days((r["days"], r["applied"]) for r in real)
    days_12 = weighted_days((r["days"], r["applied"]) for r in recent)
    retention_apps = sum(1 for r in rows if r["retention"] is not None)
    out = {
        "n_apps": len(rows), "n_payments": len(payments), "collected": collected, "reversed": reversed_amt,
        "first_pay": min(dates) if dates else None, "last_pay": max(dates) if dates else None,
        "days": days_all, "days_12": days_12, "n_12": len(recent), "collected_12": sum((r["applied"] for r in recent), D0),
        "median_days": median([r["days"] for r in real]),
        "on_time_share": (buckets["on_time"] / weighed) if weighed else None,
        "late90p_share": (buckets["late90p"] / weighed) if weighed else None,
        "buckets": [(k, TIMING_LABEL[k], buckets[k], (buckets[k] / weighed) if weighed else None) for k in TIMING_ORDER],
        "late_days": weighted_days((r["vs_due"], r["applied"]) for r in late), "late_n": len(late),
        "max_late": max((r["vs_due"] for r in late), default=None),
        "max_late_row": max(late, key=lambda r: r["vs_due"], default=None),
        "invoices_n": len(inv_list), "invoiced": sum((i["amount"] for i in inv_list), D0),
        "paid_n": sum(1 for i in inv_list if (i.get("balance") or D0) == 0),
        "open_n": len(open_inv), "open_amt": sum((i["balance"] for i in open_inv), D0), "retention_open": retention_open,
        "oldest_open": max(((today - i["due_date"]).days for i in open_inv if i.get("due_date")), default=None),
        "credits_n": len(credits), "credited": sum((abs(i.get("amount") or D0) for i in credits), D0), "zero_n": zero_n,
        "retention_apps": retention_apps, "holds_retention": retention_apps >= 3 or retention_open > 0,
        "terms": terms_code, "terms_d": terms_d,
        "grade": grade(days_all, terms_d), "grade_12": grade(days_12, terms_d),
    }
    out["by_year"] = by_year(rows, inv_list, terms_d)
    return out


def by_year(rows, inv_list, terms_d):
    """One line per calendar year of payment: collected, applications, pays-in, within-terms share, 90+ share;
    invoiced by invoice year alongside."""
    years = {}
    for r in rows:
        if not r["year"] or (r["applied"] or D0) <= 0 or r["reversal"]:
            continue
        y = years.setdefault(r["year"], {"year": r["year"], "collected": D0, "n": 0, "pairs": [], "on_time": D0, "late90p": D0, "weighed": D0, "invoiced": D0, "inv_n": 0})
        y["collected"] += r["applied"]
        y["n"] += 1
        if r["counts"]:
            y["pairs"].append((r["days"], r["applied"]))
            if r["timing"]:
                y["weighed"] += r["applied"]
                if r["timing"] == "on_time":
                    y["on_time"] += r["applied"]
                elif r["timing"] == "late90p":
                    y["late90p"] += r["applied"]
    for i in inv_list:
        d = i.get("doc_date")
        if not d:
            continue
        y = years.setdefault(d.year, {"year": d.year, "collected": D0, "n": 0, "pairs": [], "on_time": D0, "late90p": D0, "weighed": D0, "invoiced": D0, "inv_n": 0})
        y["invoiced"] += i["amount"]
        y["inv_n"] += 1
    out = []
    for y in sorted(years.values(), key=lambda y: -y["year"]):
        y["days"] = weighted_days(y["pairs"])
        y["on_time_share"] = (y["on_time"] / y["weighed"]) if y["weighed"] else None
        y["late90p_share"] = (y["late90p"] / y["weighed"]) if y["weighed"] else None
        y["grade"] = grade(y["days"], terms_d)
        del y["pairs"]
        out.append(y)
    return out


def facets(rows):
    """Chip facets for the table: years, timing buckets, methods, divisions, projects (by dollars applied)."""
    def bucket(key_fn, label_fn=None):
        acc = {}
        for r in rows:
            k = key_fn(r)
            if k in (None, ""):
                continue
            a = acc.setdefault(k, {"v": k, "n": 0, "amt": D0, "label": label_fn(r) if label_fn else str(k)})
            a["n"] += 1
            a["amt"] += r["applied"] or D0
        return acc
    years = sorted(bucket(lambda r: r["year"]).values(), key=lambda a: -a["v"])
    timing = bucket(lambda r: "reversal" if r["reversal"] else "release" if r["release"] else r["timing"])
    timing_out = [dict(timing[k], label=TIMING_LABEL[k]) for k in TIMING_ORDER if k in timing]
    if "release" in timing:
        timing_out.append(dict(timing["release"], label="Retention release"))
    if "reversal" in timing:
        timing_out.append(dict(timing["reversal"], label="Reversal"))
    methods = [dict(v, label=METHOD_LABEL[v["v"]]) for v in bucket(lambda r: r["method"]).values()]
    methods.sort(key=lambda a: -a["amt"])
    divs = sorted(bucket(lambda r: r.get("div")).values(), key=lambda a: a["v"])
    projects = sorted(bucket(lambda r: r.get("cpn"), lambda r: r.get("disp") or r.get("cpn")).values(), key=lambda a: -a["amt"])
    return {"year": years, "timing": timing_out, "method": methods, "div": divs, "proj": projects[:40]}


# ----------------------------------------------------------------------------------------------- database side
def load(cid, today):
    """Enriched rows + invoice master + summary for one SL customer id, from the local tables."""
    from apps.ingestion.bulk import fetch_dict
    inv_rows = fetch_dict("""
        SELECT i.ref_nbr, i.doc_type, i.doc_date, i.due_date, i.terms, i.cust_po, i.amount, i.balance, i.order_nbr, i.doc_desc "desc",
               i.project_id, p.canonical_project_number cpn, p.display_number disp, p.title, dv.code div, pm.canonical_name pm, pm.employee_key pm_key,
               p.lifecycle_state state, p.contract_value cv, p.billed_revenue billed, p.pm_percent_complete pct, p.close_date close, o.cnet_number cnet
        FROM finance_arinvoice i
        LEFT JOIN core_project p ON p.id = i.project_id
        LEFT JOIN core_division dv ON dv.id = p.division_id
        LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
        LEFT JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND i.order_nbr <> ''
        WHERE i.customer_id_raw = %s""", [cid])
    invoices = {(i["ref_nbr"], i["doc_type"]): i for i in inv_rows}
    apps = fetch_dict("""
        SELECT a.id, a.payment_ref, a.customer_id_raw, a.date_appl, a.applied, a.discount, a.batch_nbr, a.invoice_ref, a.invoice_type,
               a.invoice_date, a.due_date, a.terms, a.invoice_amt, a.invoice_balance, a.invoice_desc, a.project_id
        FROM finance_arpaymentapplication a WHERE a.customer_id_raw = %s""", [cid])
    payments = {(p["ref_nbr"], p["customer_id_raw"]): p for p in fetch_dict(
        "SELECT ref_nbr, customer_id_raw, doc_date, orig_amt, balance, batch_nbr FROM finance_arpayment WHERE customer_id_raw = %s", [cid])}
    # an application whose invoice predates the invoice history (or is a non-invoice document) still needs job context
    proj_ids = {a["project_id"] for a in apps if a["project_id"]} - {i["project_id"] for i in inv_rows if i["project_id"]}
    extra = {}
    if proj_ids:
        for p in fetch_dict("""SELECT p.id, p.canonical_project_number cpn, p.display_number disp, p.title, dv.code div, pm.canonical_name pm, pm.employee_key pm_key,
                                      p.lifecycle_state state, p.contract_value cv, p.billed_revenue billed, p.pm_percent_complete pct, p.close_date close
                               FROM core_project p LEFT JOIN core_division dv ON dv.id = p.division_id LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
                               WHERE p.id IN %s""", [tuple(proj_ids)]):
            extra[p["id"]] = p
    for a in apps:
        if (a["invoice_ref"], a["invoice_type"]) not in invoices and a["project_id"] in extra:
            a.update({k: v for k, v in extra[a["project_id"]].items() if k != "id"})
        a.setdefault("desc", a.get("invoice_desc") or "")
    rows = enrich(apps, invoices, payments, today)
    return rows, invoices, summarize(rows, invoices, today)


_CACHE = {}
_CACHE_MAX = 8


def load_cached(cid, today):
    """load() memoised per (customer, day, latest AR refresh) — the page and every scroll page of its JSON table share one
    enrichment; a new finance refresh (new ingestion_run on finance_arpayment) invalidates it."""
    from apps.ingestion.bulk import fetch_dict
    run_id = fetch_dict("SELECT MAX(ingestion_run_id) r FROM finance_arpayment")[0]["r"]
    key = (cid, today, run_id)
    hit = _CACHE.get(key)
    if hit is None:
        hit = load(cid, today)
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = hit
    return hit


def page_context(cid, today):
    """Everything the customer page's Payment history card needs (rows stay server-side; the table is paged JSON)."""
    rows, invoices, summary = load_cached(cid, today)
    summary = dict(summary)
    summary["facets"] = facets(rows)
    return summary
