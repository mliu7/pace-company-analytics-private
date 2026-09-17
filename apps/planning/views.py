"""Production › Today and Status board (SharePoint spec §6.0, §6.1; parity A.6 PS-01…PS-10)."""

import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.access.audit import log as audit
from apps.core.models import Employee, Project
from apps.dashboard.views import _ctx
from apps.ingestion.bulk import fetch_dict

from . import api, enrich, loaders, rules
from .models import (ApprovalRequest, PunchItem, PunchProject, Source, StatusHistory, StatusRow, StatusVocabulary)

DIVISIONS = ["040", "070", "080"]


# ------------------------------------------------------------------------------------------------ shared helpers
def _user(request):
    u = getattr(request, "user", None)
    return u if (u is not None and getattr(u, "is_authenticated", False)) else None


def _who(request):
    acc = getattr(request, "acc", None)
    if acc is not None and acc.real_account is not None:
        return acc.real_account.display_name
    u = _user(request)
    return (u.get_username() if u else "") or "local"


def _user_name(u):
    if u is None:
        return ""
    acct = getattr(u, "pca_account", None)
    return acct.display_name if acct is not None else (u.get_full_name() or u.get_username())


def _body(request):
    if request.content_type and request.content_type.startswith("application/json"):
        try:
            return json.loads(request.body.decode("utf-8") or "{}")
        except ValueError:
            return {}
    return {k: v for k, v in request.POST.items()}


def _bad(msg, status=400, **extra):
    return JsonResponse(dict(error=msg, **extra), status=status)


def _iso(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return timezone.localtime(v).isoformat(timespec="minutes")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _dec(v, default=None):
    if v is None or v == "":
        return default
    try:
        return Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError):
        return default


def _today():
    return timezone.localdate()


def _tab_counts():
    today = _today()
    active = StatusRow.objects.filter(completed=False, percent__lt=100).count()
    open_items = PunchItem.objects.filter(date_completed__isnull=True)
    return {
        "status": StatusRow.objects.filter(completed=False).count(),
        "tasks": active,
        "punch": open_items.filter(due_by__lt=today).count(),
        "approvals": ApprovalRequest.objects.filter(status__in=["pending", "reopened"]).count(),
    }


def _asset_version():
    """mtime-based cache buster for apps/planning/static/planning/* (the dev server sends no cache headers)."""
    import os
    d = Path(__file__).resolve().parent / "static" / "planning"
    try:
        return int(max(os.stat(p).st_mtime for p in d.iterdir()))
    except (OSError, ValueError):
        return 0


def _pctx(request, sub, **extra):
    ctx = _ctx(request, "production", sub=sub, tab_counts=_tab_counts(), divisions_pl=DIVISIONS, today_iso=_today().isoformat(),
               PL_VERSION=_asset_version())
    ctx.update(extra)
    return ctx


def _vocab():
    keys, colors, aliases = StatusVocabulary.tables()
    return {"keys": keys, "colors": {k: list(v) for k, v in colors.items()}, "aliases": aliases}


def _pm_roster():
    """People who can be a PM on a status row / punch project: active PMs by PTT role, everyone who manages an open SL
    project, and anyone already referenced — roster from core_employee, never a typed list (PU-01)."""
    rows = fetch_dict("""
        SELECT e.id, e.canonical_name name, e.active, e.home_subaccount sub, e.ptt_employee_role role,
               (SELECT d.code FROM core_project p JOIN core_division d ON d.id = p.division_id
                 WHERE p.project_manager_id = e.id AND p.lifecycle_state IN ('in_progress', 'awarded_not_started', 'field_complete', 'dormant')
                 GROUP BY d.code ORDER BY COUNT(*) DESC LIMIT 1) top_div,
               (SELECT COUNT(*) FROM core_project p WHERE p.project_manager_id = e.id AND p.lifecycle_state IN ('in_progress', 'awarded_not_started')) n_open
        FROM core_employee e
        WHERE e.canonical_name <> '' AND (
              (e.active AND e.ptt_employee_role IN ('pm', 'head_pm'))
           OR e.id IN (SELECT pm_id FROM planning_statusrow WHERE pm_id IS NOT NULL)
           OR e.id IN (SELECT pm2_id FROM planning_statusrow WHERE pm2_id IS NOT NULL)
           OR e.id IN (SELECT pm_id FROM planning_punchproject WHERE pm_id IS NOT NULL)
           OR EXISTS (SELECT 1 FROM core_project p WHERE p.project_manager_id = e.id AND p.lifecycle_state IN ('in_progress', 'awarded_not_started')))
        ORDER BY e.canonical_name""")
    seen, out = set(), []
    for r in rows:
        if r["name"] in seen:      # duplicate employee rows (PTT + SL): keep the first (alphabetical id order is stable enough)
            continue
        seen.add(r["name"])
        div = r["top_div"] or ((r["sub"] or "")[:3] if (r["sub"] or "").startswith(("040", "070", "080")) else "")
        out.append({"id": r["id"], "name": r["name"], "div": div, "active": bool(r["active"]), "n_open": r["n_open"] or 0})
    return out


