"""Bids pages (SharePoint spec §5.3–5.5): overview, board, calendar, list, analytics, bid detail, notes / follow-ups /
risks, refresh-now, manual aliases. Everything reads apps.bids.analytics; writes touch PCA tables only."""

import csv
import io
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import Customer, Division, Employee
from apps.dashboard.views import _ctx
from apps.ingestion.bulk import fetch_dict

from . import analytics as A
from . import rules
from .models import Bid, BidClientAlias, BidFollowup, BidNote, BidRisk, BidSnapshot, BidVersion, BidderAlias

D0 = Decimal(0)


# ------------------------------------------------------------------------------------------- filters shared by every tab
def _filters(request):
    g = request.GET
    f = {"div": g.get("div", ""), "est": g.get("est", ""), "rep": g.get("rep", ""), "client": g.get("client", ""),
         "stage": [s for s in g.getlist("stage") if s], "year": g.get("year", ""), "q": g.get("q", "").strip(),
         "house": g.get("house", ""), "src": g.get("src", ""), "size": g.get("size", ""), "prob": g.get("prob", ""), "bom": g.get("bom", ""), "ball": g.get("ball", "")}
    where, params = ["TRUE"], []
    if f["div"]:
        where.append("b.division = %s"); params.append(f["div"])
    if f["est"].isdigit():
        where.append("b.estimator_id = %s"); params.append(int(f["est"]))
    if f["rep"].isdigit():
        where.append("b.salesperson_id = %s"); params.append(int(f["rep"]))
    if f["client"].isdigit():
        where.append("b.client_id = %s"); params.append(int(f["client"]))
    if f["stage"]:
        where.append("b.stage = ANY(%s)"); params.append(f["stage"])
    if f["year"].isdigit():
        where.append("EXTRACT(year FROM COALESCE(b.bid_due, b.submitted_on, b.portal_created::date)) = %s"); params.append(int(f["year"]))
    if f["house"] in ("1", "0"):
        where.append("b.house_account = %s"); params.append(f["house"] == "1")
    if f["src"] in ("list", "archive"):
        where.append("b.source = %s"); params.append(f["src"])
    if f["bom"]:
        where.append("UPPER(b.bom_status) = %s"); params.append(f["bom"].upper())
    if f["ball"]:
        where.append("b.ball_in_court = %s"); params.append(f["ball"])
    if f["prob"].isdigit():
        where.append("b.probability = %s"); params.append(int(f["prob"]))
    if f["q"]:
        like = "%" + f["q"] + "%"
        where.append("(b.project_name ILIKE %s OR b.client_name ILIKE %s OR b.job_number_raw ILIKE %s OR b.portal_project_id ILIKE %s OR b.comments ILIKE %s)")
        params += [like] * 5
    return f, " AND ".join(where), params


def _filters_json(request):
    """The current filter querystring as a dict, for the JS drills (they re-send it to the JSON endpoints)."""
    return json.dumps({k: (request.GET.getlist(k) if k == "stage" else request.GET.get(k)) for k in request.GET if k not in ("axis", "pv")})


def _post_filter(rows, f):
    if f["size"]:
        rows = [r for r in rows if r["size_band"] == f["size"]]
    return rows


def _options():
    return {
        "divisions": list(Division.objects.filter(active=True).order_by("code").values("code", "name")),
        "estimators": fetch_dict("SELECT e.id, e.canonical_name name, COUNT(*) n FROM bids_bid b JOIN core_employee e ON e.id = b.estimator_id GROUP BY 1, 2 ORDER BY 3 DESC"),
        "reps": fetch_dict("SELECT e.id, e.canonical_name name, COUNT(*) n FROM bids_bid b JOIN core_employee e ON e.id = b.salesperson_id GROUP BY 1, 2 ORDER BY 3 DESC"),
        "clients": fetch_dict("SELECT c.id, c.canonical_name name, COUNT(*) n FROM bids_bid b JOIN core_customer c ON c.id = b.client_id GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 400"),
        "years": [r["y"] for r in fetch_dict("SELECT DISTINCT EXTRACT(year FROM COALESCE(bid_due, submitted_on, portal_created::date))::int y FROM bids_bid WHERE COALESCE(bid_due, submitted_on, portal_created) IS NOT NULL ORDER BY 1 DESC")],
        "stages": [(k, rules.STAGE_LABEL[k]) for k in sorted(rules.STAGE_ORDER, key=rules.STAGE_ORDER.get)],
        "sizes": [b[2] for b in A.SIZE_BANDS],
    }


