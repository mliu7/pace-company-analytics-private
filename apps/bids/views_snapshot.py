"""Pipeline Snapshot (Owner, 2026-09-10): the Project Snapshot's shape for proposals — what was submitted (and won,
lost, due) in a day or week window, by division or by estimator; every open proposal with value, budget, GP quoted,
stated probability, PCA's estimated win rate, expected GP, attention labels and the documents behind it; and the
same table for the quotes still in progress. Everything comes from the Project Portal mirror (apps.bids) joined to
SL for the outcome; nothing is written."""

import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Division, Employee
from apps.dashboard.snapshot_windows import day_window, week_window
from apps.dashboard.views import _ctx
from apps.ingestion.bulk import fetch_dict

from . import analytics as A
from . import rules, winrate

D0 = Decimal(0)


def _f(v):
    return float(v) if v is not None else None


def _div_nav(request, current_code):
    """Prev / next through All divisions + every active division, keeping the window and the other filters
    (the Project Snapshot's carousel, pointed at this page)."""
    seq = [{"code": "", "label": "All divisions"}] + [{"code": d.code, "label": "%s %s" % (d.code, d.name)} for d in Division.objects.filter(active=True).order_by("code")]
    base = reverse("bids:bids_snapshot")
    for x in seq:
        q = request.GET.copy()
        if x["code"]:
            q["div"] = x["code"]
        else:
            q.pop("div", None)
        x["url"] = "%s?%s" % (base, q.urlencode()) if q else base
    i = next((n for n, x in enumerate(seq) if x["code"] == (current_code or "")), 0)
    return {"cur": seq[i], "at": i + 1, "of": len(seq), "prev": seq[(i - 1) % len(seq)], "next": seq[(i + 1) % len(seq)]}


def _params(request):
    view = "week" if request.GET.get("view") == "week" else "day"
    raw = None
    for param in ("day", "week"):
        if request.GET.get(param):
            try:
                raw = date.fromisoformat(request.GET[param][:10])
            except ValueError:
                pass
    div = request.GET.get("div", "")
    if div and not Division.objects.filter(code=div).exists():
        div = ""
    est = request.GET.get("est", "")
    est_emp = Employee.objects.filter(employee_key=est).first() if est else None
    by = "estimator" if request.GET.get("by") == "estimator" else "division"
    return view, raw, div, est_emp, by


def _window(view, raw, today):
    """The Project Snapshot's windows: business days (Friday = Fri–Sun) or Mon–Sun weeks."""
    fn = week_window if view == "week" else day_window
    start, end, label, prev_key, next_key = fn(raw or today)
    # step over the windows that actually had a submission (latest first), like the Project Snapshot's active days
    subs = [r["d"] for r in fetch_dict("SELECT DISTINCT submitted_on d FROM bids_bid WHERE source = 'list' AND submitted_on IS NOT NULL AND submitted_on >= %s AND submitted_on <= %s ORDER BY 1 DESC",
                                       [today - timedelta(days=500), today + timedelta(days=60)])]
    keys = sorted({(d - timedelta(days=d.weekday())) if view == "week" else day_window(d)[0] for d in subs}, reverse=True)
    if raw is None and keys:
        start, end, label, prev_key, next_key = fn(keys[0] if keys[0] <= today else max(k for k in keys if k <= today) if any(k <= today for k in keys) else keys[0])
    prev_key = next((k for k in keys if k < start), None)
    next_key = next((k for k in reversed(keys) if k > start), None)
    return start, end, label, prev_key, next_key