def _resolve_employee(name, roster=None):
    """Employee pk for a typed / chosen PM name: exact canonical name, else the bids alias rules (surname etc.)."""
    name = (name or "").strip()
    if not name:
        return None
    e = Employee.objects.filter(canonical_name__iexact=name).order_by("-active", "id").first()
    if e is not None:
        return e.id
    try:
        from apps.bids.loaders import _employee_pool
        from apps.bids.rules import alias_candidates
        cands = alias_candidates(name, _employee_pool())
        if cands and cands[0][1] >= 0.8:
            return Employee.objects.filter(canonical_name=cands[0][0]).values_list("id", flat=True).first()
    except Exception:  # noqa
        pass
    return None


def _resolve_project(number):
    """(project pk, canonical number) for a typed job number, else (None, digits)."""
    known = set(Project.objects.filter(canonical_project_number__in=rules.project_candidates(number)).values_list("canonical_project_number", flat=True))
    canon = rules.resolve_project(number, known)
    if canon:
        return Project.objects.filter(canonical_project_number=canon).values_list("id", flat=True).first(), canon
    return None, (number or "").strip()[:32]


def serialize_status_row(r, en=None, planner=None):
    d = {
        "id": r.id, "division": r.division, "name": r.name, "project_number": r.project_number_raw, "job_key": r.job_key,
        "project_id": r.project_id, "task_num": r.task_num,
        "phase_status": r.phase_status, "equipment_status": r.equipment_status,
        "pm_id": r.pm_id, "pm": r.pm.canonical_name if r.pm else "", "pm_raw": r.pm_raw,
        "pm2_id": r.pm2_id, "pm2": r.pm2.canonical_name if r.pm2 else "", "pm2_raw": r.pm2_raw,
        "engineer": r.engineer, "foreman": r.foreman, "start": _iso(r.start), "end": _iso(r.end),
        "hours_left": float(r.hours_left or 0), "percent": float(r.percent or 0), "notes": r.notes,
        "site_contact": r.site_contact, "assigned_to": r.assigned_to, "priority_raw": r.priority_raw,
        "completed": r.completed, "completed_at": _iso(r.completed_at), "source": r.source,
        "last_update_date": _iso(r.last_update_date), "updated_at": _iso(r.updated_at),
        "updated_by": _user_name(r.updated_by), "version": r.version,
        "sent_to_scheduler_at": _iso(r.sent_to_scheduler_at), "plan_project_id": r.plan_project_id,
        "planner": (planner or {}).get(r.project.canonical_project_number) if (planner and r.project_id) else None,
        "enrich": (en or {}).get(r.project_id) if r.project_id else None,
    }
    d["pm_label"] = d["pm"] or d["pm_raw"]
    d["pm2_label"] = d["pm2"] or d["pm2_raw"]
    return d


def status_dataset(rows=None):
    """Everything the status board / active tasks pages need, as one JSON-able dict (full data, no row caps)."""
    qs = rows if rows is not None else StatusRow.objects.all()
    rows = list(qs.select_related("pm", "pm2", "project", "updated_by").order_by("division", "completed", "task_num", "id"))
    en = enrich.enrich_projects([r.project_id for r in rows])
    planner = api.planner_labels({r.project.canonical_project_number for r in rows if r.project_id})
    data = [serialize_status_row(r, en, planner) for r in rows]
    divisions = sorted({r.division for r in rows} | set(DIVISIONS))
    return {"rows": data, "vocab": _vocab(), "pms": _pm_roster(), "divisions": divisions, "today": _today().isoformat(),
            "planner_available": bool(planner), "closed_in_sl": sorted(enrich.closed_in_sl([r.project_id for r in rows]))}


