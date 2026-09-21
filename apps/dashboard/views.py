import json
import os
import subprocess
import sys
import re
from django.urls import reverse
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractYear
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.analytics.models import EntityRating, ProjectPrediction, RatingRun
from apps.core import related_parties as rp
from apps.core.models import Customer, Division, Employee, Project, ProjectRoleAssignment
from apps.finance.models import ProjectCommercialChange, ProjectFinancialSnapshot, ProjectFinancialTransaction
from apps.ingestion.bulk import fetch_dict
from apps.access.redaction import rate_visible
from apps.ingestion.models import DataQualityIssue, IngestionRun, RefreshRequest, SourceWatermark
from apps.operations.models import RemainingHoursRevision, TimeEntry
from . import queries as Q_


def _f(v):
    return float(v) if v is not None else None


def _ctx(request, nav, **extra):
    d, div_label, div_code = Q_.division_scope(request)
    ctx = {"nav": nav, "freshness": Q_.freshness(), "division": d, "division_label": div_label, "div_code": div_code,
           "divisions": Q_.Division.objects.filter(active=True).order_by("code")}
    ctx.update(extra)
    return ctx


def _division_nav(request, current_code):
    """Prev/next steps through the division carousel used by the Snapshot header: All divisions,
    then every active division by code, wrapping at both ends. Owner walks one day across every
    division, so the arrows keep the current window (and the PM / rep filter where it still
    applies) and only swap `div`. All links point at the project snapshot -- that view redirects
    010 to its hardware-sales body -- so the two pages share one carousel."""
    seq = [{"code": "", "label": "All divisions"}]
    seq += [{"code": d.code, "label": "%s %s" % (d.code, d.name)}
            for d in Q_.Division.objects.filter(active=True).order_by("code")]
    base = reverse("dashboard:project_snapshot")
    for s in seq:
        q = request.GET.copy()
        if s["code"] == "010":
            q.pop("pm", None)        # 010 has no PMs; the 010 body filters by sales rep
        else:
            q.pop("rep", None)       # and no other division has reps
        if s["code"]:
            q["div"] = s["code"]
        else:
            q.pop("div", None)
        s["url"] = "%s?%s" % (base, q.urlencode()) if q else base
    i = next((n for n, s in enumerate(seq) if s["code"] == (current_code or "")), 0)
    return {"cur": seq[i], "at": i + 1, "of": len(seq),
            "prev": seq[(i - 1) % len(seq)], "next": seq[(i + 1) % len(seq)]}


def _window(request):
    years = int(request.GET.get("years", 5) or 5)
    years = max(1, min(years, 12))
    start, end = Q_.default_window(years)
    return years, start, end


# ============================================================================ command center
def command_center(request):
    # Access Spec v1 §7.1: Division Managers see only their division(s); ?div outside scope redirects.
    acc = request.acc
    scoped = acc.allowed_divisions if not acc.cc_all and not acc.is_superadmin else None
    if scoped is not None:
        if not scoped:
            return render(request, "access/denied.html", {"reason": "forbidden"}, status=403)
        code = request.GET.get("div")
        if code not in scoped:
            return redirect("%s?div=%s" % (request.path, scoped[0]))
    d, _, _ = Q_.division_scope(request)
    years, start, end = _window(request)
    hist = Q_.historical_summary(d, start, end)
    active = Q_.active_book(d)
    periods = Q_.by_period(d, start, end, "quarter")
    yearly = Q_.by_period(d, start, end, "year")
    pnl_years = Q_.division_pnl_by_year(d)
    modes = Q_.breakdown(d, start, end, "p.project_mode_rule", limit=12, order="revenue DESC")
    solutions = Q_.breakdown(d, start, end, "p.solution_class", limit=12, order="revenue DESC")
    customers = Q_.breakdown(d, start, end, "c.sl_customer_id", "c.canonical_name", "LEFT JOIN core_customer c ON c.id=p.customer_id", limit=10, order="revenue DESC")
    sectors = Q_.breakdown(d, start, end, "COALESCE(NULLIF(c.market_sector,''),'(unassigned)')", None, "LEFT JOIN core_customer c ON c.id=p.customer_id", limit=12, order="revenue DESC")
    pms = Q_.breakdown(d, start, end, "e.employee_key", "e.canonical_name", "LEFT JOIN core_employee e ON e.id=p.project_manager_id", limit=10, order="revenue DESC")
    at_risk = Q_.project_rows(d, "p.lifecycle_state IN %s AND pr.risk_level IN ('high','critical')", [Q_.OPEN], order="pr.risk_score DESC, pr.eac_gp_dollars ASC", limit=8)
    biggest_open = Q_.project_rows(d, "p.lifecycle_state IN %s", [Q_.OPEN], order="p.contract_value DESC NULLS LAST", limit=8)
    recent_closed = Q_.project_rows(d, "p.lifecycle_state IN %s AND p.project_mode_rule NOT IN %s AND p.billed_revenue > 0", [Q_.CLOSED, Q_.REAL_MODES_EXCLUDED], order="p.close_date DESC NULLS LAST", limit=8)
    # margin preservation histogram (closed, cv>0) — the bar click (command_center_jobs) reuses the same filter
    hw, hparams = _cc_preservation_where(d, start, end)
    hist_rows = fetch_dict("SELECT %s b, COUNT(*) n FROM core_project p WHERE %s GROUP BY 1 ORDER BY 1" % (HIST_BUCKET_SQL, hw), hparams)
    buckets = {r["b"]: r["n"] for r in hist_rows}
    hist_values = [buckets.get(i, 0) for i in range(18)]
    w, params = Q_.div_where(d)
    scatter = fetch_dict("""SELECT p.canonical_project_number k, p.display_number, p.title, p.contract_value cv, p.sold_gp_percent s, p.actual_gp_percent a, p.project_mode_rule m,
                                   p.close_date cd, p.billed_revenue rev, p.actual_direct_cost cost, p.actual_gp_dollars gp, p.ptt_hours_total h,
                                   c.canonical_name cust, pm.canonical_name pm
                            FROM core_project p LEFT JOIN core_customer c ON c.id = p.customer_id LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
                            WHERE %s AND p.lifecycle_state IN %%s AND p.close_date BETWEEN %%s AND %%s AND p.contract_value > 1000 AND p.billed_revenue > 0
                              AND p.project_mode_rule NOT IN %%s AND p.descriptive_eligible AND p.sold_gp_percent BETWEEN -0.5 AND 0.95 AND p.actual_gp_percent BETWEEN -1 AND 0.95
                            ORDER BY p.contract_value DESC LIMIT 700""" % w, params + [Q_.CLOSED, start, end, Q_.REAL_MODES_EXCLUDED])
    changes = ProjectCommercialChange.objects.select_related("project").order_by("-detected_at", "-id")[:8]
    charts = {
        "periods": [{"p": r["period"].isoformat(), "rev": _f(r["revenue"]), "gp": _f(r["gp"]), "n": r["n"],
                     "pct": _f(r["gp"] / r["revenue"]) if r["revenue"] else None,
                     "sold": _f(r["sold_gp_with"] / r["cv_with"]) if r["cv_with"] else None,
                     "final": _f(r["gp_with"] / r["rev_with"]) if r["rev_with"] else None} for r in periods],
        "cost_mix": {"Labor wages": _f(hist["wage"]), "Labor burden": _f(hist["burden"]), "Material (net of purchase variance)": _f((hist["material"] or 0) + (hist["purchase_variance"] or 0)),
                     "Subcontract": _f(hist["subcontract"]), "Other direct": _f(hist["other_direct"])},
        "hist": {"labels": HIST_LABELS, "values": hist_values},
        "scatter": [{"x": _f(r["s"]), "y": _f(r["a"]), "r": max(3, min(14, (float(r["cv"]) ** 0.5) / 60)), "n": r["display_number"], "t": r["title"], "m": r["m"],
                     "k": r["k"], "cust": r["cust"], "pm": r["pm"], "cd": r["cd"].isoformat() if r["cd"] else None,
                     "cv": _f(r["cv"]), "rev": _f(r["rev"]), "cost": _f(r["cost"]), "gp": _f(r["gp"]), "h": _f(r["h"])} for r in scatter],
        "modes": [{"k": r["k"], "rev": _f(r["revenue"]), "gp": _f(r["gp"]), "pct": _f(r["gp_pct"]), "n": r["n"]} for r in modes],
        "solutions": [{"k": r["k"], "rev": _f(r["revenue"]), "gp": _f(r["gp"]), "pct": _f(r["gp_pct"]), "n": r["n"]} for r in solutions],
    }
    scoped_divs = Q_.Division.objects.filter(active=True, code__in=scoped).order_by("code") if scoped is not None else None
    return render(request, "dashboard/command_center.html", _ctx(request, "home", **({"divisions": scoped_divs, "division_locked": True} if scoped_divs is not None else {}), years=years, start=start, end=end, hist=hist, active=active, yearly=yearly, pnl_years=pnl_years,
                                                                 customers=customers, sectors=sectors, pms=pms, modes=modes, solutions=solutions,
                                                                 at_risk=at_risk, biggest_open=biggest_open, recent_closed=recent_closed, changes=changes,
                                                                 charts=charts))


# Margin-preservation histogram: 18 buckets — 0 = below −40 pts, 1..16 = 5-point bins from −40 to +40, 17 = above +40.
HIST_BUCKET_SQL = "width_bucket((p.actual_gp_percent - p.sold_gp_percent)*100, -40, 40, 16)"
HIST_LABELS = ["<-40"] + ["%d..%d" % (-40 + 5 * i, -35 + 5 * i) for i in range(16)] + [">40"]


def _cc_preservation_where(d, start, end):
    """The closed-job population behind the Command Center's margin-preservation histogram (and its bar drill-down):
    closed in the window, real modes only, has a contract value and billings, both margins known."""
    w, params = Q_.div_where(d)
    where = ("%s AND p.lifecycle_state IN %%s AND p.close_date BETWEEN %%s AND %%s AND p.contract_value > 0 AND p.billed_revenue > 0 "
             "AND p.project_mode_rule NOT IN %%s AND p.descriptive_eligible AND p.sold_gp_percent IS NOT NULL AND p.actual_gp_percent IS NOT NULL") % w
    return where, params + [Q_.CLOSED, start, end, Q_.REAL_MODES_EXCLUDED]


def command_center_jobs(request):
    """JSON (PCADrill shape): the closed jobs behind one bar of the margin-preservation histogram (?bucket=0..17) or a set of
    overlapping scatter dots (?keys=a,b,c). Same division scoping as the Command Center itself — a Division Manager cannot
    ask for another division's jobs; keys outside the scoped division are dropped."""
    acc = request.acc
    scoped = acc.allowed_divisions if not acc.cc_all and not acc.is_superadmin else None
    if scoped is not None and request.GET.get("div") not in scoped:
        return JsonResponse({"error": "forbidden"}, status=403)
    d, _, _ = Q_.division_scope(request)
    years, start, end = _window(request)
    where, params = _cc_preservation_where(d, start, end)
    if request.GET.get("keys"):
        keys = tuple(k.strip().upper() for k in request.GET["keys"].split(",") if k.strip())[:200]
        if not keys:
            return JsonResponse({"error": "no keys"}, status=400)
        w, params = Q_.div_where(d)
        where = "%s AND p.canonical_project_number IN %%s" % w
        params = params + [keys]
        title = "%d overlapping jobs at this point" % len(keys)
    else:
        try:
            bucket = int(request.GET.get("bucket", ""))
        except ValueError:
            return JsonResponse({"error": "bucket or keys required"}, status=400)
        if not 0 <= bucket <= 17:
            return JsonResponse({"error": "bucket out of range"}, status=400)
        where = "%s AND %s = %%s" % (where, HIST_BUCKET_SQL)
        params = params + [bucket]
        label = HIST_LABELS[bucket]
        title = "Margin preservation %s points · closed %s → %s" % (label.replace("..", " to ") if bucket not in (0, 17) else ("below −40" if bucket == 0 else "above +40"),
                                                                  start.strftime("%b %Y"), end.strftime("%b %Y"))
    rows = fetch_dict("""SELECT p.canonical_project_number key, p.display_number project, p.title, p.project_mode_rule mode, p.close_date closed,
                                p.contract_value cv, p.billed_revenue billed, p.actual_gp_dollars gp, p.sold_gp_percent sold_pct, p.actual_gp_percent final_pct,
                                c.canonical_name customer, pm.canonical_name pm
                         FROM core_project p LEFT JOIN core_customer c ON c.id = p.customer_id LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
                         WHERE %s ORDER BY p.contract_value DESC NULLS LAST LIMIT 400""" % where, params)
    n_all = fetch_dict("SELECT COUNT(*) n, COALESCE(SUM(p.billed_revenue),0) rev, COALESCE(SUM(p.actual_gp_dollars),0) gp, COALESCE(SUM(p.contract_value),0) cv, COALESCE(SUM(p.sold_gp_dollars),0) sold_gp FROM core_project p WHERE %s" % where, params)[0]
    out = []
    for r in rows:
        s_, a_ = _f(r["sold_pct"]), _f(r["final_pct"])
        out.append({"project": r["project"], "link": reverse("dashboard:project_detail", args=[r["key"]]), "title": (r["title"] or "")[:44], "customer": (r["customer"] or "")[:30],
                    "pm": r["pm"] or "", "closed": r["closed"].isoformat() if r["closed"] else "", "mode": (r["mode"] or "").replace("_", " "),
                    "cv": _round0(r["cv"]), "billed": _round0(r["billed"]), "gp": _round0(r["gp"]),
                    "sold_pct": s_, "final_pct": a_, "delta": (a_ - s_) * 100 if s_ is not None and a_ is not None else None})
    final_w = float(n_all["gp"]) / float(n_all["rev"]) if n_all["rev"] else None
    sold_w = float(n_all["sold_gp"]) / float(n_all["cv"]) if n_all["cv"] else None
    pres = (final_w - sold_w) * 100 if final_w is not None and sold_w is not None else None
    summary = "%d job%s · %s billed · sold %s → final %s%s" % (
        n_all["n"], "" if n_all["n"] == 1 else "s", "${:,.0f}".format(n_all["rev"]),
        "—" if sold_w is None else "%.1f%%" % (sold_w * 100), "—" if final_w is None else "%.1f%%" % (final_w * 100),
        "" if pres is None else " (%+.1f pts, revenue-weighted)" % pres)
    if n_all["n"] > len(out):
        summary += " · showing the %d largest by contract value" % len(out)
    # 12 columns on purpose (Owner: wide tables need fewer columns) — direct cost = billed − GP, hours live on the project page
    cols = [("project", "Project"), ("title", "Title"), ("customer", "Customer"), ("pm", "PM"), ("closed", "Closed"), ("mode", "Mode"),
            ("cv", "Contract"), ("billed", "Billed"), ("gp", "GP $"), ("sold_pct", "Sold %"), ("final_pct", "Final %"), ("delta", "Δ pts")]
    return JsonResponse({"title": title, "cols": cols, "rows": out, "n": n_all["n"], "total": float(n_all["rev"]), "summary": summary,
                         "truncated": n_all["n"] > len(out), "money_cols": ["cv", "billed", "gp"], "money_digits": 0, "pct_cols": ["sold_pct", "final_pct"],
                         "pts_cols": ["delta"], "text_cols": ["title"], "link_map": {"project": "link"}})


def _round0(v):
    return int(round(float(v))) if v is not None else None


# ============================================================================ project list
SORTS = {
    "created": "p.sl_created_at", "closed": "p.close_date", "number": "p.canonical_project_number", "title": "p.title", "customer": "c.canonical_name",
    "pm": "pm.canonical_name", "cv": "p.contract_value", "billed": "p.billed_revenue", "cost": "p.actual_direct_cost", "gp": "p.actual_gp_dollars",
    "gp_pct": "p.actual_gp_percent", "sold_pct": "p.sold_gp_percent", "hours": "p.ptt_hours_total", "budget_hours": "p.budget_labor_hours",
    "remaining": "p.pm_remaining_hours", "pc": "p.pm_percent_complete", "risk": "pr.risk_score", "eac_gp": "pr.eac_gp_dollars", "eac_pct": "pr.eac_gp_percent",
    "rate": "p.effective_loaded_labor_rate", "state": "p.lifecycle_state", "mode": "p.project_mode_rule", "last_work": "p.last_work_date",
}


def _list_filters(request):
    where, params = ["TRUE"], []
    g = request.GET
    if g.get("q"):
        where.append("(p.canonical_project_number ILIKE %s OR p.title ILIKE %s OR c.canonical_name ILIKE %s)")
        like = "%" + g["q"].strip() + "%"
        params += [like, like, like]
    if g.get("state"):
        if g["state"] == "open":
            where.append("p.lifecycle_state IN %s"); params.append(Q_.OPEN)
        elif g["state"] == "closed":
            where.append("p.lifecycle_state IN %s"); params.append(Q_.CLOSED)
        else:
            where.append("p.lifecycle_state = %s"); params.append(g["state"])
    if g.get("mode"):
        where.append("p.project_mode_rule = %s"); params.append(g["mode"])
    if g.get("solution"):
        where.append("p.solution_class = %s"); params.append(g["solution"])
    if g.get("pm"):
        where.append("pm.employee_key = %s"); params.append(g["pm"])
    if g.get("customer"):
        where.append("c.sl_customer_id = %s"); params.append(g["customer"])
    if g.get("sector"):
        where.append("c.market_sector = %s"); params.append(g["sector"])
    if g.get("year"):
        where.append("EXTRACT(year FROM p.sl_created_at) = %s"); params.append(int(g["year"]))
    if g.get("closed_year"):
        where.append("EXTRACT(year FROM p.close_date) = %s"); params.append(int(g["closed_year"]))
    if g.get("risk"):
        where.append("pr.risk_level = %s"); params.append(g["risk"])
    if g.get("margin") == "loss":
        where.append("p.actual_gp_dollars < 0")
    elif g.get("margin") == "under10":
        where.append("p.actual_gp_percent < 0.10 AND p.billed_revenue > 0")
    elif g.get("margin") == "over40":
        where.append("p.actual_gp_percent >= 0.40")
    if g.get("flag"):
        where.append("p.data_quality_flags::jsonb ? %s"); params.append(g["flag"])
    if g.get("min_cv"):
        where.append("p.contract_value >= %s"); params.append(Decimal(g["min_cv"]))
    if not g.get("include_internal"):
        where.append("NOT p.is_internal_bucket AND NOT p.is_template_or_void")
    return " AND ".join(where), params


MARGIN_SORTS = {"cv", "billed", "cost", "gp", "gp_pct", "sold_pct", "eac_gp", "eac_pct", "risk", "rate"}


def project_list(request):
    d, _, _ = Q_.division_scope(request)
    where, params = _list_filters(request)
    sort = request.GET.get("sort", "-created")
    if not request.acc.margins and sort.lstrip("-") in MARGIN_SORTS:
        sort = "created"
    desc = sort.startswith("-")
    col = SORTS.get(sort.lstrip("-"), "p.sl_created_at")
    order = "%s %s NULLS LAST, p.id" % (col, "DESC" if desc else "ASC")
    page = max(1, int(request.GET.get("page", 1) or 1))
    per = 100
    total = Q_.count_projects(d, where, params)
    rows = Q_.project_rows(d, where, params, order=order, limit=per, offset=(page - 1) * per)
    # totals for the filtered set
    w, dparams = Q_.div_where(d)
    tot = fetch_dict("""SELECT SUM(p.contract_value) cv, SUM(p.billed_revenue) billed, SUM(p.actual_direct_cost) cost, SUM(p.actual_gp_dollars) gp, SUM(p.ptt_hours_total) hours
                        FROM core_project p LEFT JOIN core_customer c ON c.id=p.customer_id LEFT JOIN core_employee pm ON pm.id=p.project_manager_id
                        LEFT JOIN analytics_projectprediction pr ON pr.project_id=p.id AND pr.as_of_date=(SELECT MAX(as_of_date) FROM analytics_projectprediction)
                        WHERE %s AND (%s)""" % (w, where), dparams + params)[0]
    tot["gp_pct"] = (tot["gp"] / tot["billed"]) if tot["billed"] else None
    options = {
        "pms": fetch_dict("SELECT e.employee_key k, e.canonical_name n, COUNT(*) c FROM core_project p JOIN core_employee e ON e.id=p.project_manager_id GROUP BY 1,2 ORDER BY c DESC LIMIT 60"),
        "modes": fetch_dict("SELECT project_mode_rule k, COUNT(*) c FROM core_project GROUP BY 1 ORDER BY 1"),
        "solutions": fetch_dict("SELECT solution_class k, COUNT(*) c FROM core_project WHERE solution_class <> '' GROUP BY 1 ORDER BY 1"),
        "sectors": fetch_dict("SELECT market_sector k, COUNT(*) c FROM core_customer WHERE market_sector <> '' GROUP BY 1 ORDER BY 1"),
        "years": fetch_dict("SELECT EXTRACT(year FROM sl_created_at)::int k, COUNT(*) c FROM core_project WHERE sl_created_at IS NOT NULL GROUP BY 1 ORDER BY 1 DESC"),
        "states": [s for s in Q_.OPEN + Q_.CLOSED + ("canceled", "unknown", "template")],
    }
    return render(request, "dashboard/project_list.html", _ctx(request, "projects", rows=rows, total=total, page=page, per=per, pages=(total + per - 1) // per,
                                                               tot=tot, options=options, sort=sort, g=request.GET))