def _row_json(r):
    """The list / board / calendar JSON row: floats and ISO dates for the browser."""
    return {"id": r["id"], "num": r["job_number_raw"], "pid": r["portal_project_id"], "name": r["project_name"], "client": r["client_label"] or "",
            "client_id": r["client_id"], "stage": r["stage"], "stage_label": r["stage_label"], "flag": r["stage_flag"], "status_raw": r["status_raw"],
            "due": r["bid_due"].isoformat() if r["bid_due"] else "", "submitted": r["submitted_on"].isoformat() if r["submitted_on"] else "",
            "awarded": r["awarded_on"].isoformat() if r["awarded_on"] else "", "value": float(r["value"]) if r["value"] is not None else None,
            "budget": float(r["budget"]) if r["budget"] is not None else None, "margin": float(r["margin"]) if r["margin"] is not None else None,
            "prob": r["probability"], "est": r["estimator"] or "", "est_id": r["estimator_id"], "inferred": bool(r["estimator_inferred"]),
            "rep": ("House" if r["house_account"] else (r["rep"] or "")), "rep_id": r["salesperson_id"], "house": bool(r["house_account"]),
            "div": r["division"] or "", "year": r["year"], "size": r["size_band"], "bom": r["bom_status"] or "", "ball": r["ball_in_court"] or "",
            "walk": r["walkthrough_raw"] or "", "won": r["won"], "won_by_sl": bool(r["won_by_sl"]), "job": r["pdisp"] or "", "cpn": r["cpn"] or "",
            "cv": float(r["cv"]) if r["cv"] is not None else None, "gp": r["realised_gp"], "acc": r["accuracy"], "pstate": r["pstate"] or "",
            "modified": r["portal_modified"].isoformat() if r["portal_modified"] else "", "src": r["source"], "sector": r["sector"] or "", "url": r["web_url"] or ""}


# ------------------------------------------------------------------------------------------- overview
def overview(request):
    f, where, params = _filters(request)
    o = A.overview(division=f["div"] or None, estimator=int(f["est"]) if f["est"].isdigit() else None, rep=int(f["rep"]) if f["rep"].isdigit() else None,
                   client=int(f["client"]) if f["client"].isdigit() else None, year=int(f["year"]) if f["year"].isdigit() else None)
    axis = request.GET.get("axis") if request.GET.get("axis") in ("estimator", "rep", "division", "client") else "estimator"
    pv = A.quoting_pivot(o["rows"], axis=axis, value=request.GET.get("pv") or "value")
    kp = o["kp"]
    eb = {str(w): {"stated": float(v["stated"]), "historical": float(v["historical"]), "count": v["count"]} for w, v in kp["expected"].items()}
    kp["eb90"] = kp["expected"][90]; kp["eb_overdue"] = kp["expected"]["overdue"]
    charts = o["charts"]
    cj = {"by_bidder": [{"key": x["key"], "n": x["n"], "value": float(x["value"])} for x in charts["by_bidder"]],
          "by_probability": [{"key": x["key"], "n": x["n"], "value": float(x["value"])} for x in charts["by_probability"]],
          "by_ball": [{"key": x["key"], "n": x["n"], "value": float(x["value"])} for x in charts["by_ball"]],
          "funnel": {k: {"n": v["n"], "value": float(v["value"])} for k, v in charts["funnel"].items()},
          "funnel_by_division": {d: {k: {"n": v["n"], "value": float(v["value"])} for k, v in st.items()} for d, st in charts["funnel_by_division"].items()},
          "win_trend": charts["win_trend"], "expected": eb}
    followups = BidFollowup.objects.filter(done_at=None).select_related("bid", "owner").order_by("due")[:12]
    return render(request, "bids/overview.html", _ctx(request, "bids", f=f, opts=_options(), kp=kp, charts=charts, charts_json=json.dumps(cj, default=str),
                                                     pivot=pv, axis=axis, pv_value=request.GET.get("pv") or "value", followups=followups, tab="overview",
                                                     attention=kp["attention"][:80], leaks=kp["leaks"][:60], stale=kp["stale"][:60], cycles=o["cycles"],
                                                     today=timezone.localdate(), axes=[("estimator", "estimator"), ("rep", "sales rep"), ("division", "division"), ("client", "client")],
                                                     filters_json=_filters_json(request)))


