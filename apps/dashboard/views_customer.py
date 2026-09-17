"""Customer page JSON: the payment-history table (apps.analytics.customer_payments), filtered, multi-key sorted and
paged server-side — the project ledger's pattern (views.project_transactions), so a hardware customer with 25,000
applications never ships them all to the browser."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.analytics import customer_payments as CP
from apps.core.models import Customer

PAGE = 200
SORTS = {"paid": lambda r: r["pay_date"] or date.min, "ref": lambda r: r["payment_ref"], "pamt": lambda r: r["pay_amt"] or Decimal(0),
         "inv": lambda r: r["invoice_ref"], "idate": lambda r: r["inv_date"] or date.min, "due": lambda r: r["due"] or date.min,
         "days": lambda r: r["days"] if r["days"] is not None else -10 ** 6, "vs_due": lambda r: r["vs_due"] if r["vs_due"] is not None else -10 ** 6,
         "iamt": lambda r: r["inv_amt"] or Decimal(0), "applied": lambda r: r["applied"] or Decimal(0), "open": lambda r: r["open_after"] if r["open_after"] is not None else Decimal(-1),
         "proj": lambda r: r.get("disp") or "", "cv": lambda r: r.get("cv") or Decimal(0)}
DEFAULT_SORT = ("-paid", "ref", "-applied")


def _f(v):
    return float(v) if v is not None else None


def _d(v):
    return v.isoformat() if v else ""


def _csv(g, key):
    return [v.strip() for v in (g.get(key) or "").split(",") if v.strip()]


def _filter(g):
    """Parse the querystring once into a predicate over enriched rows."""
    sets = {k: set(_csv(g, k)) for k in ("year", "timing", "proj", "div", "method")}
    dates = {}
    for key in ("date_from", "date_to"):
        v = (g.get(key) or "").strip()
        if v:
            try:
                dates[key] = date.fromisoformat(v)
            except ValueError:
                pass
    q = (g.get("q") or "").strip().lower()
    num = None
    if q:
        try:
            num = abs(Decimal(q.replace(",", "").replace("$", ""))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            num = None

    def keep(r):
        if sets["year"] and str(r["year"]) not in sets["year"]:
            return False
        if sets["timing"] and ("reversal" if r["reversal"] else "release" if r["release"] else r["timing"]) not in sets["timing"]:
            return False
        if sets["proj"] and (r.get("cpn") or "") not in sets["proj"]:
            return False
        if sets["div"] and (r.get("div") or "") not in sets["div"]:
            return False
        if sets["method"] and r["method"] not in sets["method"]:
            return False
        if dates and not r["pay_date"]:
            return False
        if "date_from" in dates and r["pay_date"] < dates["date_from"]:
            return False
        if "date_to" in dates and r["pay_date"] > dates["date_to"]:
            return False
        if q:
            hay = " ".join(str(r.get(k) or "") for k in ("payment_ref", "invoice_ref", "disp", "cpn", "title", "desc", "cust_po", "order_nbr", "pm", "batch")).lower()
            if q in hay:
                return True
            return num is not None and any(v is not None and abs(Decimal(v)).quantize(Decimal("0.01")) == num for v in (r["applied"], r["inv_amt"], r["pay_amt"]))
        return True
    return keep


def _row(r):
    return {"id": r["id"], "paid": _d(r["pay_date"]), "pref": r["payment_ref"], "method": r["method"], "pamt": _f(r["pay_amt"]), "pbal": _f(r["pay_bal"]),
            "batch": r["batch"], "appl": _d(r.get("date_appl")), "inv": r["invoice_ref"], "itype": r["invoice_type"], "idate": _d(r["inv_date"]), "due": _d(r["due"]),
            "terms": r["terms"], "terms_d": r["terms_d"], "iamt": _f(r["inv_amt"]), "applied": _f(r["applied"]), "disc": _f(r["discount"]),
            "open": _f(r["open_after"]), "ibal": _f(r["inv_bal"]), "seq": r["seq"], "n_apps": r["n_apps"],
            "retention": _f(r["retention"]), "release": r["release"], "reversal": r["reversal"], "days": r["days"], "vs_due": r["vs_due"], "timing": r["timing"],
            "cpn": r.get("cpn") or "", "disp": r.get("disp") or "", "title": r.get("title") or "", "div": r.get("div") or "", "pm": r.get("pm") or "", "pm_key": r.get("pm_key") or "",
            "state": r.get("state") or "", "cv": _f(r.get("cv")), "billed": _f(r.get("billed")), "pct": _f(r.get("pct")), "close": _d(r.get("close")),
            "order": r.get("order_nbr") or "", "cnet": r.get("cnet") or "", "desc": r.get("desc") or "", "po": r.get("cust_po") or ""}


def customer_payments(request, cust):
    """JSON: one customer's payment applications (every one since 2013), filtered / sorted / paged.
    Filters: q (payment or invoice ref, project, title, description, PO, order, PM; a number matches an exact amount),
    year=, timing= (on_time,late30,late60,late90,late90p,release,reversal), proj= (canonical project numbers), div=,
    method= (wire,check,card,other), date_from= / date_to= (paid-on date). sort= comma list of [-]paid|ref|pamt|inv|idate|
    due|days|vs_due|iamt|applied|open|proj|cv (default -paid,ref,-applied); offset= / limit= paging."""
    c = get_object_or_404(Customer, sl_customer_id=cust)
    # Match middleware and the customer-page card, including saved overrides.
    if not request.acc.can_view("customer_payments"):
        raise Http404
    today = timezone.localdate()
    rows, _inv, _summary = CP.load_cached(c.sl_customer_id, today)
    g = request.GET
    keep = _filter(g)
    kept = [r for r in rows if keep(r)]
    per_payment = {}
    for r in rows:
        per_payment[(r["payment_ref"], r["customer_id_raw"])] = per_payment.get((r["payment_ref"], r["customer_id_raw"]), 0) + 1
    keys = [s.strip() for s in (g.get("sort") or "").split(",") if s.strip() and s.strip().lstrip("-") in SORTS] or list(DEFAULT_SORT)
    for s in reversed(keys):
        kept.sort(key=SORTS[s.lstrip("-")], reverse=s.startswith("-"))
    try:
        offset = max(int(g.get("offset") or 0), 0)
        limit = min(max(int(g.get("limit") or PAGE), 1), 2000)
    except ValueError:
        offset, limit = 0, PAGE
    page = kept[offset:offset + limit]
    counting = [r for r in kept if r["counts"]]
    applied = sum((r["applied"] for r in kept if not r["reversal"]), Decimal(0))
    on_time = sum((r["applied"] for r in counting if r["timing"] == "on_time"), Decimal(0))
    weighed = sum((r["applied"] for r in counting if r["timing"]), Decimal(0))
    out = []
    for r in page:
        d = _row(r)
        d["pn"] = per_payment.get((r["payment_ref"], r["customer_id_raw"]), 1)
        out.append(d)
    return JsonResponse({"customer": c.sl_customer_id, "n": len(kept), "total": _f(applied), "payments": len({(r["payment_ref"], r["customer_id_raw"]) for r in kept}),
                         "days": _f(CP.weighted_days((r["days"], r["applied"]) for r in counting)),
                         "on_time": _f(on_time / weighed) if weighed else None,
                         "offset": offset, "limit": limit, "has_more": offset + len(out) < len(kept), "rows": out})