# ============================================================================ project detail
def project_detail(request, key):
    p = get_object_or_404(Project.objects.select_related("customer", "project_manager", "division_head", "salesperson", "estimator", "division"), canonical_project_number=key.upper())
    pred = ProjectPrediction.objects.filter(project=p).order_by("-as_of_date").first()
    summary = fetch_dict("""SELECT sl_acct, category, SUM(actual_amount) actual, SUM(actual_units) units, SUM(budget_amount) budget, SUM(budget_units) budget_units,
                                   SUM(committed_amount) committed, MAX(source_updated_at) updated, COUNT(DISTINCT task_id) tasks
                            FROM finance_projectaccountsummary WHERE project_id=%s AND is_current GROUP BY sl_acct, category ORDER BY MIN(CASE sl_acct
                              WHEN 'CONTRACT VALUE' THEN 0 WHEN 'REVENUE' THEN 1 WHEN 'LABOR' THEN 2 WHEN 'LABORUNION' THEN 3 WHEN 'BURDEN' THEN 4 WHEN 'MATERIALS' THEN 5
                              WHEN 'SUBCONTRACT' THEN 6 WHEN 'ODC' THEN 7 ELSE 9 END)""", [p.id])
    by_task = fetch_dict("""SELECT task_id, SUM(CASE WHEN sl_acct='CONTRACT VALUE' THEN budget_amount END) cv, SUM(CASE WHEN category='revenue' THEN actual_amount END) billed,
                                   SUM(CASE WHEN category IN ('labor_wage','labor_burden','material','subcontract','other_direct') THEN actual_amount END) cost,
                                   SUM(CASE WHEN category IN ('labor_wage','labor_burden','material','subcontract','other_direct') THEN budget_amount END) budget_cost,
                                   SUM(CASE WHEN category='labor_wage' THEN actual_units END) hours, SUM(CASE WHEN category='labor_wage' THEN budget_units END) budget_hours
                            FROM finance_projectaccountsummary WHERE project_id=%s AND is_current GROUP BY task_id ORDER BY task_id""", [p.id])
    tasks = {t.task_id: (t.description or "") for t in p.tasks.all()}
    # SL commitment detail (PJCOMDET copy, netted): open PO lines, unshipped warehouse allocations, and phantom rows (already shipped)
    commit_lines = fetch_dict("""SELECT source_type, is_phantom, voucher_matched, task_id, sl_acct, item_id, po_number, vendor_id, po_date, promise_date, receipt_number, receipt_date,
                                        units, qty_shipped_to_project, open_units, amount, open_amount, comment
                                 FROM finance_projectcommitmentline WHERE project_id=%s
                                 ORDER BY is_phantom, source_type, COALESCE(po_date, receipt_date), po_number, receipt_number, receipt_line""", [p.id])
    commit_by_acct = defaultdict(lambda: [Decimal(0), Decimal(0)])
    commit_summary = {"sl": Decimal(0), "open": Decimal(0), "phantom": Decimal(0), "open_po": Decimal(0), "warehouse": Decimal(0), "n_open": 0, "n_phantom": 0}
    for c in commit_lines:
        commit_by_acct[c["sl_acct"]][0] += c["open_amount"]
        commit_by_acct[c["sl_acct"]][1] += c["amount"] - c["open_amount"]
        commit_summary["sl"] += c["amount"]; commit_summary["open"] += c["open_amount"]; commit_summary["phantom"] += c["amount"] - c["open_amount"]
        if c["is_phantom"]:
            commit_summary["n_phantom"] += 1
        else:
            commit_summary["n_open"] += 1
            commit_summary["open_po" if c["source_type"] == "open_po" else "warehouse"] += c["open_amount"]
    for r in summary:
        r["delta"] = (r["actual"] or 0) - (r["budget"] or 0)
        r["open_commit"], r["phantom_commit"] = commit_by_acct.get(r["sl_acct"], (None, None))
    vs_sold_pts = (p.actual_gp_percent - p.sold_gp_percent) if (p.actual_gp_percent is not None and p.sold_gp_percent is not None) else None
    monthly = fetch_dict("""SELECT date_trunc('month', transaction_date)::date m, category, SUM(amount) amt, SUM(units) units
                            FROM finance_projectfinancialtransaction WHERE project_id=%s AND transaction_date IS NOT NULL GROUP BY 1,2 ORDER BY 1""", [p.id])
    tran_groups = fetch_dict("""SELECT category, sub_tag, system_cd, batch_type, COUNT(*) n, SUM(amount) amt, SUM(units) units, MIN(transaction_date) first_d, MAX(transaction_date) last_d
                                FROM finance_projectfinancialtransaction WHERE project_id=%s GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""", [p.id])
    acc = request.acc
    # Transaction ledger rows come from project_transactions (JSON: every posting, server-filtered / multi-key sorted / paged);
    # the page itself only carries the facet counts behind the ledger's filter chips
    ledger = _ledger_facets(p, tran_groups)
    entries_qs = _project_entries_qs(p)
    entries_total = entries_qs.count()
    entries = list(entries_qs[:WORKLOG_FIRST])      # the rest arrives through project_entries(?offset=) when Owner clicks "Show all"
    crew = fetch_dict("""SELECT e.employee_key, e.canonical_name, e.union_code, e.labor_class, e.ptt_employee_type, e.is_field_hourly, a.actual_hours, a.actual_labor_cost, a.share_of_project_hours, a.first_date, a.last_date,
                                (SELECT COUNT(*) FROM core_projectroleassignment x WHERE x.project_id=a.project_id AND x.employee_id=a.employee_id AND x.role='crew_lead') lead
                         FROM core_projectroleassignment a JOIN core_employee e ON e.id=a.employee_id WHERE a.project_id=%s AND a.role='field' ORDER BY a.actual_hours DESC""", [p.id])
    for c in crew:
        c["rate_ok"] = rate_visible(acc, c["is_field_hourly"])
    weekly_hours = fetch_dict("""SELECT date_trunc('week', work_date)::date w, SUM(hours_total) h, COUNT(DISTINCT employee_id) n FROM operations_timeentry
                                 WHERE project_id=%s AND source_status=1 AND form_type=1 GROUP BY 1 ORDER BY 1""", [p.id])
    cost_revisions = []
    if acc.margins:
        # Cost observations have their own timeline: PTT hours history does not
        # contain historic expense estimates. Never backfill it with today's costs.
        cost_revisions = fetch_dict("""
            SELECT o.*, e.canonical_name AS revised_by_name,
                   s.actual_material + COALESCE(s.actual_purchase_variance,0) AS material_spent,
                   s.actual_direct_cost - s.actual_labor AS expense_spent,
                   s.as_of_date AS cost_date
            FROM operations_percentcompleteobservation o
            LEFT JOIN core_employee e ON e.id=o.ptt_last_updated_by_id
            LEFT JOIN finance_projectfinancialsnapshot s ON s.project_id=o.project_id
                AND s.as_of_date=(o.observed_at AT TIME ZONE 'America/Chicago')::date
            WHERE o.project_id=%s ORDER BY o.observed_at, o.id
        """, [p.id])
        previous = None
        for r in cost_revisions:
            r["expense_projected"] = (r["expense_spent"] + r["ptt_remaining_expense_costs"]) if r["expense_spent"] is not None and r["ptt_remaining_expense_costs"] is not None else None
            r["expense_change"] = (r["ptt_remaining_expense_costs"] - previous["ptt_remaining_expense_costs"]) if previous and r["ptt_remaining_expense_costs"] is not None and previous["ptt_remaining_expense_costs"] is not None else None
            previous = r
        cost_revisions.reverse()
    revisions = list(RemainingHoursRevision.objects.filter(project=p).select_related("revised_by").order_by("revised_at", "sequence"))
    # cumulative hours at each revision for the forecast chart
    cum = fetch_dict("SELECT work_date d, SUM(hours_total) h FROM operations_timeentry WHERE project_id=%s AND source_status=1 AND form_type=1 GROUP BY 1 ORDER BY 1", [p.id])
    running, cum_series = Decimal(0), []
    for r in cum:
        running += r["h"]
        cum_series.append({"d": r["d"].isoformat(), "h": _f(running)})
    rev_series = []
    for rv in revisions:
        # hours worked up to revision date
        worked = sum((r["h"] for r in cum if r["d"] <= rv.revised_at.date()), Decimal(0))
        rev_series.append({"d": rv.revised_at.date().isoformat(), "rem": _f(rv.remaining_hours_total), "eac": _f(worked + (rv.remaining_hours_total or 0)), "by": rv.revised_by.canonical_name if rv.revised_by else "?"})
    # ---- top-of-page numbers: PTT-style progress & remaining rows + scoreboard bars ----
    D = Decimal
    hours_by_type = {r["t"]: r["h"] for r in fetch_dict("""SELECT COALESCE(e.ptt_employee_type,'') t, SUM(te.hours_total) h
                        FROM operations_timeentry te LEFT JOIN core_employee e ON e.id=te.employee_id
                        WHERE te.project_id=%s AND te.source_status=1 AND te.form_type=1 GROUP BY 1""", [p.id])}
    bud_hours = {r["sl_acct"]: r["budget_units"] for r in summary if r["sl_acct"] in ("LABOR", "LABORUNION")}
    latest_rev = revisions[-1] if revisions else None
    closed = p.is_closed

    def _prow(label, unit, used, rem, budget, proj=None, help_text="", strong=False, drill=None):
        if closed and rem is None:
            rem = D(0)
        if proj is None and (used is not None or rem is not None):
            proj = (used or D(0)) + (rem or D(0))
        over = (proj - budget) if (proj is not None and budget) else None
        return {"label": label, "unit": unit, "used": used, "rem": rem, "proj": proj, "budget": budget, "over": over,
                "help": help_text, "strong": strong, "drill": drill}   # drill = PTT employee type whose entries make up `used`

    rem_u = latest_rev.remaining_hours_union if latest_rev else None
    rem_n = latest_rev.remaining_hours_non_union if latest_rev else None
    if latest_rev is None and p.pm_remaining_hours is not None:
        rem_u = p.pm_remaining_hours
    ppv = p.actual_purchase_variance or D(0)
    mat_used = (p.actual_material or D(0)) + ppv
    # ---- PM remaining-hours revisions: spent by type as of each revision, projected vs the SL budget (Owner, 2026-09-03)
    cum_t = fetch_dict("""SELECT te.work_date d, COALESCE(e.ptt_employee_type,'') t, SUM(te.hours_total) h
                          FROM operations_timeentry te LEFT JOIN core_employee e ON e.id=te.employee_id
                          WHERE te.project_id=%s AND te.source_status=1 AND te.form_type=1 GROUP BY 1,2 ORDER BY 1""", [p.id])
    rev_bud = {"nu": bud_hours.get("LABOR"), "u": bud_hours.get("LABORUNION")}
    rev_bud["tot"] = ((rev_bud["nu"] or D(0)) + (rev_bud["u"] or D(0))) if (rev_bud["nu"] is not None or rev_bud["u"] is not None) else None
    rev_spent = {"nu": hours_by_type.get("non_union") or D(0), "u": hours_by_type.get("union") or D(0), "tot": sum(hours_by_type.values(), D(0))}
    rev_rem = {"nu": rem_n if rem_n is not None else (D(0) if closed else None), "u": rem_u if rem_u is not None else (D(0) if closed else None)}
    rev_rem["tot"] = ((rev_rem["nu"] or D(0)) + (rev_rem["u"] or D(0))) if (rev_rem["nu"] is not None or rev_rem["u"] is not None) else None
    today = timezone.localdate()
    rev_rows, rev_now = _revision_history(revisions, cum_t, rev_bud, rev_spent, rev_rem, today)
    rev_proj = {k: (rev_spent[k] or D(0)) + (rev_rem[k] or D(0)) for k in ("nu", "u", "tot")}
    rev_over = {k: (rev_proj[k] - rev_bud[k]) if rev_bud.get(k) is not None else None for k in ("nu", "u", "tot")}
    sl_hours = {r["sl_acct"]: (r["units"] or D(0)) for r in summary if r["sl_acct"] in ("LABOR", "LABORUNION")}
    rev_summary = {"bud": rev_bud, "spent": rev_spent, "rem": rev_rem, "proj": rev_proj, "over": rev_over,
                   "over_pct": (rev_over["tot"] / rev_bud["tot"]) if (rev_over["tot"] is not None and rev_bud["tot"]) else None,
                   "pct": (rev_spent["tot"] / rev_proj["tot"]) if rev_proj["tot"] else None,
                   "untyped": hours_by_type.get("", D(0)),
                   "last_at": latest_rev.revised_at if latest_rev else None, "last_by": (latest_rev.revised_by.canonical_name if latest_rev and latest_rev.revised_by else ""),
                   "last_age": (today - timezone.localtime(latest_rev.revised_at).date()).days if latest_rev else None,
                   "n": len(revisions), "first_at": revisions[0].revised_at if revisions else None,
                   "span_days": (timezone.localtime(revisions[-1].revised_at).date() - timezone.localtime(revisions[0].revised_at).date()).days if len(revisions) > 1 else 0,
                   "since_last": (rev_spent["tot"] - rev_rows[0]["spent"]["tot"]) if rev_rows else None,
                   "sl_nu": sl_hours.get("LABOR"), "sl_u": sl_hours.get("LABORUNION"), "closed": closed}
    hour_rows = [
        _prow("Union hours (PTT)", "h", hours_by_type.get("union"), rem_u, bud_hours.get("LABORUNION"), drill="union",
              help_text="PTT field hours by union employees · remaining = PM estimate (union) · budget = SL LABORUNION budget hours · click the used hours for the entries"),
        _prow("Non-union hours (PTT)", "h", hours_by_type.get("non_union"), rem_n, bud_hours.get("LABOR"), drill="non_union",
              help_text="PTT field hours by non-union employees (techs) · remaining = PM estimate (non-union) · budget = SL LABOR budget hours · click the used hours for the entries"),
    ]
    progress_rows = [r for r in hour_rows if any((r["used"], r["rem"], r["budget"]))] + [
        _prow("Labor $", "$", p.actual_labor,
              ((pred.remaining_labor_cost or D(0)) + (pred.unposted_labor_cost or D(0))) if pred else None,
              p.budget_labor, proj=(pred.eac_labor_cost if pred else (p.actual_labor if closed else None)),
              help_text="Wages + burden posted in SL · remaining = (PM remaining hours + PTT hours not yet posted) × labor rate"),
        _prow("Material $", "$", mat_used,
              (max((pred.eac_material or D(0)) - mat_used, D(0)) if pred else None),
              p.budget_material, proj=(pred.eac_material if pred else (mat_used if closed else None)),
              help_text="SL MATERIALS actuals, with purchase variance (%s) folded in · remaining covers real open commitments (%s) and the allocated PM expense estimate, with unspent budget as fallback"
                        % ("$%s" % format(int(round(ppv)), ",") if ppv else "none", "$%s" % format(int(round(p.open_commitments_material or 0)), ","))),
    ]
    if any(((p.actual_subcontract or 0), (p.budget_subcontract or 0), (p.open_commitments_subcontract or 0))):
        progress_rows.append(_prow("Subcontract $", "$", p.actual_subcontract,
                                   (max((pred.eac_subcontract or D(0)) - (p.actual_subcontract or D(0)), D(0)) if pred else None),
                                   p.budget_subcontract, proj=(pred.eac_subcontract if pred else (p.actual_subcontract if closed else None)),
                                   help_text="SL SUBCONTRACT actuals · remaining includes open subcontract commitments ($%s)" % format(int(round(p.open_commitments_subcontract or 0)), ",")))
    if any(((p.actual_other_direct or 0), (p.budget_other_direct or 0))):
        progress_rows.append(_prow("Other direct $", "$", p.actual_other_direct,
                                   (max((pred.eac_other_direct or D(0)) - (p.actual_other_direct or D(0)), D(0)) if pred else None),
                                   p.budget_other_direct, proj=(pred.eac_other_direct if pred else (p.actual_other_direct if closed else None)),
                                   help_text="ODC + travel (freight rides inside ODC)"))
    progress_rows.append(_prow("Total direct cost", "$", p.actual_direct_cost,
                               (max((pred.eac_direct_cost or D(0)) - (p.actual_direct_cost or D(0)), D(0)) if pred else None),
                               p.budget_direct_cost, proj=(pred.eac_direct_cost if pred else (p.actual_direct_cost if closed else None)),
                               help_text="Labor + material + subcontract + other direct (purchase variance included)", strong=True))

    def _barw(numer, denom):
        if not denom or denom <= 0:
            return None
        return round(min(max(float(numer or 0) / float(denom) * 100, 0), 100), 1)

    eac_cost = pred.eac_direct_cost if pred else p.actual_direct_cost
    cost_denom = max([v for v in (eac_cost, p.budget_direct_cost, p.actual_direct_cost) if v is not None] or [D(0)])
    over_billed = ((p.billed_revenue or D(0)) - p.earned_revenue) if p.earned_revenue is not None else None
    board = {
        "billed_pct": _barw(p.billed_revenue, p.contract_value), "earned_pct": _barw(p.earned_revenue, p.contract_value),
        "cost_pct": _barw(p.actual_direct_cost, cost_denom), "cost_budget_pct": _barw(p.budget_direct_cost, cost_denom),
        "cost_eac_pct": _barw(eac_cost, cost_denom), "over_billed": over_billed,
    }
    snapshots = list(ProjectFinancialSnapshot.objects.filter(project=p).order_by("-as_of_date")[:30])
    changes = list(ProjectCommercialChange.objects.filter(project=p).order_by("-detected_at"))
    # ---- change orders & budget history (apps/analytics/change_orders.py) ----
    from apps.analytics.change_orders import summarize as _co_summarize
    from apps.finance.models import ProjectChangeEvent
    co_events = list(ProjectChangeEvent.objects.filter(project=p).order_by("event_at", "task_id", "field"))
    co_groups = OrderedDict()
    for e in co_events:
        key = (e.event_at, e.task_id, e.entered_by, e.kind, e.source)
        g = co_groups.setdefault(key, {"at": e.event_at, "task_id": e.task_id, "task_description": e.task_description, "by": e.entered_by, "kind": e.kind,
                                       "source": e.source, "confidence": e.confidence, "prog": e.source_prog, "note": e.note, "fields": {}})
        f = g["fields"].get(e.field)
        if f is None:
            g["fields"][e.field] = e
        else:  # several SL accounts feed one field (e.g. LABOR + MATERIALS -> cost budget): combine
            f.new_value = (f.new_value or 0) + (e.new_value or 0)
            f.prior_value = (f.prior_value + e.prior_value) if (f.prior_value is not None and e.prior_value is not None) else None
            f.delta = (f.delta + e.delta) if (f.delta is not None and e.delta is not None) else None
    co_groups = sorted(co_groups.values(), key=lambda g: g["at"], reverse=True)
    for g in co_groups:
        g["cv"] = g["fields"].get("contract_value")
        g["cost"] = g["fields"].get("budget_direct_cost")
        g["hours"] = g["fields"].get("budget_labor_hours")
    co_summary = _co_summarize([{"kind": e.kind, "field": e.field, "delta": e.delta, "confidence": e.confidence, "event_at": e.event_at,
                                 "task_id": e.task_id, "entered_by": e.entered_by} for e in co_events], p.contract_value)
    co_summary["first_history"] = date(2026, 8, 17)
    # project-level before -> after for every event, walking back from today's totals (newest first).
    # An edit whose prior value SL did not keep is assumed not to have changed the total; everything
    # earlier than it is then marked approximate (≈ in the table, dashed on the chart).
    co_fields = {"contract_value": p.contract_value, "budget_direct_cost": p.budget_direct_cost, "budget_labor_hours": p.budget_labor_hours}
    co_steps = {}
    for field, current in co_fields.items():
        running, certain = current, current is not None
        steps = []
        for g in co_groups:  # newest first
            f = g["fields"].get(field)
            if f is None:
                continue
            f.proj_after = running
            f.after_certain = certain
            if f.delta is None:
                certain = False
                f.assumed = True
                f.proj_before = running
            else:
                f.assumed = False
                f.proj_before = (running - f.delta) if running is not None else None
            f.before_certain = certain
            running = f.proj_before
            steps.append({"d": g["at"].date().isoformat(), "after": _f(f.proj_after), "certain": f.after_certain})
        steps.reverse()
        start = (p.sl_created_at.date() if p.sl_created_at else None)
        co_steps[field] = {"start": start.isoformat() if start else None, "initial": _f(running), "initial_certain": certain, "steps": steps}
    if co_summary.get("original_cv") is None and co_steps["contract_value"]["initial"] is not None:
        co_summary["original_cv_assumed"] = co_steps["contract_value"]["initial"]

    def _marker_text(g):
        parts = []
        if acc.margins and g["cv"] is not None:
            parts.append("contract %s" % (("{:+,.0f}".format(g["cv"].delta)) if g["cv"].delta is not None else "edited (prior unknown)"))
        if acc.margins and g["cost"] is not None:
            parts.append("cost budget %s" % (("{:+,.0f}".format(g["cost"].delta)) if g["cost"].delta is not None else "edited"))
        if g["hours"] is not None:
            parts.append("budget hours %s" % (("{:+,.0f} h".format(g["hours"].delta)) if g["hours"].delta is not None else "edited"))
        label = {"change_order": "Change order", "scope_added": "Task added", "budget_edited": "Budget edited"}[g["kind"]]
        return "%s %s%s · %s · by %s" % (label, g["task_id"], (" " + g["task_description"]) if g["task_description"] else "", ", ".join(parts) or "budget row edited", g["by"] or "?")
    co_markers = [{"d": g["at"].date().isoformat(), "m": g["at"].strftime("%Y-%m"), "kind": g["kind"], "text": _marker_text(g)}
                  for g in co_groups if g["kind"] != "budget_edited" or g["cv"] is not None or g["hours"] is not None]
    issues = list(DataQualityIssue.objects.filter(project=p, status="open").order_by("severity"))
    system_mix = sorted((p.system_mix or {}).items(), key=lambda kv: -kv[1])
    work_mix = sorted((p.work_type_mix or {}).items(), key=lambda kv: -kv[1])
    # context ratings: every rated entity attached to this job — superadmin tier (Access Spec v1 §6); computed ONLY for holders
    ratings = []
    if acc.ratings:
        gp_q = Q(entity_type="project_manager", entity_key=str(p.project_manager_id)) | Q(entity_type="customer", entity_key=str(p.customer_id))
        if p.project_mode_rule:
            gp_q |= Q(entity_type="project_mode", entity_key=p.project_mode_rule)
        if p.solution_class:
            gp_q |= Q(entity_type="solution", entity_key=p.solution_class)
        if p.salesperson_code and not p.salesperson_non_commission:
            gp_q |= Q(entity_type="salesperson", entity_key=p.salesperson_code)
        order = {"project_manager": 0, "salesperson": 1, "customer": 2, "project_mode": 3, "solution": 4}
        label_map = {"project_mode": "project_type"}
        ratings = [{"label": label_map.get(r.entity_type, r.entity_type), "name": r.entity_name, "effect": r.adjusted_effect, "unit": "pts", "status": r.publication_status, "url": None}
                   for r in sorted(EntityRating.objects.filter(rating_run__is_current=True, metric_name="final_gp_pct").filter(gp_q),
                                   key=lambda r: order.get(r.entity_type, 9))]
        crew_ids = {str(c["employee_key"]): c for c in crew}
        emp_by_id = {str(e.id): e.employee_key for e in Employee.objects.filter(employee_key__in=list(crew_ids))}
        if emp_by_id:
            for r in EntityRating.objects.filter(rating_run__is_current=True, entity_type="field_employee",
                                                 metric_name="field_hours_saved_per_1000", entity_key__in=list(emp_by_id)).order_by("-adjusted_effect"):
                ratings.append({"label": "field_crew", "name": r.entity_name, "effect": r.adjusted_effect, "unit": "h1000",
                                "status": r.publication_status, "url": emp_by_id.get(r.entity_key)})
    # ---- materials on the job (apps/analytics/project_materials.py): SO lines, shippers, PO lines (direct + deduced), receipts,
    # AP vouchers on those POs, the ChannelOnline documents behind the sales orders, and the ledger's freight/variance rows ----
    from apps.analytics.project_materials import assemble as _mat_assemble
    from apps.ingestion.finance_loaders import MATERIALS_DAYS_BACK
    mat_so = fetch_dict("""SELECT so_nbr, line_ref, ord_date, so_type, behavior, status, line_status, cnet_quote, crtd_user, slsper_id, cust_ord_nbr, ship_name, tot_frt,
                                  item_id, descr, qty_ord, qty_ship, qty_bo, unit_cost, tot_cost, sls_price, tot_ord, task_id, site_id, prom_date, req_date, drop_ship
                           FROM finance_projectsalesorderline WHERE project_id=%s ORDER BY ord_date, so_nbr, line_ref""", [p.id])
    mat_ship = fetch_dict("""SELECT shipper_id, ship_date, status, so_nbr, invc_nbr, invc_date, crtd_user, ship_via, tracking_nbr, tot_frt_cost, tot_frt_invc,
                                    item_id, descr, qty_ship, unit_cost, tot_cost, sls_price, tot_invc
                             FROM finance_projectshipmentline WHERE project_id=%s ORDER BY ship_date NULLS LAST, shipper_id, line_ref""", [p.id])
    mat_po = fetch_dict("""SELECT po_nbr, po_date, vendor_id, vendor_name, status, buyer, crtd_user, po_type, ship_via, po_freight, last_rcpt_date, line_ref, item_id, descr,
                                  qty_ord, qty_rcvd, unit_cost, ext_cost, qty_vouched, cost_vouched, prom_date, site_id, (project_id = %s) AS direct, deduce_basis
                           FROM finance_poline WHERE project_id=%s OR deduced_project_id=%s ORDER BY po_date, po_nbr, line_ref""", [p.id, p.id, p.id])
    mat_rc = fetch_dict("""SELECT rcpt_nbr, rcpt_date, po_nbr, vendor_name, item_id, descr, qty, unit_cost, ext_cost, crtd_user, vend_invc_nbr
                           FROM finance_poreceiptline WHERE project_id=%s OR deduced_project_id=%s ORDER BY rcpt_date, rcpt_nbr""", [p.id, p.id])
    po_nbrs = tuple({r["po_nbr"] for r in mat_po})
    mat_v = fetch_dict("""SELECT ref_nbr, doc_type, doc_date, vendor_id, vendor_name, amount, freight_amt, po_nbr, status, crtd_user
                          FROM finance_povoucher WHERE po_nbr IN %s ORDER BY doc_date, ref_nbr""", [po_nbrs]) if po_nbrs else []
    quotes = tuple({r["cnet_quote"] for r in mat_so if r["cnet_quote"]})
    mat_docs = fetch_dict("""SELECT id, document_number, doc_type, status, total_item_cost, subtotal, shipping_handling, salesperson_name, created_by_name, ordered_by_name, sl_ord_nbr
                             FROM sales_cnetdocument WHERE document_number IN %s ORDER BY doc_type, created_at""", [quotes]) if quotes else []
    mat_cl = fetch_dict("""SELECT d.document_number, l.part_number, l.unit_cost, l.unit_price, l.qty, l.ext_cost, l.ext_price, l.supplier_name, l.dropship
                           FROM sales_cnetdocumentline l JOIN sales_cnetdocument d ON d.id = l.document_id WHERE d.document_number IN %s""", [quotes]) if quotes else []
    mat_tx = fetch_dict("""SELECT transaction_date, system_cd, batch_type, sl_acct, vendor_num, voucher_num, comment, amount, units
                           FROM finance_projectfinancialtransaction WHERE project_id=%s AND category IN ('material','other_direct') ORDER BY transaction_date""", [p.id])
    materials = _mat_assemble(p, mat_so, mat_ship, mat_po, mat_rc, mat_v, mat_docs, mat_cl, mat_tx, since=timezone.localdate() - timedelta(days=MATERIALS_DAYS_BACK))
    charts = {
        "weekly": [{"w": r["w"].isoformat(), "h": _f(r["h"]), "n": r["n"]} for r in weekly_hours],
        "cum": cum_series, "rev": rev_series, "budget_hours": _f(p.budget_labor_hours),
        "system_mix": system_mix, "markers": co_markers, "budget_steps": co_steps["budget_labor_hours"],
    }
    if acc.margins:   # dollar charts exist in the payload only for viewers allowed to see dollars (spec §12: never compute-then-hide)
        charts["monthly"] = [{"m": r["m"].isoformat(), "c": r["category"], "amt": _f(r["amt"])} for r in monthly]
        charts["budget_vs_actual"] = [{"k": r["sl_acct"], "b": _f(r["budget"]), "a": _f(r["actual"])} for r in summary if r["category"] not in ("excluded_memo", "contract_value") and r["sl_acct"] != "CONTRACT VALUE"]
    else:
        progress_rows = [r for r in progress_rows if r["unit"] == "h"]
    return render(request, "dashboard/project_detail.html", _ctx(request, "projects", p=p, pred=pred, summary=summary, by_task=by_task, tasks=tasks, tran_groups=tran_groups,
                                                                 ledger=ledger, entries=entries, entries_total=entries_total, entries_more=entries_total - len(entries), crew=crew, revisions=revisions, rev_rows=rev_rows, rev_now=rev_now, rev_summary=rev_summary, rev_today=today, cost_revisions=cost_revisions, snapshots=snapshots, changes=changes, issues=issues,
                                                                 system_mix=system_mix, work_mix=work_mix, ratings=ratings, charts=charts, vs_sold_pts=vs_sold_pts,
                                                                 commit_lines=commit_lines, commit_summary=commit_summary, progress_rows=progress_rows, board=board, materials=materials,
                                                                 co_groups=co_groups, co_summary=co_summary,
                                                                 first_snapshot=(snapshots[-1].as_of_date if snapshots else None)))



def _revision_history(revisions, cum_t, bud, spent_now, rem_now, today):
    """Rows for the PM remaining-hours table, newest first. For each revision: PTT hours spent up to that day by
    employee type (non-union / union / total incl. untyped), the PM's remaining estimate, projected at completion =
    spent + remaining, its over/under vs the current SL labor budget (LABOR = non-union, LABORUNION = union), and the
    movement since the previous revision (Δ remaining, Δ projected, days between, hours logged between). A "now" row is
    added when hours were logged after the last revision, so the live projection sits above the history."""
    D0 = Decimal(0)
    keys = ("nu", "u", "tot")

    def spent_at(day):
        s = {"nu": D0, "u": D0, "tot": D0}
        for r in cum_t:
            if r["d"] > day:
                break
            s["tot"] += r["h"]
            if r["t"] == "non_union":
                s["nu"] += r["h"]
            elif r["t"] == "union":
                s["u"] += r["h"]
        return s

    def build(day, when, by, spent, rem, prev, is_current=False, live=False):
        proj = {k: (spent[k] or D0) + (rem[k] or D0) for k in keys}
        over = {k: (proj[k] - bud[k]) if bud.get(k) is not None else None for k in keys}
        row = {"day": day, "when": when, "by": by, "spent": spent, "rem": rem, "proj": proj, "over": over, "is_current": is_current, "live": live,
               "over_pct": (over["tot"] / bud["tot"]) if (over["tot"] is not None and bud.get("tot")) else None,
               "pct": (spent["tot"] / proj["tot"]) if proj["tot"] else None,
               "d_rem": {k: None for k in keys}, "d_proj": {k: None for k in keys}, "gap_days": None, "burn": None}
        if prev:
            for k in keys:
                if rem[k] is not None and prev["rem"][k] is not None:
                    row["d_rem"][k] = rem[k] - prev["rem"][k]
                row["d_proj"][k] = proj[k] - prev["proj"][k]
            row["gap_days"] = (day - prev["day"]).days
            row["burn"] = spent["tot"] - prev["spent"]["tot"]
        return row

    rows, prev = [], None
    for rv in revisions:
        day = timezone.localtime(rv.revised_at).date()
        rem = {"nu": rv.remaining_hours_non_union, "u": rv.remaining_hours_union, "tot": rv.remaining_hours_total}
        if rem["tot"] is None and (rem["nu"] is not None or rem["u"] is not None):
            rem["tot"] = (rem["nu"] or D0) + (rem["u"] or D0)
        row = build(day, rv.revised_at, rv.revised_by.canonical_name if rv.revised_by else "?", spent_at(day), rem, prev, is_current=rv.is_current)
        rows.append(row)
        prev = row
    now_row = None
    if rows and spent_now["tot"] > rows[-1]["spent"]["tot"]:
        now_row = build(today, None, "", spent_now, rem_now, rows[-1], live=True)
    rows.reverse()
    return rows, now_row


WORKLOG_FIRST = 45     # work-log rows rendered with the page; "Show all" fetches the rest


def _project_entries_qs(p):
    """Live PTT Job Report entries on a project, newest first — the one definition behind the hour figures, the crew table and the work log."""
    return TimeEntry.objects.filter(project=p, source_status=1, form_type=1).select_related("employee", "submitted_by").order_by("-work_date", "employee__canonical_name", "-source_key")


def project_entries(request, key):
    """JSON: the individual PTT Job Report entries behind a project's hour figures — every entry by employee type
    (?type=union|non_union, the Used union / non-union hours click), one crew member's entries (?emp=KEY, the Crew
    expand) or the work log past the first WORKLOG_FIRST rows (?offset=N, the "Show all" button). No compensation
    data: hours, notes, tasks and who submitted — the same columns the page's work log already shows."""
    p = get_object_or_404(Project, canonical_project_number=key.upper())
    qs = _project_entries_qs(p)
    etype, emp = request.GET.get("type", ""), request.GET.get("emp", "")
    if etype in ("union", "non_union"):
        qs = qs.filter(employee__ptt_employee_type=etype)
    if emp:
        qs = qs.filter(employee__employee_key=emp)
    try:
        offset = max(int(request.GET.get("offset") or 0), 0)
    except ValueError:
        offset = 0
    total = qs.count()
    rows, hours = [], Decimal(0)
    by = defaultdict(lambda: {"n": 0, "h": Decimal(0), "key": ""})
    for e in qs[offset:offset + 10000]:
        who = e.employee.canonical_name if e.employee else "—"
        hours += e.hours_total or 0
        g = by[who]
        g["n"] += 1
        g["h"] += e.hours_total or 0
        g["key"] = e.employee.employee_key if e.employee else ""
        rows.append({"d": e.work_date.isoformat(), "emp": who, "key": g["key"], "sys": e.system_choice, "wt": e.work_type_choice,
                     "on": _f(e.hours_onsite), "ot": _f(e.hours_ot), "off": _f(e.hours_offsite), "tot": _f(e.hours_total), "task": e.task_id_text,
                     "act": e.activity_note, "iss": e.open_issues_note, "done": bool(e.completed_flag),
                     "by": e.submitted_by.canonical_name if (e.submitted_by and e.submitted_by_id != e.employee_id) else ""})
    groups = sorted([{"who": k, "key": v["key"], "n": v["n"], "h": _f(v["h"])} for k, v in by.items()], key=lambda g: -g["h"])
    return JsonResponse({"project": p.display_number, "type": etype, "emp": emp, "offset": offset, "n": total, "hours": _f(hours), "rows": rows, "groups": groups})


def project_account_drill(request, key):
    """JSON: every SL posting behind one account row of a project's Budget-vs-actual table (the
    'what are these materials?' click). Vendor names from the local SL vendor copy; labor amounts
    follow the same per-row compensation redaction as the ledger (Access Spec v1 §7.2)."""
    p = get_object_or_404(Project, canonical_project_number=key.upper())
    acct = request.GET.get("acct", "")
    acc = request.acc
    if not acc.margins:
        raise Http404
    rows = fetch_dict("""SELECT t.transaction_date, t.fiscal_period, t.task_id, t.system_cd, t.batch_type, t.vendor_num, COALESCE(v.name, '') vendor_name,
                                t.voucher_num, t.comment, t.amount, t.units, t.gl_account, t.employee_id, t.employee_key, e.canonical_name employee_name, e.is_field_hourly
                         FROM finance_projectfinancialtransaction t
                         LEFT JOIN finance_slvendor v ON v.vendor_id = t.vendor_num
                         LEFT JOIN core_employee e ON e.id = t.employee_id
                         WHERE t.project_id = %s AND t.sl_acct = %s
                         ORDER BY t.transaction_date DESC, t.source_created_at DESC""", [p.id, acct])
    labor = acct in ("LABOR", "LABORUNION", "BURDEN")
    out, total, units = [], Decimal(0), Decimal(0)
    by = defaultdict(lambda: {"n": 0, "amt": Decimal(0), "units": Decimal(0)})
    for r in rows:
        visible = (not labor) or r["employee_id"] is None or rate_visible(acc, r["is_field_hourly"])
        who = (r["employee_name"] or r["employee_key"]) if labor else (r["vendor_name"] or r["vendor_num"] or "")
        amt = r["amount"] if visible else None
        total += r["amount"] or 0
        units += r["units"] or 0
        g = by[who or "—"]
        g["n"] += 1
        g["units"] += r["units"] or 0
        g["amt"] = (g["amt"] + (r["amount"] or 0)) if (visible and g["amt"] is not None) else (None if not visible else g["amt"])
        out.append({"date": r["transaction_date"].isoformat() if r["transaction_date"] else "", "period": r["fiscal_period"], "task": r["task_id"],
                    "module": "%s/%s" % (r["system_cd"], r["batch_type"] or ""), "who": who, "ref": r["voucher_num"] or "",
                    "comment": r["comment"] or "", "amt": float(amt) if amt is not None else None, "units": float(r["units"] or 0), "gl": r["gl_account"]})
    groups = sorted([{"who": k, "n": v["n"], "amt": (float(v["amt"]) if v["amt"] is not None else None), "units": float(v["units"])} for k, v in by.items()],
                    key=lambda g: -(g["amt"] or 0))
    return JsonResponse({"project": p.display_number, "acct": acct, "n": len(out), "total": float(total), "units": float(units),
                         "who_label": "Employee" if labor else "Vendor", "rows": out, "groups": groups})


LEDGER_SORTS = {"date": "t.transaction_date", "created": "t.source_created_at", "amount": "t.amount", "units": "t.units",
                "period": "t.fiscal_period", "cat": "t.category", "acct": "t.sl_acct"}
LEDGER_PAGE = 300      # rows per fetch; the table loads the next page as Owner scrolls


def _ledger_facets(p, tran_groups):
    """Filter-chip values (with posting counts) for the project page's transaction ledger. '-' stands for a blank
    sub-tag so the chip and the ?tag= filter can name it."""
    from apps.dashboard.templatetags.pca import nice

    def facet(key, label=nice):
        agg = defaultdict(int)
        for g in tran_groups:
            agg[key(g)] += g["n"]
        return [{"v": v, "n": n, "label": "(none)" if v == "-" else label(v)} for v, n in sorted(agg.items())]
    accts = fetch_dict("SELECT sl_acct, COUNT(*) n FROM finance_projectfinancialtransaction WHERE project_id=%s GROUP BY 1 ORDER BY 1", [p.id])
    return {"n": sum(g["n"] for g in tran_groups),
            "facets": {"cat": facet(lambda g: g["category"]), "tag": facet(lambda g: g["sub_tag"] or "-"),
                       "mod": facet(lambda g: "%s/%s" % (g["system_cd"], g["batch_type"] or ""), label=str),
                       "acct": [{"v": r["sl_acct"], "n": r["n"], "label": r["sl_acct"]} for r in accts]}}


def _ledger_visible_sql(acc):
    """SQL predicate for the ledger's per-row compensation redaction (Access Spec v1 §7.2): a labor amount on an
    employee is visible to superadmin, or to a rates_field viewer when the employee is field-hourly; never otherwise."""
    rates = "TRUE" if acc.is_superadmin else ("COALESCE(e.is_field_hourly, FALSE)" if acc.rates_field else "FALSE")
    return "(t.category NOT IN ('labor_wage','labor_burden') OR t.employee_id IS NULL OR %s)" % rates