# ------------------------------------------------------------------------------------------- board
def board(request):
    f, where, params = _filters(request)
    rows = _post_filter(A.bids(where + " AND b.source = 'list' AND b.stage IN ('quoting','submitted','on_hold')", params, order="b.bid_due ASC NULLS LAST"), f)
    today = timezone.localdate()
    for r in rows:
        r["days_in_stage"] = (timezone.now() - r["portal_modified"]).days if r["portal_modified"] else None
        r["overdue"] = bool(r["bid_due"] and r["bid_due"] < today)
    cols = [(s, rules.STAGE_LABEL[s], [r for r in rows if r["stage"] == s]) for s in ("quoting", "submitted", "on_hold")]
    data = json.dumps([dict(_row_json(r), days=r["days_in_stage"], overdue=r["overdue"]) for r in rows], default=str)
    return render(request, "bids/board.html", _ctx(request, "bids", f=f, opts=_options(), cols=cols, rows_json=data, tab="board", n=len(rows),
                                                  total=sum((r["value"] or D0) for r in rows), filters_json=_filters_json(request)))


# ------------------------------------------------------------------------------------------- calendar
def calendar(request):
    f, where, params = _filters(request)
    return render(request, "bids/calendar.html", _ctx(request, "bids", f=f, opts=_options(), tab="calendar", filters_json=_filters_json(request)))


def calendar_json(request):
    f, where, params = _filters(request)
    try:
        start = date.fromisoformat(request.GET.get("start", "")[:10])
        end = date.fromisoformat(request.GET.get("end", "")[:10])
    except ValueError:
        start, end = timezone.localdate().replace(day=1), timezone.localdate() + timedelta(days=45)
    rows = A.bids(where + """ AND ((b.bid_due BETWEEN %s AND %s) OR (b.submitted_on BETWEEN %s AND %s) OR (b.awarded_on BETWEEN %s AND %s)
                              OR (b.walkthrough_date BETWEEN %s AND %s) OR (b.start BETWEEN %s AND %s))""", params + [start, end] * 5)
    events = []
    for r in _post_filter(rows, f):
        j = _row_json(r)
        for kind, d in (("due", r["bid_due"]), ("submitted", r["submitted_on"]), ("awarded", r["awarded_on"]), ("walkthrough", r["walkthrough_date"]), ("start", r["start"])):
            if d and start <= d <= end:
                events.append(dict(j, kind=kind, date=d.isoformat()))
    return JsonResponse({"events": events, "start": start.isoformat(), "end": end.isoformat()})


# ------------------------------------------------------------------------------------------- list + export
def bids_list(request):
    f, where, params = _filters(request)
    return render(request, "bids/list.html", _ctx(request, "bids", f=f, opts=_options(), tab="list", filters_json=_filters_json(request)))


def bids_list_json(request):
    f, where, params = _filters(request)
    rows = _post_filter(A.bids(where, params), f)
    return JsonResponse({"rows": [_row_json(r) for r in rows], "n": len(rows)})