# ------------------------------------------------------------------------------------------------ Today (6.0)
def today(request):
    today = _today()
    soon = today + timedelta(days=rules.DUE_SOON_DAYS)
    active = StatusRow.objects.filter(completed=False, percent__lt=100)
    open_items = PunchItem.objects.filter(date_completed__isnull=True)
    cards = []
    for div in DIVISIONS:
        a = active.filter(division=div)
        oi = open_items.filter(punch_project__division=div)
        bids_due = None
        try:
            from django.db import transaction
            from apps.bids.models import Bid
            with transaction.atomic():      # a bids schema still mid-phase must never sink Today
                bids_due = Bid.objects.filter(division=div, bid_due__gte=today, bid_due__lt=today + timedelta(days=7)).exclude(stage__in=["lost", "did_not_bid", "awarded"]).count()
        except Exception:  # noqa
            bids_due = None
        cards.append({
            "div": div,
            "active": a.count(),
            "past_stop": a.filter(end__lt=today).count(),
            "due_soon": a.filter(end__gte=today, end__lte=soon).count(),
            "needs_schedule": a.filter(Q(phase_status="NEED TO SCHEDULE") | Q(equipment_status="NEED TO SCHEDULE")).count(),
            "missing_stop": a.filter(end__isnull=True).count(),
            "overdue_punch": oi.filter(due_by__lt=today).count(),
            "critical_punch": oi.filter(critical__in=[4, 5]).count(),
            "pending_approvals": ApprovalRequest.objects.filter(status__in=["pending", "reopened"], project__division__code=div).count(),
            "bids_due": bids_due,
        })
    unlinked_pending = ApprovalRequest.objects.filter(status__in=["pending", "reopened"], project__isnull=True).count()
    overdue_needed = [a for a in ApprovalRequest.objects.filter(status__in=["pending", "reopened"], needed_by__lt=today)]
    queue = None
    if getattr(request, "acc", None) is not None and request.acc.planning_approve:
        queue = {"n": ApprovalRequest.objects.filter(status__in=["pending", "reopened"]).count(), "overdue": len(overdue_needed)}
    recent = [{"at": h.changed_at, "by": _user_name(h.changed_by) or "—", "row": h.row, "field": h.field, "old": h.old, "new": h.new}
              for h in StatusHistory.objects.select_related("row", "changed_by").order_by("-changed_at")[:12]]
    return render(request, "planning/today.html", _pctx(request, "today", cards=cards, queue=queue, recent=recent,
                                                        unlinked_pending=unlinked_pending, soon=soon))


# ------------------------------------------------------------------------------------------------ Status board (6.1)
def status_board(request):
    ds = status_dataset()
    can_write = bool(getattr(request, "acc", None) and request.acc.planning_write)
    return render(request, "planning/status.html", _pctx(request, "status", dataset=ds, can_write=can_write, who=_who(request)))


def status_json(request):
    if request.GET.get("history"):          # one row's change history (drawer › History)
        row = get_object_or_404(StatusRow, pk=request.GET["history"])
        hist = [{"at": _iso(h.changed_at), "by": _user_name(h.changed_by), "field": h.field, "old": h.old, "new": h.new}
                for h in row.history.select_related("changed_by")[:200]]
        return JsonResponse({"row": row.id, "history": hist})
    qs = StatusRow.objects.all()
    div = request.GET.get("div")
    if div and div != "ALL":
        qs = qs.filter(division=div)
    view = request.GET.get("view")
    if view == "active":
        qs = qs.filter(completed=False)
    elif view == "completed":
        qs = qs.filter(completed=True)
    return JsonResponse(status_dataset(qs))


EDITABLE = {"name", "division", "phase_status", "equipment_status", "pm", "pm2", "engineer", "foreman", "start", "end",
            "hours_left", "percent", "notes", "site_contact", "assigned_to", "task_num", "project_number"}


