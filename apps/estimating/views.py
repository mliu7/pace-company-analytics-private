"""Estimating workbench pages (SharePoint spec §8, parity PI-01 … PI-10). Every URL name is registered in
apps/access/registry.py (estimating.view; writes need estimating.write) — the access middleware gates by name, so the
views only need the actor (`request.acc.real_account`) for ownership and audit."""

import json
import time
from decimal import Decimal

from django.core.cache import cache
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.access.audit import log as audit
from apps.dashboard.views import _ctx
from apps.ingestion.bulk import fetch_dict

from . import api, loaders, rules, services
from .models import CatalogItem, CatalogSource, Estimate, EstimateVersion, HygieneReport, LaborRate

NAV = "estimating"
PER_DEFAULT, PER_MAX, CANDIDATE_CAP = 100, 500, 25000
SORTS = {"match": "Best match", "cost_asc": "Cost ↑", "cost_desc": "Cost ↓", "alpha": "A–Z"}


# ----------------------------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------------------------
def _acct(request):
    return getattr(getattr(request, "acc", None), "real_account", None)


def _can_write(request):
    acc = getattr(request, "acc", None)
    return bool(acc and acc.can("estimating.write"))


def _body(request):
    if request.content_type and request.content_type.startswith("application/json"):
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return {}
    return {k: (request.POST.getlist(k) if k.endswith("[]") else request.POST.get(k)) for k in request.POST}


def _wants_json(request):
    return request.content_type and request.content_type.startswith("application/json") or request.headers.get("Accept", "").startswith("application/json")


def _report():
    return HygieneReport.objects.first()


def _stats():
    rep = _report()
    key = "estimating.stats.%s" % (rep.id if rep else 0)
    s = cache.get(key)
    if s is None:
        row = fetch_dict("""SELECT COUNT(*) FILTER (WHERE NOT archived) current, COUNT(*) total, COUNT(DISTINCT manufacturer) FILTER (WHERE NOT archived) brands,
                                   COUNT(DISTINCT source_id) sources, MAX(date_key) FILTER (WHERE NOT archived) latest
                            FROM estimating_catalogitem""")[0]
        s = {k: int(v or 0) for k, v in row.items()}
        s["latest_label"] = rules.date_key_label(s["latest"])
        s["report"] = rep.counts if rep else {}
        s["ran_at"] = rep.ran_at if rep else None
        cache.set(key, s, 600)
    return s


def _brands():
    rep = _report()
    key = "estimating.brands.%s" % (rep.id if rep else 0)
    b = cache.get(key)
    if b is None:
        b = [{"name": r["manufacturer"], "n": int(r["n"])} for r in
             fetch_dict("SELECT manufacturer, COUNT(*) n FROM estimating_catalogitem WHERE NOT archived GROUP BY manufacturer ORDER BY (manufacturer = 'Other'), manufacturer")]
        cache.set(key, b, 600)
    return b


def _row(r):
    """A catalog row for the search table / selected card / compare (r from SQL with the source name joined)."""
    cost, msrp, mp = r.get("cost"), r.get("msrp"), r.get("map_price")
    return {"id": r["id"], "mfr": r["manufacturer"], "mfr_raw": r.get("manufacturer_raw") or "", "part": r["part"], "norm": r["part_norm"],
            "desc": r.get("description") or "", "cost": float(cost) if cost is not None else None, "msrp": float(msrp) if msrp is not None else None,
            "map": float(mp) if mp is not None else None, "date": rules.date_key_label(r.get("date_key")), "dk": int(r.get("date_key") or 0),
            "margin": rules.margin_pct(cost, msrp), "conf": int(r.get("confidence") or 0), "conf_label": rules.confidence_label(int(r.get("confidence") or 0)),
            "source": r.get("source_name") or "", "source_id": r.get("source_id"), "category": r.get("category") or "",
            "archived": bool(r.get("archived")), "reason": r.get("archive_reason") or "", "superseded_by": r.get("superseded_by_id"),
            "misc": rules.misc_like(r.get("category"), r["manufacturer"], r["part"], r.get("description"))}