COLUMNS = [("num", "Job #"), ("pid", "Portal ID"), ("name", "Project"), ("client", "Client"), ("stage_label", "Stage"), ("status_raw", "Portal status"), ("div", "Div"),
           ("est", "Estimator"), ("rep", "Rep"), ("due", "Bid due"), ("submitted", "Submitted"), ("awarded", "Awarded"), ("value", "Value"), ("budget", "Budget"),
           ("margin", "Bid margin"), ("prob", "Probability"), ("bom", "BOM"), ("ball", "Ball in court"), ("walk", "Walkthrough"), ("job", "SL job"), ("cv", "Contract value"),
           ("gp", "Final GP"), ("acc", "Accuracy"), ("modified", "Portal modified"), ("src", "Source")]


def export(request):
    f, where, params = _filters(request)
    rows = [_row_json(r) for r in _post_filter(A.bids(where, params), f)]
    fmt = request.GET.get("fmt", "csv")
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.title = "Bids"
        ws.append([c[1] for c in COLUMNS])
        for r in rows:
            ws.append([r.get(k) for k, _ in COLUMNS])
        ws.freeze_panes = "C2"
        buf = io.BytesIO(); wb.save(buf)
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="bids-%s.xlsx"' % timezone.localdate().isoformat()
        return resp
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="bids-%s.csv"' % timezone.localdate().isoformat()
    w = csv.writer(resp)
    w.writerow([c[1] for c in COLUMNS])
    for r in rows:
        w.writerow([r.get(k) for k, _ in COLUMNS])
    return resp