def _apply_field(row, field, value, aliases):
    """Set one editable field from a JSON value; returns [(field, old, new)] history tuples (a PM edit may write two)."""
    hist = []

    def setf(name, new):
        old = getattr(row, name)
        if old != new:
            setattr(row, name, new)
            hist.append((name, loaders._s(old), loaders._s(new)))

    if field == "name":
        v = (value or "").strip()
        if v:
            setf("name", v[:300])
    elif field == "division":
        v = rules.division_from(value) or (value or "").strip()[:8]
        if v:
            setf("division", v)
    elif field in ("phase_status", "equipment_status"):
        setf(field, rules.canonical_status(value, aliases)[:64])
    elif field in ("pm", "pm2"):
        name = (value or "").strip()
        emp_id = _resolve_employee(name)
        emp_name = Employee.objects.filter(pk=emp_id).values_list("canonical_name", flat=True).first() if emp_id else ""
        setf(field + "_raw", (emp_name or name)[:120])
        setf(field + "_id", emp_id)
    elif field in ("engineer", "foreman", "site_contact", "assigned_to"):
        setf(field, (value or "").strip()[:300 if field in ("site_contact", "assigned_to") else 120])
    elif field in ("start", "end"):
        setf(field, rules.parse_date_cell(value))
    elif field == "hours_left":
        setf("hours_left", _dec(value, Decimal(0)))
    elif field == "percent":
        p = rules.parse_pct(value)
        setf("percent", Decimal(str(round(p if p is not None else 0, 1))))
    elif field == "notes":
        setf("notes", value or "")
    elif field == "task_num":
        try:
            setf("task_num", int(value) if value not in (None, "") else None)
        except (TypeError, ValueError):
            pass
    elif field == "project_number":
        pid, canon = _resolve_project(value)
        setf("project_number_raw", canon)
        setf("project_id", pid)
    return hist


def _conflict(row):
    return _bad("changed by %s at %s — reload" % (_user_name(row.updated_by) or "someone", timezone.localtime(row.updated_at).strftime("%H:%M")),
                status=409, row=serialize_status_row(row, enrich.enrich_projects([row.project_id]) if row.project_id else None))


