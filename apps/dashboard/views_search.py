"""Global search: the results page and the JSON behind the sidebar box (apps/dashboard/search.py)."""

import json

from django.http import JsonResponse
from django.shortcuts import render

from . import search as S
from .views import _ctx


def search(request):
    """/search/?q=…[&type=project] — every match, grouped, with facet chips to narrow to one kind."""
    q = (request.GET.get("q") or "").strip()[:100]
    t = (request.GET.get("type") or "").strip()
    res = S.search(q, request.acc, per_type=250, project_limit=250, page_limit=30, doc_limit=250) if q else None
    facets, groups = [], []
    if res:
        facets = [{"type": g["type"], "label": g["label"], "count": g["count"], "capped": g["capped"]} for g in res["groups"]]
        groups = [g for g in res["groups"] if not t or g["type"] == t]
    return render(request, "dashboard/search.html", _ctx(request, "search", q=q, res=res, groups=groups, facets=facets, type_filter=t,
                                                          type_label=S.GROUP_LABEL.get(t, "")))


def search_suggest(request):
    """JSON for the sidebar panel: a few per group (7 projects, 5 of everything else, 4 pages); blank query = the
    viewer's pages to jump to. The panel adds the browser's own 'recent' list."""
    q = (request.GET.get("q") or "").strip()[:100]
    data = S.search(q, request.acc, per_type=5, project_limit=7, page_limit=4, doc_limit=7)
    for g in data["groups"]:
        for it in g["items"]:
            for k in ("last", "when"):
                if it.get(k) is not None and not isinstance(it[k], str):
                    it[k] = it[k].isoformat()
            if it.get("amount") is not None:
                it["amount"] = float(it["amount"])
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False})