# ------------------------------------------------------------------------------------------- analytics
def analytics(request):
    f, where, params = _filters(request)
    rows = _post_filter(A.bids(where, params), f)
    decided = [r for r in rows if r["decided"]]
    by_client = A._group(decided, lambda r: r["client_label"] or "(no client)")[:40]
    by_sector = A._group(decided, lambda r: r["sector"] or "(no sector)")
    by_size = A._group(decided, lambda r: r["size_band"])
    by_rep = A._group(decided, lambda r: ("House" if r["house_account"] else (r["rep"] or "(no rep)")))
    matrix = defaultdict(lambda: defaultdict(lambda: {"won": 0, "lost": 0}))
    for r in decided:
        matrix[r["estimator"] or "(no bidder)"][("House" if r["house_account"] else (r["rep"] or "(no rep)"))]["won" if r["won"] else "lost"] += 1
    reps = sorted({k for v in matrix.values() for k in v}, key=lambda k: -sum(c["won"] + c["lost"] for v in matrix.values() for kk, c in v.items() if kk == k))[:10]
    ests = sorted(matrix, key=lambda e: -sum(c["won"] + c["lost"] for c in matrix[e].values()))[:15]
    cycles = [r["cycle_days"] for r in rows if r["cycle_days"] is not None]
    cycle_hist = Counter(min(r // 15 * 15, 180) for r in cycles)
    drift = [r for r in rows if r["won"] and r["cv"] and r["value"]]
    drift_pts = [{"x": float(r["value"]), "y": float(r["cv"]), "name": r["project_name"], "id": r["id"]} for r in drift if float(r["value"]) > 0][:800]
    calib = A.calibration([(r["probability"], r["won"]) for r in decided])
    pct_report = A._bar([r for r in rows if r["open"]], lambda r: ("%d%%" % r["probability"]) if r["probability"] is not None else "unscored", 20,
                        sort_key=lambda x: int(x["key"].rstrip("%")) if x["key"] != "unscored" else -1)
    hist = fetch_dict("""SELECT snapshot_date, stage, SUM(count) n, SUM(value) value, SUM(weighted_value) weighted FROM bids_bidsnapshot
                         WHERE stage IN ('quoting','submitted') GROUP BY 1, 2 ORDER BY 1""")
    trend = A._win_trend(rows)
    groups = [("client", "client", by_client), ("sector", "sector", by_sector), ("size band", "size", by_size), ("sales rep", "rep", by_rep)]
    ctx = dict(f=f, opts=_options(), tab="analytics", groups=groups, matrix=matrix, reps=reps, ests=ests, filters_json=_filters_json(request),
               cycle_hist=sorted(cycle_hist.items()), cycle_median=A.median(cycles), calib=calib, pct_report=pct_report, trend=trend, n=len(rows), decided_n=len(decided),
               charts_json=json.dumps({"cycle": sorted(cycle_hist.items()), "drift": drift_pts, "calib": calib, "trend": trend,
                                       "hist": [{"d": h["snapshot_date"].isoformat(), "stage": h["stage"], "n": h["n"], "value": float(h["value"]), "weighted": float(h["weighted"])} for h in hist],
                                       "pct": [{"key": x["key"], "n": x["n"], "value": float(x["value"])} for x in pct_report]}, default=str))
    return render(request, "bids/analytics.html", _ctx(request, "bids", **ctx))


DRILL_COLS = [("num", "Job #"), ("name", "Project"), ("client", "Client"), ("stage_label", "Stage"), ("div", "Div"), ("est", "Estimator"), ("rep", "Rep"),
              ("due", "Bid due"), ("submitted", "Submitted"), ("awarded", "Awarded"), ("value", "Value"), ("margin", "Bid margin"), ("prob", "Prob"), ("cv", "SL contract"),
              ("gp", "Final GP"), ("acc", "Accuracy")]


def analytics_json(request):
    """Rows behind a chart element, in PCADrill's JSON shape: ?kind=cycle&bucket=30 | calib&bucket=25 | stage&bucket=submitted |
    quarter&bucket=2026Q1 | prob&bucket=75 | group&axis=client&bucket=<name> | all."""
    f, where, params = _filters(request)
    rows = _post_filter(A.bids(where, params), f)
    kind = request.GET.get("kind", "")
    b = request.GET.get("bucket", "")
    if kind == "cycle" and b.lstrip("-").isdigit():
        lo = int(b); rows = [r for r in rows if r["cycle_days"] is not None and min(r["cycle_days"] // 15 * 15, 180) == lo]
    elif kind == "calib" and b.isdigit():
        rows = [r for r in rows if r["decided"] and r["probability"] == int(b)]
    elif kind == "prob":
        rows = [r for r in rows if r["open"] and (("%d%%" % r["probability"]) if r["probability"] is not None else "unscored") == b]
    elif kind == "stage":
        rows = [r for r in rows if r["stage"] == b]
    elif kind == "open":
        rows = [r for r in rows if r["open"]]
    elif kind == "bidder":
        rows = [r for r in rows if r["open"] and (r["estimator"] or "(no bidder)") == b]
    elif kind == "ball":
        rows = [r for r in rows if r["open"] and (r["ball_in_court"] or "(blank)") == b]
    elif kind == "quarter":
        def qk(r):
            d = r["awarded_on"] or r["submitted_on"] or r["bid_due"]
            return "%dQ%d" % (d.year, (d.month - 1) // 3 + 1) if d else ""
        rows = [r for r in rows if r["decided"] and qk(r) == b]
    elif kind == "group":
        axis = request.GET.get("axis", "client")
        keyf = {"client": lambda r: r["client_label"] or "(no client)", "sector": lambda r: r["sector"] or "(no sector)", "size": lambda r: r["size_band"],
                "rep": lambda r: ("House" if r["house_account"] else (r["rep"] or "(no rep)")), "estimator": lambda r: r["estimator"] or "(no bidder)",
                "division": lambda r: r["division"] or "?"}.get(axis, lambda r: "")
        rows = [r for r in rows if r["decided"] and keyf(r) == b]
    elif kind == "pivot":
        axis = request.GET.get("axis", "estimator"); month = request.GET.get("month", "")
        keyf = {"estimator": lambda r: r["estimator"] or "(no bidder)", "rep": lambda r: r["rep"] or ("House" if r["house_account"] else "(no rep)"),
                "division": lambda r: r["division"] or "?", "client": lambda r: r["client_label"] or "(no client)"}[axis if axis in ("estimator", "rep", "division", "client") else "estimator"]
        def mk(r):
            d = r["bid_due"] or r["submitted_on"]
            return "%04d-%02d" % (d.year, d.month) if d else ""
        rows = [r for r in rows if r["stage"] in ("submitted", "awarded", "lost", "quoting") and (not b or keyf(r) == b) and (not month or mk(r) == month)]
    elif kind == "expected":
        rows = [r for r in rows if r["open"]]
    out = []
    for r in rows[:1500]:
        j = _row_json(r)
        j["url"] = "/bids/%d/" % r["id"]
        out.append(j)
    return JsonResponse({"cols": DRILL_COLS, "rows": out, "n": len(rows), "total": float(sum((r["value"] or D0) for r in rows)), "money_cols": ["value", "cv"],
                         "pct_cols": ["margin", "gp"], "pts_cols": ["acc"], "num_cols": ["prob"], "link_map": {"name": "url"}, "text_cols": ["name", "client"],
                         "money_digits": 0, "truncated": len(rows) > 1500})


# ------------------------------------------------------------------------------------------- bid detail + notes
def bid_detail(request, pk):
    rows = A.bids("b.id = %s", [pk])
    if not rows:
        raise Http404
    r = rows[0]
    b = get_object_or_404(Bid.objects.select_related("estimator", "salesperson", "client", "project"), pk=pk)
    versions = list(BidVersion.objects.filter(bid=b).order_by("modified_at"))
    trail, prev = [], None
    for v in versions:
        changes = []
        if prev is None or v.stage != prev.stage:
            changes.append(("status", rules.STAGE_LABEL.get(v.stage, v.stage)))
        if prev is not None and v.value != prev.value:
            changes.append(("value", v.value))
        if prev is not None and v.budget != prev.budget:
            changes.append(("budget", v.budget))
        if prev is not None and v.bidder_raw != prev.bidder_raw:
            changes.append(("bidder", v.bidder_raw))
        if prev is not None and v.probability != prev.probability:
            changes.append(("probability", v.probability))
        if prev is not None and v.job_number_raw != prev.job_number_raw:
            changes.append(("job #", v.job_number_raw))
        if changes:
            trail.append({"when": v.modified_at, "by": v.modified_by, "changes": changes, "stage": v.stage})
        prev = v
    stage_entries = sorted(rules.stage_entries([(v.modified_at, v.stage) for v in versions]).items(), key=lambda x: x[1])
    stage_entries = [(rules.STAGE_LABEL.get(k, k), d) for k, d in stage_entries]
    others = list(Bid.objects.filter(project=b.project).exclude(pk=b.pk)) if b.project_id else []
    estimates = []
    try:
        from apps.estimating.api import estimates_for_bid
        estimates = estimates_for_bid(b.id)
    except Exception:  # noqa - Phase G not built yet
        estimates = []
    employees = Employee.objects.filter(active=True).order_by("canonical_name").values("id", "canonical_name")
    return render(request, "bids/bid_detail.html", _ctx(request, "bids", bid=b, r=r, trail=trail, stage_entries=stage_entries, others=others, estimates=estimates,
                                                       notes=b.notes.select_related("author"), followups=b.followups.select_related("owner"), risks=b.risks.select_related("owner"),
                                                       employees=employees, note_kinds=BidNote.Kind.choices, sev=BidRisk.Severity.choices, tab="detail",
                                                       today=timezone.localdate(), cv_delta=(r["cv"] - b.value) if (r["cv"] is not None and b.value) else None))


def _who(request):
    u = getattr(request, "user", None)
    acc = getattr(request, "acc", None)
    name = getattr(getattr(acc, "account", None), "display_name", "") or (u.get_username() if u and u.is_authenticated else "")
    return (u if (u and u.is_authenticated) else None), name


@require_POST
def note_add(request, pk):
    b = get_object_or_404(Bid, pk=pk)
    user, name = _who(request)
    body = (request.POST.get("body") or "").strip()
    if body:
        BidNote.objects.create(bid=b, kind=request.POST.get("kind") or "general", body=body[:5000], author=user, author_name=name[:128])
        messages.success(request, "Note added.")
    return redirect("bids:bid_detail", pk=pk)


@require_POST
def followup(request, pk):
    b = get_object_or_404(Bid, pk=pk)
    user, name = _who(request)
    act = request.POST.get("action", "add")
    if act == "done" and request.POST.get("id", "").isdigit():
        BidFollowup.objects.filter(bid=b, pk=int(request.POST["id"])).update(done_at=timezone.now())
    elif act == "reopen" and request.POST.get("id", "").isdigit():
        BidFollowup.objects.filter(bid=b, pk=int(request.POST["id"])).update(done_at=None)
    else:
        text = (request.POST.get("text") or "").strip()
        if text:
            due = request.POST.get("due") or None
            owner = request.POST.get("owner") or None
            BidFollowup.objects.create(bid=b, text=text[:500], due=due, owner_id=int(owner) if owner and owner.isdigit() else None, author=user)
    return redirect("bids:bid_detail", pk=pk)


@require_POST
def risk(request, pk):
    b = get_object_or_404(Bid, pk=pk)
    user, name = _who(request)
    act = request.POST.get("action", "add")
    if act == "close" and request.POST.get("id", "").isdigit():
        BidRisk.objects.filter(bid=b, pk=int(request.POST["id"])).update(closed_at=timezone.now())
    else:
        text = (request.POST.get("text") or "").strip()
        if text:
            owner = request.POST.get("owner") or None
            BidRisk.objects.create(bid=b, text=text[:500], severity=request.POST.get("severity") or "medium", mitigation=(request.POST.get("mitigation") or "")[:2000],
                                   owner_id=int(owner) if owner and owner.isdigit() else None, author=user)
    return redirect("bids:bid_detail", pk=pk)


# ------------------------------------------------------------------------------------------- refresh now + manual aliases
@require_POST
def refresh_now(request):
    """Re-pull the Project List and re-link in-process (≈20 s) — the button on the Bids pages (spec §13.1)."""
    from apps.ingestion.models import IngestionRun
    from . import loaders
    run = IngestionRun.objects.create(source_system="sharepoint", trigger="manual", status="running", started_at=timezone.now())
    try:
        res = {"list": loaders.load_project_list(run), "link": loaders.link_to_sl(run), "aliases": loaders.resolve_aliases(run), "apply": loaders.apply_aliases(run),
               "versions": loaders.load_versions(run, limit=150), "snapshot": loaders.snapshot_pipeline(run)}
        run.status = "succeeded"; run.add_step("refresh_now", result=res)
        messages.success(request, "Project Portal re-read: %s rows (%s new, %s updated)." % (res["list"].get("rows"), res["list"].get("new", 0), res["list"].get("updated", 0)))
    except Exception as e:  # noqa
        run.status = "failed"; run.add_step("refresh_now", error=str(e)[:500])
        messages.error(request, "Refresh failed: %s" % str(e)[:200])
    run.finished_at = timezone.now(); run.save()
    return redirect(request.POST.get("next") or "bids:bids_overview")


@require_POST
def alias_set(request):
    """Manual bidder / rep / client alias from the Data Quality page: raw + role (+ employee id) or raw + customer id."""
    kind = request.POST.get("kind")
    raw = rules.norm_name_key(request.POST.get("raw", "")) if kind == "person" else (request.POST.get("raw") or "").strip()
    target = request.POST.get("target", "")
    if kind == "person" and raw:
        role = request.POST.get("role") or "estimator"
        a, _ = BidderAlias.objects.get_or_create(raw=raw, role=role)
        a.employee_id = int(target) if target.isdigit() else None
        a.manual, a.rule, a.confidence = True, "manual", Decimal("1.00") if a.employee_id else Decimal(0)
        a.save()
    elif kind == "client" and raw:
        a, _ = BidClientAlias.objects.get_or_create(raw=raw)
        a.customer_id = int(target) if target.isdigit() else None
        a.manual, a.rule, a.confidence = True, "manual", Decimal("1.00") if a.customer_id else Decimal(0)
        a.save()
    from apps.ingestion.models import IngestionRun
    from . import loaders
    run = IngestionRun.objects.create(source_system="sharepoint", trigger="manual", status="running", started_at=timezone.now())
    loaders.apply_aliases(run)
    run.status = "succeeded"; run.finished_at = timezone.now(); run.save()
    messages.success(request, "Alias saved and applied.")
    return redirect(request.POST.get("next") or "dashboard:data_quality")