def _docs_for(bid_ids):
    """{bid_id: [files]} — the P: drive / SharePoint files linked to each bid (or its SL job), newest first, capped."""
    if not bid_ids:
        return {}
    try:
        from apps.documents.models import DocLink, File
        from apps.ingestion.sources import share_client
    except Exception:  # noqa
        return {}
    out = defaultdict(list)
    rows = File.objects.filter(is_deleted=False, linked_bid_id__in=bid_ids).select_related("repo").order_by("-mtime")[:6000]
    for f in rows:
        if len(out[f.linked_bid_id]) >= 12:
            continue
        folder = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
        sub = folder.split("/")[-1] if folder else ""
        out[f.linked_bid_id].append({"id": f.id, "name": f.name, "ext": f.ext, "size": f.size, "mtime": f.mtime.isoformat()[:10] if f.mtime else "", "sub": sub,
                                     "url": reverse("documents:document_preview", args=[f.id]),
                                     "open": share_client.smb_url(f.path) if f.repo.is_share else f.web_url, "unc": f.unc if f.repo.is_share else ""})
    folders = {}
    for l in DocLink.objects.filter(bid_id__in=bid_ids, folder__isnull=False, rule="portal_id").exclude(state="rejected").select_related("folder", "folder__repo"):
        fo = l.folder
        if not fo.is_deleted and l.bid_id not in folders:
            folders[l.bid_id] = {"path": fo.path, "open": share_client.smb_url(fo.path) if fo.repo.is_share else fo.web_url, "unc": share_client.unc_path(fo.path) if fo.repo.is_share else ""}
    return {b: {"files": out.get(b, []), "folder": folders.get(b)} for b in set(out) | set(folders)}