def project_transactions(request, key):
    """JSON: a project's full SL transaction ledger — every PJTran row, no cap — filtered, multi-key sorted and paged
    for the Transaction ledger table on the project page. Filters: q= free text over comment, employee, vendor,
    voucher, batch, GL account, task, account, period (a number also matches the exact amount); cat= tag= mod= acct=
    task= comma lists ('-' = blank sub-tag); period_from/period_to (YYYYMM); date_from/date_to (YYYY-MM-DD).
    sort= comma list of [-]date|created|amount|units|period|cat|acct, applied in order (default -date,-created).
    offset=/limit= paging (LEDGER_PAGE, max 2000). Amounts follow the ledger's per-row compensation redaction
    (Access Spec v1 §7.2): a hidden row's amt is null, the total sums visible rows only, hidden_n counts the rest."""
    from decimal import InvalidOperation
    p = get_object_or_404(Project, canonical_project_number=key.upper())
    acc = request.acc
    if not acc.margins:
        raise Http404
    g = request.GET
    where, params = ["t.project_id = %s"], [p.id]

    def _in(param, column, upper=False):
        vals = [("" if v.strip() == "-" else v.strip()) for v in (g.get(param) or "").split(",") if v.strip()]
        if vals:
            where.append("%s IN %%s" % column); params.append(tuple(v.upper() if upper else v for v in vals))
    _in("cat", "t.category"); _in("tag", "t.sub_tag"); _in("acct", "t.sl_acct", upper=True); _in("task", "t.task_id")
    mods = [m.strip().upper() for m in (g.get("mod") or "").split(",") if m.strip()]
    if mods:
        where.append("(t.system_cd || '/' || COALESCE(t.batch_type, '')) IN %s"); params.append(tuple(mods))
    for param, col, op in (("period_from", "t.fiscal_period", ">="), ("period_to", "t.fiscal_period", "<=")):
        v = (g.get(param) or "").strip()
        if v.isdigit() and len(v) == 6:
            where.append("%s %s %%s" % (col, op)); params.append(v)
    for param, op in (("date_from", ">="), ("date_to", "<=")):
        v = (g.get(param) or "").strip()
        if v:
            try:
                where.append("t.transaction_date %s %%s" % op); params.append(date.fromisoformat(v))
            except ValueError:
                where.pop()
    q = (g.get("q") or "").strip()
    if q:
        cols = ["t.comment", "e.canonical_name", "t.employee_key", "t.vendor_num", "v.name", "t.voucher_num", "t.batch_id",
                "t.gl_account", "t.task_id", "t.sl_acct", "t.fiscal_period", "t.sub_tag", "t.category"]
        parts = ["%s ILIKE %%s" % c for c in cols]
        params += ["%" + q + "%"] * len(cols)
        try:
            num = abs(Decimal(q.replace(",", "").replace("$", ""))).quantize(Decimal("0.01"))
            parts.append("ROUND(ABS(t.amount), 2) = %s"); params.append(num)
        except (InvalidOperation, ValueError):
            pass
        where.append("(" + " OR ".join(parts) + ")")
    order = []
    for s in (g.get("sort") or "-date,-created").split(","):
        s = s.strip()
        col = LEDGER_SORTS.get(s.lstrip("-"))
        if col:
            order.append("%s %s NULLS LAST" % (col, "DESC" if s.startswith("-") else "ASC"))
    order.append("t.id DESC")     # stable paging
    try:
        offset = max(int(g.get("offset") or 0), 0)
        limit = min(max(int(g.get("limit") or LEDGER_PAGE), 1), 2000)
    except ValueError:
        offset, limit = 0, LEDGER_PAGE
    w, visible = " AND ".join(where), _ledger_visible_sql(acc)
    joins = ("FROM finance_projectfinancialtransaction t LEFT JOIN core_employee e ON e.id = t.employee_id "
             "LEFT JOIN finance_slvendor v ON v.vendor_id = t.vendor_num")
    agg = fetch_dict("""SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN %s THEN t.amount END), 0) total, COALESCE(SUM(t.units), 0) units,
                               COUNT(*) FILTER (WHERE NOT %s) hidden_n %s WHERE %s""" % (visible, visible, joins, w), params)[0]
    rows = fetch_dict("""SELECT t.transaction_date, t.fiscal_period, t.task_id, t.category, t.sub_tag, t.sl_acct, t.system_cd, t.batch_type, t.batch_id,
                                t.amount, t.units, t.employee_key, e.canonical_name employee_name, e.employee_key emp_link, t.vendor_num,
                                COALESCE(v.name, '') vendor_name, t.gl_account, t.voucher_num, t.comment, t.source_created_at, (%s) visible
                         %s WHERE %s ORDER BY %s OFFSET %%s LIMIT %%s""" % (visible, joins, w, ", ".join(order)), params + [offset, limit])
    out = []
    for r in rows:
        created = r["source_created_at"]
        out.append({"date": r["transaction_date"].isoformat() if r["transaction_date"] else "", "period": r["fiscal_period"], "task": r["task_id"],
                    "cat": r["category"], "tag": r["sub_tag"], "acct": r["sl_acct"], "module": "%s/%s" % (r["system_cd"], r["batch_type"] or ""),
                    "amt": float(r["amount"]) if r["visible"] else None, "units": float(r["units"] or 0),
                    "who": r["employee_name"] or r["employee_key"] or r["vendor_name"] or r["vendor_num"] or "", "emp": r["emp_link"] or "",
                    "vendor": r["vendor_num"] or "", "gl": r["gl_account"], "ref": r["voucher_num"] or "", "batch": r["batch_id"], "comment": r["comment"] or "",
                    "created": (timezone.localtime(created) if timezone.is_aware(created) else created).date().isoformat() if created else ""})
    return JsonResponse({"project": p.display_number, "n": agg["n"], "total": float(agg["total"]), "units": float(agg["units"]), "hidden_n": agg["hidden_n"],
                         "offset": offset, "limit": limit, "has_more": offset + len(out) < agg["n"], "rows": out})


# ============================================================================ forecast / active book
def forecast(request):
    d, _, _ = Q_.division_scope(request)
    active = Q_.active_book(d)
    sort = request.GET.get("sort", "-risk")
    col = SORTS.get(sort.lstrip("-"), "pr.risk_score")
    order = "%s %s NULLS LAST, p.contract_value DESC NULLS LAST" % (col, "DESC" if sort.startswith("-") else "ASC")
    where, params = ["p.lifecycle_state IN %s AND NOT p.is_internal_bucket"], [Q_.OPEN]
    if request.GET.get("state"):
        where.append("p.lifecycle_state = %s"); params.append(request.GET["state"])
    if request.GET.get("risk"):
        where.append("pr.risk_level = %s"); params.append(request.GET["risk"])
    if request.GET.get("pm"):
        where.append("pm.employee_key = %s"); params.append(request.GET["pm"])
    rows = Q_.project_rows(d, " AND ".join(where), params, order=order)
    by_state = defaultdict(lambda: {"n": 0, "cv": Decimal(0), "eac_gp": Decimal(0)})
    for r in rows:
        s = by_state[r["lifecycle_state"]]
        s["n"] += 1
        s["cv"] += r["contract_value"] or 0
        s["eac_gp"] += r["eac_gp_dollars"] or 0
    risk_counts = defaultdict(int)
    for r in rows:
        risk_counts[r["risk_level"] or "n/a"] += 1
    pms = fetch_dict("SELECT e.employee_key k, e.canonical_name n, COUNT(*) c FROM core_project p JOIN core_employee e ON e.id=p.project_manager_id WHERE p.lifecycle_state IN %s GROUP BY 1,2 ORDER BY c DESC", [Q_.OPEN])
    return render(request, "dashboard/forecast.html", _ctx(request, "forecast", rows=rows, active=active, by_state=dict(by_state), risk_counts=dict(risk_counts), sort=sort, g=request.GET, pms=pms))


# ============================================================================ people
def people(request):
    d, _, _ = Q_.division_scope(request)
    years, start, end = _window(request)
    rows = Q_.breakdown(d, start, end, "e.employee_key", "e.canonical_name", "LEFT JOIN core_employee e ON e.id=p.project_manager_id", limit=60, order="revenue DESC")
    active = {r["k"]: r for r in fetch_dict("""SELECT e.employee_key k, COUNT(*) n, SUM(p.contract_value) cv, SUM(pr.eac_gp_dollars) eac_gp, SUM(pr.eac_revenue) eac_rev,
                                                     COUNT(*) FILTER (WHERE pr.risk_level IN ('high','critical')) at_risk,
                                                     COUNT(*) FILTER (WHERE p.pm_remaining_hours IS NULL AND p.lifecycle_state IN ('in_progress','dormant')) missing_rem
                                              FROM core_project p JOIN core_employee e ON e.id=p.project_manager_id LEFT JOIN analytics_projectprediction pr ON pr.project_id=p.id AND pr.as_of_date=(SELECT MAX(as_of_date) FROM analytics_projectprediction)
                                              WHERE p.lifecycle_state IN %s AND NOT p.is_internal_bucket AND (%s) GROUP BY 1""" % ("%s", Q_.div_where(d)[0]), [Q_.OPEN] + Q_.div_where(d)[1])}
    ratings, pres = {}, {}
    if request.acc.ratings:   # superadmin tier — never computed for others (Access Spec v1 §6)
        ratings = {r.entity_key: r for r in EntityRating.objects.filter(rating_run__is_current=True, entity_type="project_manager", metric_name="final_gp_pct")}
        pres = {r.entity_key: r for r in EntityRating.objects.filter(rating_run__is_current=True, entity_type="project_manager", metric_name="margin_preservation")}
    emp_ids = {e.employee_key: str(e.id) for e in Employee.objects.filter(employee_key__in=[r["k"] for r in rows if r["k"]])}
    for r in rows:
        r["active"] = active.get(r["k"])
        r["rating"] = ratings.get(emp_ids.get(r["k"]))
        r["pres"] = pres.get(emp_ids.get(r["k"]))
        r["preservation"] = (r["final_pct_with"] - r["sold_pct"]) if (r["final_pct_with"] is not None and r["sold_pct"] is not None) else None
    return render(request, "dashboard/people.html", _ctx(request, "people", rows=rows, years=years, start=start, end=end))


PTT_FORM_TYPES = {1: "job report", 2: "time off", 3: "other", 4: "check-in"}
TIME_LOG_PAGE = 150


def _person_time_log(request, e):
    """Detailed PTT entry log for the person page: every TTFormResponse for this person (or, with ?lwho=others,
    the ones they entered for other people), newest first, with year/month/project filters and paging.
    This is the same per-person, per-day view PTT itself shows. Query params are prefixed with `l` so they do
    not collide with the Projects-worked sort/crew filters on the same page."""
    g = request.GET
    others = g.get("lwho") == "others"
    counts = {"self": TimeEntry.objects.filter(employee=e, source_status=1).count(),
              "others": TimeEntry.objects.filter(submitted_by=e, source_status=1).exclude(employee=e).count()}
    base = TimeEntry.objects.filter(submitted_by=e).exclude(employee=e) if others else TimeEntry.objects.filter(employee=e)
    show_removed = g.get("lrm") == "1"
    live = base.filter(source_status=1)
    if not show_removed:
        base = live

    def _int(k):
        try:
            return int(g.get(k) or 0) or None
        except ValueError:
            return None
    year, month, proj = _int("ly"), _int("lm"), g.get("lproj", "")
    qs = base
    if year:
        qs = qs.filter(work_date__year=year)
    if month:
        qs = qs.filter(work_date__month=month)
    if proj:
        qs = qs.filter(project__canonical_project_number=proj)
    totals = qs.filter(source_status=1).aggregate(n=Count("id"), days=Count("work_date", distinct=True), projects=Count("project", distinct=True),
                          onsite=Sum("hours_onsite"), ot=Sum("hours_ot"), offsite=Sum("hours_offsite"), total=Sum("hours_total"), time_off=Sum("hours_time_off"))
    page = Paginator(qs.select_related("project", "employee", "submitted_by").order_by("-work_date", "employee__canonical_name", "submitted_at"), TIME_LOG_PAGE).get_page(g.get("lpage"))
    rows = list(page)
    # true day totals (all live job-report hours for that person and day, regardless of the project filter)
    day = {}
    if rows:
        emp_ids = sorted({r.employee_id for r in rows if r.employee_id})
        lo, hi = min(r.work_date for r in rows), max(r.work_date for r in rows)
        day = {(d["employee_id"], d["work_date"]): d for d in fetch_dict("""SELECT employee_id, work_date, SUM(hours_total) h, COUNT(*) n FROM operations_timeentry
                                                                            WHERE employee_id = ANY(%s) AND work_date BETWEEN %s AND %s AND source_status=1 AND form_type=1 GROUP BY 1,2""", [emp_ids, lo, hi])}
    prev = None
    for r in rows:
        d = day.get((r.employee_id, r.work_date)) or {}
        r.day_total, r.day_n = d.get("h"), d.get("n") or 0
        r.day_first = (r.employee_id, r.work_date) != prev
        prev = (r.employee_id, r.work_date)
        r.by_other = bool(r.submitted_by_id) and r.submitted_by_id != r.employee_id
        r.form_label = PTT_FORM_TYPES.get(r.form_type, str(r.form_type))
    years = list(live.annotate(y=ExtractYear("work_date")).values("y").annotate(n=Count("id")).order_by("-y"))
    projects = list(live.filter(project__isnull=False).values("project__canonical_project_number", "project__display_number", "project__title").annotate(n=Count("id")).order_by("-n")[:300])
    return {"others": others, "counts": counts, "has_any": bool(counts["self"] or counts["others"]), "show_removed": show_removed,
            "year": year, "month": month, "proj": proj, "years": years, "projects": projects,
            "months": [(i, date(2000, i, 1).strftime("%B")) for i in range(1, 13)], "totals": totals, "page": page, "rows": rows}


def _ptt_updates_by(e, window="12m"):
    """The PTT % complete saves one person made — on any job, not only the ones they manage — newest first: when, the job,
    the % in force the day before → the % saved (Δ points, Δ earned = contract × Δ), and the remaining hours they revised
    that day, if any. window: 12m (default) · 24m · all. Feeds the person page's "PTT progress updates" card
    (Owner, 2026-09-08: from the snapshot's Updated-by names, "what other jobs that person updated and when")."""
    from apps.analytics.finance_wip import pct_series, pct_at_from_series
    today = timezone.localdate()
    since = {"12m": today - timedelta(days=365), "24m": today - timedelta(days=730)}.get(window)
    if since is None:
        window = "all"
    where, params = "", [e.id]
    if since:
        where, params = "AND o.ptt_last_updated_at >= %s", [e.id, since]
    rows = fetch_dict("""
        SELECT DISTINCT ON (o.project_id, o.ptt_last_updated_at) o.project_id, o.ptt_last_updated_at at, o.ptt_percent_complete pct,
               p.canonical_project_number cpn, p.display_number, p.title, p.lifecycle_state, p.contract_value cv, d.code division,
               pm.canonical_name pm_name, pm.employee_key pm_key
        FROM operations_percentcompleteobservation o
        JOIN core_project p ON p.id = o.project_id JOIN core_division d ON d.id = p.division_id
        LEFT JOIN core_employee pm ON pm.id = p.project_manager_id
        WHERE o.ptt_last_updated_by_id = %%s AND o.ptt_last_updated_at IS NOT NULL %s
        ORDER BY o.project_id, o.ptt_last_updated_at, o.observed_at DESC""" % where, params)
    total = fetch_dict("SELECT COUNT(DISTINCT (project_id, ptt_last_updated_at)) n FROM operations_percentcompleteobservation WHERE ptt_last_updated_by_id = %s AND ptt_last_updated_at IS NOT NULL", [e.id])[0]["n"]
    pids = sorted({r["project_id"] for r in rows})
    series = pct_series(pids) if pids else {}
    rem_where, rem_params = "", [e.id]
    if since:
        rem_where, rem_params = "AND r.revised_at >= %s", [e.id, since]
    rem = {(r["project_id"], r["d"]): r["rem"] for r in fetch_dict("""
        SELECT DISTINCT ON (r.project_id, r.revised_at::date) r.project_id, r.revised_at::date d, r.remaining_hours_total rem
        FROM operations_remaininghoursrevision r WHERE r.revised_by_id = %%s %s
        ORDER BY r.project_id, r.revised_at::date, r.revised_at DESC, r.sequence DESC""" % rem_where, rem_params)}
    rem_n = fetch_dict("SELECT COUNT(*) n FROM operations_remaininghoursrevision r WHERE r.revised_by_id = %%s %s" % rem_where, rem_params)[0]["n"]
    out = []
    for r in rows:
        day = timezone.localtime(r["at"]).date()
        prev = pct_at_from_series(series.get(r["project_id"], []), day - timedelta(days=1), fallback=None) if series.get(r["project_id"]) else None
        if prev is not None and series.get(r["project_id"]) and series[r["project_id"]][0][0] > day - timedelta(days=1):
            prev = None            # nothing recorded before this save
        pct = r["pct"]
        bad = pct is not None and not (Decimal(0) <= pct <= Decimal(1))
        d = (pct - prev) if (pct is not None and prev is not None and not bad) else None
        out.append(dict(r, day=day, prev=prev, bad=bad, d=d, d_earned=((r["cv"] or Decimal(0)) * d) if d is not None else None,
                        rem=rem.get((r["project_id"], day)), own=(r["pm_key"] == e.employee_key)))
    out.sort(key=lambda r: r["at"], reverse=True)
    return {"rows": out, "n": len(out), "total": total, "jobs": len(pids), "days": len({r["day"] for r in out}), "own": sum(1 for r in out if r["own"]),
            "last": out[0]["at"] if out else None, "rem_n": rem_n, "window": window, "since": since}


def person_detail(request, key):
    e = get_object_or_404(Employee, employee_key=key)
    years, start, end = _window(request)
    # as PM
    managed = Q_.project_rows(None, "p.project_manager_id = %s AND NOT p.is_internal_bucket", [e.id], order="p.sl_created_at DESC")
    pm_closed = [r for r in managed if r["lifecycle_state"] in Q_.CLOSED and r["project_mode_rule"] not in Q_.REAL_MODES_EXCLUDED and r["close_date"] and start <= r["close_date"] <= end]
    pm_open = [r for r in managed if r["lifecycle_state"] in Q_.OPEN]
    def agg(rs):
        rev = sum((r["billed_revenue"] or 0) for r in rs); gp = sum((r["actual_gp_dollars"] or 0) for r in rs)
        cvw = sum((r["contract_value"] or 0) for r in rs if (r["contract_value"] or 0) > 0); sgp = sum((r["sold_gp_dollars"] or 0) for r in rs if (r["contract_value"] or 0) > 0)
        rw = sum((r["billed_revenue"] or 0) for r in rs if (r["contract_value"] or 0) > 0); gw = sum((r["actual_gp_dollars"] or 0) for r in rs if (r["contract_value"] or 0) > 0)
        return {"n": len(rs), "revenue": rev, "gp": gp, "gp_pct": (gp / rev) if rev else None, "sold_pct": (sgp / cvw) if cvw else None,
                "final_pct_with": (gw / rw) if rw else None, "hours": sum((r["ptt_hours_total"] or 0) for r in rs), "losers": sum(1 for r in rs if (r["actual_gp_dollars"] or 0) < 0)}
    pm_summary = agg(pm_closed)
    pm_summary["preservation"] = (pm_summary["final_pct_with"] - pm_summary["sold_pct"]) if (pm_summary["final_pct_with"] is not None and pm_summary["sold_pct"] is not None) else None
    pm_by_year = fetch_dict("""SELECT EXTRACT(year FROM close_date)::int y, COUNT(*) n, SUM(billed_revenue) rev, SUM(actual_gp_dollars) gp, SUM(ptt_hours_total) hours
                               FROM core_project WHERE project_manager_id=%s AND lifecycle_state IN %s AND project_mode_rule NOT IN %s AND close_date IS NOT NULL GROUP BY 1 ORDER BY 1""", [e.id, Q_.CLOSED, Q_.REAL_MODES_EXCLUDED])
    subject_rate_ok = rate_visible(request.acc, e.is_field_hourly)
    ratings = list(EntityRating.objects.filter(rating_run__is_current=True, entity_type__in=["project_manager", "division_head_era"], entity_key=str(e.id))) if request.acc.ratings else []
    field_rating_rows = list(EntityRating.objects.filter(rating_run__is_current=True, entity_type="field_employee", entity_key=str(e.id)))
    field_metric_meta = {"field_hours_saved_per_1000": ("Net hours / 1,000 budget h", "h1000", "Hours saved per 1,000 budget labor hours on installation/JOC jobs this person works, after controls and shrinkage. Positive = jobs need fewer hours than expected."),
                         "field_cost_saved_per_1000": ("Labor $ / $1,000 budget", "d1000", "Labor dollars saved per $1,000 of labor budget, same model on the cost outcome."),
                         "field_margin_preservation": ("Margin effect", "pts", "Effect on final-vs-sold margin (points) — secondary; material and pricing dilute it."),
                         "crew_lead_hours_saved_per_1000": ("As crew lead / 1,000", "h1000", "Additional effect of being the crew lead, over and above their own hours share.")}
    field_ratings_display = []
    if request.acc.ratings:
        field_ratings_display = [(field_metric_meta.get(r.metric_name, (r.metric_name, "pts", ""))[0], field_metric_meta.get(r.metric_name, ("", "pts", ""))[1],
                                  field_metric_meta.get(r.metric_name, ("", "", ""))[2], r) for r in field_rating_rows]
        # remaining-hours forecast accuracy for this PM: revisions they made on closed projects
    fc = {"n": 0} if not request.acc.ratings else fetch_dict("""SELECT COUNT(*) n, AVG(err) bias, AVG(ABS(err)) mae FROM (
                         SELECT r.project_id, r.remaining_hours_total + COALESCE((SELECT SUM(t.hours_total) FROM operations_timeentry t WHERE t.project_id=r.project_id AND t.source_status=1 AND t.form_type=1 AND t.work_date <= r.revised_at::date),0)
                                - p.ptt_hours_total AS err
                         FROM operations_remaininghoursrevision r JOIN core_project p ON p.id=r.project_id
                         WHERE r.revised_by_id=%s AND p.lifecycle_state='closed_stabilized' AND p.ptt_hours_total > 0) x""", [e.id])[0]
    # as field
    psorts = {"project": "p.canonical_project_number", "customer": "customer", "solution": "p.solution_class", "hours": "a.actual_hours",
              "share": "a.share_of_project_hours", "cost": "a.actual_labor_cost", "gp": "p.actual_gp_percent", "sold": "p.sold_gp_percent",
              "first": "a.first_date", "last": "a.last_date", "lead": "is_lead", "delta": "gp_delta", "hpct": "hours_pct"}
    psort = request.GET.get("sort", "-last")
    pcol = psorts.get(psort.lstrip("-"), "a.last_date")
    porder = "%s %s NULLS LAST" % (pcol, "DESC" if psort.startswith("-") else "ASC")
    crew_filter = request.GET.get("crew", "")
    crew_where = {"lead": "AND l.project_id IS NOT NULL", "member": "AND l.project_id IS NULL"}.get(crew_filter, "")
    field = fetch_dict("""SELECT p.canonical_project_number, p.display_number, p.title, p.lifecycle_state, p.actual_gp_percent, p.sold_gp_percent,
                                 a.actual_hours, a.actual_labor_cost, a.share_of_project_hours, a.first_date, a.last_date, p.solution_class, c.canonical_name customer,
                                 (l.project_id IS NOT NULL) is_lead, p.budget_labor_hours, p.ptt_hours_total, p.billed_revenue, p.project_mode_rule,
                                 (p.actual_gp_percent - p.sold_gp_percent) gp_delta,
                                 (p.ptt_hours_total / NULLIF(p.budget_labor_hours, 0)) hours_pct
                          FROM core_projectroleassignment a JOIN core_project p ON p.id=a.project_id LEFT JOIN core_customer c ON c.id=p.customer_id
                          LEFT JOIN core_projectroleassignment l ON l.project_id=a.project_id AND l.employee_id=a.employee_id AND l.role='crew_lead'
                          WHERE a.employee_id=%%s AND a.role='field' %s ORDER BY %s""" % (crew_where, porder), [e.id])
    # row tint: green = beat sold by >=2 pts, red = missed sold by >=2 pts, deeper red = job lost money (closed jobs with a sold basis)
    for r in field:
        d, g = r["gp_delta"], r["actual_gp_percent"]
        settled = r["lifecycle_state"] in Q_.CLOSED and (r["billed_revenue"] or 0) > 0
        r["row_class"] = ("rowloss" if (settled and g is not None and g < 0)
                          else "rowgood" if (settled and d is not None and d >= Decimal("0.02"))
                          else "rowbad" if (settled and d is not None and d <= Decimal("-0.02")) else "")
    crew_counts = fetch_dict("""SELECT COUNT(*) total, COUNT(l.project_id) lead, COALESCE(SUM(a.actual_hours), 0) total_hours
                                FROM core_projectroleassignment a
                                LEFT JOIN core_projectroleassignment l ON l.project_id=a.project_id AND l.employee_id=a.employee_id AND l.role='crew_lead'
                                WHERE a.employee_id=%s AND a.role='field'""", [e.id])[0]
    crew_counts["member"] = crew_counts["total"] - crew_counts["lead"]
    field_hours_by_year = fetch_dict("SELECT EXTRACT(year FROM work_date)::int y, SUM(hours_total) h, COUNT(DISTINCT project_id) projects FROM operations_timeentry WHERE employee_id=%s AND source_status=1 AND form_type=1 GROUP BY 1 ORDER BY 1", [e.id])
    field_systems = fetch_dict("SELECT system_choice k, SUM(hours_total) h FROM operations_timeentry WHERE employee_id=%s AND source_status=1 AND form_type=1 GROUP BY 1 ORDER BY 2 DESC", [e.id])
    coworkers = fetch_dict("""SELECT e2.employee_key, e2.canonical_name, COUNT(DISTINCT t1.project_id) projects, COUNT(DISTINCT t1.work_date) days
                              FROM operations_timeentry t1 JOIN operations_timeentry t2 ON t2.project_id=t1.project_id AND t2.work_date=t1.work_date AND t2.employee_id<>t1.employee_id AND t2.source_status=1 AND t2.form_type=1
                              JOIN core_employee e2 ON e2.id=t2.employee_id WHERE t1.employee_id=%s AND t1.source_status=1 AND t1.form_type=1 GROUP BY 1,2 ORDER BY days DESC LIMIT 15""", [e.id])
    rates = fetch_dict("SELECT check_date, labor_account, hours, wage_amount, payroll_tax_burden, wage_rate FROM finance_employeelaborrateobservation WHERE employee_id=%s ORDER BY check_date DESC LIMIT 26", [e.id]) if subject_rate_ok else []
    field_total = crew_counts["total_hours"]
    salesperson_codes = list(e.salesperson_codes.all())
    sold = Q_.project_rows(None, "p.salesperson_code = ANY(%s) AND NOT p.is_internal_bucket", [[s.code for s in salesperson_codes] or ["__none__"]], order="p.sl_created_at DESC") if salesperson_codes else []
    log = _person_time_log(request, e)
    ptt_upd = _ptt_updates_by(e, request.GET.get("updw", "12m"))
    return render(request, "dashboard/person_detail.html", _ctx(request, "people", e=e, log=log, managed=managed, pm_closed=pm_closed, pm_open=pm_open, pm_summary=pm_summary, pm_by_year=pm_by_year, ptt_upd=ptt_upd,
                                                                ptt_upd_windows=[("12m", "12 months"), ("24m", "24 months"), ("all", "All")],
                                                                ratings=ratings, field_ratings_display=field_ratings_display, fc=fc, field=field, field_hours_by_year=field_hours_by_year, field_systems=field_systems, coworkers=coworkers,
                                                                rates=rates, subject_rate_ok=subject_rate_ok, field_total=field_total, years=years, start=start, end=end, sold=sold, salesperson_codes=salesperson_codes,
                                                                crew_filter=crew_filter, crew_counts=crew_counts))


FIELD_SORTS = {"name": "canonical_name", "class": "classification_code", "union": "union_norm", "started": "hire_date", "rate": "ptt_base_wage",
               "div": "home_subaccount", "status": "ptt_active", "projects": "projects", "hours": "hours", "hours_recent": "hours_recent",
               "cost": "cost", "closed": "closed_projects", "gp": "hours_weighted_gp", "lead": "lead_projects", "last": "last_date",
               "rating": "rating_adj"}


def field(request):
    d, _, _ = Q_.division_scope(request)
    years, start, end = _window(request)
    w, params = Q_.div_where(d)
    g = request.GET
    sort = g.get("sort", "-hours")
    col = FIELD_SORTS.get(sort.lstrip("-"), "hours")
    order = "%s %s NULLS LAST, canonical_name" % (col, "DESC" if sort.startswith("-") else "ASC")
    emp_where, emp_params = ["TRUE"], []
    if g.get("status") == "active":
        emp_where.append("e.ptt_active IS TRUE")
    elif g.get("status") == "inactive":
        emp_where.append("e.ptt_active IS NOT TRUE")
    if g.get("union"):
        if g["union"] == "non_union":
            emp_where.append("COALESCE(e.union_code,'') = ''")
        else:
            emp_where.append("regexp_replace(upper(e.union_code),'[^0-9A-Z]','','g') = %s"); emp_params.append(g["union"])
    if g.get("class"):
        emp_where.append("e.classification_code = %s"); emp_params.append(g["class"])
    having = ["TRUE"]
    if g.get("employed") == "current":
        having.append("MAX(a.last_date) >= CURRENT_DATE - INTERVAL '2 months'")
    elif g.get("employed") == "former":
        having.append("(MAX(a.last_date) < CURRENT_DATE - INTERVAL '2 months' OR MAX(a.last_date) IS NULL)")
    rows = fetch_dict("""
        SELECT e.id employee_id, e.employee_key, e.canonical_name, e.union_code, e.is_field_hourly, regexp_replace(upper(e.union_code),'[^0-9A-Z]','','g') union_norm,
               e.classification, e.classification_code, e.classification_inferred, e.ptt_employee_type, e.ptt_active,
               e.home_subaccount, e.hire_date, e.ptt_base_wage, e.current_wage_rate,
               COUNT(DISTINCT a.project_id) projects, SUM(a.actual_hours) hours, SUM(a.actual_labor_cost) cost,
               SUM(a.actual_hours) FILTER (WHERE a.last_date >= %%s) hours_recent,
               COUNT(DISTINCT a.project_id) FILTER (WHERE p.lifecycle_state IN %%s) closed_projects,
               SUM(a.actual_hours * p.actual_gp_percent) FILTER (WHERE p.lifecycle_state IN %%s AND p.actual_gp_percent IS NOT NULL AND p.billed_revenue > 0) / NULLIF(SUM(a.actual_hours) FILTER (WHERE p.lifecycle_state IN %%s AND p.actual_gp_percent IS NOT NULL AND p.billed_revenue > 0),0) hours_weighted_gp,
               (SELECT COUNT(*) FROM core_projectroleassignment l WHERE l.employee_id=e.id AND l.role='crew_lead') lead_projects,
               MAX(a.last_date) last_date,
               NULL::numeric rating_adj, NULL::text rating_status, NULL::numeric rating_lo, NULL::numeric rating_hi,
               NULL::text rating_pair, NULL::jsonb rating_lims
        FROM core_projectroleassignment a
        JOIN core_employee e ON e.id = a.employee_id
        JOIN core_project p ON p.id = a.project_id
        WHERE a.role='field' AND %s AND %s
        GROUP BY e.id
        HAVING %s ORDER BY %s LIMIT 400""" % (w, " AND ".join(emp_where), " AND ".join(having), order),
        [start, Q_.CLOSED, Q_.CLOSED, Q_.CLOSED] + params + emp_params)
    # Ratings live in a separate database. Join by stable identity in Python;
    # an SQL join here would either leak private tables or break shared pages.
    if request.acc.ratings:
        private_ratings = {r.entity_key: r for r in EntityRating.objects.filter(
            rating_run__is_current=True, entity_type="field_employee", metric_name="field_hours_saved_per_1000")}
        for row in rows:
            rating = private_ratings.get(str(row["employee_id"]))
            if rating:
                row.update(rating_adj=rating.adjusted_effect, rating_status=rating.publication_status,
                    rating_lo=rating.interval_low, rating_hi=rating.interval_high,
                    rating_pair=rating.detail.get("pair_with"), rating_lims=rating.limitations)
        if col == "rating_adj":
            rows.sort(key=lambda row: (row["rating_adj"] is None, -(row["rating_adj"] or 0) if sort.startswith("-") else (row["rating_adj"] or 0)))
    for r in rows:
        r["rate_ok"] = rate_visible(request.acc, r.get("is_field_hourly"))
        if not request.acc.ratings:
            for k in list(r):
                if k.startswith("rating_"):
                    r[k] = None
        if isinstance(r.get("rating_lims"), str):
            try:
                r["rating_lims"] = json.loads(r["rating_lims"])
            except ValueError:
                r["rating_lims"] = []
    unions = fetch_dict("""SELECT CASE WHEN COALESCE(e.union_code,'')='' THEN 'non_union' ELSE regexp_replace(upper(e.union_code),'[^0-9A-Z]','','g') END k, COUNT(DISTINCT e.id) c
                           FROM core_employee e WHERE EXISTS (SELECT 1 FROM core_projectroleassignment a WHERE a.employee_id=e.id AND a.role='field') GROUP BY 1 ORDER BY c DESC""")
    classes = fetch_dict("""SELECT e.classification_code k, COUNT(DISTINCT e.id) c FROM core_employee e
                            WHERE e.classification_code <> '' AND EXISTS (SELECT 1 FROM core_projectroleassignment a WHERE a.employee_id=e.id AND a.role='field') GROUP BY 1 ORDER BY c DESC""")
    return render(request, "dashboard/field.html", _ctx(request, "field", rows=rows, years=years, start=start, end=end, sort=sort, g=g,
                                                        unions=unions, classes=classes))