@require_POST
def status_edit(request):
    """Row-level edits (PS-04): {op: update|create|duplicate|delete|complete, id, version, field, value, fields{}}."""
    b = _body(request)
    op = b.get("op") or ("update" if b.get("field") else "")
    user = _user(request)
    aliases = _vocab()["aliases"]
    today = _today()
    if op == "create":
        fields = b.get("fields") or {}
        row = StatusRow(division=rules.division_from(fields.get("division")) or "070", name="New task",
                        phase_status="NEED TO SCHEDULE", hours_left=Decimal(8), source=Source.MANUAL, created_by=user, updated_by=user,
                        last_update_date=today)
        hist = []
        for f, v in fields.items():
            if f in EDITABLE:
                hist += _apply_field(row, f, v, aliases)
        row.save()
        StatusHistory.objects.bulk_create([StatusHistory(row=row, changed_by=user, field="created", old="", new=_who(request))] +
                                          [StatusHistory(row=row, changed_by=user, field=f, old=o, new=n) for f, o, n in hist])
        audit("write_action", request, meta_op="status_create", meta_row=row.id)
        return JsonResponse({"ok": True, "row": serialize_status_row(row, enrich.enrich_projects([row.project_id]) if row.project_id else None)})

    row = StatusRow.objects.select_related("pm", "pm2", "project", "updated_by").filter(pk=b.get("id")).first()
    if row is None:
        return _bad("row not found", 404)
    try:
        version = int(b.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version and version != row.version:
        return _conflict(row)

    if op == "duplicate":
        dup = StatusRow.objects.get(pk=row.pk)
        dup.pk = None
        dup.name = (row.name + " copy")[:300]
        dup.completed, dup.completed_at, dup.source = False, None, Source.MANUAL
        dup.sent_to_scheduler_at, dup.plan_project_id, dup.planner_task_id, dup.source_key = None, None, "", ""
        dup.created_by = dup.updated_by = user
        dup.version, dup.last_update_date = 1, today
        dup.save()
        StatusHistory.objects.create(row=dup, changed_by=user, field="duplicated_from", old="", new=str(row.pk))
        audit("write_action", request, meta_op="status_duplicate", meta_row=dup.id, meta_from=row.id)
        return JsonResponse({"ok": True, "row": serialize_status_row(dup, enrich.enrich_projects([dup.project_id]) if dup.project_id else None)})
    if op == "delete":
        audit("write_action", request, meta_op="status_delete", meta_row=row.id, meta_name=row.name[:120])
        row.delete()
        return JsonResponse({"ok": True, "deleted": b.get("id")})

    hist = []
    if op == "complete":
        want = bool(b.get("completed")) if "completed" in b else (not row.completed)
        if want != row.completed:
            hist.append(("completed", str(row.completed), str(want)))
            row.completed = want
            row.completed_at = timezone.now() if want else None
    elif op == "update":
        updates = b.get("fields") or ({b.get("field"): b.get("value")} if b.get("field") else {})
        for f, v in updates.items():
            if f not in EDITABLE:
                return _bad("field %r is not editable" % f)
            hist += _apply_field(row, f, v, aliases)
    else:
        return _bad("unknown op %r" % op)
    if hist:
        row.updated_by = user
        row.last_update_date = today
        row.version += 1
        row.save()
        StatusHistory.objects.bulk_create([StatusHistory(row=row, changed_by=user, field=f, old=o, new=n) for f, o, n in hist])
        audit("write_action", request, meta_op="status_" + op, meta_row=row.id, meta_fields=[h[0] for h in hist])
    row = StatusRow.objects.select_related("pm", "pm2", "project", "updated_by").get(pk=row.pk)
    return JsonResponse({"ok": True, "row": serialize_status_row(row, enrich.enrich_projects([row.project_id]) if row.project_id else None),
                         "changed": [h[0] for h in hist]})


@require_POST
def status_send(request):
    b = _body(request)
    row = StatusRow.objects.select_related("pm").filter(pk=b.get("id")).first()
    if row is None:
        return _bad("row not found", 404)
    ok, msg, payload = api.send_to_scheduler(row, request)
    audit("write_action", request, meta_op="status_send", meta_row=row.id, meta_ok=ok)
    row.refresh_from_db()
    return JsonResponse({"ok": ok, "message": msg, "payload": payload, "row": serialize_status_row(row)}, status=200 if ok else 409)


# ------------------------------------------------------------------------------------------------ transition import (PS-06)
IMPORT_DIR = "planning_imports"


def status_import(request):
    """Admin transition tool: upload a Master Schedule export (or point at the P: envelopes), see the mapping and the
    dry-run report, then apply. Never automatic (AGENT_BRIEF: with a dry-run report, never automatically)."""
    ctx = _pctx(request, "status", report=None, mappings=None, token=None, applied=False, err="")
    if request.method != "POST":
        return render(request, "planning/status_import.html", ctx)
    user = _user(request)
    folder = Path(settings.APP_SUPPORT_DIR) / IMPORT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    mode = request.POST.get("mode") or "dry"
    token = request.POST.get("token") or ""
    kind = request.POST.get("kind") or "excel"
    path = None
    try:
        if request.FILES.get("file"):
            f = request.FILES["file"]
            suffix = Path(f.name).suffix.lower() or ".xlsx"
            token = uuid.uuid4().hex
            path = folder / (token + suffix)
            with open(path, "wb") as fh:
                for chunk in f.chunks():
                    fh.write(chunk)
            (folder / (token + ".name")).write_text(f.name)
        elif token:
            cands = [p for p in folder.glob(token + ".*") if p.suffix != ".name"]
            path = cands[0] if cands else None
        if kind == "dir":
            d = request.POST.get("dir") or ""
            if not d or not Path(d).is_dir():
                raise ValueError("Give the directory that holds project_status_data.json / 0x0_punch_list_data.json / bom_labor_approval_data.json.")
            reps = loaders.import_envelope_dir(d, apply=(mode == "apply"), user=user)
            ctx["report"] = "\n\n".join(r.text() for r in reps)
            ctx["dir"] = d
        else:
            if path is None or not path.exists():
                raise ValueError("Choose a Master Schedule .xlsx / .xls / .csv export first.")
            rows, notes, mappings = loaders.workbook_to_rows(path)
            label = "Master Schedule %s" % ((folder / (token + ".name")).read_text() if (folder / (token + ".name")).exists() else path.name)
            rep = loaders.import_status_rows(rows, label, apply=(mode == "apply"), user=user, honor_completed=False)
            rep.sheets = notes
            ctx["report"], ctx["mappings"], ctx["token"] = rep.text(), mappings, token
        ctx["applied"] = mode == "apply"
        if ctx["applied"]:
            audit("write_action", request, meta_op="status_import", meta_kind=kind)
    except Exception as e:  # noqa
        ctx["err"] = str(e)
    return render(request, "planning/status_import.html", ctx)
