"""Production › Planner boards (SharePoint spec §9, §10): a board picker (plans grouped by kind and group), then one
plan as a kanban board / list / change log, rendered client-side from the JSON endpoint. Both URLs need
`planning.view` (access registry); the JSON carries SL figures for linked jobs only when the viewer holds projects.view."""

from datetime import timedelta

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from apps.core.models import Project
from apps.dashboard.views import _ctx
from apps.ingestion.models import IngestionRun

from . import rules
from .models import PlannerPlan, PlannerTask, PlannerTaskHistory, PlannerUser

AV_BOARD_ID = "av"
SINCE_CHOICES = (1, 7, 14, 30, 90, 365)


def _plan_row(p):
    return {"id": p.plan_id, "title": p.title, "group": p.group.name, "kind": p.board_kind, "kind_auto": p.board_kind_auto,
            "manual": p.board_kind_manual, "av_stage": p.av_stage, "av_label": rules.AV_STAGE_LABEL.get(p.av_stage, ""),
            "tasks": p.task_count, "open": p.open_count, "overdue": p.overdue_count,
            "last_change": p.last_change.isoformat() if p.last_change else None,
            "last_synced": p.last_synced.isoformat() if p.last_synced else None,
            "url": rules.plan_url(p.plan_id, p.group.tenant_domain or "pace-systems.com"), "active": p.active}


def _picker():
    plans = [_plan_row(p) for p in PlannerPlan.objects.filter(active=True).select_related("group")]
    av = sorted([p for p in plans if p["av_stage"]], key=lambda p: (rules.AV_STAGE_INDEX.get(p["av_stage"], 99), -p["tasks"], p["title"]))
    groups = []
    for kind in rules.BOARD_KINDS:
        rows = sorted([p for p in plans if p["kind"] == kind], key=lambda p: (p["group"].lower(), p["title"].lower()))
        if rows:
            groups.append({"kind": kind, "label": rules.BOARD_KIND_LABEL[kind], "help": rules.BOARD_KIND_HELP[kind], "plans": rows,
                           "open": sum(p["open"] for p in rows), "tasks": sum(p["tasks"] for p in rows)})
    stages = {p["av_stage"] for p in av}
    return {"kinds": groups, "av": av if len(stages) >= 2 else [], "av_stages": len(stages), "av_total": len(rules.AV_SEQUENCE), "n_plans": len(plans)}


def _av_plans():
    """The plans behind the combined AV board, in sequence order. A stage with several plans keeps every plan that
    has tasks; an empty duplicate of a stage that already has a plan is left out (an empty stage still shows once)."""
    plans = sorted(PlannerPlan.objects.filter(active=True).exclude(av_stage="").select_related("group"),
                   key=lambda p: (rules.AV_STAGE_INDEX.get(p.av_stage, 99), -p.task_count, p.title))
    out, seen = [], set()
    for p in plans:
        if p.task_count == 0 and p.av_stage in seen:
            continue
        out.append(p); seen.add(p.av_stage)
    return out


def _last_run():
    return IngestionRun.objects.filter(source_system="planner").order_by("-created_at").first()


def planner_page(request):
    picker = _picker()
    run = _last_run()
    connected = picker["n_plans"] > 0
    ctx = _ctx(request, "production", picker=picker, connected=connected, last_run=run,
               plan_param=(request.GET.get("plan") or "").strip(), since_choices=SINCE_CHOICES,
               kind_labels=rules.BOARD_KIND_LABEL, av_stage_labels=[l for _, l, _ in rules.AV_SEQUENCE])
    return render(request, "planner/board.html", ctx)


# ------------------------------------------------------------------------------------------- JSON
def _project_figures(ids, show_money):
    if not ids:
        return {}
    out = {}
    rows = (Project.objects.filter(id__in=ids).select_related("division", "project_manager")
            .values("id", "canonical_project_number", "display_number", "title", "division__code", "lifecycle_state", "project_manager__canonical_name",
                    "contract_value", "billed_revenue", "ptt_hours_total", "budget_labor_hours", "pm_percent_complete", "customer__canonical_name"))
    for r in rows:
        d = {"cpn": r["canonical_project_number"], "number": r["display_number"], "title": r["title"], "division": r["division__code"],
             "state": r["lifecycle_state"], "pm": r["project_manager__canonical_name"] or "", "customer": r["customer__canonical_name"] or "",
             "hours": float(r["ptt_hours_total"]) if r["ptt_hours_total"] is not None else None,
             "budget_hours": float(r["budget_labor_hours"]) if r["budget_labor_hours"] is not None else None,
             "pct": float(r["pm_percent_complete"]) if r["pm_percent_complete"] is not None else None}
        if show_money:
            d["cv"] = float(r["contract_value"]) if r["contract_value"] is not None else None
            d["billed"] = float(r["billed_revenue"]) if r["billed_revenue"] is not None else None
        out[r["id"]] = d
    return out