# ============================================================================ customers
def customers(request):
    d, _, _ = Q_.division_scope(request)
    years, start, end = _window(request)
    rows = Q_.breakdown(d, start, end, "c.sl_customer_id", "c.canonical_name || ' · ' || COALESCE(NULLIF(c.market_sector,''),'—')", "LEFT JOIN core_customer c ON c.id=p.customer_id", limit=80, order="revenue DESC")
    sectors = Q_.breakdown(d, start, end, "COALESCE(NULLIF(c.market_sector,''),'(unassigned)')", None, "LEFT JOIN core_customer c ON c.id=p.customer_id", limit=20, order="revenue DESC")
    ratings = {r.entity_key: r for r in EntityRating.objects.filter(rating_run__is_current=True, entity_type="customer", metric_name="final_gp_pct")}
    sector_ratings = {r.entity_key: r for r in EntityRating.objects.filter(rating_run__is_current=True, entity_type="sector", metric_name="final_gp_pct")}
    cid = {c.sl_customer_id: str(c.id) for c in Customer.objects.filter(sl_customer_id__in=[r["k"] for r in rows if r["k"]])}
    open_by_cust = {r["k"]: r for r in fetch_dict("SELECT c.sl_customer_id k, COUNT(*) n, SUM(p.contract_value) cv FROM core_project p JOIN core_customer c ON c.id=p.customer_id WHERE p.lifecycle_state IN %s GROUP BY 1", [Q_.OPEN])}
    for r in rows:
        r["rating"] = ratings.get(cid.get(r["k"]))
        r["open"] = open_by_cust.get(r["k"])
        r["preservation"] = (r["final_pct_with"] - r["sold_pct"]) if (r["final_pct_with"] is not None and r["sold_pct"] is not None) else None
    for r in sectors:
        r["rating"] = sector_ratings.get(r["k"])
    return render(request, "dashboard/customers.html", _ctx(request, "customers", rows=rows, sectors=sectors, years=years, start=start, end=end))


def customer_detail(request, cust):
    c = get_object_or_404(Customer, sl_customer_id=cust)
    rows = Q_.project_rows(None, "p.customer_id = %s AND NOT p.is_internal_bucket", [c.id], order="p.sl_created_at DESC")
    closed = [r for r in rows if r["lifecycle_state"] in Q_.CLOSED and r["project_mode_rule"] not in Q_.REAL_MODES_EXCLUDED]
    open_ = [r for r in rows if r["lifecycle_state"] in Q_.OPEN]
    rev = sum((r["billed_revenue"] or 0) for r in closed); gp = sum((r["actual_gp_dollars"] or 0) for r in closed)
    by_year = fetch_dict("SELECT EXTRACT(year FROM close_date)::int y, COUNT(*) n, SUM(billed_revenue) rev, SUM(actual_gp_dollars) gp, SUM(ptt_hours_total) hours FROM core_project WHERE customer_id=%s AND lifecycle_state IN %s AND project_mode_rule NOT IN %s AND close_date IS NOT NULL GROUP BY 1 ORDER BY 1", [c.id, Q_.CLOSED, Q_.REAL_MODES_EXCLUDED])
    by_pm = fetch_dict("SELECT e.employee_key, e.canonical_name, COUNT(*) n, SUM(p.billed_revenue) rev, SUM(p.actual_gp_dollars) gp FROM core_project p JOIN core_employee e ON e.id=p.project_manager_id WHERE p.customer_id=%s AND p.lifecycle_state IN %s AND p.project_mode_rule NOT IN %s GROUP BY 1,2 ORDER BY rev DESC", [c.id, Q_.CLOSED, Q_.REAL_MODES_EXCLUDED])
    by_solution = fetch_dict("SELECT solution_class k, COUNT(*) n, SUM(billed_revenue) rev, SUM(actual_gp_dollars) gp FROM core_project WHERE customer_id=%s AND lifecycle_state IN %s AND project_mode_rule NOT IN %s GROUP BY 1 ORDER BY rev DESC", [c.id, Q_.CLOSED, Q_.REAL_MODES_EXCLUDED])
    ratings = list(EntityRating.objects.filter(rating_run__is_current=True, entity_type="customer", entity_key=str(c.id)))
    # Projects card: the WIP by Job table for this customer's jobs, to date (dashboard/job_table.py) — WIP as it
    # stands, result measured from each job's start (Adj. GP = GP to date + WIP), Active / Completed toggles
    from .job_table import lifetime_table
    jt = lifetime_table("p.customer_id = %s AND NOT p.is_internal_bucket", [c.id], identity=("number", "division", "pm", "state", "mode", "created", "closed"),
                        sort=request.GET.get("sort"), today=timezone.localdate(), margins=request.acc.margins, table_id="custjobs")
    ar = pay = None
    can_customer_payments = request.acc.can_view("customer_payments")
    customer_payment_links = {}
    if request.acc.finance:      # Outstanding invoices card (finance data; the AR page and its JSON panel need the same cap)
        from .views_ar import BOOK_LABEL, customer_open_ar
        ar = customer_open_ar(c.sl_customer_id, timezone.localdate())
    if can_customer_payments:
        from apps.analytics import customer_payments
        # The card and its JSON endpoint follow the SAME saved page rule. Owner
        # opened customer_payments to customers.view on September 17; a separate
        # finance.view check here would silently disregard that approved change.
        # Payment history card: every payment since 2013 with days-to-pay / retainage logic (docs/06); the table itself
        # is paged JSON from views_customer.customer_payments
        pay = customer_payments.page_context(c.sl_customer_id, timezone.localdate())
        # Opening payment history does not grant its linked Finance, project,
        # employee or 010 pages. Render those references as plain text otherwise.
        customer_payment_links = {
            label: request.acc.can_view(view) for label, view in (
                ("project", "project_detail"), ("person", "person_detail"),
                ("order", "sales010_order_detail"), ("payday", "finance_payments"),
            )
        }
    s010 = None
    if request.acc.sales010:     # 010 hardware quotes & orders card (ChannelOnline data; same cap as the 010 pages)
        from .views_sales_pipeline import customer_sales010
        s010 = customer_sales010(c.sl_customer_id, timezone.localdate(), timezone.now())
    return render(request, "dashboard/customer_detail.html", _ctx(request, "customers", c=c, closed=closed, open=open_, rev=rev, gp=gp, gp_pct=(gp / rev) if rev else None,
                                                                  by_year=by_year, by_pm=by_pm, by_solution=by_solution, ratings=ratings, jt=jt,
                                                                  ar=ar, book_label=BOOK_LABEL if ar else {}, pay=pay,
                                                                  can_customer_payments=can_customer_payments,
                                                                  customer_payment_links=customer_payment_links, s010=s010))


# ============================================================================ ratings
GP_METRICS = [("final_gp_pct", "Final GP % vs expected", "pts"), ("margin_preservation", "Margin preservation vs expected", "pts")]
EST_METRICS = GP_METRICS + [("bid_accuracy", "Bid accuracy vs expected (final GP − bid margin)", "pts")]
FIELD_METRICS = [
    ("field_hours_saved_per_1000", "Hours saved per 1,000 budget hours", "h1000"),
    ("field_cost_saved_per_1000", "Labor $ saved per $1,000 labor budget", "d1000"),
    ("field_margin_preservation", "Margin preservation vs expected", "pts"),
    ("crew_lead_hours_saved_per_1000", "Crew-lead effect (hours / 1,000)", "h1000"),
]
ENTITY_METRICS = OrderedDict([
    ("project_manager", GP_METRICS), ("customer", GP_METRICS), ("sector", GP_METRICS), ("solution", GP_METRICS),
    ("project_mode", GP_METRICS), ("salesperson", GP_METRICS), ("division_head_era", GP_METRICS),
    ("estimator", EST_METRICS),
    ("field_employee", FIELD_METRICS),
])


def ratings(request):
    run = RatingRun.objects.filter(is_current=True).first()
    entity = request.GET.get("entity", "project_manager")
    if entity not in ENTITY_METRICS:
        entity = "project_manager"
    metrics = ENTITY_METRICS[entity]
    metric = request.GET.get("metric", metrics[0][0])
    if metric not in [m[0] for m in metrics]:
        metric = metrics[0][0]
    unit = {m[0]: m[2] for m in metrics}[metric]
    rows = list(EntityRating.objects.filter(rating_run=run, entity_type=entity, metric_name=metric)) if run else []
    rank = {"publishable": 0, "provisional": 1, "not_identifiable": 2, "insufficient": 3}
    rows.sort(key=lambda r: (rank.get(r.publication_status, 9), -(r.adjusted_effect or 0)))
    unions = classes = []
    g = request.GET
    if entity == "field_employee":
        # same filters as the Field Crew page, applied to the rated employees
        import re as _re
        emp = {str(e.id): e for e in Employee.objects.filter(id__in=[int(r.entity_key) for r in rows if r.entity_key.isdigit()])}
        last_work = {str(r["employee_id"]): r["l"] for r in fetch_dict(
            "SELECT employee_id, MAX(last_date) l FROM core_projectroleassignment WHERE role='field' GROUP BY employee_id")}
        cutoff = timezone.localdate() - timedelta(days=61)

        def _norm_union(u):
            return _re.sub(r"[^0-9A-Z]", "", (u or "").upper())

        def _keep(r):
            e = emp.get(r.entity_key)
            if e is None:
                return not any(g.get(k) for k in ("status", "employed", "union", "class"))
            if g.get("status") == "active" and not e.ptt_active:
                return False
            if g.get("status") == "inactive" and e.ptt_active:
                return False
            lw = last_work.get(r.entity_key)
            if g.get("employed") == "current" and not (lw and lw >= cutoff):
                return False
            if g.get("employed") == "former" and lw and lw >= cutoff:
                return False
            if g.get("union"):
                nu = _norm_union(e.union_code)
                if g["union"] == "non_union":
                    if nu:
                        return False
                elif nu != g["union"]:
                    return False
            if g.get("class") and e.classification_code != g["class"]:
                return False
            if g.get("pub") and r.publication_status != g["pub"]:
                return False
            return True

        rows = [r for r in rows if _keep(r)]
        unions = fetch_dict("""SELECT CASE WHEN COALESCE(e.union_code,'')='' THEN 'non_union' ELSE regexp_replace(upper(e.union_code),'[^0-9A-Z]','','g') END k, COUNT(DISTINCT e.id) c
                               FROM core_employee e WHERE EXISTS (SELECT 1 FROM core_projectroleassignment a WHERE a.employee_id=e.id AND a.role='field') GROUP BY 1 ORDER BY c DESC""")
        classes = fetch_dict("""SELECT e.classification_code k, COUNT(DISTINCT e.id) c FROM core_employee e
                                WHERE e.classification_code <> '' AND EXISTS (SELECT 1 FROM core_projectroleassignment a WHERE a.employee_id=e.id AND a.role='field') GROUP BY 1 ORDER BY c DESC""")
    counts = {}
    if run:
        for r in EntityRating.objects.filter(rating_run=run).values("entity_type").annotate(n=Count("entity_key", distinct=True)):
            counts[r["entity_type"]] = r["n"]
    entity_labels = OrderedDict([("project_manager", "Project Managers"), ("customer", "Customers"), ("sector", "Market Sectors"), ("solution", "Solutions"),
                                 ("project_mode", "Project Modes"), ("salesperson", "Salespeople (commissioned)"), ("division_head_era", "Division-Head Era"),
                                 ("estimator", "Estimators"), ("field_employee", "Field Crew")])
    link_prefix = {"project_manager": "person", "customer": "customer", "field_employee": "person", "estimator": "person"}.get(entity)
    key_map = {}
    if entity in ("project_manager", "field_employee", "estimator"):
        key_map = {str(e.id): e.employee_key for e in Employee.objects.filter(id__in=[int(r.entity_key) for r in rows if r.entity_key.isdigit()])}
    elif entity == "customer":
        key_map = {str(c.id): c.sl_customer_id for c in Customer.objects.filter(id__in=[int(r.entity_key) for r in rows if r.entity_key.isdigit()])}
    field_summary = (run.metrics or {}).get("field_crew", {}) if run else {}
    return render(request, "dashboard/ratings.html", _ctx(request, "ratings", run=run, rows=rows, entity=entity, metric=metric, unit=unit, counts=counts,
                                                          entity_labels=entity_labels, link_prefix=link_prefix, key_map=key_map,
                                                          metrics=[(m[0], m[1]) for m in metrics], field_summary=field_summary,
                                                          unions=unions, classes=classes, g=request.GET))


# ============================================================================ data quality / refresh
def data_quality(request):
    runs = list(IngestionRun.objects.filter(source_system="local").order_by("-created_at")[:15])
    issue_summary = list(DataQualityIssue.objects.filter(status="open").values("code", "severity").annotate(n=Count("id")).order_by("severity", "-n"))
    code = request.GET.get("code")
    issues = list(DataQualityIssue.objects.filter(status="open", code=code).select_related("project").order_by("-updated_at")[:300]) if code else []
    watermarks = list(SourceWatermark.objects.all().order_by("source_system", "query_name"))
    identity = fetch_dict("""SELECT COUNT(*) total, COUNT(*) FILTER (WHERE ptt_project_pk IS NOT NULL) matched, COUNT(*) FILTER (WHERE ptt_project_pk IS NULL) sl_only,
                                    COUNT(*) FILTER (WHERE is_template_or_void) templates, COUNT(*) FILTER (WHERE is_internal_bucket) internal FROM core_project""")[0]
    checksum = fetch_dict("SELECT COUNT(*) n FROM ingestion_dataqualityissue WHERE code='pjtran_checksum_mismatch' AND status='open'")[0]["n"]
    hours_check = fetch_dict("""SELECT COUNT(*) n, SUM(ptt_hours_total) ptt, SUM(actual_labor_hours_sl) sl,
                                       COUNT(*) FILTER (WHERE ABS(COALESCE(ptt_hours_total,0)-COALESCE(actual_labor_hours_sl,0)) < 0.5) exact,
                                       COUNT(*) FILTER (WHERE GREATEST(COALESCE(ptt_hours_total,0),COALESCE(actual_labor_hours_sl,0)) > 0 AND ABS(COALESCE(ptt_hours_total,0)-COALESCE(actual_labor_hours_sl,0))/GREATEST(COALESCE(ptt_hours_total,0),COALESCE(actual_labor_hours_sl,0)) > 0.05) off5
                                FROM core_project WHERE lifecycle_state IN %s AND (ptt_hours_total > 0 OR actual_labor_hours_sl > 0) AND close_date >= '2021-01-01'""", [Q_.CLOSED])[0]
    counts = fetch_dict("""SELECT (SELECT COUNT(*) FROM core_project) projects, (SELECT COUNT(*) FROM finance_projectfinancialtransaction) transactions,
                                  (SELECT COUNT(*) FROM operations_timeentry) entries, (SELECT COUNT(*) FROM operations_timeentry WHERE source_status=2) removed_entries,
                                  (SELECT COUNT(*) FROM finance_projectaccountsummary WHERE is_current) summary_rows, (SELECT COUNT(*) FROM operations_remaininghoursrevision) revisions,
                                  (SELECT COUNT(*) FROM core_employee) employees, (SELECT COUNT(*) FROM core_customer) customers,
                                  (SELECT COUNT(*) FROM finance_projectcommercialchange) changes, (SELECT COUNT(*) FROM analytics_projectprediction WHERE as_of_date=(SELECT MAX(as_of_date) FROM analytics_projectprediction)) predictions""")[0]
    requests_ = list(RefreshRequest.objects.order_by("-created_at")[:5])
    # contract values the app uses that differ from SL (apps/analytics/contract_value.py) — the active log rows
    cv_all = fetch_dict("""SELECT a.basis, a.sl_value, a.effective_value, a.effective_from, a.explanation, p.canonical_project_number cpn, p.display_number, p.title, p.lifecycle_state
                           FROM finance_contractvalueadjustment a JOIN core_project p ON p.id = a.project_id
                           WHERE a.superseded_at IS NULL AND a.basis <> 'sl' ORDER BY ABS(COALESCE(a.effective_value,0) - COALESCE(a.sl_value,0)) DESC""")
    cv_by_basis = {}
    for r in cv_all:
        b = cv_by_basis.setdefault(r["basis"], {"basis": r["basis"], "n": 0, "delta": Decimal(0)})
        b["n"] += 1; b["delta"] += (r["effective_value"] or 0) - (r["sl_value"] or 0)
    cv_show_all = request.GET.get("cv") == "all"
    cv_adj = cv_all if cv_show_all else [r for r in cv_all if r["lifecycle_state"] in Q_.OPEN][:15]
    cv_tot = {"n": len(cv_all), "delta": sum(((r["effective_value"] or 0) - (r["sl_value"] or 0)) for r in cv_all),
              "open": sum(1 for r in cv_all if r["lifecycle_state"] in Q_.OPEN), "shown": len(cv_adj), "all": cv_show_all,
              "by_basis": sorted(cv_by_basis.values(), key=lambda b: -b["n"])}
    return render(request, "dashboard/data_quality.html", _ctx(request, "dq", runs=runs, issue_summary=issue_summary, issues=issues, code=code, watermarks=watermarks, identity=identity,
                                                               checksum=checksum, hours_check=hours_check, counts=counts, requests=requests_, cv_adj=cv_adj, cv_tot=cv_tot))