def _last_activity(rows, docs, notes_at, follow_at):
    """{bid_id: {'date', 'kind', 'text'}} — the most recent thing that happened to the bid: a portal edit (the version
    history says what changed), a file added or updated in its folder, a PCA note or follow-up, else its creation."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    versions = defaultdict(list)
    for v in fetch_dict("""SELECT bid_id, modified_at, modified_by, stage, value, budget, bidder_raw, probability, job_number_raw
                           FROM bids_bidversion WHERE bid_id = ANY(%s) ORDER BY bid_id, modified_at""", [ids]):
        versions[v["bid_id"]].append(v)
    out = {}
    for r in rows:
        cands = []
        if r["portal_created"]:
            cands.append((r["portal_created"], "created", "row created in the portal" + (" by " + r["created_by_raw"] if r.get("created_by_raw") else "")))
        vs = versions.get(r["id"]) or []
        if len(vs) >= 2 and vs[-1]["modified_at"]:
            a, b = vs[-2], vs[-1]
            changes = []
            if a["stage"] != b["stage"]:
                changes.append("status → %s" % rules.STAGE_LABEL.get(b["stage"], b["stage"]))
            if a["value"] != b["value"]:
                changes.append("value %s" % ("set" if a["value"] in (None, 0) else "changed"))
            if a["budget"] != b["budget"]:
                changes.append("budget %s" % ("set" if a["budget"] in (None, 0) else "changed"))
            if a["probability"] != b["probability"]:
                changes.append("chance → %s" % ("%d%%" % b["probability"] if b["probability"] is not None else "unscored"))
            if (a["bidder_raw"] or "") != (b["bidder_raw"] or ""):
                changes.append("bidder → %s" % (b["bidder_raw"] or "blank"))
            if (a["job_number_raw"] or "") != (b["job_number_raw"] or ""):
                changes.append("job # → %s" % (b["job_number_raw"] or "blank"))
            cands.append((b["modified_at"], "portal", (", ".join(changes) if changes else "portal row edited") + (" · " + b["modified_by"] if b["modified_by"] else "")))
        elif r["portal_modified"]:
            cands.append((r["portal_modified"], "portal", "portal row edited"))
        d = (docs.get(r["id"]) or {}).get("files") or []
        if d and d[0].get("mtime"):
            newest = max(d, key=lambda x: x["mtime"])
            cands.append((timezone.make_aware(timezone.datetime.fromisoformat(newest["mtime"])), "doc", "file updated: %s" % newest["name"]))
        if notes_at.get(r["id"]):
            cands.append((notes_at[r["id"]], "note", "note added in PCA"))
        if follow_at.get(r["id"]):
            cands.append((follow_at[r["id"]], "note", "follow-up added in PCA"))
        cands = [c for c in cands if c[0]]
        if not cands:
            continue
        when, kind, text = max(cands, key=lambda c: c[0])
        out[r["id"]] = {"date": when.date().isoformat() if hasattr(when, "date") else str(when)[:10], "kind": kind, "text": text}
    return out


def _rows(where, params, model, today, with_docs=True, order="b.submitted_on DESC NULLS LAST, b.bid_due ASC NULLS LAST"):
    rows = A.bids(where, params, order=order)
    notes = defaultdict(int)
    for r in fetch_dict("SELECT bid_id, COUNT(*) n FROM bids_bidnote WHERE bid_id = ANY(%s) GROUP BY 1", [[r["id"] for r in rows]]) if rows else []:
        notes[r["bid_id"]] = r["n"]
    followups = defaultdict(int)
    for r in fetch_dict("SELECT bid_id, COUNT(*) n FROM bids_bidfollowup WHERE done_at IS NULL AND bid_id = ANY(%s) GROUP BY 1", [[r["id"] for r in rows]]) if rows else []:
        followups[r["bid_id"]] = r["n"]
    docs = _docs_for([r["id"] for r in rows]) if with_docs else {}
    notes_at = {r["bid_id"]: r["t"] for r in fetch_dict("SELECT bid_id, MAX(created_at) t FROM bids_bidnote WHERE bid_id = ANY(%s) GROUP BY 1", [[r["id"] for r in rows]])} if rows else {}
    follow_at = {r["bid_id"]: r["t"] for r in fetch_dict("SELECT bid_id, MAX(created_at) t FROM bids_bidfollowup WHERE bid_id = ANY(%s) GROUP BY 1", [[r["id"] for r in rows]])} if rows else {}
    activity = _last_activity(rows, docs, notes_at, follow_at)
    out = []
    for r in rows:
        r["work_type"] = rules.work_type(r["project_name"], r["division"])
        p, breakdown = model.predict(r)
        gp = (r["value"] - r["budget"]) if (r["value"] is not None and r["budget"] is not None) else None
        prob = r["probability"]
        d = docs.get(r["id"]) or {}
        out.append({
            "id": r["id"], "url": reverse("bids:bid_detail", args=[r["id"]]), "portal": r["web_url"] or "", "pid": r["portal_project_id"],
            "name": r["project_name"] or r["job_number_raw"] or "(untitled)", "client": r["client_label"] or "", "client_id": r["client_id"],
            "div": r["division"] or "", "est": r["estimator"] or "", "est_id": r["estimator_id"], "inferred": bool(r["estimator_inferred"]),
            "est_url": reverse("bids:estimator_detail", args=[r["est_key"]]) if r.get("est_key") else "",
            "client_url": reverse("dashboard:customer_detail", args=[r["cust_key"]]) if r.get("cust_key") else "",
            "rep": "House" if r["house_account"] else (r["rep"] or ""), "stage": r["stage"], "stage_label": r["stage_label"], "flag": r["stage_flag"] or "",
            "type": r["work_type"], "sector": r["sector"] or "", "size": r["size_band"],
            "submitted": r["submitted_on"].isoformat() if r["submitted_on"] else "", "due": r["bid_due"].isoformat() if r["bid_due"] else "",
            "awarded": r["awarded_on"].isoformat() if r["awarded_on"] else "", "modified": r["portal_modified"].isoformat()[:10] if r["portal_modified"] else "",
            "age": (today - r["submitted_on"]).days if r["submitted_on"] else None,
            "value": _f(r["value"]), "budget": _f(r["budget"]), "gp": _f(gp), "gp_pct": (float(gp) / float(r["value"])) if (gp is not None and r["value"]) else None,
            "prob": prob, "pca": round(p, 3), "exp_gp": (float(gp) * prob / 100) if (gp is not None and prob is not None) else None,
            "exp_gp_pca": (float(gp) * p) if gp is not None else None, "exp_value": (float(r["value"]) * prob / 100) if (r["value"] is not None and prob is not None) else None,
            "breakdown": breakdown, "attention": A.attention_reasons(r, today), "ball": r["ball_in_court"] or "", "bom": r["bom_status"] or "", "walk": r["walkthrough_raw"] or "",
            "job": r["pdisp"] or "", "job_url": reverse("dashboard:project_detail", args=[r["cpn"]]) if r["cpn"] else "", "won_by_sl": bool(r["won_by_sl"]),
            "notes": notes.get(r["id"], 0), "followups": followups.get(r["id"], 0), "docs": d.get("files", []), "folder": d.get("folder"),
            "act": activity.get(r["id"]), "act_days": (today - date.fromisoformat(activity[r["id"]]["date"])).days if r["id"] in activity else None,
        })
    return out


def _totals(rows):
    t = {"n": len(rows), "value": 0.0, "budget": 0.0, "gp": 0.0, "exp_gp": 0.0, "exp_gp_pca": 0.0, "exp_value": 0.0, "scored": 0, "attention": 0}
    for r in rows:
        t["value"] += r["value"] or 0; t["budget"] += r["budget"] or 0; t["gp"] += r["gp"] or 0
        t["exp_gp"] += r["exp_gp"] or 0; t["exp_gp_pca"] += r["exp_gp_pca"] or 0; t["exp_value"] += r["exp_value"] or 0
        t["scored"] += 1 if r["prob"] is not None else 0; t["attention"] += 1 if r["attention"] else 0
    t["gp_pct"] = (t["gp"] / t["value"]) if t["value"] else None
    t["pca_avg"] = (sum(r["pca"] * (r["value"] or 0) for r in rows) / t["value"]) if t["value"] else None
    return t


def _by_surname(rows):
    """'Steingard (6), Potoky (1)' — the estimators behind a set of bids, by surname, most first."""
    from collections import Counter
    c = Counter((r["est"].split()[-1] if r["est"] else "no bidder") for r in rows)
    return ", ".join("%s (%d)" % (k, n) for k, n in sorted(c.items(), key=lambda x: (-x[1], x[0])))


def _group(rows, key):
    by = defaultdict(list)
    for r in rows:
        by[r[key] or "(none)"].append(r)
    out = [dict(_totals(v), key=k, rows=[r["id"] for r in v], url=(v[0].get("est_url") if key == "est" else "")) for k, v in by.items()]
    out.sort(key=lambda x: -x["value"])
    return out


def _estimator_options(start, end, window_rows=None):
    """Every estimator with an open bid or a submission in the window, with the number they submitted in the window."""
    counts = {r["k"]: r["c"] for r in fetch_dict("""SELECT e.employee_key k, COUNT(*) c FROM bids_bid b JOIN core_employee e ON e.id = b.estimator_id
                                                    WHERE b.source = 'list' AND b.submitted_on BETWEEN %s AND %s GROUP BY 1""", [start, end])}
    people = fetch_dict("""SELECT DISTINCT e.employee_key k, e.canonical_name n FROM bids_bid b JOIN core_employee e ON e.id = b.estimator_id
                           WHERE b.source = 'list' AND (b.stage IN ('quoting','submitted','on_hold') OR b.submitted_on BETWEEN %s AND %s)""", [start, end])
    out = [{"k": p["k"], "n": p["n"], "c": counts.get(p["k"], 0)} for p in people]
    out.sort(key=lambda x: (-x["c"], x["n"]))
    return out


def pipeline_snapshot(request):
    today = timezone.localdate()
    view, raw, div, est_emp, by = _params(request)
    start, end, label, prev_key, next_key = _window(view, raw, today)
    model = winrate.Model(today=today)
    scope, params = ["b.source = 'list'"], []
    if div:
        scope.append("b.division = %s"); params.append(div)
    if est_emp:
        scope.append("b.estimator_id = %s"); params.append(est_emp.id)
    scope_sql = " AND ".join(scope)
    # ---- the window: submitted, awarded, lost, due
    win = _rows(scope_sql + " AND ((b.submitted_on BETWEEN %s AND %s) OR (b.awarded_on BETWEEN %s AND %s) OR (b.bid_due BETWEEN %s AND %s) OR (b.stage = 'lost' AND b.portal_modified::date BETWEEN %s AND %s))",
                params + [start, end] * 4, model, today)
    submitted = [r for r in win if r["submitted"] and start.isoformat() <= r["submitted"] <= end.isoformat()]
    awarded = [r for r in win if r["awarded"] and start.isoformat() <= r["awarded"] <= end.isoformat()]
    lost = [r for r in win if r["stage"] == "lost" and r["modified"] and start.isoformat() <= r["modified"] <= end.isoformat() and r["id"] not in {x["id"] for x in awarded}]
    due = [r for r in win if r["due"] and start.isoformat() <= r["due"] <= end.isoformat() and r["stage"] in ("quoting", "submitted", "on_hold")]
    # ---- the open book
    open_sub = _rows(scope_sql + " AND b.stage = 'submitted'", params, model, today)
    quoting = _rows(scope_sql + " AND b.stage IN ('quoting', 'on_hold')", params, model, today, order="b.bid_due ASC NULLS LAST, b.portal_modified DESC")
    # ---- the strip: submissions per window over the recent past
    strip = fetch_dict("""SELECT %s AS k, COUNT(*) n, COALESCE(SUM(value), 0) v FROM bids_bid b WHERE %s AND b.submitted_on IS NOT NULL AND b.submitted_on >= %%s AND b.submitted_on <= %%s GROUP BY 1 ORDER BY 1"""
                       % (("date_trunc('week', b.submitted_on)::date" if view == "week" else "b.submitted_on"), scope_sql),
                       params + [today - timedelta(days=120 if view == "week" else 45), today])
    cal = model.calibration()
    ctx = dict(
        view=view, start=start, end=end, label=label, prev_key=prev_key, next_key=next_key, div_code=div, est=est_emp, est_key=est_emp.employee_key if est_emp else "", by=by,
        divisions=Division.objects.filter(active=True).order_by("code"),
        estimators=_estimator_options(start, end, submitted if not est_emp else None),
        div_nav=_div_nav(request, div),
        submitted=submitted, awarded=awarded, lost=lost, due=due, tot_sub=_totals(submitted), tot_aw=_totals(awarded), tot_lost=_totals(lost), tot_due=_totals(due),
        due_by_est=_by_surname(due),
        groups=_group(submitted, "div" if by == "division" else "est"), open_tot=_totals(open_sub), quoting_tot=_totals(quoting),
        model={"n": model.n, "base": model.base, "damping": model.damping, "cal": cal},
        data_json=json.dumps({"window": submitted, "open": open_sub, "quoting": quoting, "awarded": awarded, "lost": lost, "due": due,
                              "strip": [{"k": r["k"].isoformat(), "n": r["n"], "v": float(r["v"])} for r in strip], "today": today.isoformat(),
                              "start": start.isoformat(), "end": end.isoformat(), "view": view}, default=str),
        today=today, nav_qs=request.GET.urlencode(),
    )
    return render(request, "bids/pipeline_snapshot.html", _ctx(request, "pipeline-snapshot", **ctx))


def pipeline_snapshot_json(request):
    """The three tables as JSON (same shape as the page's embedded data) — for the drill modal / other pages."""
    today = timezone.localdate()
    view, raw, div, est_emp, by = _params(request)
    model = winrate.Model(today=today)
    scope, params = ["b.source = 'list'"], []
    if div:
        scope.append("b.division = %s"); params.append(div)
    if est_emp:
        scope.append("b.estimator_id = %s"); params.append(est_emp.id)
    which = request.GET.get("table", "open")
    stage_sql = {"open": "b.stage = 'submitted'", "quoting": "b.stage IN ('quoting', 'on_hold')"}.get(which, "b.stage = 'submitted'")
    rows = _rows(" AND ".join(scope) + " AND " + stage_sql, params, model, today, with_docs=request.GET.get("docs") == "1")
    return JsonResponse({"rows": rows, "n": len(rows), "totals": _totals(rows)})