def _task_row(t, labels_by_plan, projects, tenant, column_id=None):
    labels = labels_by_plan.get(t.plan_id) or {}
    colors = []
    for key, name in zip(t.label_keys or [], t.labels or []):
        c = labels.get(key) or dict(label=name, **rules.label_color(key))
        colors.append({"label": name, "bg": c["bg"], "fg": c["fg"], "key": key})
    return {
        "id": t.task_id, "title": t.title, "bucket_id": column_id or (t.bucket.bucket_id if t.bucket else ""), "bucket": t.bucket.name if t.bucket else "",
        "plan_id": t.plan.plan_id, "plan": t.plan.title, "group": t.plan.group.name,
        "percent": t.percent, "state": rules.state_of(t.percent, t.completed_at), "priority": t.priority, "priority_label": rules.priority_label(t.priority),
        "start": t.start.isoformat() if t.start else None, "due": t.due.isoformat() if t.due else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None, "created": t.task_created.isoformat() if t.task_created else None,
        "labels": list(t.labels or []), "label_colors": colors, "assignees": list(t.assignee_names or []), "assignee_ids": list(t.assignee_ids or []),
        "checklist_done": t.checklist_done, "checklist_total": t.checklist_total, "checklist": list(t.checklist or []),
        "has_description": t.has_description, "description": t.description, "references": list(t.references or []), "reference_count": t.reference_count,
        "details_pending": t.details_pending, "url": rules.task_url(t.task_id, tenant), "order": t.order_hint,
        "project": projects.get(t.project_id) if t.project_id else None, "match_rule": t.project_rule, "quote_ref": t.quote_ref,
        "last_change": t.last_change.isoformat() if t.last_change else None,
    }


def _me_ids(request):
    acc = getattr(request, "acc", None)
    account = getattr(acc, "account", None)
    if account is None:
        return []
    email = (account.email or "").lower()
    ids = set()
    if email:
        ids.update(PlannerUser.objects.filter(mail__iexact=email).values_list("user_id", flat=True))
        ids.update(PlannerUser.objects.filter(upn__iexact=email).values_list("user_id", flat=True))
    if account.employee_id:
        ids.update(PlannerUser.objects.filter(employee_id=account.employee_id).values_list("user_id", flat=True))
    return sorted(ids)


def _history(task_qs, since_days):
    cutoff = timezone.now() - timedelta(days=since_days)
    rows = (PlannerTaskHistory.objects.filter(task__in=task_qs, changed_at__gte=cutoff)
            .select_related("task", "task__bucket").order_by("-changed_at", "-id"))
    return [{"at": h.changed_at.isoformat(), "task_id": h.task.task_id, "task_title": h.task.title, "bucket": h.task.bucket.name if h.task.bucket else "",
             "field": h.field, "old": h.old, "new": h.new, "plan_id": h.task.plan_id} for h in rows]


def planner_json(request):
    """?plan=<plan id> -> that plan's buckets, tasks, labels and recent history; ?plan=av -> the combined AV production
    board (columns = the AV-sequence plans, in order); no plan -> the picker."""
    plan_id = (request.GET.get("plan") or "").strip()
    try:
        since = int(request.GET.get("since") or 14)
    except ValueError:
        since = 14
    since = since if since in SINCE_CHOICES else 14
    acc = getattr(request, "acc", None)
    show_money = bool(acc and acc.can("projects.view"))
    if not plan_id:
        return JsonResponse({"picker": _picker(), "now": timezone.now().isoformat()})

    if plan_id == AV_BOARD_ID:
        plans = _av_plans()
        if len({p.av_stage for p in plans}) < 2:
            raise Http404
        tenant = plans[0].group.tenant_domain or "pace-systems.com"
        meta = {"id": AV_BOARD_ID, "title": "AV production", "group": ", ".join(sorted({p.group.name for p in plans})), "kind": "workflow",
                "combined": True, "url": "", "plans": [_plan_row(p) for p in plans],
                "last_synced": max([p.last_synced for p in plans if p.last_synced] or [None]).isoformat() if any(p.last_synced for p in plans) else None}
        buckets = [{"id": p.plan_id, "name": "%s · %s" % (rules.AV_STAGE_LABEL.get(p.av_stage, p.av_stage), p.title), "plan_url": rules.plan_url(p.plan_id, tenant),
                    "order": "%02d" % i} for i, p in enumerate(plans)]
        task_qs = PlannerTask.objects.filter(plan__in=plans, active=True).select_related("plan__group", "bucket")
        labels_by_plan = {p.id: rules.plan_labels(p.category_descriptions) for p in plans}
        tasks = list(task_qs)
        projects = _project_figures({t.project_id for t in tasks if t.project_id}, show_money)
        rows = [_task_row(t, labels_by_plan, projects, tenant, column_id=t.plan.plan_id) for t in tasks]
        rows.sort(key=lambda r: (r["bucket_id"], r["bucket"], r["order"]))
    else:
        plan = PlannerPlan.objects.filter(plan_id=plan_id).select_related("group").first()
        if plan is None:
            raise Http404
        tenant = plan.group.tenant_domain or "pace-systems.com"
        meta = dict(_plan_row(plan), combined=False, labels=rules.plan_labels(plan.category_descriptions), site_path=plan.group.site_path)
        buckets = [{"id": b.bucket_id, "name": b.name, "order": b.order_hint} for b in plan.buckets.filter(active=True).order_by("order_hint")]
        task_qs = PlannerTask.objects.filter(plan=plan, active=True).select_related("plan__group", "bucket")
        tasks = list(task_qs)
        projects = _project_figures({t.project_id for t in tasks if t.project_id}, show_money)
        rows = [_task_row(t, {plan.id: meta["labels"]}, projects, tenant) for t in tasks]
        rows.sort(key=lambda r: (r["bucket"], r["order"]))
    return JsonResponse({"plan": meta, "buckets": buckets, "tasks": rows, "history": _history(task_qs, since), "since": since,
                         "me": _me_ids(request), "money": show_money, "now": timezone.now().isoformat()})