SELECT = """SELECT i.id, i.manufacturer, i.manufacturer_raw, i.part, i.part_norm, i.description, i.cost, i.msrp, i.map_price, i.category, i.date_key,
                   i.confidence, i.archived, i.archive_reason, i.superseded_by_id, i.source_id, s.name source_name
            FROM estimating_catalogitem i JOIN estimating_catalogsource s ON s.id = i.source_id"""


def _my_estimates(request, limit=60):
    a = _acct(request)
    qs = Estimate.objects.select_related("owner").order_by("-updated_at")
    mine = list(qs.filter(owner=a)[:limit]) if a else []
    if len(mine) < 8:
        seen = {e.id for e in mine}
        mine += [e for e in qs[:limit] if e.id not in seen][: limit - len(mine)]
    return mine


# ----------------------------------------------------------------------------------------------------------------
# catalog search + compare (PI-01, PI-02, PI-03)
# ----------------------------------------------------------------------------------------------------------------
def home(request):
    stats = _stats()
    ests = _my_estimates(request)
    est_id = request.GET.get("est") or ""
    return render(request, "estimating/home.html", _ctx(
        request, NAV, stats=stats, brands=_brands(), estimates=ests, est_id=est_id, sorts=SORTS, q=request.GET.get("q", ""),
        brand=request.GET.get("brand", ""), can_write=_can_write(request), labor_types=rules.LABOR_TYPES,
        examples=["ATW-T3201", "TesiraFORTE", "ALF-SC61E", "VIO L208", "wireless", "Biamp"]))