@require_POST
def refresh(request):
    if IngestionRun.objects.filter(source_system="local", status="running").exists() or RefreshRequest.objects.filter(status__in=["queued", "running"]).exists():
        messages.warning(request, "A refresh is already running.")
        return redirect("dashboard:data_quality")
    req = RefreshRequest.objects.create(scope=request.POST.get("scope", "all"), status="queued")
    log_dir = settings.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("refresh_%s.log" % timezone.now().strftime("%Y%m%d_%H%M%S"))
    args = [sys.executable, str(settings.BASE_DIR / "manage.py"), "refresh_all", "--trigger", "manual", "--request-id", str(req.id)]
    if request.POST.get("with_ratings"):
        args.append("--ratings")
    with open(log_path, "ab") as fh:
        subprocess.Popen(args, cwd=settings.BASE_DIR, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    messages.info(request, "Refresh started (request #%d). This page will show progress." % req.id)
    return redirect("dashboard:data_quality")


def refresh_status(request):
    running = IngestionRun.objects.filter(source_system="local", status="running").order_by("-created_at").first()
    last = IngestionRun.objects.filter(source_system="local").exclude(status="running").order_by("-created_at").first()
    def ser(r):
        return {"id": r.id, "status": r.status, "started_at": r.started_at.isoformat() if r.started_at else None, "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "steps": [{"step": s.get("step"), "seconds": s.get("seconds"), "error": s.get("error")} for s in r.steps]} if r else None
    return JsonResponse({"running": ser(running), "last": ser(last)})


def about(request):
    return render(request, "dashboard/about.html", _ctx(request, "about"))


# ============================================================================ financial reports
FIN_BUCKETS = [("current", "Current"), ("d30", "1–30"), ("d60", "31–60"), ("d90", "61–90"), ("over90", ">90")]
FIN_AR_BOOKS = [("project", "Project billings"), ("so1", "SO1 orders"), ("so2", "SO2 orders"), ("rma", "RMA"),
                ("other", "Other / unassigned"), ("credit", "Credits & unapplied payments")]


def _fin_monthly_pnl(months=13):
    """Trailing monthly Revenue / COGS / Overhead from GLAccountBalance (natural sign: both positive)."""
    from apps.finance.models import GLAccountBalance
    today = timezone.localdate()
    fys = {str(today.year), str(today.year - 1), str(today.year - 2)}
    agg = defaultdict(lambda: {"rev": Decimal(0), "cogs": Decimal(0), "ovh": Decimal(0)})
    for r in GLAccountBalance.objects.filter(fiscal_year__in=fys, acct_type__in=["3I", "4E"]).values("acct", "acct_type", "fiscal_year", *["p%02d" % i for i in range(12)]):
        for m in range(12):
            key = "%s%02d" % (r["fiscal_year"], m + 1)
            v = r["p%02d" % m] or Decimal(0)
            if r["acct_type"] == "3I":
                if r["acct"] not in ("40100",):
                    agg[key]["rev"] += v
            elif r["acct"].startswith("5") or r["acct"] in ("60000", "60005"):
                agg[key]["cogs"] += v
            elif r["acct"][0] in ("6", "7"):
                agg[key]["ovh"] += v
    out = []
    y, m = today.year, today.month
    for _ in range(months):
        key = "%04d%02d" % (y, m)
        a = agg.get(key, {"rev": Decimal(0), "cogs": Decimal(0), "ovh": Decimal(0)})
        out.append({"p": key, "rev": _f(a["rev"]), "cogs": _f(a["cogs"]), "ovh": _f(a["ovh"])})
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _daily_snapshot_for(request, latest):
    """The snapshot row to show: ?date=YYYY-MM-DD (the nearest earlier stored day when that exact day has
    no row) or the latest live pull. Returns (row, nav) — nav carries the adjacent stored dates, the
    available range and a notice when the requested day was substituted."""
    from apps.finance.models import DailyFinanceSnapshot
    raw = (request.GET.get("date") or "").strip()
    notice = None
    snap = latest
    if raw:
        try:
            want = date.fromisoformat(raw)
        except ValueError:
            want, notice = None, "Unrecognised date %r — showing the latest snapshot." % raw
        if want:
            row = DailyFinanceSnapshot.objects.filter(snapshot_date=want).first()
            if not row:
                row = DailyFinanceSnapshot.objects.filter(snapshot_date__lt=want).order_by("-snapshot_date").first()
                if want > latest.snapshot_date:
                    row = latest
                elif row:
                    notice = "No snapshot is stored for %s — showing %s, the nearest earlier day." % (want.strftime("%a %b %-d, %Y"), row.snapshot_date.strftime("%a %b %-d, %Y"))
                else:
                    notice = "Nothing is stored on or before %s — showing the latest snapshot." % want.strftime("%a %b %-d, %Y")
            snap = row or latest
    prev_row = DailyFinanceSnapshot.objects.filter(snapshot_date__lt=snap.snapshot_date).order_by("-snapshot_date").only("snapshot_date").first()
    next_row = DailyFinanceSnapshot.objects.filter(snapshot_date__gt=snap.snapshot_date).order_by("snapshot_date").only("snapshot_date").first()
    first = DailyFinanceSnapshot.objects.order_by("snapshot_date").only("snapshot_date").first()
    return snap, {"prev": prev_row.snapshot_date if prev_row else None, "next": next_row.snapshot_date if next_row else None,
                  "first": first.snapshot_date if first else None, "last": latest.snapshot_date, "notice": notice,
                  "is_latest": snap.pk == latest.pk}


def _daily_bs(snap, latest):
    """Balance-sheet / cash figures for a snapshot row: stored when the row was a live pull, else rolled
    back from today's ledger when the day is inside the GL posting window, else None."""
    from apps.analytics.finance_snapshot import gl_rollback_as_of
    if snap.assets is not None:
        return {k: getattr(snap, k) for k in ("assets", "liabilities", "equity", "net_income_ytd", "working_capital", "cash_checking",
                                              "cash_payroll", "cash_mma", "cash_petty", "credit_line_gl", "inventory_gl")} | {
            "identity_gap": (snap.detail or {}).get("identity_gap"), "source": "live", "pnl": None, "pnl_days": None, "prev_business_day": snap.prev_business_day}
    if snap.snapshot_date >= latest.snapshot_date:
        return None
    return gl_rollback_as_of(snap.snapshot_date, latest.snapshot_date)


def finance_daily(request):
    from datetime import datetime
    from apps.finance.models import BankFigureEntry, DailyFinanceSnapshot
    from apps.analytics.finance_snapshot import page_extras
    latest = DailyFinanceSnapshot.objects.filter(reconstructed=False).order_by("-snapshot_date").first()
    if not latest:
        return render(request, "dashboard/finance_daily.html", _ctx(request, "finance-daily", snap=None))
    snap, nav = _daily_snapshot_for(request, latest)
    is_latest = nav["is_latest"]
    prev = DailyFinanceSnapshot.objects.filter(snapshot_date__lt=snap.snapshot_date).order_by("-snapshot_date").first()
    bs = _daily_bs(snap, latest)
    prev_bs = _daily_bs(prev, latest) if (prev and bs) else None
    Z = Decimal(0)

    def delta(field):
        a, b = getattr(snap, field), getattr(prev, field, None) if prev else None
        return (a - b) if (a is not None and b is not None) else None

    deltas = {f: delta(f) for f in ("ar_total", "ar_over90", "ap_total", "wip_net")}
    for f in ("assets", "liabilities", "net_income_ytd"):
        a, b = (bs or {}).get(f), (prev_bs or {}).get(f)
        deltas[f] = (a - b) if (a is not None and b is not None) else None

    def tier(value, bands):
        """bands: list of (upper_bound_exclusive_or_None, label, css). css: low=green, ok=blue, moderate=amber, critical=red."""
        for bound, label, css in bands:
            if bound is None or value < bound:
                return {"label": label, "css": css}
        return {"label": "?", "css": ""}
    # ratios (her formulas; see docs/finance_daily_snapshot_plan.md)
    A, L, E = (bs or {}).get("assets") or Z, (bs or {}).get("liabilities") or Z, (bs or {}).get("equity") or Z
    ratios = {
        "debt_to_assets": (L / A) if A else Z, "equity_ratio": ((A - L) / A) if A else Z,
        "leverage": (A / E) if E else Z,
        "over90_pct": ((snap.ar_over90 or 0) / snap.ar_total) if snap.ar_total else Z,
        "ap_ar": ((snap.ap_total or 0) / snap.ar_total) if snap.ar_total else Z,
        "net_exposure": (snap.ar_total or 0) - (snap.ap_total or 0),
    }
    ratio_status = {
        "debt_to_assets": tier(float(ratios["debt_to_assets"]), [(0.4, "good", "low"), (0.55, "normal", "ok"), (0.7, "elevated", "moderate"), (None, "high", "critical")]),
        "equity_ratio": tier(float(ratios["equity_ratio"]), [(0.3, "weak", "critical"), (0.45, "adequate", "moderate"), (0.55, "solid", "ok"), (None, "strong", "low")]),
        "leverage": tier(float(ratios["leverage"]), [(2, "low risk", "low"), (2.5, "manageable", "ok"), (3, "moderate", "moderate"), (None, "high", "critical")]),
        "over90_pct": tier(float(ratios["over90_pct"]), [(0.10, "good", "low"), (0.20, "monitor", "moderate"), (None, "high", "critical")]),
        "ap_ar": tier(float(ratios["ap_ar"]), [(0.6, "good", "low"), (0.75, "watch", "ok"), (0.9, "tight", "moderate"), (None, "strained", "critical")]),
    }
    # ---- WIP: change windows (day / week-to-date / month-to-date) + contributors + movers ----
    ws = _wip_state(snap, live_rows=is_latest)
    wip_live, wip_baselines, wip_changes, baseline = ws["live"], ws["baselines"], ws["changes"], ws["baseline"]
    wip_meta = snap.detail.get("wip") or {"jobs": len(wip_live), "under_jobs": sum(1 for j in wip_live if j["wip"] > 0),
                                          "over_jobs": sum(1 for j in wip_live if j["wip"] < 0), "net_cost": None}
    from apps.analytics.finance_wip import service_agreement_summary
    wip_sa = service_agreement_summary()   # the open service agreements the WIP total leaves out (today's set, docs/07 §3)
    # WIP-adjusted net income. The accountants book WIP into COGS at every month-end (50701,
    # docs/07 §3), so YTD net income already carries the earned view for closed months; only the
    # months not yet closed get PCA's own snapshot ΔWIP (adding the full YTD swing double counted).
    from apps.analytics.divisional_pnl import Model as _PnlModel
    _pm = _PnlModel([snap.snapshot_date.year, snap.snapshot_date.year - 1], today=snap.snapshot_date)
    ni_adjusted = wip_ytd_delta = wip_close_date = None
    wip_open_parts = []
    ni_ytd = (bs or {}).get("net_income_ytd")
    _open_ytd = []
    if ni_ytd is not None:
        _cur_ym = "%04d%02d" % (snap.snapshot_date.year, snap.snapshot_date.month)
        _open_ytd = [m for m in sorted(_pm.open_months) if m[:4] == str(snap.snapshot_date.year) and m <= _cur_ym]
        _deltas = [_pm.wip.delta(None, m) for m in _open_ytd]
        if all(v is not None for v in _deltas):
            wip_ytd_delta = sum(_deltas, Decimal(0))
            ni_adjusted = ni_ytd + wip_ytd_delta
            # the chip is "ΔWIP since the last accountant close": name that close and show each open
            # month's part, because month-end invoices (dated into the old month) and the PMs' month-end
            # % updates (keyed in the new month) can land on opposite sides of a month-end and see-saw
            from apps.analytics.divisional_pnl import month_end as _month_end, prior_month as _prior_month
            wip_close_date = _month_end(_prior_month(_open_ytd[0])) if _open_ytd else None
            wip_open_parts = [{"ym": m, "label": date(int(m[:4]), int(m[4:]), 1).strftime("%b") + (" MTD" if m == _cur_ym else ""), "delta": d}
                              for m, d in zip(_open_ytd, _deltas)]
    open_months_ytd = _open_ytd
    net_abs = abs(snap.wip_net or 1) or 1
    wip_contributors = [{"cpn": j["canonical_project_number"], "display": j["display_number"], "title": j["title"],
                         "customer": j["customer"], "division": j["division"], "wip": j["wip"],
                         "pct": j["pm_pct"], "billed": j["billed"], "cv": j["cv"],
                         "share": float(j["wip"]) / float(net_abs)}
                        for j in wip_live[:10]]
    now_map = proj_meta = ws["now_map"]
    movers = {}
    for key in ("day", "wtd", "mtd"):  # movers windows (ytd shown in chips only)
        for cpn, x in (ws["movement"].get(key) or {}).items():
            m = movers.setdefault(cpn, {"cpn": cpn, "wip": float(now_map[cpn]["wip"]) if cpn in now_map else 0.0})
            m[key] = x["d"]
            m[key + "_billed"] = x["billed"]
            m[key + "_earned"] = x["earned"]
    # movement now also lists jobs whose WIP stayed flat while earned and billed moved alike — not movers here
    mover_rows = sorted([m for m in movers.values() if any(m.get(k) for k in ("day", "wtd", "mtd"))],
                        key=lambda m: -abs(m.get("mtd", m.get("wtd", m.get("day", 0)) or 0) or 0))[:10]
    for m in mover_rows:
        meta = proj_meta.get(m["cpn"])
        if meta:
            m["display"], m["title"], m["customer"] = meta["display_number"], meta["title"], meta["customer"]
        else:
            r = fetch_dict("SELECT display_number, title, (SELECT canonical_name FROM core_customer c WHERE c.id=p.customer_id) customer FROM core_project p WHERE canonical_project_number=%s", [m["cpn"]])
            m["display"], m["title"], m["customer"] = (r[0]["display_number"], r[0]["title"], r[0]["customer"]) if r else (m["cpn"], "", "")
            m["closed_or_excluded"] = True

    # AR matrix rows from the snapshot detail (reconciles to ar_total by construction); days without the
    # by-book split (reconstructed history) show the stored bucket totals as one row
    matrix = snap.detail.get("matrix", {})
    ar_rows = []
    if matrix:
        for key, label in FIN_AR_BOOKS:
            cells = matrix.get(key, {})
            cells = {b: Decimal(str(cells.get(b, 0))) for b, _l in FIN_BUCKETS}
            total = sum(cells.values())
            if key in ("rma", "other") and not any(cells.values()):
                continue
            ar_rows.append({"key": key, "label": label, "cells": cells, "total": total,
                            "pct": (total / snap.ar_total) if snap.ar_total else None})
    elif snap.ar_total is not None:
        cells = {"current": snap.ar_current or Z, "d30": snap.ar_d30 or Z, "d60": snap.ar_d60 or Z, "d90": snap.ar_d90 or Z, "over90": snap.ar_over90 or Z}
        ar_rows.append({"key": "all", "label": "All books (split by book not captured for this day)", "cells": cells, "total": sum(cells.values()), "pct": Decimal(1)})
    ar_col_totals = {b: sum((r["cells"][b] for r in ar_rows), Decimal(0)) for b, _l in FIN_BUCKETS}
    # document-list sections: stored with the snapshot (detail["extras"]) since 2026-09-01; the latest
    # live row computes them fresh when it predates that; older days show nothing rather than today's lists
    extras = snap.detail.get("extras")
    if extras is None and is_latest:
        extras = page_extras(snap.snapshot_date)
    ap_type_labels = {"VO": "Vouchers", "PP": "Prepayments", "AD": "Debit adjustments"}
    if extras:
        ap_matrix = [{"key": t, "label": ap_type_labels.get(t, t), "cells": {b: Decimal(str(cells.get(b, 0))) for b, _l in FIN_BUCKETS}}
                     for t, cells in extras["ap_matrix"].items()]
        for r in ap_matrix:
            r["total"] = sum(r["cells"].values())
        ap_matrix.sort(key=lambda r: -r["total"])
        top_over90 = [dict(r, oldest=date.fromisoformat(r["oldest"]) if r["oldest"] else None) for r in extras["top_over90"]]
        ap_due = [dict(r, due_date=date.fromisoformat(r["due_date"])) for r in extras["ap_due"]]
        ap_due_from, ap_due_to = date.fromisoformat(extras["ap_due_from"]), date.fromisoformat(extras["ap_due_to"])
        largest_open = [dict(r, doc_date=date.fromisoformat(r["doc_date"]) if r["doc_date"] else None, due_date=date.fromisoformat(r["due_date"]) if r["due_date"] else None)
                        for r in extras["largest_open"]]
        pending_count = extras["pending_count"]
        # owner-family disclosure lines (apps/core/related_parties); older stored snapshots predate them
        top_over90_note = extras.get("top_over90_note", "")
        largest_open_note = extras.get("largest_open_note", "")
    else:
        ap_matrix, top_over90, ap_due, largest_open, pending_count = None, None, None, None, None
        top_over90_note = largest_open_note = ""
        ap_due_from = ap_due_to = None
        if snap.ap_total is not None:
            cells = {"current": snap.ap_current or Z, "d30": snap.ap_d30 or Z, "d60": snap.ap_d60 or Z, "d90": snap.ap_d90 or Z, "over90": snap.ap_over90 or Z}
            ap_matrix = [{"key": "all", "label": "All types (split by type not captured for this day)", "cells": cells, "total": sum(cells.values())}]
    ap_col_totals = {b: sum((r["cells"][b] for r in (ap_matrix or [])), Decimal(0)) for b, _l in FIN_BUCKETS}
    ap_due_total = sum((r["amt"] for r in (ap_due or [])), 0)
    # trend series
    trend = [{"d": r["snapshot_date"].isoformat(), "over90": _f(r["ar_over90"]), "ar": _f(r["ar_total"]), "ap": _f(r["ap_total"]),
              "wip": _f(r["wip_net"]), "wip_u": _f(r["wip_underbilled"]), "wip_o": _f(r["wip_overbilled"]), "rec": r["reconstructed"]}
             for r in fetch_dict("SELECT snapshot_date, ar_over90, ar_total, ap_total, wip_net, wip_underbilled, wip_overbilled, reconstructed FROM finance_dailyfinancesnapshot ORDER BY snapshot_date")]
    history = list(DailyFinanceSnapshot.objects.filter(snapshot_date__lte=snap.snapshot_date + timedelta(days=5)).order_by("-snapshot_date")[:30])
    if is_latest:
        bank = BankFigureEntry.objects.order_by("-entry_date").first()
    else:
        bank = BankFigureEntry.objects.filter(entry_date=snap.snapshot_date).first()
    bank_stale = bool(bank) and is_latest and (timezone.localdate() - bank.entry_date).days > 1
    monthly = _fin_monthly_pnl()
    pnl_days = snap.detail.get("pnl_days") if snap.detail.get("pnl_days") is not None else (bs or {}).get("pnl_days")   # None = not captured
    charts = {"trend": trend, "pnl_days": pnl_days, "monthly": monthly, "selected": snap.snapshot_date.isoformat()}
    cash_total = sum((v for v in ((bs or {}).get("cash_checking"), (bs or {}).get("cash_payroll"), (bs or {}).get("cash_mma"), (bs or {}).get("cash_petty")) if v is not None), Decimal(0)) if bs else None

    def pnl(rev, cogs, ovh, rev_pd, cost_pd):
        rev, cogs, ovh = rev or 0, cogs or 0, ovh or 0
        return {"rev": rev, "cogs": cogs, "ovh": ovh, "gm": rev - cogs, "gm_pct": ((rev - cogs) / rev) if rev else None,
                "op": rev - cogs - ovh, "op_pct": ((rev - cogs - ovh) / rev) if rev else None,
                "rev_pd": rev_pd, "cost_pd": cost_pd}
    d = snap.snapshot_date
    prior_last = d.replace(day=1) - timedelta(days=1)
    # P&L: stored on live rows; rolled back from the posting window for past days inside it; else absent
    src = snap if snap.revenue_mtd is not None else ((bs or {}).get("pnl"))
    if src is not None:
        gv = (lambda k: getattr(src, k)) if src is snap else (lambda k: src.get(k))
        pnl_cur = pnl(gv("revenue_mtd"), gv("cogs_mtd"), gv("overhead_mtd"), gv("revenue_prev_day_cur"), gv("cost_prev_day_cur"))
        pnl_prior = pnl(gv("revenue_prior_month"), gv("cogs_prior_month"), gv("overhead_prior_month"), gv("revenue_prev_day_prior"), gv("cost_prev_day_prior"))
    else:
        pnl_cur = pnl_prior = None
    prev_bd = snap.prev_business_day or (bs or {}).get("prev_business_day")
    # WIP change per month -> percentage-of-completion adjustment of the monthly P&L
    def pnl_wip(p, ym):
        # closed month: the WIP entry the accountants booked (already inside COGS -> op income);
        # open month: PCA's snapshot ΔWIP, added on top
        p["wip_booked"] = not _pm.is_open(ym)
        dwip = _pm.gl.lines(None, [ym])["wip_booked"] if p["wip_booked"] else _pm.wip.delta(None, ym)
        if dwip is None:
            p["wip"] = p["adj_op"] = p["adj_pct"] = None
            return
        p["wip"] = dwip
        p["adj_op"] = p["op"] if p["wip_booked"] else p["op"] + dwip
        adj_rev = p["rev"] + dwip
        p["adj_pct"] = (p["adj_op"] / adj_rev) if adj_rev else None
    if pnl_cur:
        pnl_wip(pnl_prior, "%04d%02d" % (prior_last.year, prior_last.month))
        pnl_wip(pnl_cur, "%04d%02d" % (d.year, d.month))
    cur_period = "%04d%02d" % (d.year, d.month)
    prior_period = "%04d%02d" % (prior_last.year, prior_last.month)
    return render(request, "dashboard/finance_daily.html",
                  _ctx(request, "finance-daily", snap=snap, prev=prev, deltas=deltas, ratios=ratios, bs=bs, daynav=nav, is_latest=is_latest,
                       ar_rows=ar_rows, ar_col_totals=ar_col_totals, ap_matrix=ap_matrix, ap_col_totals=ap_col_totals,
                       buckets=FIN_BUCKETS, top_over90=top_over90, ap_due=ap_due, largest_open=largest_open,
                       top_over90_note=top_over90_note, largest_open_note=largest_open_note,
                       history=history, bank=bank, bank_stale=bank_stale, charts=charts, cash_total=cash_total,
                       ap_due_total=ap_due_total, pending_count=pending_count, wip_sa=wip_sa, wip_meta=wip_meta, ratio_status=ratio_status,
                       wip_changes=wip_changes, wip_contributors=wip_contributors, wip_movers=mover_rows,
                       wip_window_labels=[("day", "Previous day"), ("wtd", "Week to date"), ("mtd", "Month to date"), ("ytd", "Year to date")],
                       ni_adjusted=ni_adjusted, wip_ytd_delta=wip_ytd_delta, open_months_ytd=open_months_ytd, wip_close_date=wip_close_date, wip_open_parts=wip_open_parts, pnl_cur=pnl_cur, pnl_prior=pnl_prior,
                       prev_bd=prev_bd, cur_month_label=d.strftime("%B"), prior_month_label=prior_last.strftime("%B"),
                       cur_period=cur_period, prior_period=prior_period,
                       ap_due_from=ap_due_from, ap_due_to=ap_due_to))



def _wip_state(snap, live_rows=True):
    """Live per-job WIP rows plus the baseline snapshots and per-job movement behind every WIP
    window (previous day / week-, month-, year-to-date). Shared by the Daily Snapshot and the
    WIP-by-job page so both show the same numbers."""
    from apps.finance.models import DailyFinanceSnapshot
    from apps.analytics.finance_wip import wip_jobs, wip_movement, wip_job_meta, rebase_jobs, restate_stored
    if live_rows:
        live = wip_jobs()
    else:
        # a past day: the per-job WIP stored in that day's snapshot ([wip, earned, billed] per job),
        # billed re-anchored on the ledger as it stands (rebase_jobs), decorated with the job's
        # current title / customer / contract for display
        stored = restate_stored(rebase_jobs((snap.detail or {}).get("wip_jobs") or {}, snap.snapshot_date), snap.snapshot_date)
        meta = wip_job_meta(stored.keys())
        live = []
        for cpn, v in stored.items():
            m = meta.get(cpn) or {"id": None, "canonical_project_number": cpn, "display_number": cpn, "title": "", "customer": None, "division": "?", "cv": None}
            r = dict(m, wip=Decimal(str(v[0])), earned=Decimal(str(v[1])), billed=Decimal(str(v[2])))
            r["pm_pct"] = (r["earned"] / r["cv"]) if (r.get("cv") and v[1]) else None
            live.append(r)
        live.sort(key=lambda r: -abs(r["wip"]))
    today_d = snap.snapshot_date

    def baseline(before_date):
        return DailyFinanceSnapshot.objects.filter(snapshot_date__lt=before_date, wip_net__isnull=False).order_by("-snapshot_date").first()

    starts = {"day": today_d, "wtd": today_d - timedelta(days=today_d.weekday()),
              "mtd": today_d.replace(day=1), "ytd": today_d.replace(month=1, day=1)}
    baselines = {k: baseline(v) for k, v in starts.items()}
    # baseline per-job WIP with billed re-anchored on the ledger as it stands (docs/07 §3): an invoice
    # entered after the baseline but dated before it belongs to the earlier period, not to this window
    base_jobs_by = {k: (restate_stored(rebase_jobs((b.detail or {}).get("wip_jobs", {}) or {}, b.snapshot_date), b.snapshot_date) if b else {}) for k, b in baselines.items()}
    now_net = sum((j["wip"] for j in live), Decimal(0))
    changes = {}
    for key, b in baselines.items():
        if b:
            bj = base_jobs_by[key]
            if bj:
                b_net = sum(Decimal(str(v[0])) for v in bj.values())
                b_under = sum(Decimal(str(v[0])) for v in bj.values() if v[0] > 0)
                b_over = -sum(Decimal(str(v[0])) for v in bj.values() if v[0] < 0)
                changes[key] = {"date": b.snapshot_date, "net": now_net - b_net,
                                "under": sum((j["wip"] for j in live if j["wip"] > 0), Decimal(0)) - b_under,
                                "over": -sum((j["wip"] for j in live if j["wip"] < 0), Decimal(0)) - b_over}
            else:
                changes[key] = {"date": b.snapshot_date, "net": (snap.wip_net or 0) - (b.wip_net or 0),
                                "under": (snap.wip_underbilled or 0) - (b.wip_underbilled or 0),
                                "over": (snap.wip_overbilled or 0) - (b.wip_overbilled or 0)}
    now_map = {j["canonical_project_number"]: j for j in live}
    movement = {}
    for key, b in baselines.items():
        base_jobs = base_jobs_by[key]
        movement[key] = wip_movement(now_map, base_jobs) if base_jobs else None
    return {"live": live, "now_map": now_map, "baselines": baselines, "changes": changes, "movement": movement, "baseline": baseline}


WIP_WINDOWS = [("day", "Previous day"), ("wtd", "Week to date"), ("mtd", "Month to date"), ("ytd", "Year to date")]


def finance_wip(request):
    """WIP by job — every job in the company WIP population for a chosen period: live (this month /
    year to date, previous day, week to date) or any past month / year (WIP at the period end, from the
    stored snapshot where one exists, else reconstructed). Each row carries the formula inputs, the PTT
    progress entries that drive it (how old, by whom), the change over the period split into its earned
    and billed drivers, cost and hours; filters and sortable columns. The table itself (row derivation,
    columns, bands, sorting, totals) is the shared job table — dashboard/job_table.py — which the customer
    page reuses for one customer's jobs."""
    from apps.finance.models import DailyFinanceSnapshot
    from apps.analytics.finance_wip import (wip_job_meta, wip_movement, wip_rows_as_of, jobs_at,
                                            ptt_hours_window, hours_breakdown, resolve_period, period_options, pct_series,
                                            period_months, ledger_window, STALE_DAYS)
    from . import job_table as JT
    snap = DailyFinanceSnapshot.objects.filter(reconstructed=False).order_by("-snapshot_date").first()
    if not snap:
        return render(request, "dashboard/finance_wip.html", _ctx(request, "finance-wip", snap=None))
    g = request.GET
    today_d = snap.snapshot_date
    per = resolve_period(g.get("period"), today_d, g.get("window"))
    if per["live"]:
        ws = _wip_state(snap)
        end_rows = ws["live"]
        base = ws["baselines"].get(per["live"])
        per["start"] = base.snapshot_date if base else None
        movement = ws["movement"].get(per["live"])
        sources = {"end": "live", "start": "snapshot" if base else None}
    else:
        end_rows, end_src = wip_rows_as_of(per["end"])
        start_jobs, start_src = jobs_at(per["start"])
        movement = wip_movement({r["canonical_project_number"]: r for r in end_rows}, start_jobs) if start_jobs else None
        sources = {"end": end_src, "start": start_src if start_jobs else None}
    now_map = {r["canonical_project_number"]: r for r in end_rows}
    ids = [r["id"] for r in end_rows]
    pct_by, rem_by, pct_key = JT.ptt_people(ids, None if per["live"] else per["end"])
    hours = ptt_hours_window(ids, per["start"], per["end"]) if per["start"] else {}
    hb = hours_breakdown(ids, None if per["live"] else per["end"])   # Hrs % hover / click: union vs non-union used, budget, remaining
    # the period's ledger result per job: revenue and direct cost posted in the accountants' month(s) (or
    # dated inside a day / week window). GP = billed − cost is real profit; ΔWIP is not — it is only the
    # change in the earned-but-unbilled booking. Adjusted GP = GP + ΔWIP is the job's true result (docs/07 §3).
    led_months = period_months(per)
    has_ledger = bool(led_months or per["start"])
    ledger = ledger_window(ids, months=led_months, start=per["start"], end=per["end"]) if has_ledger else {}
    pop_net = sum(float(r["wip"]) for r in end_rows)
    end_day = timezone.localdate() if per["live"] else per["end"]   # live rows are as fresh as the last pull, so age against today
    # % complete in force at the period start (real PTT validity windows, docs/07 §3) so a row can show
    # "old → new"; and the job's margin picture — sold GP% (current SL budget) and the EAC as of the period end
    ctx = {"per": per, "end_day": end_day, "movement": movement, "ledger": ledger, "has_ledger": has_ledger,
           "prof": JT.profitability(ids, end_day), "pct_by": pct_by, "rem_by": rem_by, "pct_key": pct_key, "hours": hours, "hb": hb,
           "cseries": pct_series(ids) if per["start"] else {}, "net_abs": abs(pop_net) or 1.0}
    rows = [JT.decorate(j, ctx) for j in end_rows]
    side = g.get("side", "")
    from apps.analytics.finance_wip import service_agreement_rows, service_agreement_summary
    sa_rows = service_agreement_rows()
    sa_sum = service_agreement_summary(sa_rows)
    if side == "sa":
        # the open service agreements: outside the population by rule, listed here with their contract and billings so
        # the exclusion is visible rather than silent; decorate() zeroes their WIP because in_pop is False
        for j in sa_rows:
            j["in_pop"] = False
        rows = [JT.decorate(j, ctx) for j in sa_rows]
    if side in ("moved", "left"):
        # jobs in the baseline but no longer in the population at the period end (closed / reclassified):
        # their WIP fell to zero, which is real movement — shown only in the movement views
        left_cpns = {cpn for cpn in (movement or {}) if cpn not in now_map}
        meta = wip_job_meta(left_cpns)
        if has_ledger and meta:   # a job that closed in the period usually billed its last invoice in it
            ledger.update(ledger_window([m["id"] for m in meta.values()], months=led_months, start=per["start"], end=per["end"]))
        rows += [JT.decorate(meta[c], ctx, left=True) for c in left_cpns if c in meta]
    # filter options come from the unfiltered population
    divisions = sorted({r["division"] for r in rows})
    pms = sorted({(r["pm_key"], r["pm_name"]) for r in rows if r["pm_key"]}, key=lambda t: t[1])
    states = sorted({r["lifecycle_state"] for r in rows})
    # ---- filters ----
    if side == "under":
        rows = [r for r in rows if r["wip_f"] > 0]
    elif side == "over":
        rows = [r for r in rows if r["wip_f"] < 0]
    elif side == "moved":
        rows = [r for r in rows if r["d_win"]]
    elif side == "left":
        rows = [r for r in rows if r["left"]]
    if g.get("division"):
        rows = [r for r in rows if r["division"] == g["division"]]
    if g.get("pm"):
        rows = [r for r in rows if r["pm_key"] == g["pm"]]
    if g.get("state"):
        rows = [r for r in rows if r["lifecycle_state"] == g["state"]]
    stale = g.get("stale", "")
    if stale == "never":
        rows = [r for r in rows if r["touched_age"] is None]
    elif stale == "nopct":
        rows = [r for r in rows if not r["pm_pct"]]
    elif stale == "fresh":
        rows = [r for r in rows if r["touched_age"] is not None and r["touched_age"] <= 14]
    elif stale.isdigit():
        rows = [r for r in rows if r["touched_age"] is None or r["touched_age"] > int(stale)]
    try:
        min_abs = float(g.get("min") or 0)
    except ValueError:
        min_abs = 0.0
    if min_abs > 0:
        rows = [r for r in rows if r["abs_wip"] >= min_abs or (r["abs_move"] or 0) >= min_abs]
    if g.get("q"):
        q = g["q"].strip().lower()
        rows = [r for r in rows if q in " ".join(str(r.get(k) or "") for k in ("cpn", "display_number", "title", "customer", "pm_name")).lower()]
    # ---- sort (None always last, whichever direction) and totals for the filtered set ----
    sort = g.get("sort") or "-abs_wip"
    rows = JT.sort_rows(rows, sort)
    tot = JT.totals(rows, per, has_ledger, movement is not None)
    identity = ["number", "customer"] + ([] if g.get("division") else ["division"]) + ([] if g.get("pm") else ["pm"]) + ["state"]
    cols, band_cells = JT.columns(per, identity, margins=request.acc.margins, ledger_by_period=bool(led_months))
    jt = {"rows": rows, "cols": cols, "band_cells": band_cells, "tots": [dict(tot, key="all")], "per": per, "sort_key": sort.lstrip("-"),
          "stale_days": STALE_DAYS, "id": "wiptable", "fit": True, "hidden": ["cust"], "legacy_key": "pca.wip.cols", "sets": None,
          "n_identity": len(identity), "has_state": True, "groups": JT.chip_groups(cols, per), "margins": request.acc.margins}
    return render(request, "dashboard/finance_wip.html",
                  _ctx(request, "finance-wip", snap=snap, jt=jt, tot=tot, g=g, sort=sort, per=per, period_groups=period_options(today_d),
                       sources=sources, pop_n=len(end_rows), sa_sum=sa_sum, pop_net=pop_net, divisions=divisions, pms=pms, states=states, stale_days=STALE_DAYS))


# ============================================================================ bank reconciliation
BANK_BUCKET_LABELS = [("check", "Cleared checks"), ("check_reversal", "Check reversals"), ("deposit", "Remote / package deposits"), ("ach_in", "ACH / EDI receipts"),
                      ("wire_in", "Incoming wires"), ("loan_draw", "Line-of-credit draws"), ("ach", "ACH debits"), ("ach_batch", "ACH origination settlements"),
                      ("wire", "Outgoing wires"), ("sweep", "Sweeps / loan paydowns"), ("payroll", "Payroll (Paylocity)"), ("fee", "Bank fees")]


def finance_bank(request):
    """Bank Reconciliation: every imported statement with its bridge result; scan-and-reconcile button."""
    from apps.finance.models import BankStatement
    stmts = list(BankStatement.objects.all())
    for st in stmts:
        flags = (st.result or {}).get("flags", [])
        st.n_flags = {lvl: sum(1 for f in flags if f["level"] == lvl) for lvl in ("critical", "high", "moderate", "low")}
        st.outstanding = (st.result or {}).get("outstanding", {})
        st.res_css = "" if st.residual is None else ("pos" if abs(st.residual) < 1 else ("warn-ink" if abs(st.residual) < 10000 else "neg"))
    running = IngestionRun.objects.filter(source_system="local", status="running").exists()
    from apps.finance.models import BankUpload
    uploads = BankUpload.objects.select_related("statement").all()[:200]
    return render(request, "dashboard/finance_bank.html", _ctx(request, "finance-bank", stmts=stmts, folder=settings.BANK_STATEMENTS_DIR,
                                                                folder_exists=settings.BANK_STATEMENTS_DIR.exists(), running=running, uploads=uploads))


def finance_bank_detail(request, pk):
    from apps.finance.models import BankStatement
    st = get_object_or_404(BankStatement, pk=pk)
    r = st.result or {}
    g = request.GET
    lines = list(st.lines.all())
    for l in lines:
        l.status = "matched" if l.match_kind else "unmatched"
        l.signed = -l.amount if l.kind in ("wd", "check") else l.amount
    buckets_present = sorted({l.bucket for l in lines})
    bucket_labels = dict(BANK_BUCKET_LABELS)
    view = g.get("view", "all")
    if g.get("bucket"):
        lines = [l for l in lines if l.bucket == g["bucket"]]
    if g.get("status") in ("matched", "unmatched"):
        lines = [l for l in lines if l.status == g["status"]]
    if g.get("q"):
        q = g["q"].lower()
        lines = [l for l in lines if q in (l.description + " " + l.payee + " " + l.sl_ref + " " + l.match_kind).lower()]
    sort = g.get("sort") or "line"
    key = {"line": lambda l: l.line_no, "date": lambda l: (l.posted_date, l.line_no), "amount": lambda l: -abs(l.amount), "desc": lambda l: l.description}.get(sort.lstrip("-"), lambda l: l.line_no)
    lines.sort(key=key, reverse=sort.startswith("-"))
    lvl_rank = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
    flags = sorted(r.get("flags", []), key=lambda f: lvl_rank.get(f["level"], 9))
    res = float(st.residual) if st.residual is not None else None
    res_css = "" if res is None else ("pos" if abs(res) < 1 else ("warn-ink" if abs(res) < 10000 else "neg"))
    others = list(BankStatement.objects.filter(gl_account=st.gl_account).exclude(pk=st.pk).order_by("-period_end")[:24])
    return render(request, "dashboard/finance_bank_detail.html",
                  _ctx(request, "finance-bank", st=st, r=r, flags=flags, lines=lines, n_lines=st.lines.count(), buckets_present=buckets_present,
                       bucket_labels=bucket_labels, g=g, sort=sort, res=res, res_css=res_css, others=others,
                       by_age=sorted(r.get("outstanding", {}).get("by_age", {}).items(), key=lambda kv: ["0–7 days", "8–30 days", "31–60 days", "over 60 days"].index(kv[0]) if kv[0] in ["0–7 days", "8–30 days", "31–60 days", "over 60 days"] else 9),
                       payroll=r.get("payroll", {}), bridge=r.get("bridge", [])))


@require_POST
def finance_bank_reconcile(request):
    """Scan the statements folder and reconcile anything new (or everything with ?all=1) in the background."""
    if IngestionRun.objects.filter(source_system="local", status="running").exists():
        messages.warning(request, "A refresh is already running.")
        return redirect("dashboard:finance_bank")
    log_dir = settings.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("reconcile_bank_%s.log" % timezone.now().strftime("%Y%m%d_%H%M%S"))
    args = [sys.executable, str(settings.BASE_DIR / "manage.py"), "reconcile_bank"] + (["--all"] if request.POST.get("all") else [])
    with open(log_path, "ab") as fh:
        subprocess.Popen(args, cwd=settings.BASE_DIR, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    messages.info(request, "Scanning %s and reconciling — reload in ~30 seconds." % settings.BANK_STATEMENTS_DIR)
    return redirect("dashboard:finance_bank")

def _today_docs_notice(day):
    """Drill-down opened from a past day of the snapshot: open AR/AP/WIP exist only as of the latest pull."""
    from django.utils.formats import date_format
    from apps.finance.models import DailyFinanceSnapshot
    latest = DailyFinanceSnapshot.objects.filter(reconstructed=False).order_by("-snapshot_date").only("as_of").first()
    pulled = date_format(timezone.localtime(latest.as_of), "D M j, g:i A") if latest and latest.as_of else "today"
    return ("Today's open documents (SL pull %s). Open AR, AP and WIP are not stored document by document, so %s's list "
            "is not available and the total here is today's." % (pulled, day.strftime("%a %b %-d")))


def finance_drill(request):
    """JSON detail behind any cell of the daily finance report. Reads local tables only.
    `as_of=YYYY-MM-DD` (set when the snapshot page shows a past day) cuts the GL drills off at postings
    created on or before that day — the same rule the stored figures used — and, for the open-document
    drills, which cannot be rolled back, adds a notice saying the list is today's."""
    kind = request.GET.get("kind", "")
    g = request.GET
    money_cols = {"amt", "orig"}
    link_map = {}
    as_of = as_of_ts = None
    if g.get("as_of"):
        try:
            as_of = date.fromisoformat(g["as_of"])
        except ValueError:
            raise Http404
        # a stored live day was pulled at a moment, not at midnight: cut at that timestamp so the GL drills reproduce the
        # figure on the page (Sep 2 was pulled at 5:00 PM; $247K of August revenue was entered at 5:14 PM the same day)
        from apps.finance.models import DailyFinanceSnapshot
        stored = DailyFinanceSnapshot.objects.filter(snapshot_date=as_of, reconstructed=False).only("as_of").first()
        as_of_ts = stored.as_of if stored else None
        gl_cutoff = ("sl_created_at <= %s", as_of_ts) if as_of_ts else ("sl_created_at::date <= %s", as_of)
    notice = ""
    if kind == "ar":
        where, params = ["released"], []
        if g.get("book"):
            where.append("book = %s"); params.append(g["book"])
        if g.get("bucket"):
            where.append("bucket = %s"); params.append(g["bucket"])
        if g.get("cust"):
            where.append("customer_id_raw = %s"); params.append(g["cust"])
        rows = fetch_dict("""SELECT d.ref_nbr, d.doc_type, d.customer_name, d.project_id_raw, d.order_nbr, d.doc_date, d.due_date,
                                    d.days_past_due, d.sign*d.doc_bal amt, d.doc_desc, d.terms, d.cust_po,
                                    CASE WHEN p.canonical_project_number IS NOT NULL THEN '/projects/' || p.canonical_project_number || '/' END AS link,
                                    CASE WHEN c.sl_customer_id IS NOT NULL THEN '/customers/' || c.sl_customer_id || '/' END AS customer_link
                             FROM finance_aropendocument d
                             LEFT JOIN core_project p ON p.id = d.project_id
                             LEFT JOIN core_customer c ON c.id = d.customer_id
                             WHERE %s ORDER BY d.sign*d.doc_bal DESC LIMIT 400""" % " AND ".join(where).replace("released", "d.released").replace("book =", "d.book =").replace("bucket =", "d.bucket =").replace("customer_id_raw =", "d.customer_id_raw ="), params)
        agg = fetch_dict("SELECT COUNT(*) n, COALESCE(SUM(sign*doc_bal),0) s FROM finance_aropendocument WHERE %s" % " AND ".join(where), params)[0]
        cols = [("ref_nbr", "Ref"), ("doc_type", "Type"), ("customer_name", "Customer"), ("project_id_raw", "Project"),
                ("order_nbr", "Order"), ("doc_date", "Date"), ("due_date", "Due"), ("days_past_due", "Days late"),
                ("amt", "Balance"), ("doc_desc", "Description"), ("cust_po", "Cust PO")]
        link_map = {"project_id_raw": "link", "customer_name": "customer_link"}
        title = "Open AR"
    elif kind == "ar_pending":
        rows = fetch_dict("""SELECT d.ref_nbr, d.doc_type, d.customer_name, d.project_id_raw, d.doc_date, d.due_date, d.orig_amt amt, d.doc_desc,
                                    CASE WHEN p.canonical_project_number IS NOT NULL THEN '/projects/' || p.canonical_project_number || '/' END AS link
                             FROM finance_aropendocument d LEFT JOIN core_project p ON p.id = d.project_id
                             WHERE NOT d.released ORDER BY d.orig_amt DESC LIMIT 400""")
        agg = fetch_dict("SELECT COUNT(*) n, COALESCE(SUM(orig_amt),0) s FROM finance_aropendocument WHERE NOT released")[0]
        cols = [("ref_nbr", "Ref"), ("customer_name", "Customer"), ("project_id_raw", "Project"), ("doc_date", "Date"),
                ("amt", "Amount"), ("doc_desc", "Description")]
        link_map = {"project_id_raw": "link"}
        title = "Pending (unreleased) AR invoices"
    elif kind == "ap":
        where, params = ["TRUE"], []
        if g.get("bucket"):
            where.append("bucket = %s"); params.append(g["bucket"])
        if g.get("doc_type"):
            where.append("doc_type = %s"); params.append(g["doc_type"])
        if g.get("due_from") and g.get("due_to"):
            where.append("due_date BETWEEN %s AND %s"); params += [g["due_from"], g["due_to"]]
        if g.get("vendor"):
            where.append("vendor_id = %s"); params.append(g["vendor"])
        rows = fetch_dict("""SELECT ref_nbr, doc_type, vendor_name, invoice_nbr, po_nbr, doc_date, due_date, days_past_due, sign*doc_bal amt, doc_desc
                             FROM finance_apopendocument WHERE %s ORDER BY sign*doc_bal DESC LIMIT 400""" % " AND ".join(where), params)
        agg = fetch_dict("SELECT COUNT(*) n, COALESCE(SUM(sign*doc_bal),0) s FROM finance_apopendocument WHERE %s" % " AND ".join(where), params)[0]
        cols = [("ref_nbr", "Ref"), ("doc_type", "Type"), ("vendor_name", "Vendor"), ("invoice_nbr", "Invoice"),
                ("po_nbr", "PO"), ("doc_date", "Date"), ("due_date", "Due"), ("days_past_due", "Days late"),
                ("amt", "Balance"), ("doc_desc", "Description")]
        title = "Open AP"
    elif kind == "wip":
        from apps.analytics.finance_wip import service_agreement_rows, wip_jobs
        sa_side = g.get("side") == "sa"
        jobs = service_agreement_rows() if sa_side else wip_jobs()
        if g.get("side") == "under":
            jobs = [j for j in jobs if j["wip"] > 0]
        elif g.get("side") == "over":
            jobs = [j for j in jobs if j["wip"] < 0]
        rows = [{"project": j["display_number"], "link": "/projects/%s/" % j["canonical_project_number"], "title": (j["title"] or "")[:40],
                 "customer": (j["customer"] or "")[:32], "division": j["division"], "mode": j["project_mode_rule"],
                 "cv": j["cv"], "pm_pct": (round(float(j["pm_pct"]) * 100) if j["pm_pct"] is not None else 0),
                 "billed": j["billed"], "earned": j["earned"], "amt": j["wip"], "cost": j["cost"],
                 "net_cost": (j["under_cost"] or 0) - (j["over_cost"] or 0)}
                for j in jobs[:400]]
        cols = [("project", "Project"), ("title", "Title"), ("customer", "Customer"), ("division", "Div"), ("mode", "Mode"),
                ("cv", "Contract"), ("pm_pct", "PTT %"), ("earned", "Earned (CV × %)"), ("billed", "Billed"),
                ("amt", "WIP (earned − billed)"), ("cost", "Cost to date"), ("net_cost", "Cost basis net")]
        link_map = {"project": "link"}
        agg = {"n": len(jobs), "s": sum((j["wip"] for j in jobs), Decimal(0))}
        money_cols = {"cv", "earned", "billed", "amt", "cost", "net_cost"}
        title = ("Service agreements — outside WIP by rule (the WIP column is what the formula would say; it is never added)" if sa_side
                 else "WIP by job — CV × PTT % complete − billed (positive = underbilled)")
    elif kind == "gl":
        where, params = ["TRUE"], []
        if g.get("period"):
            where.append("per_post = %s"); params.append(g["period"])
        if g.get("day"):
            where.append("sl_created_at::date = %s"); params.append(g["day"])
        if as_of:
            where.append(gl_cutoff[0]); params.append(gl_cutoff[1])
        if g.get("group") == "rev":
            where.append("acct_type = '3I' AND acct NOT IN ('40100')")
        elif g.get("group") == "cogs":
            where.append("acct_type = '4E' AND (LEFT(acct,1) = '5' OR acct IN ('60000','60005'))")
        elif g.get("group") == "ovh":
            where.append("acct_type = '4E' AND LEFT(acct,1) IN ('6','7') AND acct NOT IN ('60000','60005')")
        elif g.get("group") == "cost":
            where.append("acct_type = '4E'")
        else:
            where.append("acct_type IN ('3I','4E')")   # the table also holds liability rows (2L) — P&L drills never show them
        rows = fetch_dict("""SELECT acct, MAX(acct_descr) descr, module, per_post, COUNT(*) n,
                                    SUM(CASE WHEN acct_type='3I' THEN cr_amt-dr_amt ELSE dr_amt-cr_amt END) amt
                             FROM finance_glrecentposting WHERE %s GROUP BY acct, module, per_post ORDER BY ABS(SUM(CASE WHEN acct_type='3I' THEN cr_amt-dr_amt ELSE dr_amt-cr_amt END)) DESC LIMIT 400""" % " AND ".join(where), params)
        agg = fetch_dict("""SELECT COUNT(*) n, COALESCE(SUM(CASE WHEN acct_type='3I' THEN cr_amt-dr_amt ELSE dr_amt-cr_amt END),0) s
                            FROM finance_glrecentposting WHERE %s""" % " AND ".join(where), params)[0]
        cols = [("acct", "Account"), ("descr", "Description"), ("module", "Module"), ("per_post", "Period"), ("n", "Rows"), ("amt", "Amount")]
        title = "GL postings"
    elif kind == "gl_acct":
        # ledger postings on one balance-sheet account, newest first; amt = the natural-sign change
        # (liability: cr - dr = more owed; asset: dr - cr = more owned)
        acct = g.get("acct", "")
        if not acct:
            raise Http404
        where, params = ["acct = %s", "acct_type IN ('1A','2L')"], [acct]
        if g.get("sub"):
            where.append("sub = %s"); params.append(g["sub"])
        if as_of:
            where.append(gl_cutoff[0]); params.append(gl_cutoff[1])
        change = "CASE WHEN acct_type = '1A' THEN dr_amt - cr_amt ELSE cr_amt - dr_amt END"
        rows = fetch_dict("""SELECT tran_date, sl_created_at::date entered, per_post, module, jrnl_type, batch_nbr, ref_nbr, sub, tran_desc,
                                    %s amt, cr_amt, dr_amt
                             FROM finance_glrecentposting WHERE %s ORDER BY tran_date DESC, sl_created_at DESC LIMIT 400""" % (change, " AND ".join(where)), params)
        agg = fetch_dict("SELECT COUNT(*) n, COALESCE(SUM(%s),0) s FROM finance_glrecentposting WHERE %s" % (change, " AND ".join(where)), params)[0]
        cols = [("tran_date", "Date"), ("entered", "Entered"), ("per_post", "Period"), ("module", "Module"), ("jrnl_type", "Journal"),
                ("batch_nbr", "Batch"), ("ref_nbr", "Ref"), ("sub", "Sub"), ("tran_desc", "Description"), ("amt", "Change"),
                ("cr_amt", "Credit"), ("dr_amt", "Debit")]
        money_cols = {"amt", "cr_amt", "dr_amt"}
        title = "Ledger postings · account %s" % acct
        trunc_note = " · showing the 400 most recent"
    else:
        raise Http404
    if as_of:
        if kind in ("gl", "gl_acct"):
            from django.utils.formats import date_format
            cut = (date_format(timezone.localtime(as_of_ts), "D M j, g:i A") + " (this day's SL pull)") if as_of_ts else as_of.strftime("%a %b %-d")
            notice = "Postings entered through %s — the ledger as it stood when this day's figures were stored." % cut
        else:
            notice = _today_docs_notice(as_of)
    for r in rows:
        for k, v in r.items():
            if isinstance(v, Decimal):
                r[k] = float(v)
            elif hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return JsonResponse({"title": g.get("title") or title, "cols": [list(c) for c in cols], "rows": rows,
                         "total": float(agg["s"]), "money_cols": list(money_cols), "n": agg["n"],
                         "link_map": link_map, "truncated": agg["n"] > len(rows), "trunc_note": locals().get("trunc_note", ""),
                         "notice": notice})


def _ledger_breakdown_view(request, build, url_name):
    """What makes up 'Total liabilities' / 'Total assets' on the daily snapshot (apps.analytics.finance_ledger).
    Returns the modal fragment; ?page=1 renders the same content as a standalone page. Always today's ledger:
    per-account balances are not stored day by day. `?date=YYYY-MM-DD` (the snapshot page showing a past
    day) only adds a notice saying so, with that day's stored headline for comparison."""
    from django.urls import reverse
    from apps.finance.models import DailyFinanceSnapshot
    snap = DailyFinanceSnapshot.objects.filter(reconstructed=False).order_by("-snapshot_date").first()
    if not snap:
        raise Http404
    prev = DailyFinanceSnapshot.objects.filter(reconstructed=False, snapshot_date__lt=snap.snapshot_date).order_by("-snapshot_date").first()
    data = build(snap, prev)
    viewed_date = viewed_headline = None
    if request.GET.get("date"):
        try:
            want = date.fromisoformat(request.GET["date"].strip())
        except ValueError:
            want = None
        if want and want < snap.snapshot_date:
            viewed_date = want
            viewed = DailyFinanceSnapshot.objects.filter(snapshot_date=want).first()
            viewed_headline = getattr(viewed, data["side"]["snapshot_field"], None) if viewed else None
    standalone = request.GET.get("page") == "1"
    ctx = dict(snap=snap, prev=prev, standalone=standalone, buckets=FIN_BUCKETS, ar_books=dict(FIN_AR_BOOKS),
               page_url=reverse("dashboard:" + url_name), sub_total=data[data["side"]["sub_tab"]["amount_key"]]["total"],
               viewed_date=viewed_date, viewed_headline=viewed_headline, **data)
    if standalone:
        return render(request, "dashboard/finance_ledger_page.html", _ctx(request, "finance-daily", **ctx))
    return render(request, "dashboard/finance_ledger_breakdown.html", ctx)


def finance_liabilities(request):
    from apps.analytics.finance_liabilities import liability_breakdown
    return _ledger_breakdown_view(request, liability_breakdown, "finance_liabilities")


def finance_assets(request):
    from apps.analytics.finance_assets import asset_breakdown
    return _ledger_breakdown_view(request, asset_breakdown, "finance_assets")


@require_POST
def finance_bank_figures(request):
    from apps.finance.models import BankFigureEntry
    def dec(name):
        v = (request.POST.get(name) or "").replace(",", "").replace("$", "").strip()
        try:
            return Decimal(v) if v else None
        except Exception:  # noqa
            return None
    BankFigureEntry.objects.update_or_create(
        entry_date=timezone.localdate(),
        defaults=dict(checking_bank_balance=dec("checking_bank_balance"), float_amount=dec("float_amount"),
                      borrowing_base_available=dec("borrowing_base_available"), credit_line_used=dec("credit_line_used"),
                      payroll_note=(request.POST.get("payroll_note") or "").strip()))
    messages.info(request, "Bank figures saved for today.")
    return redirect("dashboard:finance_daily")


@require_POST
def project_contract_value(request, key):
    """A person's decision about the contract value the app uses for one project (finance.write): a value, or
    'SL is right'. Stored locally, applied immediately through the same rule engine, logged. SL is never touched."""
    from apps.analytics.contract_value import apply_contract_values
    from apps.finance.models import ContractValueOverride
    p = get_object_or_404(Project, canonical_project_number=key.upper())
    if request.POST.get("clear"):
        ContractValueOverride.objects.filter(project=p).delete()
        msg = "Contract value override removed; the nightly rules decide again."
    else:
        raw = (request.POST.get("value") or "").replace(",", "").replace("$", "").strip()
        confirm = bool(request.POST.get("confirm_sl"))
        try:
            value = Decimal(raw) if raw else None
        except Exception:  # noqa
            value = None
        reason = (request.POST.get("reason") or "").strip()
        if not reason or (value is None and not confirm):
            messages.warning(request, "Enter a contract value (or tick 'SL is right') and a reason.")
            return redirect("dashboard:project_detail", p.canonical_project_number)
        acc = getattr(request, "acc", None)
        who = acc.real_account.display_name if acc is not None and acc.real_account is not None else "local"
        ContractValueOverride.objects.update_or_create(
            project=p, defaults=dict(value=None if confirm else value, confirm_sl=confirm, reason=reason[:300], set_by=who, set_at=timezone.now()))
        msg = "SL's contract value confirmed for this project." if confirm else "Contract value set to $%s for this project." % format(int(round(value)), ",")
    apply_contract_values(run=None, project_ids=[p.id])
    messages.info(request, msg)
    return redirect("dashboard:project_detail", p.canonical_project_number)


@require_POST
def finance_refresh(request):
    if IngestionRun.objects.filter(source_system="local", status="running").exists():
        messages.warning(request, "A refresh is already running.")
        return redirect("dashboard:finance_daily")
    log_dir = settings.LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("refresh_finance_%s.log" % timezone.now().strftime("%Y%m%d_%H%M%S"))
    with open(log_path, "ab") as fh:
        subprocess.Popen([sys.executable, str(settings.BASE_DIR / "manage.py"), "refresh_finance"],
                         cwd=settings.BASE_DIR, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    messages.info(request, "Finance refresh started — reload in ~15 seconds.")
    return redirect("dashboard:finance_daily")


def finance_allocations(request):
    """View + edit the corporate (000) overhead allocation shares used by the P&L-by-year table.

    The corporate pool = GL subaccount 0000 overhead (6xxxx/7xxxx excl 60000/60005). Shares were
    seeded from the accountants' own workbooks (the "000" pivot columns of each division tab);
    editing here changes the loaded operating income on every division P&L. Local data only —
    SL is never written. See docs/07_pnl_and_wip.md.
    """
    from apps.core.models import Division
    from apps.finance.models import DivisionOverheadShare
    divisions = list(Division.objects.filter(active=True).order_by("code"))
    cur_year = str(timezone.localdate().year)
    if request.method == "POST":
        n = 0
        for key, val in request.POST.items():
            if not key.startswith("share_"):
                continue
            _, fy, code = key.split("_", 2)
            val = (val or "").replace("%", "").strip()
            existing = DivisionOverheadShare.objects.filter(fiscal_year=fy, division_code=code)
            if val == "":
                n += existing.delete()[0]
                continue
            try:
                share = Decimal(val) / 100
            except Exception:  # noqa
                continue
            if share < 0 or share > 1:
                continue
            DivisionOverheadShare.objects.update_or_create(fiscal_year=fy, division_code=code, defaults={"share": share})
            n += 1
        messages.info(request, "Allocation shares saved (%d cells)." % n)
        return redirect("dashboard:finance_allocations")
    shares = list(DivisionOverheadShare.objects.all())
    years = sorted({s.fiscal_year for s in shares} | {cur_year})
    pool = {r["fiscal_year"]: r["p"] for r in fetch_dict("""
        SELECT fiscal_year, SUM(p00+p01+p02+p03+p04+p05+p06+p07+p08+p09+p10+p11+p12) p
        FROM finance_glaccountbalance
        WHERE sub='0000' AND acct_type='4E' AND LEFT(acct,1) IN ('6','7') AND acct NOT IN ('60000','60005')
        GROUP BY fiscal_year""")}
    by = {(s.fiscal_year, s.division_code): s.share for s in shares}
    share_years = sorted({s.fiscal_year for s in shares})

    def effective(fy, code):
        if (fy, code) in by:
            return by[(fy, code)], False
        eligible = [y for y in share_years if y <= fy] or share_years[:1]
        if eligible:
            v = by.get((eligible[-1], code))
            if v is not None:
                return v, True
        return None, True
    grid = []
    for div in divisions:
        cells = []
        for fy in years:
            v, inherited = effective(fy, div.code)
            explicit = by.get((fy, div.code))
            cells.append({"fy": fy, "explicit": explicit, "effective": v, "inherited": inherited and explicit is None,
                          "alloc": (Decimal(str(pool.get(fy) or 0)) * v) if v else None})
        grid.append({"division": div, "cells": cells})
    col_totals = []
    for i, fy in enumerate(years):
        tot = sum((r["cells"][i]["effective"] or 0 for r in grid), Decimal(0))
        alloc = sum((r["cells"][i]["alloc"] or 0 for r in grid), Decimal(0))
        p = Decimal(str(pool.get(fy) or 0))
        col_totals.append({"fy": fy, "share": tot, "pool": p, "alloc": alloc, "unalloc": p - alloc})
    return render(request, "dashboard/finance_allocations.html",
                  _ctx(request, "finance-alloc", grid=grid, years=years, col_totals=col_totals, cur_year=cur_year))


def finance_payments(request):
    """Daily cash receipts — the in-app version of SL report 08820 'Payment Applications - Detail'
    (the PDF a finance team member emails daily). Day key = the PAYMENT's entry date, matching the
    report (verified 8/26/26: 11 payments, $172,757.70 exact); applications made today on OLDER
    payments are shown separately so the day's full cash-application activity is visible.
    Reads local copies only (ARPayment / ARPaymentApplication, trailing ~14 months)."""
    from apps.finance.models import ARPayment, ARPaymentApplication
    # doc_date <= today guards against fat-fingered future dates in SL (e.g. a payment entered as
    # 2035-09-03) — without it the "next day" arrow jumped a decade ahead.
    days = [r["doc_date"] for r in fetch_dict(
        "SELECT DISTINCT doc_date FROM finance_arpayment WHERE doc_date IS NOT NULL AND doc_date <= CURRENT_DATE ORDER BY doc_date DESC LIMIT 400")]
    day = None
    if request.GET.get("day"):
        try:
            day = date.fromisoformat(request.GET["day"])
        except ValueError:
            day = None
    if day is None:
        day = days[0] if days else timezone.localdate()
    if day in days:
        idx = days.index(day)
        prev_day = days[idx + 1] if idx + 1 < len(days) else None
        next_day = days[idx - 1] if idx > 0 else None
    else:  # picked a day with no payments: offer the nearest active days on both sides
        prev_day = next((d for d in days if d < day), None)
        next_day = next((d for d in reversed(days) if d > day), None)

    payments = list(ARPayment.objects.filter(doc_date=day).select_related("customer").order_by("-orig_amt"))
    keys = [(p.ref_nbr, p.customer_id_raw) for p in payments]
    apps = list(ARPaymentApplication.objects.filter(
        payment_ref__in=[k[0] for k in keys] or ["__none__"],
        customer_id_raw__in=[k[1] for k in keys] or ["__none__"]).select_related(
        "project", "project__customer").order_by("-applied"))
    apps_by_key = defaultdict(list)
    for a in apps:
        if (a.payment_ref, a.customer_id_raw) in set(keys):
            apps_by_key[(a.payment_ref, a.customer_id_raw)].append(a)
    # the order behind each invoice: a job-less SO1 invoice is a 010 hardware order ("(SO1 Order)" in the Project column)
    from apps.analytics.payment_summary import late_label, summarize_payment
    inv_meta = {(r["ref_nbr"], r["doc_type"], r["customer_id_raw"]): r for r in fetch_dict("""
        SELECT i.ref_nbr, i.doc_type, i.customer_id_raw, i.so_type, i.order_nbr, o.cnet_number
        FROM finance_arinvoice i LEFT JOIN sales_slcnetorder o ON o.ord_nbr = i.order_nbr AND i.order_nbr <> ''
        WHERE i.ref_nbr = ANY(%s)""", [list({a.invoice_ref for a in apps})])} if apps else {}
    groups = []
    for p in payments:
        rows = apps_by_key.get((p.ref_nbr, p.customer_id_raw), [])
        for a in rows:
            pr = a.project
            a.pct_billed = (a.applied / a.invoice_amt) if a.invoice_amt else None
            if pr:
                a.proj_pct = pr.pm_percent_complete
                a.proj_cv = pr.contract_value
                a.proj_billed = pr.billed_revenue
            m = inv_meta.get((a.invoice_ref, a.invoice_type, a.customer_id_raw)) or {}
            a.so_type, a.order_nbr, a.cnet_number = m.get("so_type") or "", m.get("order_nbr") or "", m.get("cnet_number")
            a.is_so1 = a.so_type == "SO1" and not a.project_id
            a.days_late = (a.date_appl - a.due_date).days if (a.date_appl and a.due_date) else None
            a.paid_label = late_label(a.days_late)
        s = summarize_payment(rows)   # the payment row's own values, filled in from its invoices
        groups.append({"p": p, "apps": rows, "s": s, "applied": sum((a.applied for a in rows), Decimal(0)),
                       "late_applied": any(a.date_appl != p.doc_date for a in rows),
                       "group_project": s["project"]})
    groups.sort(key=lambda g: -g["p"].orig_amt)
    n_customers = len({g["p"].customer_id_raw for g in groups})
    # applications made this day against OLDER payments (the part the emailed report misses)
    late_apps = list(ARPaymentApplication.objects.filter(date_appl=day).exclude(
        payment_ref__in=[k[0] for k in keys] or ["__none__"]).select_related("project").order_by("-applied"))
    for a in late_apps:
        if a.project:
            a.proj_pct = a.project.pm_percent_complete
            a.proj_cv = a.project.contract_value
    totals = {"received": sum((p.orig_amt for p in payments), Decimal(0)),
              "applied": sum((g["applied"] for g in groups), Decimal(0)),
              "unapplied": sum((p.balance for p in payments), Decimal(0)),
              "n": len(payments), "customers": n_customers,
              "late": sum((a.applied for a in late_apps), Decimal(0)),
              "inv_amt": sum((g["s"]["inv_amt"] for g in groups), Decimal(0)),
              "open": sum((g["s"]["open"] for g in groups), Decimal(0))}
    # 30-day receipts trend for context
    recent = fetch_dict("""SELECT doc_date d, SUM(orig_amt) amt, COUNT(*) n FROM finance_arpayment
                           WHERE doc_date > %s - INTERVAL '45 days' AND doc_date <= %s GROUP BY 1 ORDER BY 1""", [day, day])
    charts = {"recent": [{"d": r["d"].isoformat(), "amt": _f(r["amt"]), "n": r["n"]} for r in recent]}
    return render(request, "dashboard/finance_payments.html",
                  _ctx(request, "finance-payments", day=day, days=days[:60], prev_day=prev_day, next_day=next_day,
                       groups=groups, late_apps=late_apps, totals=totals, charts=charts))


# ============================================================================ vendors (finance capability)
def _attach_check_apps(checks):
    """Attach the paid vouchers (APCheckApplication) to APCheck rows: c.vouchers largest-first
    with per-voucher invoice date / due date / days held, plus c.wait_days ($-weighted days from
    vendor invoice to check), c.inv_min/inv_max, c.early (any voucher paid under 30 days from
    its invoice date — paying before it's necessary)."""
    from apps.finance.models import APCheckApplication
    if not checks:
        return
    apps = defaultdict(list)
    for a in APCheckApplication.objects.filter(check_ref__in={c.ref_nbr for c in checks}):
        apps[(a.check_ref, a.vendor_id)].append(a)
    for c in checks:
        vs = sorted(apps.get((c.ref_nbr, c.vendor_id), []), key=lambda a: -abs(a.adj_amount or 0))
        num = den = Decimal(0)
        c.early = False
        for a in vs:
            a.inv_date = a.invoice_date or a.voucher_date
            a.wait = (c.doc_date - a.inv_date).days if (c.doc_date and a.inv_date) else None
            # paying under 30 days from the invoice is paying before it's necessary (Owner's rule)
            a.early = a.wait is not None and a.wait < 30
            c.early = c.early or a.early
            if a.wait is not None and a.adj_amount:
                num += abs(a.adj_amount) * a.wait
                den += abs(a.adj_amount)
        c.vouchers = vs
        c.wait_days = int(num / den) if den else None
        dates = [a.inv_date for a in vs if a.inv_date]
        c.inv_min, c.inv_max = (min(dates), max(dates)) if dates else (None, None)


def _vendor_period(request, today, first_year):
    """The Vendors page window: ?p=ytd (default) | 12mo | all | YYYY (a calendar year) | custom (&from=&to=).
    Returns (key, label, start, end, prior_start, prior_end, prior_label) — the prior span is the same window a year
    earlier (YTD → last year to the same date, a calendar year → the year before, custom → the preceding span)."""
    p = (request.GET.get("p") or "ytd").lower()
    prior = None

    def same_day_last_year(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:          # Feb 29
            return d.replace(year=d.year - 1, day=28)

    if p == "12mo":
        start, end = today - timedelta(days=365), today
        prior = (start - timedelta(days=366), start - timedelta(days=1), "prior 12 mo")
        label = "last 12 months"
    elif p == "all":
        start, end, label = date(first_year, 1, 1), today, "all years (%d–%d)" % (first_year, today.year)
    elif p == "custom" and request.GET.get("from") and request.GET.get("to"):
        try:
            start, end = date.fromisoformat(request.GET["from"]), date.fromisoformat(request.GET["to"])
        except ValueError:
            return _vendor_period_default(today)
        if end < start:
            start, end = end, start
        span = (end - start).days + 1
        prior = (start - timedelta(days=span), start - timedelta(days=1), "preceding %d days" % span)
        label = "%s – %s" % (start.strftime("%b %-d, %Y"), end.strftime("%b %-d, %Y"))
    elif p.isdigit() and first_year <= int(p) <= today.year:
        y = int(p)
        start, end = date(y, 1, 1), (date(y, 12, 31) if y < today.year else today)
        prior = (date(y - 1, 1, 1), date(y - 1, 12, 31), "%d" % (y - 1)) if y - 1 >= first_year else None
        label = ("%d" % y) if y < today.year else ("%d to date" % y)
        p = str(y)
    else:
        return _vendor_period_default(today)
    return (p, label, start, end) + (prior or (None, None, None))


def _vendor_period_default(today):
    start, end = date(today.year, 1, 1), today
    try:
        pend = today.replace(year=today.year - 1)
    except ValueError:
        pend = today.replace(year=today.year - 1, day=28)
    return ("ytd", "%d YTD" % today.year, start, end, date(today.year - 1, 1, 1), pend, "%d to %s" % (today.year - 1, pend.strftime("%b %-d")))


def vendors(request):
    """Vendor index: everyone paid in the selected window (checks / EFTs, voids negative — AP payments are kept
    for every year since 2013), plus anyone with an open AP balance or open POs; the same window a year earlier
    sits beside it. Searchable, largest paid first. Receipts follow the window where the local copy reaches
    (project-tied PO receipts, ~3 years)."""
    from apps.finance.models import SLVendor, CARD_CLEARING_ACCOUNTS
    q = (request.GET.get("q") or "").strip()
    today = timezone.localdate()
    years = [int(r["y"]) for r in fetch_dict("SELECT DISTINCT EXTRACT(year FROM doc_date)::int y FROM finance_apcheck WHERE doc_date <= %s ORDER BY 1", [today])]
    first_year = years[0] if years else today.year
    pkey, label, start, end, pstart, pend, plabel = _vendor_period(request, today, first_year)
    # "paid" splits into cash from the bank and payables settled by a company credit card (drawn on the card
    # holding account — the card issuer's own row carries the cash), so the total never counts a purchase twice
    NETAMT = "SUM(CASE WHEN doc_type='VC' THEN -amount ELSE amount END)"
    CARDAMT = "SUM(CASE WHEN cash_acct IN %s THEN (CASE WHEN doc_type='VC' THEN -amount ELSE amount END) ELSE 0 END)"   # inserted verbatim: one %s placeholder
    paid = {r["vendor_id"]: r for r in fetch_dict(
        """SELECT vendor_id, %s amt, %s card, COUNT(*) n, MAX(doc_date) last, MAX(vendor_name) vendor_name
           FROM finance_apcheck WHERE doc_date BETWEEN %%s AND %%s GROUP BY 1""" % (NETAMT, CARDAMT), [CARD_CLEARING_ACCOUNTS, start, end])}
    prior = {r["vendor_id"]: r for r in fetch_dict(
        """SELECT vendor_id, %s amt, %s card, COUNT(*) n
           FROM finance_apcheck WHERE doc_date BETWEEN %%s AND %%s GROUP BY 1""" % (NETAMT, CARDAMT), [CARD_CLEARING_ACCOUNTS, pstart, pend])} if pstart else {}
    open_ap = {r["vendor_id"]: r for r in fetch_dict(
        "SELECT vendor_id, SUM(sign*doc_bal) bal, COUNT(*) n FROM finance_apopendocument GROUP BY 1")}
    open_po = {r["vendor_id"]: r for r in fetch_dict(
        """SELECT vendor_id, SUM((qty_ord - COALESCE(qty_rcvd,0)) * COALESCE(unit_cost,0)) amt, COUNT(DISTINCT po_nbr) n
           FROM finance_poline WHERE status='O' AND qty_ord > COALESCE(qty_rcvd,0) GROUP BY 1""")}
    rcpt_min = fetch_dict("SELECT MIN(rcpt_date) d FROM finance_poreceiptline")[0]["d"]
    rcvd = {r["vendor_id"]: r for r in fetch_dict(
        "SELECT vendor_id, SUM(ext_cost) amt, MAX(rcpt_date) last FROM finance_poreceiptline WHERE rcpt_date BETWEEN %s AND %s GROUP BY 1", [start, end])}
    master = {v.vendor_id: v for v in SLVendor.objects.all()}
    rows = []
    for vid in set(paid) | set(open_ap) | set(open_po) | set(rcvd):
        v = master.get(vid)
        name = (v.name if v else "") or (paid.get(vid) or {}).get("vendor_name") or vid
        if q and q.lower() not in name.lower() and q.lower() not in vid.lower():
            continue
        g = lambda d, k: (d.get(vid) or {}).get(k) or 0
        pd_all, pd_card = g(paid, "amt"), g(paid, "card")
        pr_all, pr_card = g(prior, "amt"), g(prior, "card")
        pd_, pr = pd_all - pd_card, pr_all - pr_card       # cash from the bank
        rows.append({"id": vid, "name": name, "cls": v.class_id if v else "", "terms": v.terms if v else "",
                     "city": ", ".join(x for x in ((v.city if v else ""), (v.state if v else "")) if x),
                     "paid": pd_, "card": pd_card, "paid_all": pd_all, "paid_n": g(paid, "n"), "last_paid": (paid.get(vid) or {}).get("last"),
                     "prior": pr, "prior_card": pr_card, "prior_n": g(prior, "n"), "delta": (pd_ - pr), "delta_pct": ((pd_ - pr) / pr) if pr else None,
                     "open_ap": g(open_ap, "bal"), "open_ap_n": g(open_ap, "n"),
                     "open_po": g(open_po, "amt"), "open_po_n": g(open_po, "n"),
                     "rcvd": g(rcvd, "amt"), "last_rcvd": (rcvd.get(vid) or {}).get("last"),
                     "family": rp.is_related_vendor(vid)})   # owner-family: labelled, never treated as a supplier anomaly
    rows.sort(key=lambda r: -abs(r["paid_all"]))
    tot = {k: sum((r[k] for r in rows), Decimal(0)) for k in ("paid", "card", "prior", "prior_card", "open_ap", "open_po", "rcvd")}
    tot["delta"] = tot["paid"] - tot["prior"]
    tot["delta_pct"] = (tot["delta"] / tot["prior"]) if tot["prior"] else None
    tot["paid_vendors"] = sum(1 for r in rows if r["paid"] or r["card"])
    period = {"key": pkey, "label": label, "start": start, "end": end, "prior_label": plabel, "prior_start": pstart, "prior_end": pend,
              "years": list(reversed(years)), "first_year": first_year, "rcpt_from": rcpt_min,
              "rcpt_partial": bool(rcpt_min and start < rcpt_min), "custom_from": request.GET.get("from", ""), "custom_to": request.GET.get("to", "")}
    return render(request, "dashboard/vendors.html", _ctx(request, "finance-vendors", rows=rows, tot=tot, q=q, period=period))


def vendor_detail(request, vendor_id):
    """One vendor's whole relationship. Payments (every year since 2013): by year with the company share and
    how they were paid (ACH / printed check / EFT / hand check, and the cash or holding account they hit);
    where the money went — division and GL account of every expense line (APTran) by year — and the jobs it
    was charged to; payment behaviour (days held vs terms, early payments, discounts); recent payments with
    the invoices each covered and their expense split; open AP, open PO lines, receipts, top items."""
    from apps.finance.models import APCheck, APOpenDocument, POLine, POReceiptLine, SLVendor, CARD_CLEARING_ACCOUNTS, settled_by_card
    v = SLVendor.objects.filter(vendor_id=vendor_id).first()
    today = timezone.localdate()
    yr = today - timedelta(days=365)
    D0 = Decimal(0)
    checks = list(APCheck.objects.filter(vendor_id=vendor_id).order_by("-doc_date", "-ref_nbr")[:60])
    _attach_check_apps(checks)
    name = (v.name if v else "") or (checks[0].vendor_name if checks else vendor_id)
    NET = "SUM(CASE WHEN doc_type='VC' THEN -amount ELSE amount END)"

    # ---- payments by year, with the company's AP total that year and the payment mix
    years = fetch_dict("""SELECT EXTRACT(year FROM doc_date)::int y, COUNT(*) n, %s amt, MAX(CASE WHEN doc_type <> 'VC' THEN amount END) biggest,
                                 SUM(CASE WHEN ((doc_type='CK' AND ref_nbr LIKE '01%%%%') OR doc_type='EP') AND cash_acct NOT IN %%s THEN amount ELSE 0 END) ach,
                                 SUM(CASE WHEN ((doc_type='CK' AND ref_nbr NOT LIKE '01%%%%') OR doc_type='HC') AND cash_acct NOT IN %%s THEN amount ELSE 0 END) chk,
                                 SUM(CASE WHEN cash_acct IN %%s AND doc_type <> 'VC' THEN amount ELSE 0 END) card,
                                 SUM(CASE WHEN doc_type='VC' THEN 1 ELSE 0 END) voids, MIN(doc_date) first_d, MAX(doc_date) last_d
                          FROM finance_apcheck WHERE vendor_id=%%s AND doc_date <= %%s GROUP BY 1 ORDER BY 1 DESC""" % NET,
                       [CARD_CLEARING_ACCOUNTS, CARD_CLEARING_ACCOUNTS, CARD_CLEARING_ACCOUNTS, vendor_id, today])
    company = {r["y"]: r["amt"] for r in fetch_dict("SELECT EXTRACT(year FROM doc_date)::int y, %s amt FROM finance_apcheck WHERE doc_date <= %%s GROUP BY 1" % NET, [today])}
    prev = None
    for r in reversed(years):
        r["share"] = (r["amt"] / company[r["y"]]) if company.get(r["y"]) else None
        r["delta"] = (r["amt"] - prev["amt"]) if prev else None
        r["delta_pct"] = ((r["amt"] - prev["amt"]) / prev["amt"]) if (prev and prev["amt"]) else None
        r["ach_share"] = (r["ach"] / (r["ach"] + r["chk"])) if (r["ach"] + r["chk"]) else None
        paid_pos = r["ach"] + r["chk"] + r["card"]          # non-void payments, whichever way they settled
        r["card_share"] = (r["card"] / paid_pos) if paid_pos else None
        prev = r
    first_paid = min((r["first_d"] for r in years), default=None)
    total_paid = sum((r["amt"] for r in years), D0)
    total_n = sum(r["n"] for r in years)
    # YTD vs the same span last year, rank among vendors this year
    jan1 = date(today.year, 1, 1)
    try:
        same_day = today.replace(year=today.year - 1)
    except ValueError:
        same_day = today.replace(year=today.year - 1, day=28)
    ytd_rows = fetch_dict("SELECT vendor_id, %s amt FROM finance_apcheck WHERE doc_date BETWEEN %%s AND %%s GROUP BY 1 ORDER BY 2 DESC" % NET, [jan1, today])
    ytd = next((r["amt"] for r in ytd_rows if r["vendor_id"] == vendor_id), D0)
    rank = next((i + 1 for i, r in enumerate(ytd_rows) if r["vendor_id"] == vendor_id), None)
    ytd_company = sum((r["amt"] for r in ytd_rows), D0)
    prior_ytd = fetch_dict("SELECT %s amt FROM finance_apcheck WHERE vendor_id=%%s AND doc_date BETWEEN %%s AND %%s" % NET, [vendor_id, date(today.year - 1, 1, 1), same_day])[0]["amt"] or D0
    ytd_card = fetch_dict("SELECT COALESCE(%s, 0) amt FROM finance_apcheck WHERE vendor_id=%%s AND doc_date BETWEEN %%s AND %%s AND cash_acct IN %%s" % NET,
                          [vendor_id, jan1, today, CARD_CLEARING_ACCOUNTS])[0]["amt"] or D0
    k = fetch_dict("SELECT %s amt, COUNT(*) n FROM finance_apcheck WHERE vendor_id=%%s AND doc_date >= %%s" % NET, [vendor_id, yr])[0]

    # ---- how they are paid: type mix over the whole history, cash / holding account per payment, bank clearing
    cash_lines = fetch_dict("""SELECT ref_nbr, gl_account, SUM(amount) amt FROM finance_apvoucherline
                               WHERE vendor_id=%s AND tran_type IN ('CK','HC','EP','VC','ZC') AND gl_account LIKE '1%%' GROUP BY 1,2""", [vendor_id])
    acct_descr = {r["acct"]: (r["descr"] or "").strip() for r in fetch_dict("SELECT DISTINCT ON (acct) acct, descr FROM finance_glaccountbalance ORDER BY acct, fiscal_year DESC")}
    cash_of = {r["ref_nbr"]: r["gl_account"] for r in cash_lines}
    cash_mix = defaultdict(lambda: {"n": 0, "amt": D0})
    for r in cash_lines:
        cash_mix[r["gl_account"]]["n"] += 1
        cash_mix[r["gl_account"]]["amt"] += r["amt"] or D0
    cash_mix = sorted([{"acct": a, "descr": acct_descr.get(a, a), **m} for a, m in cash_mix.items()], key=lambda m: -m["amt"])

    def how_paid(c):
        if c.doc_type == "VC":
            return "void"
        if settled_by_card(getattr(c, "cash_acct", "")):
            return "card"          # cleared through the card holding account — no cash to the vendor
        if c.doc_type == "EP":
            return "EFT"
        if c.doc_type == "HC":
            return "hand check"
        if c.doc_type == "ZC":
            return "zero check"
        return "ACH" if (c.ref_nbr or "").startswith("01") else "check"
    mix = defaultdict(lambda: {"n": 0, "amt": D0})
    for r in fetch_dict("SELECT ref_nbr, doc_type, amount, cash_acct FROM finance_apcheck WHERE vendor_id=%s", [vendor_id]):
        class _C:  # noqa — tiny shim so the same rule serves rows and model instances
            pass
        c = _C(); c.doc_type, c.ref_nbr, c.cash_acct = r["doc_type"], r["ref_nbr"], r["cash_acct"]
        h = how_paid(c)
        mix[h]["n"] += 1
        mix[h]["amt"] += r["amount"] or D0
    paid_total_pos = sum((m["amt"] for h, m in mix.items() if h != "void"), D0)
    pay_mix = sorted([{"how": h, "share": (m["amt"] / paid_total_pos) if (paid_total_pos and h != "void") else None, **m} for h, m in mix.items()], key=lambda m: -m["amt"])
    pay_mix = [m for m in pay_mix if m["how"] == "void" or m["share"] is None or m["share"] >= Decimal("0.005")]   # drop the 0% noise
    cleared = {r["sl_ref"]: r["d"] for r in fetch_dict("SELECT sl_ref, MIN(posted_date) d FROM finance_bankstatementline WHERE sl_ref = ANY(%s) GROUP BY 1", [[c.ref_nbr for c in checks]])} if checks else {}
    for c in checks:
        c.cash_acct = c.cash_acct or cash_of.get(c.ref_nbr, "")   # the payment doc's own cash account; AP lines as fallback
        c.how = how_paid(c)
        c.cash_descr = acct_descr.get(c.cash_acct, c.cash_acct)
        c.cleared = cleared.get(c.ref_nbr)
    # payment cadence: median days between distinct payment dates (last 3 years)
    dates = sorted({r["d"] for r in fetch_dict("SELECT DISTINCT doc_date d FROM finance_apcheck WHERE vendor_id=%s AND doc_type <> 'VC' AND doc_date >= %s", [vendor_id, today - timedelta(days=3 * 365)])})
    gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
    cadence = gaps[len(gaps) // 2] if gaps else None

    # ---- payment behaviour (12 months): days held vs terms, early share, discounts taken
    w = fetch_dict("""SELECT SUM(adj_amount * (check_date - COALESCE(invoice_date, voucher_date))) num,
                             SUM(CASE WHEN COALESCE(invoice_date, voucher_date) IS NOT NULL THEN adj_amount END) den,
                             SUM(CASE WHEN check_date - COALESCE(invoice_date, voucher_date) < 30 THEN adj_amount ELSE 0 END) early,
                             SUM(CASE WHEN due_date IS NOT NULL AND check_date > due_date THEN adj_amount ELSE 0 END) late,
                             SUM(adj_amount) all_amt, SUM(disc_amount) disc, COUNT(DISTINCT voucher_ref) vouchers
                      FROM finance_apcheckapplication
                      WHERE vendor_id=%s AND check_type != 'VC' AND check_date >= %s""", [vendor_id, yr])[0]
    avg_wait = int(w["num"] / w["den"]) if w["den"] else None
    early_pct = (w["early"] / w["all_amt"]) if w["all_amt"] else None
    late_pct = (w["late"] / w["all_amt"]) if w["all_amt"] else None
    terms_days = int(v.terms) if (v and (v.terms or "").strip().isdigit()) else None

    # ---- where the money went, per year since 2013: the expense side of vouchers (APTran) by division = subaccount,
    # plus, for PO purchases (the voucher only clears PO clearing), what the PO RECEIPTS debited (POTran): the
    # receipt's subaccount, or the project's division when the receipt names a job; sub 0000 without a job = warehouse stock.
    year_cols = [today.year - i for i in range(5)]
    earliest = year_cols[-1]
    clearing = {a for a, d in acct_descr.items() if "CLEARING" in d.upper()} | {"20001"}
    vo = fetch_dict("""SELECT left(gl_subaccount, 3) div, gl_account acct, EXTRACT(year FROM tran_date)::int y, project_id_raw pk,
                              SUM(CASE WHEN dr_cr = 'D' THEN amount ELSE -amount END) amt, COUNT(*) n
                       FROM finance_apvoucherline WHERE vendor_id=%s AND tran_type IN ('VO','AD') GROUP BY 1,2,3,4""", [vendor_id])
    clearing_amt = sum((r["amt"] for r in vo if r["acct"] in clearing), D0)
    clearing_n = sum(r["n"] for r in vo if r["acct"] in clearing)
    rc = fetch_dict("""SELECT gl_subaccount sub, gl_account acct, EXTRACT(year FROM rcpt_date)::int y, project_id_raw pk, SUM(ext_cost) amt, COUNT(*) n
                       FROM finance_poreceiptdist WHERE vendor_id=%s GROUP BY 1,2,3,4""", [vendor_id])
    div_names = {d.code: d.name for d in Division.objects.all()}
    pkeys = {r["pk"].strip().upper() for r in vo + rc if r["pk"]}
    pdiv = {}
    if pkeys:
        for cpn, code in Project.objects.filter(canonical_project_number__in=pkeys).values_list("canonical_project_number", "division__code"):
            pdiv[cpn] = code or ""

    def div_label(code):
        return "%s %s" % (code, div_names[code]) if div_names.get(code) else "sub %s" % code
    rows_all = []
    for r in vo:
        if r["acct"] in clearing:
            continue
        rows_all.append({"dkey": r["div"], "dlabel": div_label(r["div"]), "acct": r["acct"], "y": r["y"], "amt": r["amt"], "n": r["n"], "pk": r["pk"], "src": "vouchers"})
    for r in rc:
        pk = r["pk"].strip().upper() if r["pk"] else ""
        code = (pdiv.get(pk) or r["sub"][:3]) if pk else r["sub"][:3]
        if code == "000" and not pk:
            dkey, dlabel = "000-stock", "warehouse stock (sub 0000, no job)"
        else:
            dkey, dlabel = code, div_label(code)
        rows_all.append({"dkey": dkey, "dlabel": dlabel, "acct": r["acct"], "y": r["y"], "amt": r["amt"], "n": r["n"], "pk": pk, "src": "receipts"})

    def bucket(rows, key_fn, label_fn):
        out = {}
        for r in rows:
            k = key_fn(r)
            b = out.setdefault(k, {"key": k, "label": label_fn(r), "years": {y: D0 for y in year_cols}, "earlier": D0, "total": D0, "n": 0, "jobs": set(), "src": set()})
            if r["y"] in b["years"]:
                b["years"][r["y"]] += r["amt"]
            elif r["y"] and r["y"] < earliest:
                b["earlier"] += r["amt"]
            b["total"] += r["amt"]
            b["n"] += r["n"]
            b["src"].add(r["src"])
            if r["pk"]:
                b["jobs"].add(r["pk"])
        rows_out = sorted(out.values(), key=lambda b: -abs(b["total"]))
        total = sum((b["total"] for b in rows_out), D0)
        for b in rows_out:
            b["share"] = (b["total"] / total) if total else None
            b["cols"] = [b["years"][y] for y in year_cols]
            b["jobs"] = len(b["jobs"])
            b["src"] = " + ".join(sorted(b["src"]))
        return rows_out, total
    by_div, exp_total = bucket(rows_all, lambda r: r["dkey"], lambda r: r["dlabel"])
    by_acct, _ = bucket(rows_all, lambda r: r["acct"], lambda r: "%s %s" % (r["acct"], acct_descr.get(r["acct"], "")))
    has_earlier = any(b["earlier"] for b in by_div)
    exp_years = {y: sum((b["years"][y] for b in by_div), D0) for y in year_cols}
    exp_earlier = sum((b["earlier"] for b in by_div), D0)
    rcpt_total = sum((r["amt"] for r in rc), D0)
    rcpt_n = sum(r["n"] for r in rc)
    # jobs this vendor was charged to: voucher lines naming a project + receipts naming a project
    jobs = {}
    for r in rows_all:
        if not r["pk"]:
            continue
        j = jobs.setdefault(r["pk"], {"pk": r["pk"], "amt": D0, "n": 0, "years": set()})
        j["amt"] += r["amt"]
        j["n"] += r["n"]
        if r["y"]:
            j["years"].add(r["y"])
    job_total = {"jobs": len(jobs), "amt": sum((j["amt"] for j in jobs.values()), D0)}
    job_rows = sorted(jobs.values(), key=lambda j: -abs(j["amt"]))[:15]
    pmap = {p.canonical_project_number: p for p in Project.objects.filter(canonical_project_number__in=[j["pk"] for j in job_rows]).select_related("division")}
    for j in job_rows:
        j["project"] = pmap.get(j["pk"])
        j["first_y"], j["last_y"] = (min(j["years"]), max(j["years"])) if j["years"] else (None, None)
    # expense split of the vouchers behind the listed payments (acct · division · job · amount); PO-clearing lines
    # are explained by their receipts (what the receipt debited, for which job)
    vrefs = {a.voucher_ref for c in checks for a in c.vouchers}
    vlines = defaultdict(list)
    if vrefs:
        for r in fetch_dict("""SELECT ref_nbr, gl_account, gl_subaccount, project_id_raw, task_id, tran_desc, rcpt_nbr, po_nbr, CASE WHEN dr_cr = 'D' THEN amount ELSE -amount END amt
                               FROM finance_apvoucherline WHERE vendor_id=%s AND tran_type IN ('VO','AD') AND ref_nbr = ANY(%s) ORDER BY ref_nbr, line_nbr""", [vendor_id, list(vrefs)]):
            r["acct_descr"] = acct_descr.get(r["gl_account"], "")
            r["clearing"] = r["gl_account"] in clearing
            r["div"] = r["gl_subaccount"][:3]
            r["div_name"] = div_names.get(r["div"], "")
            r["pk"] = r["project_id_raw"].strip().upper() if r["project_id_raw"] else ""
            r["via"] = []
            vlines[r["ref_nbr"]].append(r)
        rnbrs = {r["rcpt_nbr"] for ls in vlines.values() for r in ls if r["clearing"] and r["rcpt_nbr"]}
        if rnbrs:
            via = defaultdict(list)
            for r in fetch_dict("""SELECT rcpt_nbr, gl_subaccount sub, project_id_raw pk, SUM(ext_cost) amt FROM finance_poreceiptdist
                                   WHERE vendor_id=%s AND rcpt_nbr = ANY(%s) GROUP BY 1,2,3 ORDER BY 4 DESC""", [vendor_id, list(rnbrs)]):
                pk = r["pk"].strip().upper() if r["pk"] else ""
                code = (pdiv.get(pk) or r["sub"][:3]) if pk else r["sub"][:3]
                via[r["rcpt_nbr"]].append({"div": code, "div_name": ("warehouse stock" if (code == "000" and not pk) else div_names.get(code, "")), "pk": pk, "amt": r["amt"]})
            for ls in vlines.values():
                for r in ls:
                    if r["clearing"]:
                        r["via"] = via.get(r["rcpt_nbr"], [])
        need = {r["pk"] for ls in vlines.values() for r in ls if r["pk"]} | {x["pk"] for ls in vlines.values() for r in ls for x in r["via"] if x["pk"]}
        need -= set(pmap)
        if need:
            for p in Project.objects.filter(canonical_project_number__in=need).select_related("division"):
                pmap[p.canonical_project_number] = p
        for ls in vlines.values():
            for r in ls:
                r["project"] = pmap.get(r["pk"]) if r["pk"] else None
                for x in r["via"]:
                    x["project"] = pmap.get(x["pk"]) if x["pk"] else None
    for c in checks:
        for a in c.vouchers:
            # collapse a voucher's lines to one entry per division · account · job (a phone bill has one line per number)
            agg = {}
            for r in vlines.get(a.voucher_ref, []):
                key = (r["clearing"], r["div"], r["gl_account"], r["pk"], tuple((x["div"], x["pk"]) for x in r["via"]))
                g = agg.setdefault(key, dict(r, amt=D0, n=0))
                g["amt"] += r["amt"]
                g["n"] += 1
            a.lines = sorted(agg.values(), key=lambda g: -abs(g["amt"]))

    # ---- open items, receipts (unchanged)
    open_docs = list(APOpenDocument.objects.filter(vendor_id=vendor_id).order_by("due_date"))
    open_ap = sum((d.sign * d.doc_bal for d in open_docs), D0)
    past_due = sum(1 for d in open_docs if d.sign > 0 and d.days_past_due and d.days_past_due > 0)
    po_open = [ln for ln in POLine.objects.filter(vendor_id=vendor_id, status="O")
               .select_related("project", "deduced_project").order_by("-po_date")
               if (ln.qty_ord or 0) > (ln.qty_rcvd or 0)]
    for ln in po_open:
        ln.open_qty = (ln.qty_ord or 0) - (ln.qty_rcvd or 0)
        ln.open_amt = ln.open_qty * (ln.unit_cost or 0)
    open_po = sum((ln.open_amt for ln in po_open), D0)
    rcvd_yr = fetch_dict("SELECT COALESCE(SUM(ext_cost),0) a FROM finance_poreceiptline WHERE vendor_id=%s AND rcpt_date >= %s",
                         [vendor_id, yr])[0]["a"]
    receipts = list(POReceiptLine.objects.filter(vendor_id=vendor_id, rcpt_date__gte=today - timedelta(days=90))
                    .select_related("project", "deduced_project").order_by("-rcpt_date")[:60])
    months = fetch_dict("""SELECT date_trunc('month', doc_date)::date m, %s amt, COUNT(*) n
                           FROM finance_apcheck WHERE vendor_id=%%s AND doc_date >= %%s GROUP BY 1 ORDER BY 1""" % NET, [vendor_id, today - timedelta(days=14 * 30)])
    mmax = max((abs(m["amt"]) for m in months), default=Decimal(1)) or Decimal(1)
    for m in months:
        m["px"] = max(2, int(44 * abs(m["amt"]) / mmax))
    top_items = fetch_dict("""SELECT item_id, MAX(descr) descr, SUM(qty) qty, SUM(ext_cost) amt, COUNT(*) n
                              FROM finance_poreceiptline WHERE vendor_id=%s AND rcpt_date >= %s AND item_id != ''
                              GROUP BY 1 ORDER BY 4 DESC LIMIT 12""", [vendor_id, yr])
    lines_loaded = bool(rows_all) or fetch_dict("SELECT EXISTS (SELECT 1 FROM finance_apvoucherline LIMIT 1) e")[0]["e"]
    # owner-family vendors: the early-payment warning is a supplier-discipline signal and does not apply to
    # shareholder distributions, so it is suppressed here (apps/core/related_parties). Every figure is unchanged.
    is_family = rp.is_related_vendor(vendor_id)
    if is_family:
        early_pct = None
    return render(request, "dashboard/vendor_detail.html",
                  _ctx(request, "finance-vendors", ytd_card=ytd_card, v=v, vid=vendor_id, vname=name, checks=checks,
                       is_family=is_family, family_title=rp.CHIP_TITLE, family_chip=rp.CHIP_LABEL,
                       paid_yr=k["amt"] or 0, paid_n=k["n"] or 0, avg_wait=avg_wait, early_pct=early_pct, late_pct=late_pct, terms_days=terms_days,
                       disc=w["disc"] or D0, vouchers_12=w["vouchers"] or 0,
                       years=years, first_paid=first_paid, total_paid=total_paid, total_n=total_n,
                       ytd=ytd, prior_ytd=prior_ytd, ytd_delta=ytd - prior_ytd, ytd_delta_pct=((ytd - prior_ytd) / prior_ytd) if prior_ytd else None,
                       rank=rank, rank_of=len(ytd_rows), ytd_share=(ytd / ytd_company) if ytd_company else None, same_day=same_day,
                       pay_mix=pay_mix, cash_mix=cash_mix, cadence=cadence,
                       year_cols=year_cols, by_div=by_div, by_acct=by_acct, exp_total=exp_total, exp_years=[exp_years[y] for y in year_cols],
                       exp_earlier=exp_earlier, has_earlier=has_earlier, earliest=earliest, lines_loaded=lines_loaded,
                       clearing_amt=clearing_amt, clearing_n=clearing_n, rcpt_total=rcpt_total, rcpt_n=rcpt_n,
                       job_rows=job_rows, job_total=job_total,
                       open_docs=open_docs, open_ap=open_ap, past_due=past_due, po_open=po_open, open_po=open_po,
                       rcvd_yr=rcvd_yr, receipts=receipts, months=months, top_items=top_items, today=today))


# ============================================================================ project snapshot (docs/project_snapshot_spec.md)
SNAP_BIG = {"invoice": Decimal("25000"), "payment": Decimal("25000"), "check": Decimal("25000"),
            "po": Decimal("10000"), "receipt": Decimal("10000")}


def project_snapshot(request):
    """Daily / Weekly Project Snapshot: what happened on the jobs in a window — who worked where,
    window economics (Δearned vs estimated labor), job health with trend, money and material
    movement. Spec: docs/project_snapshot_spec.md. NEVER shows field-crew ratings (shared page).
    Day windows: business days, Friday = Fri+Sat+Sun. Week windows: calendar Mon-Sun."""
    from apps.analytics import finance_wip as _fw
    from apps.analytics.finance_wip import pct_at_from_series, pct_series
    from apps.analytics.labor_rates import loaded_rate_for, rate_tables
    from apps.dashboard.snapshot_windows import day_window, week_window
    from apps.finance.models import APCheck, ARInvoice, ARPaymentApplication, POLine, POReceiptLine

    view = "week" if request.GET.get("view") == "week" else "day"
    # company-wide by default (spec §1) — unlike most pages, no implicit 070; explicit ?div= filters
    from apps.core.models import Division
    div_param = request.GET.get("div", "")
    if div_param == "010":   # 010 has no projects: its own body, same header (docs/010_daily_snapshot_plan.md)
        q = request.GET.copy()
        q.pop("pm", None)
        return redirect(reverse("dashboard:sales010_snapshot") + ("?" + q.urlencode() if q else ""))
    d = Division.objects.filter(code=div_param).first() if div_param and div_param != "all" else None
    div_code = d.code if d else ""
    pm_key = request.GET.get("pm", "")
    pm = Employee.objects.filter(employee_key=pm_key).first() if pm_key else None

    # ---- window + navigation over days/weeks that actually had field activity ----
    today = timezone.localdate()
    raw_key = None
    for param in ("day", "week"):
        if request.GET.get(param):
            try:
                raw_key = date.fromisoformat(request.GET[param])
            except ValueError:
                pass
    win_fn = week_window if view == "week" else day_window
    start, end, label, prev_key, next_key = win_fn(raw_key or today)
    active_days = [r["k"] for r in fetch_dict("""
        SELECT DISTINCT (CASE WHEN EXTRACT(dow FROM work_date) IN (6,0) THEN work_date - ((EXTRACT(dow FROM work_date)::int + 2) %% 7) ELSE work_date END) k
        FROM operations_timeentry WHERE source_status=1 AND form_type=1 AND work_date >= %s AND work_date <= %s
        ORDER BY 1 DESC""", [today - timedelta(days=460), today])]
    if view == "week":
        keys = sorted({k - timedelta(days=k.weekday()) for k in active_days}, reverse=True)
    else:
        keys = sorted({day_window(k)[0] for k in active_days}, reverse=True)
    if raw_key is None and keys:
        start, end, label, prev_key, next_key = win_fn(keys[0])
    prev_key = next((k for k in keys if k < start), None)
    next_key = next((k for k in reversed(keys) if k > start), None)
    strip = fetch_dict("""
        SELECT {grain} k, SUM(hours_total) h FROM (
            SELECT (CASE WHEN EXTRACT(dow FROM work_date) IN (6,0) THEN work_date - ((EXTRACT(dow FROM work_date)::int + 2) %% 7) ELSE work_date END) wd, hours_total
            FROM operations_timeentry WHERE source_status=1 AND form_type=1 AND work_date >= %s) x
        GROUP BY 1 ORDER BY 1""".format(grain="date_trunc('week', wd)::date" if view == "week" else "wd"),
        [today - timedelta(days=84 if view == "week" else 30)])

    # ---- project filter fragment (division / PM) ----
    pwhere, pparams = ["TRUE"], []
    if d:
        pwhere.append("p.division_id = %s"); pparams.append(d.id)
    if pm:
        pwhere.append("p.project_manager_id = %s"); pparams.append(pm.id)
    PW = " AND ".join(pwhere)

    # ---- field work in window, by job and by person ----
    work = fetch_dict("""
        SELECT te.project_id, p.canonical_project_number cpn, p.display_number, p.title, p.contract_value cv,
               p.is_internal_bucket, p.pm_percent_complete, p.billed_revenue, p.budget_labor_hours, p.ptt_hours_total, p.division_id,
               dd.code division, pmgr.employee_key pm_key, pmgr.canonical_name pm_name, c.canonical_name customer,
               te.employee_id, e.employee_key, e.canonical_name person, e.ptt_employee_type,
               SUM(te.hours_total) h, SUM(te.hours_ot) ot,
               COUNT(DISTINCT te.work_date) days_worked,
               array_agg(DISTINCT te.work_type_choice) work_types,
               (array_agg(te.activity_note ORDER BY te.work_date DESC))[1] note
        FROM operations_timeentry te
        JOIN core_project p ON p.id = te.project_id
        JOIN core_division dd ON dd.id = p.division_id
        LEFT JOIN core_employee pmgr ON pmgr.id = p.project_manager_id
        LEFT JOIN core_customer c ON c.id = p.customer_id
        LEFT JOIN core_employee e ON e.id = te.employee_id
        WHERE te.source_status=1 AND te.form_type=1 AND te.work_date BETWEEN %%s AND %%s AND %s
        GROUP BY te.project_id, p.canonical_project_number, p.display_number, p.title, p.contract_value,
                 p.is_internal_bucket, p.pm_percent_complete, p.billed_revenue, p.budget_labor_hours, p.ptt_hours_total, p.division_id,
                 dd.code, pmgr.employee_key, pmgr.canonical_name, c.canonical_name, te.employee_id, e.employee_key,
                 e.canonical_name, e.ptt_employee_type""" % PW, [start, end] + pparams)
    touched_ids = sorted({r["project_id"] for r in work})

    # ---- field hours in window by job and person (estimated loaded labor cost) ----
    emp_rates, div_rates = rate_tables(end)
    jobs = {}
    for r in work:
        j = jobs.setdefault(r["project_id"], {
            "display": r["display_number"], "title": r["title"], "division": r["division"],
            "internal": r["is_internal_bucket"],
            "hours": Decimal(0), "ot": Decimal(0), "labor_cost": Decimal(0), "crew": []})
        j["hours"] += r["h"] or 0
        j["ot"] += r["ot"] or 0
        rate = loaded_rate_for(r["employee_id"], r["division_id"], emp_rates, div_rates)
        cost = (r["h"] or 0) * rate
        j["labor_cost"] += cost
        j["crew"].append({"key": r["employee_key"], "name": r["person"], "h": r["h"], "ot": r["ot"],
                          "days": r["days_worked"], "cost": cost, "note": (r["note"] or "").strip(),
                          "work_types": [w for w in (r["work_types"] or []) if w]})
    for j in jobs.values():
        j["crew"].sort(key=lambda cr: -(cr["h"] or 0))
        j["lone"] = len(j["crew"]) == 1

    # ---- 9999 overhead buckets, grouped by bucket job (division-labeled, person drill) ----
    oh_rows = sorted(({"bucket": j["display"], "title": j["title"], "division": j["division"],
                       "hours": j["hours"], "ot": j["ot"], "cost": j["labor_cost"], "people": j["crew"]}
                      for j in jobs.values() if j["internal"]), key=lambda b: -b["hours"])
    oh_tot = {"hours": sum((b["hours"] for b in oh_rows), Decimal(0)),
              "ot": sum((b["ot"] for b in oh_rows), Decimal(0)),
              "cost": sum((b["cost"] for b in oh_rows), Decimal(0))}

    # ---- job activity: ONE merged table (spec §3.2+§3.6; Owner 2026-08-28) — every open job with
    # a % update, field hours, or posted non-labor cost in the window. Worked jobs additionally
    # carry crew drill + health/trend. Net = Δearned − est. labor − other posted cost, sorted
    # SIGNED descending (winners top, losers bottom — never by absolute value). ----
    cand = fetch_dict("""
        SELECT DISTINCT ON (o.project_id) o.project_id, e.canonical_name updated_by, e.employee_key updated_key, o.ptt_last_updated_at upd_at, o.ptt_percent_complete raw_pct
        FROM operations_percentcompleteobservation o
        JOIN core_project p ON p.id = o.project_id
        LEFT JOIN core_employee e ON e.id = o.ptt_last_updated_by_id
        WHERE o.ptt_last_updated_at::date BETWEEN %%s AND %%s AND NOT p.is_internal_bucket AND %s
        ORDER BY o.project_id, o.ptt_last_updated_at DESC""" % PW, [start, end] + pparams)
    cand_map = {r["project_id"]: r for r in cand}
    # SL cost postings dated in the window other than labor (labor is the PTT-hours estimate):
    # material incl. purchase variance, subcontract, other direct. Open, non-internal jobs only.
    other_cost = {r["project_id"]: r["amt"] for r in fetch_dict("""
        SELECT t.project_id, SUM(t.amount) amt
        FROM finance_projectfinancialtransaction t JOIN core_project p ON p.id = t.project_id
        WHERE t.transaction_date BETWEEN %%s AND %%s AND t.category IN ('material','subcontract','other_direct')
          AND p.lifecycle_state IN %%s AND NOT p.is_internal_bucket AND %s
        GROUP BY 1 HAVING SUM(t.amount) != 0""" % PW, [start, end, Q_.OPEN] + pparams)}
    prog_ids = sorted({pid for pid, j in jobs.items() if not j["internal"]} | set(cand_map) | set(other_cost))
    pmeta = {r["id"]: r for r in fetch_dict("""
        SELECT p.id, p.canonical_project_number cpn, p.display_number, p.title, p.contract_value cv,
               p.is_internal_bucket internal, p.pm_percent_complete pct_now, p.sold_gp_percent sold_pct, dd.code division,
               p.project_mode_rule mode, pmgr.employee_key pm_key, pmgr.canonical_name pm_name
        FROM core_project p JOIN core_division dd ON dd.id = p.division_id
        LEFT JOIN core_employee pmgr ON pmgr.id = p.project_manager_id
        WHERE p.id = ANY(%s)""", [prog_ids or [0]])
    }
    cseries = pct_series(prog_ids)
    prog_rows = []
    for pid in prog_ids:
        m = pmeta.get(pid)
        if not m or m["internal"]:
            continue
        pts = cseries.get(pid, [])
        p0 = pct_at_from_series(pts, start - timedelta(days=1))
        p1 = pct_at_from_series(pts, end)
        dpct = (p1 - p0) if (p0 is not None and p1 is not None) else None
        changed = bool(dpct)
        # service agreements and T&M carry no earned value (outside WIP by rule, docs/07 §3): their % moves are shown
        # but never priced, so the Work margin agrees with every WIP figure in the app
        no_ev = _fw.is_service_agreement(m) or m["mode"] in _fw.WIP_EXCLUDED_MODES
        d_earned = ((m["cv"] or Decimal(0)) * dpct) if (changed and not no_ev) else None
        j = jobs.get(pid)
        c = cand_map.get(pid)
        # PTT's derived % can leave 0-100 (rules.pct_fraction; pct_series already skipped the point, so p0 == p1):
        # show the row with the PTT value flagged, move no earned value
        bad_pct = c["raw_pct"] if (c and c["raw_pct"] is not None and not (Decimal(0) <= c["raw_pct"] <= Decimal(1))) else None
        labor = j["labor_cost"] if j else Decimal(0)
        oth = other_cost.get(pid, Decimal(0))
        # keep only rows with real window activity: a % change, hours, posted cost, or an invalid % to flag.
        # (PTT has zero-hour note-only entries, and PMs often re-save an unchanged % — noise.)
        if not (changed or (j and j["hours"]) or oth or bad_pct is not None):
            continue
        prog_rows.append({
            "pid": pid, "cpn": m["cpn"], "display_number": m["display_number"], "title": m["title"],
            "division": m["division"], "pm_key": m["pm_key"], "pm_name": m["pm_name"], "sold_pct": m["sold_pct"],
            "updated_by": c["updated_by"] if c else None, "updated_key": (c["updated_key"] or "unknown") if c else None, "touched": bool(c), "changed": changed, "bad_pct": bad_pct,
            "no_ev": no_ev, "mode": m["mode"],
            "p0": p0, "p1": p1, "pct_now": p1 if p1 is not None else m["pct_now"],
            "d_earned": d_earned, "hours": j["hours"] if j else Decimal(0), "ot": j["ot"] if j else Decimal(0),
            "labor": labor, "other": oth, "net": (d_earned or Decimal(0)) - labor - oth,
            "crew": j["crew"] if j else [], "lone": bool(j and j["lone"]),
            "worked": bool(j and j["hours"])})
    # job health + trend from prediction history (nearest as_of at end / -7d / -30d)
    preds = fetch_dict("""SELECT project_id, as_of_date, eac_gp_percent, projected_margin_change_points, risk_level
                          FROM analytics_projectprediction WHERE project_id = ANY(%s) AND as_of_date <= %s
                          ORDER BY project_id, as_of_date DESC""", [prog_ids or [0], end])
    by_proj = defaultdict(list)
    for r in preds:
        by_proj[r["project_id"]].append(r)

    def pred_at(pid, at):
        for r in by_proj.get(pid, []):
            if r["as_of_date"] <= at:
                return r
        return None
    for r_ in prog_rows:
        cur = pred_at(r_["pid"], end)
        r_["eac_pct"] = cur["eac_gp_percent"] if cur else None
        r_["vs_sold"] = cur["projected_margin_change_points"] if cur else None
        r_["risk"] = cur["risk_level"] if cur else None
        # one GP column (Owner 2026-09-01): sold GP% → EAC GP% in bold, coloured by the direction vs sold
        if r_["vs_sold"] is None and r_["eac_pct"] is not None and r_["sold_pct"] is not None:
            r_["vs_sold"] = r_["eac_pct"] - r_["sold_pct"]
        for tag, back in (("trend7", 7), ("trend30", 30)):
            old = pred_at(r_["pid"], end - timedelta(days=back))
            r_[tag] = (cur["eac_gp_percent"] - old["eac_gp_percent"]) if (cur and old and old["as_of_date"] < cur["as_of_date"] and old["eac_gp_percent"] is not None and cur["eac_gp_percent"] is not None) else None
    prog_rows.sort(key=lambda r: -r["net"])
    prog_all, worked_all = prog_rows, [r for r in prog_rows if r["worked"]]
    # Updated-by filter (multi-select, ?upd=KEY&upd=KEY; 'none' = no % update in the window) — narrows the Job activity
    # table (both layouts and its Worked / All footers); the page's KPIs, flags and money stay window-wide
    upd_sel = [k for k in request.GET.getlist("upd") if k]
    upd_counts = {}
    for r_ in prog_all:
        if r_["touched"]:
            k_ = (r_["updated_key"], r_["updated_by"] or "—")
            upd_counts[k_] = upd_counts.get(k_, 0) + 1
    updaters = sorted(({"key": k_, "name": n_, "n": c_} for (k_, n_), c_ in upd_counts.items()), key=lambda u: u["name"])
    upd_none = sum(1 for r_ in prog_all if not r_["touched"])
    upd_names = [u["name"] for u in updaters if u["key"] in upd_sel] + (["no % update"] if "none" in upd_sel else [])
    if upd_sel:
        prog_rows = [r_ for r_ in prog_all if (r_["touched"] and r_["updated_key"] in upd_sel) or ("none" in upd_sel and not r_["touched"])]
    worked_rows = [r for r in prog_rows if r["worked"]]
    # the same jobs the WIP way (Owner, 2026-09-08): the shared job table over this window — WIP at the window end, Δ WIP
    # against the day-before baseline, the window's GP + Δ WIP, hours — keeping PM, Updated by and the crew drill
    from .job_table import window_table
    extra = {r["pid"]: {"updated_by": r["updated_by"], "updated_key": None if r["updated_key"] == "unknown" else r["updated_key"], "touched": r["touched"],
                        "changed": r["changed"], "bad_pct": r["bad_pct"], "crew": r["crew"], "worked": r["worked"], "lone": r["lone"]} for r in prog_rows}
    jt = window_table([r["pid"] for r in prog_rows], start, end, ("the " + label[0].lower() + label[1:]) if view == "week" else label,
                      today=today, identity=("number", "division", "pm", "updated", "state"), sort=request.GET.get("sort"),
                      margins=request.acc.margins, table_id="jobwip", extra=extra,
                      sets=(("all", "All activity", None), ("worked", "Jobs worked", lambda r: r.get("worked"))))
    if not request.GET.get("sort"):   # default = the Activity table's order: the job's result, signed, winners top (never absolute)
        jt["rows"].sort(key=lambda r: (r["adj_gp"] is None, -(r["adj_gp"] or 0)))

    def _tots(rows):
        return {"n": len(rows), "no_ev": sum(1 for r in rows if r.get("no_ev") and r.get("changed")),
                "d_earned": sum((r["d_earned"] for r in rows if r["d_earned"] is not None), Decimal(0)),
                "hours": sum((r["hours"] for r in rows), Decimal(0)),
                "labor": sum((r["labor"] for r in rows), Decimal(0)),
                "other": sum((r["other"] for r in rows), Decimal(0)),
                "net": sum((r["net"] for r in rows), Decimal(0))}
    prog_tot = _tots(prog_rows)              # the table's footers (after the Updated-by filter)
    tot_worked = _tots(worked_rows)
    prog_tot_all = _tots(prog_all)           # the page's KPIs (window-wide)
    tot_worked_all = _tots(worked_all)

    # ---- exceptions ----
    person_days = fetch_dict("""
        SELECT e.employee_key, e.canonical_name, te.work_date, SUM(te.hours_total) h, SUM(te.hours_ot) ot,
               COUNT(DISTINCT te.project_id) jobs
        FROM operations_timeentry te JOIN core_employee e ON e.id = te.employee_id
        JOIN core_project p ON p.id = te.project_id
        WHERE te.source_status=1 AND te.form_type=1 AND te.work_date BETWEEN %%s AND %%s AND %s
        GROUP BY 1, 2, 3""" % PW, [start, end] + pparams)
    big_ot = [r for r in person_days if (r["ot"] or 0) > 2 or (r["h"] or 0) > 10]
    spread = [r for r in person_days if r["jobs"] >= 3]
    no_hours = []
    if not pm:
        worked_keys = {r["employee_key"] for r in person_days}
        nh_where, nh_params = "", []
        if d:
            nh_where = "AND e.home_subaccount LIKE %s"; nh_params = [d.code + "%"]
        no_hours = [r for r in fetch_dict("""
            SELECT e.employee_key, e.canonical_name, e.home_subaccount FROM core_employee e
            WHERE e.ptt_active AND e.is_field_hourly %s ORDER BY e.canonical_name""" % nh_where, nh_params)
            if r["employee_key"] not in worked_keys]
    lone_jobs = [r for r in worked_all if r["lone"]]

    # ---- money in the window (project-filter aware; company-wide rows only when unfiltered) ----
    filtered = bool(d or pm)
    inv_qs = ARInvoice.objects.filter(doc_date__gte=start, doc_date__lte=end).select_related("project", "project__project_manager", "customer")
    invoices = [i for i in inv_qs if (not filtered) or (i.project and (not d or i.project.division_id == d.id) and (not pm or i.project.project_manager_id == pm.id))]
    invoices.sort(key=lambda i: -abs(i.amount))
    pay_qs = ARPaymentApplication.objects.filter(date_appl__gte=start, date_appl__lte=end).select_related("project", "project__project_manager", "customer")
    payments = [a for a in pay_qs if (not filtered) or (a.project and (not d or a.project.division_id == d.id) and (not pm or a.project.project_manager_id == pm.id))]
    payments.sort(key=lambda a: -abs(a.applied))
    checks = list(APCheck.objects.filter(doc_date__gte=start, doc_date__lte=end).order_by("-amount")) if not filtered else []
    _attach_check_apps(checks)

    # ---- material ordered / received (per-PO and per-receipt rollups with line detail) ----
    def rollup(lines, key_fields):
        groups = {}
        for ln in lines:
            k = tuple(getattr(ln, f) for f in key_fields)
            g = groups.setdefault(k, {"lines": [], "total": Decimal(0), "first": ln})
            g["lines"].append(ln)
            g["total"] += ln.ext_cost or 0
        out = sorted(groups.values(), key=lambda g: -g["total"])
        for g in out:
            g["lines"].sort(key=lambda ln: -(ln.ext_cost or 0))
            g["mixed"] = len({(ln.project_id, ln.deduced_project_id) for ln in g["lines"]}) > 1
        return out
    # div/PM filters honor the deduced tie too (a stock-received line deduced to a 070 job
    # belongs on the 070 page); deduced ties are always labeled as such in the template
    def _eff(ln):
        return ln.project or ln.deduced_project
    po_qs = POLine.objects.filter(po_date__gte=start, po_date__lte=end).select_related(
        "project", "project__project_manager", "deduced_project", "deduced_project__project_manager")
    po_lines = [ln for ln in po_qs if (not filtered) or (_eff(ln) and (not d or _eff(ln).division_id == d.id) and (not pm or _eff(ln).project_manager_id == pm.id))]
    ordered = rollup(po_lines, ("po_nbr",))
    rc_qs = POReceiptLine.objects.filter(rcpt_date__gte=start, rcpt_date__lte=end).select_related(
        "project", "project__project_manager", "deduced_project", "deduced_project__project_manager")
    rc_lines = [ln for ln in rc_qs if (not filtered) or (_eff(ln) and (not d or _eff(ln).division_id == d.id) and (not pm or _eff(ln).project_manager_id == pm.id))]
    received = rollup(rc_lines, ("rcpt_nbr",))

    # ---- KPIs ----
    tot = {
        "job_hours": tot_worked_all["hours"],
        "ot": sum((r["ot"] for r in worked_all), oh_tot["ot"]),
        "people": len({r["employee_key"] for r in person_days}),
        "jobs": len(worked_all),
        "d_earned": prog_tot_all["d_earned"], "no_ev": prog_tot_all["no_ev"],
        "labor_cost": prog_tot_all["labor"],
        "other": prog_tot_all["other"],
        "billing": sum(((i.amount if i.doc_type != "CM" else -i.amount) for i in invoices), Decimal(0)),
        "cash_in": sum((a.applied for a in payments), Decimal(0)),
        "ap_out": sum((c.amount if c.doc_type != "VC" else -c.amount for c in checks if not c.settled_by_card), Decimal(0)),
        "ap_card": sum((c.amount if c.doc_type != "VC" else -c.amount for c in checks if c.settled_by_card), Decimal(0)),
        "ordered": sum((g["total"] for g in ordered), Decimal(0)),
        "received": sum((g["total"] for g in received), Decimal(0)),
    }
    tot["hours"] = tot["job_hours"] + oh_tot["hours"]   # all field hours incl. overhead buckets
    # Owner's definition (2026-08-28): window margin = the accountability net (Δearned − est.
    # labor − other posted costs, across EVERY job with activity) minus overhead-bucket labor.
    tot["margin"] = prog_tot_all["net"] - oh_tot["cost"]
    tot["labor_all"] = prog_tot_all["labor"] + oh_tot["cost"]   # job labor + overhead-bucket labor, for the table's Work margin row
    pms = fetch_dict("""SELECT DISTINCT e.employee_key k, e.canonical_name n FROM core_project p
                        JOIN core_employee e ON e.id = p.project_manager_id
                        WHERE p.lifecycle_state IN %s ORDER BY n""", [Q_.OPEN])
    charts = {"strip": [{"d": r["k"].isoformat(), "h": _f(r["h"])} for r in strip]}
    return render(request, "dashboard/project_snapshot.html",
                  _ctx(request, "project-snapshot", view=view, start=start, end=end, label=label, div_code=div_code,
                       prev_key=prev_key, next_key=next_key, tot=tot, tot_worked=tot_worked, oh_rows=oh_rows, oh_tot=oh_tot,
                       big_ot=big_ot, spread=spread, no_hours=no_hours, lone_jobs=lone_jobs,
                       invoices=invoices, payments=payments, checks=checks, ordered=ordered, received=received,
                       prog_rows=prog_rows, prog_tot=prog_tot, prog_tot_all=prog_tot_all, jt=jt, pm_sel=pm_key, pms=pms, filtered=filtered,
                       updaters=updaters, upd_sel=upd_sel, upd_none=upd_none, upd_names=upd_names, prog_all_n=len(prog_all),
                       big=SNAP_BIG, charts=charts, group=request.GET.get("group", "job"),
                       div_nav=_division_nav(request, div_code)))



# ============================================================================ project map
def project_map(request):
    """3D map of projects (docs/project_map_plan.md). The page shell; data comes from project_map_data."""
    cfg = {"data_url": reverse("dashboard:project_map_data"), "locate_url": reverse("dashboard:project_map_locate"),
           "window": request.GET.get("w") or "90d", "margins": bool(request.acc.margins), "sales": bool(request.acc.sales010),
           "people": bool(request.acc.people), "customers": bool(request.acc.customers), "query": request.GET.urlencode()}
    return render(request, "dashboard/project_map.html", _ctx(request, "map", cfg=cfg))


def project_map_data(request):
    from apps.analytics.project_map import map_payload
    acc = request.acc
    scoped = getattr(acc, "allowed_divisions", None) if not acc.cc_all and not acc.is_superadmin else None
    payload = map_payload(request.GET.get("w") or "90d", margins=bool(acc.margins), division_codes=scoped, sales=bool(acc.sales010))
    return JsonResponse(payload)


@require_POST
def project_map_locate(request):
    """Manual location fix for one project (local table only): an address to geocode, a 'lat,lng',
    or action=reset to fall back to the automatic location."""
    from apps.core.models import Project
    from apps.geo import geocode as G
    from apps.geo.models import ProjectLocation
    from apps.ingestion import geo_loaders
    cpn = (request.POST.get("cpn") or "").strip().upper()
    project = Project.objects.filter(canonical_project_number=cpn).first()
    if not project:
        raise Http404
    if request.POST.get("action") == "reset":
        ProjectLocation.objects.filter(project=project, source="manual").delete()
        geo_loaders.build_locations(None)
        loc = ProjectLocation.objects.filter(project=project).first()
        return JsonResponse({"ok": True, "lat": loc.lat if loc else None, "lng": loc.lng if loc else None, "source": loc.source if loc else None})
    text = (request.POST.get("where") or "").strip()
    note = (request.POST.get("note") or "").strip()[:200]
    m = re.match(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$", text)
    if m:
        lat, lng, address = float(m.group(1)), float(m.group(2)), "%s, %s" % (m.group(1), m.group(2))
    else:
        if not text:
            return JsonResponse({"ok": False, "error": "Enter an address or lat,lng"}, status=400)
        try:
            hit = G.census_oneline(text) or G.Nominatim().search(text)
        except Exception as e:  # noqa
            return JsonResponse({"ok": False, "error": "geocoder error: %s" % str(e)[:120]}, status=502)
        if not hit:
            return JsonResponse({"ok": False, "error": "No match for that address"}, status=404)
        lat, lng, address = hit[0], hit[1], (hit[3] or text)[:200]
    ProjectLocation.objects.update_or_create(project=project, defaults=dict(lat=lat, lng=lng, source="manual", quality="exact",
                                                                            address=address, note=note, geocoded_address=None))
    return JsonResponse({"ok": True, "lat": lat, "lng": lng, "address": address})


# ============================================================================ divisional P&L
PNL_METRICS = [("op_income", "Operating income"), ("gross", "Gross profit"), ("revenue", "Revenue"), ("costs", "Costs"),
               ("wip", "WIP adjustment"), ("overhead", "Overhead"), ("corp_alloc", "Corporate allocation"),
               ("gross_pct", "Gross %"), ("op_pct", "Operating %")]
PNL_LINES = [("revenue", "Revenue", "revenue"), ("other_income", "Other income", "other"), ("costs", "Costs", "costs"),
             ("wip", "WIP adjustment", "wip"), ("gross", "Gross profit", "gross"), ("gross_pct", "Gross %", None),
             ("overhead", "Overhead", "overhead"), ("payroll_lag", "Unposted payroll (est.)", "overhead"),
             ("corp_alloc", "Corporate allocation", "corp"), ("op_income", "Operating income", "op"), ("op_pct", "Operating %", None)]


def _pnl_years():
    return [r["fy"] for r in fetch_dict("SELECT DISTINCT fiscal_year fy FROM finance_glaccountbalance WHERE acct_type IN ('3I','4E') ORDER BY 1")]


def finance_pnl(request):
    """Divisional P&L — the fiscal-period income statement by division for any month, quarter or
    year, with the accountants' booked WIP for closed months, PCA's estimate for open ones, the
    unposted-payroll estimate and the corporate allocation. Every number links to its drill-down.
    docs/10_divisional_pnl.md."""
    from apps.analytics.divisional_pnl import Model, Period, parse_period, periods_in_year, DIV_NAMES
    g = request.GET
    today = timezone.localdate()
    years_avail = _pnl_years()
    sel = None
    if g.get("p"):
        try:
            sel = parse_period(g["p"])
        except ValueError:
            sel = None
    kind = sel.kind if sel else (g.get("grain") if g.get("grain") in ("month", "quarter", "year") else "month")
    try:
        year = int(g.get("year") or (sel.year if sel else today.year))
    except ValueError:
        year = today.year
    year = max(int(years_avail[0]) if years_avail else today.year, min(year, today.year))
    if sel is None:
        if kind == "year":
            sel = Period("year", year)
        else:
            cands = [p for p in periods_in_year(kind, year) if p.months[0] <= "%04d%02d" % (today.year, today.month)]
            sel = cands[-1] if cands else periods_in_year(kind, year)[0]
    div = (g.get("div") or "ALL").upper()
    metric = g.get("metric") if g.get("metric") in dict(PNL_METRICS) else "op_income"
    load_years = years_avail if kind == "year" else [str(year), str(year - 1)]
    M = Model(load_years, today)
    tables = M.matrix(kind, year)
    by_key = {t["period"].key: t for t in tables}
    if sel.key not in by_key:
        by_key[sel.key] = M.table(sel)
        tables = sorted(tables + [by_key[sel.key]], key=lambda t: t["period"].key)
    cur = by_key[sel.key]
    divisions = [(d, M.name(d)) for d in M.division_codes()]
    if div != "ALL" and div not in dict(divisions):
        div = "ALL"
    # trend matrix: divisions x periods for one metric (company view) or lines x periods (one division)
    periods = [t["period"] for t in tables]
    matrix = []
    if div == "ALL":
        for code, name in divisions + [("000", "Unallocated corporate"), ("ALL", "Company")]:
            cells = []
            for t in tables:
                row = t["total"] if code == "ALL" else t["unallocated"] if code == "000" else next((r for r in t["rows"] if r["div"] == code), None)
                cells.append({"period": t["period"], "v": (row or {}).get(metric), "open": bool(row and row.get("open")), "row": row})
            if any(c["row"] is not None and c["v"] is not None for c in cells):   # dormant subs (060) stay out
                matrix.append({"div": code, "name": name, "cells": cells})
    else:
        for key, label, anchor in PNL_LINES:
            cells = []
            for t in tables:
                row = next((r for r in t["rows"] if r["div"] == div), None)
                cells.append({"period": t["period"], "v": (row or {}).get(key), "open": bool(row and row.get("open")), "row": row})
            matrix.append({"key": key, "name": label, "anchor": anchor, "cells": cells, "pct": key.endswith("_pct")})
    # year-to-date column: the fiscal year's own table (its months through the current month), one
    # cell per matrix row — percentages come from the year totals, not a sum of monthly percentages
    ytd = None
    if kind != "year":
        yp = Period("year", year)
        ytd_t = M.table(yp)
        ytd = {"period": yp, "label": "YTD" if year == today.year else "%d total" % year, "table": ytd_t}

        def ytd_row(code):
            return ytd_t["total"] if code == "ALL" else ytd_t["unallocated"] if code == "000" else next((r for r in ytd_t["rows"] if r["div"] == code), None)
        for line in matrix:
            yrow = ytd_row(line["div"] if div == "ALL" else div)
            ykey = metric if div == "ALL" else line["key"]
            line["ytd"] = {"period": yp, "v": (yrow or {}).get(ykey), "open": bool(yrow and yrow.get("open")), "row": yrow}
    # neighbours for the period stepper
    prev_p, next_p = sel.prev(), sel.next()
    if next_p.months[0] > "%04d%02d" % (today.year, today.month):
        next_p = None
    if years_avail and prev_p.fiscal_year < years_avail[0]:
        prev_p = None
    return render(request, "dashboard/finance_pnl.html", _ctx(
        request, "finance-pnl", kind=kind, year=year, years_avail=years_avail, sel=sel, cur=cur, tables=tables,
        pnl_divisions=divisions, pnl_div=div, pnl_div_name=(M.name(div) if div != "ALL" else "All divisions"),
        metric=metric, metrics=PNL_METRICS, metric_label=dict(PNL_METRICS)[metric], metric_pct=metric.endswith("_pct"),
        matrix=matrix, periods=periods, prev_p=prev_p, next_p=next_p, open_months=sorted(M.open_months),
        latest_wip=M.wip.latest_date, lines=PNL_LINES, ytd=ytd))


def finance_pnl_detail(request, div, period):
    """The full income statement behind one Divisional P&L cell: accounts grouped like the
    accountants' workbook, each with its postings and the projects that make it up, the WIP
    adjustment by month and by job, the corporate allocation and the payroll-lag estimate."""
    from apps.analytics.divisional_pnl import (Model, parse_period, Period, COST_GROUPS, PAYROLL_LAG_ACCTS, CORP,
                                               LAG_BASELINE_MONTHS, months_back, lag_estimate)
    try:
        P = parse_period(period)
    except ValueError:
        raise Http404("bad period")
    today = timezone.localdate()
    div = (div or "ALL").upper()
    code = None if div == "ALL" else div
    years = [P.fiscal_year, str(P.year - 1)]
    M = Model(years, today)
    if code and code not in M.gl.divisions:
        raise Http404("unknown division")
    T = M.table(P)
    row = T["total"] if code is None else next((r for r in T["rows"] if r["div"] == code), None)
    if row is None:
        row = M.row(code, P)
    st = M.statement(code, P)
    pj = M.projects(code, P)
    # project attribution per group (revenue + each cost group) with the GL residual
    attribution = {}
    gl_group_amt = {"Revenue": sum((i["amount"] for i in st["revenue"]), Decimal(0))}
    for grp in st["costs"]:
        gl_group_amt[grp["group"]] = grp["amount"]
    for gname, gl_amt in gl_group_amt.items():
        items = []
        for j in pj["projects"]:
            v = j["revenue"] if gname == "Revenue" else j["groups"].get(gname)
            if v:
                items.append({"cpn": j["cpn"], "title": j["title"], "customer": j["customer"], "amount": v, "division": j["division"]})
        items.sort(key=lambda i: -abs(i["amount"]))
        attributed = sum((i["amount"] for i in items), Decimal(0))
        attribution[gname] = {"items": items, "attributed": attributed, "residual": gl_amt - attributed, "gl": gl_amt, "n": len(items)}
    # corporate pool detail: 000 overhead by account for the period x this division's share
    months = st["months"]
    pool_accts = []
    if code and code != CORP:
        corp_accts = M.gl.accounts(CORP, months)
        for acct, amt in sorted(corp_accts.items(), key=lambda kv: -abs(kv[1])):
            t, descr = M.gl.meta[acct]
            if amt and t == "4E" and acct[:1] in ("6", "7") and acct not in ("60000", "60005"):
                pool_accts.append({"acct": acct, "descr": descr, "amount": amt, "alloc": amt * Decimal(str(row.get("share") or 0))})
    # payroll-lag detail for open months
    lag_detail = []
    for m in row.get("months_open", []):
        base = [b for b in months_back(m, 14) if not M.is_open(b)][:LAG_BASELINE_MONTHS]
        for acct in PAYROLL_LAG_ACCTS:
            if code:
                posted = M.gl.cells.get((code, m), {}).get(acct, Decimal(0))
                basev = [M.gl.cells.get((code, b), {}).get(acct, Decimal(0)) for b in base]
            else:
                posted = sum((M.gl.cells.get((d, m), {}).get(acct, Decimal(0)) for d in M.gl.divisions), Decimal(0))
                basev = [sum((M.gl.cells.get((d, b), {}).get(acct, Decimal(0)) for d in M.gl.divisions), Decimal(0)) for b in base]
            avg = (sum(basev, Decimal(0)) / len(basev)) if basev else Decimal(0)
            if avg or posted:
                lag_detail.append({"month": m, "acct": acct, "descr": M.gl.meta.get(acct, ("", ""))[1], "avg": avg, "posted": posted, "gap": avg - posted})
    lag_base_months = [b for b in months_back(row["months_open"][0], 14) if not M.is_open(b)][:LAG_BASELINE_MONTHS] if row.get("months_open") else []
    # WIP by job (PCA) over the period
    wip_jobs = [j for j in pj["projects"] if j.get("dwip")]
    wip_jobs.sort(key=lambda j: -abs(j["dwip"]))
    wip_pca_total = sum((j["dwip"] for j in wip_jobs), Decimal(0))
    wip_tot = {k: sum((j.get(k) or Decimal(0) for j in wip_jobs), Decimal(0))
               for k in ("revenue", "costs", "gross", "dwip", "contribution", "dearned", "dbilled")}
    # the WIP-by-job page runs by month or year (?period=YYYY-MM | YYYY); a quarter opens its last month
    wip_period = P.key if P.kind != "quarter" else "%s-%s" % (P.months[-1][:4], P.months[-1][4:])
    prev_p, next_p = P.prev(), P.next()
    if next_p.months[0] > "%04d%02d" % (today.year, today.month):
        next_p = None
    posting_lo = fetch_dict("SELECT MIN(per_post) lo FROM finance_glrecentposting")[0]["lo"]
    postings_available = bool(posting_lo and min(months) >= posting_lo) if months else False
    return render(request, "dashboard/finance_pnl_detail.html", _ctx(
        request, "finance-pnl", P=P, code=code, div=div, div_name=(M.name(code) if code else "Company"), row=row, T=T, st=st,
        attribution=attribution, projects=pj["projects"][:400], projects_total=len(pj["projects"]), group_totals=pj["group_totals"], cost_groups=COST_GROUPS,
        wip_jobs=wip_jobs, wip_pca_total=wip_pca_total, wip_start=pj["wip_start_date"], wip_end=pj["wip_end_date"],
        wip_tot=wip_tot, wip_period=wip_period, wip_drivers=pj["wip_drivers"],
        pool_accts=pool_accts, lag_detail=lag_detail, lag_base_months=lag_base_months, prev_p=prev_p, next_p=next_p,
        postings_available=postings_available, months=months, latest_wip=M.wip.latest_date,
        quarter_key=Period("quarter", P.year, (int(P.months[0][4:]) - 1) // 3 + 1).key, year_key=P.fiscal_year))


def finance_pnl_lines(request):
    """JSON line-level drill for the Divisional P&L detail page: GL postings behind an account
    (trailing window) or the project transactions behind a project's line. Local tables only."""
    from apps.analytics.divisional_pnl import parse_period, postings, project_transactions
    g = request.GET
    try:
        P = parse_period(g.get("p"))
    except ValueError:
        return JsonResponse({"error": "bad period"}, status=400)
    today = timezone.localdate()
    months = [m for m in P.months if m <= "%04d%02d" % (today.year, today.month)]
    div = (g.get("div") or "ALL").upper()
    code = None if div == "ALL" else div
    kind = g.get("kind")
    if kind == "postings":
        rows, avail = postings(code, months, g.get("acct", ""))
        cols = [("tran_date", "Date"), ("per_post", "Period"), ("sub", "Sub"), ("module", "Module"), ("jrnl_type", "Journal"),
                ("batch_nbr", "Batch"), ("ref_nbr", "Ref"), ("tran_desc", "Description"), ("amt", "Amount")]
        title = "%s · GL postings · %s · %s" % (g.get("acct", ""), div, P.label)
        note = None if avail else "Posting lines are only kept for the trailing ~3 months of GL activity; older periods show the account balance only."
    elif kind == "pjtran":
        rows = project_transactions(g.get("cpn", ""), months, g.get("group") or None)
        cols = [("transaction_date", "Date"), ("fiscal_period", "Period"), ("system_cd", "Sys"), ("group", "Group"), ("gl_account", "GL"),
                ("vendor", "Vendor"), ("employee", "Employee"), ("comment", "Comment"), ("voucher_num", "Voucher"), ("units", "Units"), ("amount", "Amount")]
        title = "%s · %s · %s" % (g.get("cpn", ""), g.get("group") or "all lines", P.label)
        note = "Project ledger lines (PJTran) in the period. Payroll allocations carry no GL account — they are the COGS-LABOR / burden postings."
    else:
        return JsonResponse({"error": "bad kind"}, status=400)
    total = sum(((r.get("amt") if "amt" in r else r.get("amount")) or 0 for r in rows), Decimal(0))
    out_rows = []
    for r in rows:
        o = {}
        for k, _ in cols:
            v = r.get(k)
            if isinstance(v, Decimal):
                v = float(v)
            elif hasattr(v, "isoformat"):
                v = v.isoformat()
            o[k] = v
        out_rows.append(o)
    return JsonResponse({"title": title, "cols": cols, "rows": out_rows, "n": len(rows), "total": float(total), "note": note,
                         "money": ["amt", "amount"], "link": ("/projects/%s/" % g.get("cpn")) if kind == "pjtran" else None})
