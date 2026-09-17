"""Resource Scheduler views (SharePoint spec §6.3). Access is enforced by the access middleware from
`apps/access/registry.py` (planning_schedule* → planning.view; the edit endpoint → planning.write)."""

import json

from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.dashboard.views import _ctx

from . import exports, maths as M, services as S
from .models import PlanProject


def _monday(request):
    raw = request.GET.get("week") or (request.POST.get("week") if request.method == "POST" else None)
    try:
        d = M.parse_date(raw) if raw else None
    except Exception:
        d = None
    return M.monday_of(d or S.today())


@require_GET
def schedule(request):
    monday = _monday(request)
    can_write = bool(getattr(request, "acc", None) and request.acc.can("planning.write"))
    cfg = {"urls": {"json": reverse("scheduling:planning_schedule_json"), "edit": reverse("scheduling:planning_schedule_edit"),
                    "export": reverse("scheduling:planning_schedule_export"), "page": reverse("scheduling:planning_schedule")},
           "can_write": can_write, "monday": monday.isoformat(), "today": S.today().isoformat(), "phases": M.PHASES, "phase_type": M.PHASE_TYPE,
           "phase_colours": M.PHASE_COLORS, "trades": S.TRADE_VALUES, "pm_choices": S.pm_choices(), "daily_cap": M.DAILY_CAP, "ot_daily": M.OT_DAILY}
    ctx = _ctx(request, "schedule", title="Resource Scheduler", monday=monday.isoformat(), week_label=M.week_label(monday), can_write=can_write, cfg=cfg,
               n_resources=S.Resource.objects.filter(active=True).count(), n_projects=PlanProject.objects.count())
    return render(request, "scheduling/schedule.html", ctx)


@require_GET
def schedule_json(request):
    monday = _monday(request)
    kind = request.GET.get("kind", "board")
    if kind == "project":
        p = PlanProject.objects.filter(pk=request.GET.get("id")).select_related("project").first()
        if not p:
            return JsonResponse({"ok": False, "error": "Project not found."}, status=404)
        return JsonResponse({"ok": True, "project": S.project_editor_payload(p)})
    if kind == "project_search":
        return JsonResponse({"ok": True, "results": S.project_search(request.GET.get("q", ""))})
    if kind == "employee_search":
        return JsonResponse({"ok": True, "results": S.employee_search(request.GET.get("q", ""))})
    if kind == "pms":
        return JsonResponse({"ok": True, "pms": S.pm_choices()})
    return JsonResponse({"ok": True, "board": S.board_payload(monday)})


ACTIONS = {
    "resource_save": S.resource_save, "resource_delete": S.resource_delete, "resource_ot": S.resource_ot,
    "project_save": S.project_save, "project_delete": S.project_delete,
    "assignment_save": S.assignment_save, "assignment_delete": S.assignment_delete, "assignment_move": S.assignment_move,
    "phase_panel": S.phase_panel, "copy_apply": S.copy_apply,
    "completed_early": S.completed_early, "completed_early_clear": S.completed_early_clear,
}
PREVIEWS = {"copy_preview": S.copy_preview, "ce_preview": S.ce_preview, "assist": S.assist}


@require_POST
def schedule_edit(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return HttpResponseBadRequest("invalid JSON")
    action = payload.get("action")
    actor = S.account_of(request)
    try:
        if action in PREVIEWS:
            return JsonResponse({"ok": True, **PREVIEWS[action](payload)})
        fn = ACTIONS.get(action)
        if fn is None:
            return JsonResponse({"ok": False, "error": "Unknown action %r." % action}, status=400)
        result = fn(payload, actor)
    except S.EditError as e:
        return JsonResponse({"ok": False, "error": e.message, "reason": e.reason}, status=e.status)
    S.audit(request, action, id=payload.get("id") or payload.get("project_id") or payload.get("resource_id"), week=payload.get("week"))
    out = {"ok": True}
    if hasattr(result, "pk"):
        out["id"] = result.pk
        out["version"] = getattr(result, "version", None)
    elif isinstance(result, dict):
        out.update(result)
    elif result is not None:
        out["n"] = result
    monday = M.monday_of(M.parse_date(payload.get("week")) if payload.get("week") else S.today())
    out["board"] = S.board_payload(monday)
    if action == "project_save":
        out["project"] = S.project_editor_payload(result)
    return JsonResponse(out)


@require_GET
def schedule_export(request):
    monday = _monday(request)
    kind = request.GET.get("kind", "week_csv")
    state = S.load_state()
    if kind == "xlsx":
        return exports.week_xlsx(state, monday)
    if kind == "resources_html":
        ids = [x for x in request.GET.get("resources", "").split(",") if x.strip().isdigit()]
        return exports.resources_html(state, monday, ids or None)
    return exports.week_csv(state, monday)
