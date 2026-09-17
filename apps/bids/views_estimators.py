"""Estimators pages (SharePoint spec §4.5): roster, detail, and the project page's bid card."""

import json
from datetime import date, timedelta

from django.http import Http404
from django.shortcuts import render
from django.utils import timezone

from apps.core.models import Division, Employee
from apps.dashboard.views import _ctx
from apps.ingestion.bulk import fetch_dict

from . import analytics as A
from . import rules

PERIODS = [("24m", "Last 24 months"), ("12m", "Last 12 months"), ("ytd", "This year"), ("prev", "Last year"), ("all", "All time")]


def _period(request):
    p = request.GET.get("period") or "24m"
    today = timezone.localdate()
    if p == "12m":
        return p, today - timedelta(days=365), None
    if p == "ytd":
        return p, date(today.year, 1, 1), None
    if p == "prev":
        return p, date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if p == "all":
        return p, None, None
    return "24m", today - timedelta(days=730), None


def estimators(request):
    div = request.GET.get("div") or ""
    period, since, until = _period(request)
    rows = A.estimator_roster(division=div or None, since=since, until=until)
    show_inferred = request.GET.get("inferred") == "1"
    if not show_inferred:
        rows = [r for r in rows if r["bids"] - r["inferred"] > 0]
    # roster answers "who estimates": PMs / DMs / dedicated
    roles = {"head_pm": "Division head", "pm": "Project manager", "regular": "Staff", "": "—"}
    for r in rows:
        r["role_label"] = roles.get(r["role"] or "", r["role"] or "—")
    tot = {"bids": sum(r["bids"] for r in rows), "submitted_value": sum((r["submitted_value"] for r in rows), A.D0),
           "won": sum(r["won"] for r in rows), "lost": sum(r["lost"] for r in rows)}
    tot["hit_rate"] = A.hit_rate(tot["won"], tot["lost"])
    gap = fetch_dict("""SELECT COUNT(*) n, AVG((b.value - b.budget) / b.value) bid_margin, AVG(p.actual_gp_percent) final_gp
                        FROM bids_bid b JOIN core_project p ON p.id = b.project_id
                        WHERE b.value > 0 AND b.budget IS NOT NULL AND p.lifecycle_state = 'closed_stabilized' AND p.actual_gp_percent IS NOT NULL
                          AND (b.stage = 'awarded' OR b.won_by_sl) AND NOT b.estimator_inferred""")[0]
    divisions = Division.objects.filter(active=True).order_by("code")
    rows_json = json.dumps([{"name": r["name"], "key": r["key"], "won": r["won"], "lost": r["lost"], "hit_rate": r["hit_rate"],
                             "submitted_value": float(r["submitted_value"] or 0), "margin": r["margin"], "accuracy": r["accuracy"], "accuracy_n": r["accuracy_n"]}
                            for r in rows], default=str)
    return render(request, "bids/estimators.html", _ctx(request, "estimators", rows=rows, tot=tot, gap=gap, period=period, periods=PERIODS,
                                                        div=div, divisions_list=divisions, show_inferred=show_inferred, rows_json=rows_json))


def estimator_detail(request, key):
    e = Employee.objects.filter(employee_key=key).first()
    if e is None:
        raise Http404
    d = A.estimator_detail(e.id)
    real = [r for r in d["rows"] if not r["estimator_inferred"]]
    decided = [r for r in real if r["decided"]]
    won = [r for r in decided if r["won"]]
    kp = {
        "bids": len(d["rows"]), "inferred": sum(1 for r in d["rows"] if r["estimator_inferred"]), "open": len(d["open"]),
        "open_value": sum((r["value"] or A.D0) for r in d["open"]),
        "won": len(won), "lost": len(decided) - len(won), "hit": A.hit_rate(len(won), len(decided) - len(won)), "hit_w": A.weighted(decided, "won", "value"),
        "margin": A.median([float(r["margin"]) for r in real if r["margin"] is not None]),
        "gp": A.median([r["realised_gp"] for r in real if r["realised_gp"] is not None and r["won"] and r["pstate"] == "closed_stabilized"]),
        "accuracy": A.median([r["accuracy"] for r in real if r["accuracy"] is not None]), "accuracy_n": sum(1 for r in real if r["accuracy"] is not None),
        "cost_vs_budget": A.median([r["cost_vs_budget"] for r in real if r["cost_vs_budget"] is not None]),
        "cycle": A.median([r["cycle_days"] for r in real if r["cycle_days"] is not None]),
        "late": sum(1 for r in real if (r["late_days"] or 0) > 0), "late_n": sum(1 for r in real if r["late_days"] is not None),
        "submitted_value": sum((r["value"] or A.D0) for r in real if r["stage"] in (rules.SUBMITTED, rules.AWARDED, rules.LOST)),
    }
    divisions = {}
    for r in d["rows"]:
        divisions[r["division"] or "?"] = divisions.get(r["division"] or "?", 0) + 1
    trend_json = json.dumps([{"q": t["q"], "hit": t["hit"], "margin": t["margin"], "gp": t["gp"], "bids": t["bids"], "value": float(t["value"] or 0)} for t in d["trend"]], default=str)
    calib_json = json.dumps(d["calibration"], default=str)
    # the concealed rating (spec §4.4): only rendered for principals with ratings.view (Owner); the template checks acc.ratings too
    ratings = []
    acc = getattr(request, "acc", None)
    if acc is not None and getattr(acc, "ratings", False):
        from apps.analytics.models import EntityRating, RatingRun
        run = RatingRun.objects.filter(is_current=True).first()
        if run:
            ratings = list(EntityRating.objects.filter(rating_run=run, entity_type="estimator", entity_key=str(e.id)).order_by("metric_name"))
    return render(request, "bids/estimator_detail.html", _ctx(request, "estimators", e=e, kp=kp, d=d, divisions_mix=sorted(divisions.items(), key=lambda x: -x[1]),
                                                             trend_json=trend_json, calib_json=calib_json, ratings=ratings))


def project_bid_card(project):
    """Context for bids/_project_bid_card.html (included on every project page): the bids linked to the project with
    their status trail, plus estimate-vs-actual figures. Returns {} when the project has no bid."""
    from .models import Bid, BidVersion
    bids = list(Bid.objects.filter(project=project).select_related("estimator", "salesperson").order_by("-portal_modified"))
    if not bids:
        return {}
    b = bids[0]
    trail = []
    for v in BidVersion.objects.filter(bid=b).order_by("modified_at"):
        if not trail or trail[-1]["stage"] != v.stage:
            trail.append({"stage": v.stage, "label": rules.STAGE_LABEL.get(v.stage, v.stage), "when": v.modified_at, "by": v.modified_by, "value": v.value})
    margin = rules.bid_margin(b.value, b.budget)
    actual_cost = project.actual_direct_cost if project.lifecycle_state in ("closed_stabilized", "closed_stabilizing") else None
    return {"bid": b, "others": bids[1:], "trail": trail, "margin": margin,
            "stage_label": rules.STAGE_LABEL.get(b.stage, b.stage),
            "cv_delta": (project.contract_value - b.value) if (project.contract_value is not None and b.value) else None,
            "cost_vs_budget": (float(actual_cost) / float(b.budget)) if (actual_cost and b.budget) else None,
            "realised_gp": project.actual_gp_percent, "accuracy": (float(project.actual_gp_percent) - float(margin)) if (project.actual_gp_percent is not None and margin is not None and project.lifecycle_state == "closed_stabilized") else None}