def search(request):
    """JSON. kind=catalog (default): q / brand / has_cost / archived / sort / page / per / ids. kind=bids | customers: pickers."""
    kind = request.GET.get("kind", "catalog")
    q = (request.GET.get("q") or "").strip()
    if kind == "bids":
        from apps.bids.models import Bid
        qs = Bid.objects.select_related("client").order_by("-portal_modified")
        if q:
            qs = qs.filter(project_name__icontains=q) | qs.filter(client_name__icontains=q) | qs.filter(job_number_raw__icontains=q)
        rows = [{"id": b.id, "project_name": b.project_name, "client_name": b.client_name, "stage": b.stage, "value": float(b.value) if b.value is not None else None,
                 "budget": float(b.budget) if b.budget is not None else None, "job": b.job_number_raw, "due": b.bid_due.isoformat() if b.bid_due else ""}
                for b in qs[:25]]
        return JsonResponse({"rows": rows})
    if kind == "customers":
        from apps.core.models import Customer
        qs = Customer.objects.order_by("canonical_name")
        if q:
            qs = qs.filter(canonical_name__icontains=q)
        return JsonResponse({"rows": [{"id": c.id, "name": c.canonical_name, "sl_id": c.sl_customer_id} for c in qs[:25]]})
    t0 = time.time()
    ids = [int(x) for x in (request.GET.get("ids") or "").split(",") if x.strip().isdigit()]
    brand = (request.GET.get("brand") or "").strip()
    has_cost = request.GET.get("has_cost", "1") == "1"
    archived = request.GET.get("archived") == "1"
    sort = request.GET.get("sort") or "match"
    page = max(1, int(request.GET.get("page") or 1))
    per = max(10, min(PER_MAX, int(request.GET.get("per") or PER_DEFAULT)))
    where, params = [], []
    if ids:
        where.append("i.id = ANY(%s)")
        params.append(ids)
    else:
        if not archived:
            where.append("NOT i.archived")
        if has_cost:
            where.append("i.cost IS NOT NULL")
        if brand:
            where.append("i.manufacturer = %s")
            params.append(brand)
        if q:
            qn = rules.norm_part(q)
            ors, ps = ["i.part ILIKE %s", "i.manufacturer ILIKE %s", "i.description ILIKE %s"], ["%" + q + "%"] * 3
            if qn:
                ors.append("i.part_norm LIKE %s")
                ps.append("%" + qn + "%")
            for w in [w for w in q.lower().split() if len(w) > 2][:6]:
                ors += ["i.description ILIKE %s", "i.part_norm ILIKE %s"]
                ps += ["%" + w + "%", "%" + w + "%"]
            where.append("(" + " OR ".join(ors) + ")")
            params += ps
        elif not brand:
            return JsonResponse({"q": "", "rows": [], "total": 0, "page": 1, "pages": 0, "per": per, "capped": False, "ms": 0})
    sql = SELECT + " WHERE " + " AND ".join(where) + " LIMIT %d" % (CANDIDATE_CAP + 1)
    cands = fetch_dict(sql, params)
    capped = len(cands) > CANDIDATE_CAP
    cands = cands[:CANDIDATE_CAP]
    rows = []
    qn = rules.norm_part(q)
    for r in cands:
        sc = rules.score(r["part"], r["part_norm"], r["manufacturer"], r["description"], q, qn) if q else 0
        if q and sc <= 0:
            continue
        d = _row(r)
        d["score"] = sc
        d["score_label"] = rules.score_label(sc) if q else ""
        d["score_class"] = rules.score_class(sc) if q else ""
        rows.append(d)
    big = 10 ** 12
    keyfn = {
        "match": lambda d: (-d["score"], d["cost"] if d["cost"] is not None else big, d["mfr"], d["part"]),
        "cost_asc": lambda d: (d["cost"] if d["cost"] is not None else big, -d["score"]),
        "cost_desc": lambda d: (-(d["cost"] if d["cost"] is not None else -1), -d["score"]),
        "alpha": lambda d: (d["mfr"].lower(), d["part"].lower()),
    }
    if sort in keyfn:
        rows.sort(key=keyfn[sort])
    else:
        col, _, dr = sort.partition(":")
        desc = dr == "desc"
        colkey = {"mfr": lambda d: d["mfr"].lower(), "part": lambda d: d["part"].lower(), "cost": lambda d: d["cost"] if d["cost"] is not None else (-1 if desc else big),
                  "msrp": lambda d: d["msrp"] if d["msrp"] is not None else (-1 if desc else big), "date": lambda d: d["dk"],
                  "margin": lambda d: d["margin"] if d["margin"] is not None else (-1 if desc else big), "score": lambda d: d["score"]}.get(col)
        if colkey:
            rows.sort(key=colkey, reverse=desc)
    total = len(rows)
    pages = max(1, (total + per - 1) // per)
    page = min(page, pages)
    return JsonResponse({"q": q, "rows": rows[(page - 1) * per: page * per], "total": total, "page": page, "pages": pages, "per": per,
                         "capped": capped, "cap": CANDIDATE_CAP, "ms": int((time.time() - t0) * 1000)})


def item(request, pk):
    """One catalog part: the current row, every source row for the same normalized part (price history across files,
    archived rows with their reason and the row that superseded them), and the estimates that use it."""
    it = get_object_or_404(CatalogItem.objects.select_related("source"), pk=pk)
    rows = [_row(r) for r in fetch_dict(SELECT + " WHERE i.part_norm = %s ORDER BY i.archived, i.date_key DESC, i.id", [it.part_norm])]
    cur = next((r for r in rows if not r["archived"]), None)
    this = next((r for r in rows if r["id"] == it.id), None)
    if request.GET.get("format") == "json":
        return JsonResponse({"item": this, "current": cur, "rows": rows})
    uses = fetch_dict("""SELECT e.id, e.title, e.client_name, e.status, e.updated_at, l.qty, l.sell, r.name room
                         FROM estimating_line l JOIN estimating_room r ON r.id = l.room_id JOIN estimating_estimate e ON e.id = r.estimate_id
                         WHERE l.catalog_item_id = ANY(%s) ORDER BY e.updated_at DESC LIMIT 50""", [[r["id"] for r in rows]])
    same_mfr = CatalogItem.objects.filter(manufacturer=it.manufacturer, archived=False).count()
    return render(request, "estimating/item.html", _ctx(request, NAV, it=it, this=this, cur=cur, rows=rows, uses=uses, same_mfr=same_mfr,
                                                        estimates=_my_estimates(request), can_write=_can_write(request)))


# ----------------------------------------------------------------------------------------------------------------
# estimates (PI-04, PI-06, PI-10)
# ----------------------------------------------------------------------------------------------------------------
def estimates(request):
    a = _acct(request)
    who = request.GET.get("who") or "mine"
    status = request.GET.get("status") or ""
    q = (request.GET.get("q") or "").strip()
    qs = Estimate.objects.select_related("owner", "updated_by", "bid", "customer").order_by("-updated_at")
    if who == "mine" and a is not None:
        qs = qs.filter(owner=a)
    if status:
        qs = qs.filter(status=status)
    if q:
        qs = qs.filter(title__icontains=q) | qs.filter(client_name__icontains=q) | qs.filter(notes__icontains=q)
    rows = list(qs[:1000])
    kp = {"n": len(rows), "sell": sum((e.total_sell for e in rows), Decimal("0")), "cost": sum((e.total_cost for e in rows), Decimal("0")),
          "hours": sum((e.labor_hours for e in rows), Decimal("0")), "draft": sum(1 for e in rows if e.status == "draft"),
          "submitted": sum(1 for e in rows if e.status == "submitted"), "approved": sum(1 for e in rows if e.status == "approved")}
    kp["margin"] = float((kp["sell"] - kp["cost"]) / kp["sell"] * 100) if kp["sell"] else None
    return render(request, "estimating/estimates.html", _ctx(request, NAV, rows=rows, kp=kp, who=who, status=status, q=q,
                                                             statuses=Estimate.Status.choices, can_write=_can_write(request),
                                                             all_n=Estimate.objects.count(), mine_n=Estimate.objects.filter(owner=a).count() if a else 0))


def estimate_detail(request, pk):
    est = get_object_or_404(Estimate.objects.select_related("owner", "updated_by", "bid", "bid__estimator", "customer", "project"), pk=pk)
    payload = services.estimate_payload(est)
    if request.GET.get("format") == "json":
        return JsonResponse(payload)
    versions = list(est.versions.select_related("saved_by").order_by("-version_no")[:30])
    view_version = None
    v = request.GET.get("v")
    if v and v.isdigit():
        view_version = est.versions.filter(version_no=int(v)).select_related("saved_by").first()
    return render(request, "estimating/estimate_detail.html", _ctx(
        request, NAV, est=est, payload=payload, versions=versions, view_version=view_version, can_write=_can_write(request),
        labor_types=rules.LABOR_TYPES, notes=rules.ESTIMATOR_NOTES, approval_available=api.approval_available(),
        statuses=Estimate.Status.choices, rates=services.rates_list(services.current_rates())))


def _json_or_redirect(request, data, url):
    if _wants_json(request):
        return JsonResponse(data)
    return redirect(url)


@require_POST
def estimate_save(request):
    """All writes to estimates, by `action`: create · save · meta · add_line · duplicate · delete · status · attach_bid ·
    approval · restore_version. JSON in → JSON out (form posts redirect). A stale save (version_no behind) gets 409."""
    a = _acct(request)
    b = _body(request)
    action = b.get("action") or "save"
    if action == "create":
        est = services.new_estimate(a, title=b.get("title") or "", client_name=b.get("client_name") or "", bid_id=b.get("bid_id") or None)
        audit("estimate_create", request, meta_estimate=est.id)
        return _json_or_redirect(request, {"ok": True, "id": est.id, "url": reverse("estimating:estimate_detail", args=[est.id])},
                                 reverse("estimating:estimate_detail", args=[est.id]))
    est = Estimate.objects.filter(pk=b.get("id") or b.get("estimate_id") or 0).first()
    if est is None:
        raise Http404
    url = reverse("estimating:estimate_detail", args=[est.id])
    if action == "save":
        try:
            est = services.save_estimate(est, b, a, note=b.get("note") or "Saved")
        except services.Stale as e:
            return JsonResponse({"ok": False, "stale": True, "error": str(e), "who": e.who, "when": e.when}, status=409)
        audit("estimate_save", request, meta_estimate=est.id, meta_version=est.version_no, meta_sell=float(est.total_sell))
        return JsonResponse({"ok": True, "payload": services.estimate_payload(est), "saved_at": timezone.localtime().strftime("%-I:%M:%S %p")})
    if action == "meta":
        est = services.save_meta(est, b, a)
        audit("estimate_meta", request, meta_estimate=est.id)
        return _json_or_redirect(request, {"ok": True, "version_no": est.version_no, "payload": services.estimate_payload(est)}, url)
    if action == "add_line":
        it = CatalogItem.objects.select_related("source").filter(pk=b.get("item_id") or 0).first()
        if it is None and not b.get("part"):
            return JsonResponse({"ok": False, "error": "no such catalog item"}, status=400)
        line = services.add_line(est, a, item=it, room_id=b.get("room_id") or None, qty=rules.line_qty(b.get("qty") or 1), area=b.get("area") or "",
                                 part=b.get("part") or "")
        est.refresh_from_db()
        audit("estimate_add_line", request, meta_estimate=est.id, meta_part=line.part)
        return JsonResponse({"ok": True, "line_id": line.id, "room_id": line.room_id, "estimate": api.estimate_summary(est) | {"total_sell": float(est.total_sell), "total_cost": float(est.total_cost), "profit": float(est.profit), "labor_hours": float(est.labor_hours), "updated_at": est.updated_at.isoformat()}})
    if action == "duplicate":
        copy = services.duplicate_estimate(est, a)
        audit("estimate_duplicate", request, meta_estimate=est.id, meta_copy=copy.id)
        return _json_or_redirect(request, {"ok": True, "id": copy.id, "url": reverse("estimating:estimate_detail", args=[copy.id])},
                                 reverse("estimating:estimate_detail", args=[copy.id]))
    if action == "delete":
        audit("estimate_delete", request, meta_estimate=est.id, meta_title=str(est), meta_sell=float(est.total_sell))
        est.delete()
        return _json_or_redirect(request, {"ok": True}, reverse("estimating:estimating_estimates"))
    if action == "status":
        est = services.save_meta(est, {"status": b.get("status")}, a)
        audit("estimate_status", request, meta_estimate=est.id, meta_status=est.status)
        return _json_or_redirect(request, {"ok": True, "status": est.status, "version_no": est.version_no}, url)
    if action == "attach_bid":
        est = services.save_meta(est, {"bid_id": b.get("bid_id") or None}, a)
        audit("estimate_attach_bid", request, meta_estimate=est.id, meta_bid=est.bid_id)
        return _json_or_redirect(request, {"ok": True, "payload": services.estimate_payload(est)}, url)
    if action == "approval":
        ok, msg = api.raise_approval(est, a, note=b.get("note") or "", kind=b.get("kind") or "BOM", needed_by=b.get("needed_by") or None,
                                     department=b.get("department") or "")
        audit("estimate_approval", request, meta_estimate=est.id, meta_ok=ok, meta_msg=msg)
        return _json_or_redirect(request, {"ok": ok, "message": msg, "approval_ref": est.approval_ref}, url + "?msg=" + msg[:120])
    if action == "restore_version":
        ver = est.versions.filter(version_no=int(b.get("version_no") or 0)).first()
        if ver is None:
            raise Http404
        snap = ver.snapshot or {}
        payload = {"rooms": snap.get("rooms", []), "title": snap.get("meta", {}).get("title", est.title), "notes": snap.get("meta", {}).get("notes", est.notes),
                   "client_name": snap.get("meta", {}).get("client_name", est.client_name)}
        est = services.save_estimate(est, payload, a, note="Restored v%d" % ver.version_no)
        audit("estimate_restore", request, meta_estimate=est.id, meta_from=ver.version_no)
        return _json_or_redirect(request, {"ok": True, "payload": services.estimate_payload(est)}, url)
    return JsonResponse({"ok": False, "error": "unknown action %s" % action}, status=400)


def estimate_export(request, pk):
    est = get_object_or_404(Estimate.objects.select_related("bid", "customer"), pk=pk)
    payload = services.estimate_payload(est)
    fmt = request.GET.get("fmt") or "xlsx"
    stamp = timezone.localdate().isoformat()
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in (est.title or "PACE_Estimate"))[:60].strip() or "PACE_Estimate"
    audit("estimate_export", request, meta_estimate=est.id, meta_fmt=fmt)
    if fmt == "csv":
        room = int(request.GET.get("room") or 0) or None
        resp = HttpResponse(services.export_csv(est, payload, room_id=room), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="PACE_Estimate_Detail_%s.csv"' % stamp
        return resp
    if fmt == "txt":
        return HttpResponse(services.export_text(est, payload), content_type="text/plain; charset=utf-8")
    if fmt == "json":
        return JsonResponse(payload)
    data = services.export_xlsx(est, payload)
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="%s_%s.xlsx"' % (safe.replace(" ", "_"), stamp)
    return resp


# ----------------------------------------------------------------------------------------------------------------
# rate card (PI-05)
# ----------------------------------------------------------------------------------------------------------------
def rates(request):
    cur = services.current_rates()
    return render(request, "estimating/rates.html", _ctx(
        request, NAV, rates=services.rates_list(cur), history=services.rates_history(), notes=rules.ESTIMATOR_NOTES,
        card_notes=rules.rate_card_notes(cur), today=timezone.localdate(), can_write=_can_write(request), saved=request.GET.get("saved"),
        labor_types=rules.LABOR_TYPES))


@require_POST
def rates_save(request):
    a = _acct(request)
    eff = request.POST.get("effective_from") or timezone.localdate().isoformat()
    try:
        eff = timezone.datetime.fromisoformat(eff).date()
    except ValueError:
        eff = timezone.localdate()
    changed = 0
    if request.POST.get("action") == "reset":
        for i, (rid, label, group, cost, sell) in enumerate(rules.DEFAULT_RATES):
            LaborRate.objects.update_or_create(rate_id=rid, effective_from=eff, defaults={"label": label, "group": group, "cost": cost, "sell": sell, "order": i, "set_by": a})
            changed += 1
        audit("rates_reset", request, meta_effective=eff.isoformat())
    else:
        cur = services.current_rates(eff)
        for i, (rid, label, group, dcost, dsell) in enumerate(rules.DEFAULT_RATES):
            cost = rules.q2(request.POST.get("cost_" + rid, cur[rid]["cost"]))
            sell = rules.q2(request.POST.get("sell_" + rid, cur[rid]["sell"]))
            if cost < 0 or sell < 0:
                continue
            if cost == cur[rid]["cost"] and sell == cur[rid]["sell"] and cur[rid]["effective_from"] != eff:
                continue
            LaborRate.objects.update_or_create(rate_id=rid, effective_from=eff, defaults={"label": label, "group": group, "cost": cost, "sell": sell, "order": i, "set_by": a})
            changed += 1
        audit("rates_save", request, meta_effective=eff.isoformat(), meta_changed=changed)
    return redirect(reverse("estimating:estimating_rates") + "?saved=%d" % changed)


# ----------------------------------------------------------------------------------------------------------------
# sources + hygiene + Data Files (PI-07, PI-08, PI-09)
# ----------------------------------------------------------------------------------------------------------------
def sources(request):
    q = (request.GET.get("q") or "").strip()
    mfr = (request.GET.get("mfr") or "").strip()
    srcs = CatalogSource.objects.select_related("loaded_by").order_by("-rows", "name")
    if q:
        srcs = srcs.filter(name__icontains=q) | srcs.filter(vendor__icontains=q)
    srcs = list(srcs)
    for s in srcs:
        s.current = 0
    cur = {r["source_id"]: int(r["n"]) for r in fetch_dict("SELECT source_id, COUNT(*) n FROM estimating_catalogitem WHERE NOT archived GROUP BY source_id")}
    for s in srcs:
        s.current = cur.get(s.id, 0)
    mfrs = fetch_dict("""SELECT manufacturer, COUNT(*) n, COUNT(cost) with_cost, COUNT(msrp) with_msrp, COUNT(map_price) with_map,
                                COUNT(DISTINCT source_id) files, MAX(date_key) latest
                         FROM estimating_catalogitem WHERE NOT archived GROUP BY manufacturer ORDER BY (manufacturer = 'Other'), n DESC""")
    for m in mfrs:
        m["latest_label"] = rules.date_key_label(m["latest"])
    mfr_files = []
    if mfr:
        mfr_files = fetch_dict("""SELECT s.id, s.name, s.date_key, COUNT(*) n, COUNT(i.cost) with_cost FROM estimating_catalogitem i
                                  JOIN estimating_catalogsource s ON s.id = i.source_id WHERE NOT i.archived AND i.manufacturer = %s
                                  GROUP BY s.id, s.name, s.date_key ORDER BY s.date_key DESC, n DESC""", [mfr])
        for f in mfr_files:
            f["date_label"] = rules.date_key_label(f["date_key"])
    reports = list(HygieneReport.objects.order_by("-ran_at")[:12])
    return render(request, "estimating/sources.html", _ctx(
        request, NAV, sources=srcs, q=q, mfrs=mfrs, mfr=mfr, mfr_files=mfr_files, reports=reports, report=reports[0] if reports else None,
        stats=_stats(), can_write=_can_write(request), folder=loaders.vendor_price_dir(), kinds=dict(CatalogSource.Kind.choices),
        reasons=dict(CatalogItem.Archive.choices)))


def _template_xlsx():
    from openpyxl import Workbook
    import io
    wb = Workbook()
    ws = wb.active
    ws.title = "Price List"
    ws.append(["Manufacturer", "Part Number", "Description", "Dealer Cost", "MSRP / List Price", "MAP Price", "Effective Date"])
    ws.append(["Example Manufacturer", "MODEL-100", "Example product description", 100, 165, 149, timezone.localdate().isoformat()])
    for col, w in zip("ABCDEFG", (24, 22, 48, 14, 18, 14, 16)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _catalog_csv():
    """The current catalog as CSV (the dashboard's "Export Current Catalog" — 190k rows; xlsx of that size is impractical)."""
    import csv
    import io
    from django.db import connection
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Manufacturer", "Part Number", "Normalized", "Description", "Dealer Cost", "MSRP / List Price", "MAP Price", "Category", "Effective Date", "Source File", "Confidence"])
    with connection.cursor() as cur:
        cur.execute("""SELECT i.manufacturer, i.part, i.part_norm, i.description, i.cost, i.msrp, i.map_price, i.category, i.date_key, s.name, i.confidence
                       FROM estimating_catalogitem i JOIN estimating_catalogsource s ON s.id = i.source_id WHERE NOT i.archived ORDER BY i.manufacturer, i.part""")
        for m, p, n, d, c, ms, mp, cat, dk, src, conf in cur.fetchall():
            w.writerow([m, p, n, d, c or "", ms or "", mp or "", cat, rules.date_key_label(dk), src, conf])
    return buf.getvalue()


def import_(request):
    """Two importers on one page: an estimate from a BOM workbook (room column or sheet-per-room) and vendor catalog
    files (multi-file, dry-run preview, upsert / add-only / prices-only). Form posts carry the files twice (preview,
    then apply) so nothing is parked in the session."""
    if request.GET.get("template") == "1":
        resp = HttpResponse(_template_xlsx(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="PACE_Catalog_Import_Template.xlsx"'
        return resp
    if request.GET.get("export") == "catalog":
        audit("catalog_export", request)
        resp = HttpResponse(_catalog_csv(), content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="PACE_Current_Pricing_Catalog_%s.csv"' % timezone.localdate().strftime("%Y%m%d")
        return resp
    a = _acct(request)
    ctx = {"mode": "upsert", "modes": loaders.MODES, "est_result": None, "cat_result": None, "errors": [], "message": request.GET.get("msg", ""),
           "estimate_id": request.GET.get("estimate") or "", "estimates": _my_estimates(request), "can_write": _can_write(request),
           "history": list(CatalogSource.objects.filter(kind__in=["upload", "file"]).select_related("loaded_by").order_by("-loaded_at")[:20]),
           "folder": loaders.vendor_price_dir(), "stats": _stats()}
    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "remove_source":
            res = loaders.remove_source(request.POST.get("source_id"))
            audit("catalog_remove_source", request, meta_source=(res or {}).get("name"), meta_rows=(res or {}).get("rows"))
            return redirect(reverse("estimating:estimating_sources") + ("?msg=removed+%s" % (res or {}).get("rows", 0)))
        if action in ("estimate_preview", "estimate_create"):
            f = request.FILES.get("estimate_file")
            if not f:
                ctx["errors"].append("Choose a workbook first.")
            else:
                data = f.read()
                rows, found, errors, sheets = loaders.parse_file(data=data, name=f.name)
                ctx["errors"] += errors
                res = services.import_rooms(sheets, "Imported estimate: " + f.name)
                res["file"] = f.name
                res["lines"] = sum(len(r["lines"]) for r in res["rooms"])
                ctx["est_result"] = res
                if not res["rooms"]:
                    ctx["errors"].append("No estimate room sheets found in %s — I looked for Item / Manufacturer / Qty / Model # / Description headers." % f.name)
                elif action == "estimate_create":
                    target = Estimate.objects.filter(pk=request.POST.get("estimate_id") or 0).first()
                    title = (request.POST.get("title") or "").strip() or f.name.rsplit(".", 1)[0]
                    if target is None:
                        target = services.new_estimate(a, title=title)
                        target.source_file = f.name[:255]
                        target.save(update_fields=["source_file"])
                        payload = {"rooms": res["rooms"], "title": title}
                    else:
                        existing = services.rooms_from_db(target)
                        payload = {"rooms": [{"name": r["name"], "notes": r["notes"], "lines": r["lines"]} for r in existing] + res["rooms"]}
                    est = services.save_estimate(target, payload, a, note="Imported %s (%d rooms)" % (f.name, len(res["rooms"])))
                    audit("estimate_import", request, meta_estimate=est.id, meta_file=f.name, meta_rooms=len(res["rooms"]), meta_matched=res["matched"])
                    return redirect(reverse("estimating:estimate_detail", args=[est.id]) + "?msg=imported")
        if action in ("catalog_preview", "catalog_apply"):
            files = request.FILES.getlist("catalog_files")
            mode = request.POST.get("mode") if request.POST.get("mode") in loaders.MODES else "upsert"
            ctx["mode"] = mode
            if not files:
                ctx["errors"].append("Choose one or more vendor catalog files first.")
            else:
                results, total = [], {"files": len(files), "sheets": 0, "rows": 0, "brands": set(), "existing": 0, "new": 0, "stale": 0, "added": 0, "updated": 0, "skipped": 0}
                for f in files:
                    data = f.read()
                    rows, found, errors, _ = loaders.parse_file(data=data, name=f.name)
                    ctx["errors"] += errors
                    total["sheets"] += found
                    if not rows:
                        ctx["errors"].append("%s: no product rows recognised." % f.name)
                        continue
                    res = loaders.apply_catalog_rows(rows, f.name, mode=mode, dry_run=(action == "catalog_preview"), kind="upload", account=a)
                    c = res["counts"]
                    total["rows"] += c["rows"]
                    total["existing"] += c["existing"]
                    total["new"] += c["new"]
                    total["stale"] += c["stale"]
                    total["added"] += c["added"]
                    total["updated"] += c["updated"]
                    total["skipped"] += c["skipped"]
                    total["brands"] |= {p["manufacturer"] for p in res.get("preview", [])} if action == "catalog_preview" else set()
                    results.append({"file": f.name, "counts": c, "preview": res.get("preview", [])[:500]})
                total["brands"] = len(total["brands"])
                ctx["cat_result"] = {"applied": action == "catalog_apply", "total": total, "results": results}
                if action == "catalog_apply":
                    audit("catalog_import", request, meta_files=[f.name for f in files], meta_mode=mode, meta_added=total["added"], meta_updated=total["updated"])
                    cache.clear()
                    ctx["history"] = list(CatalogSource.objects.filter(kind__in=["upload", "file"]).select_related("loaded_by").order_by("-loaded_at")[:20])
                    ctx["stats"] = _stats()
    return render(request, "estimating/import.html", _ctx(request, NAV, **ctx))
