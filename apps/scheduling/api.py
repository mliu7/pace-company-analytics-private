"""Receiver for the status board's "Send to Scheduler" (SharePoint spec §6.1 → §6.3, parity RS-14).

Phase D calls `create_plan_project(payload, user)` with the planner's payload; the dashboards had no receiver at all
(the inventory verified it), so this contract is defined here from the payload the planner posts:

    source          "Project Status"
    status_row_id   the StatusRow id (kept so a re-send updates instead of duplicating)
    project_id      core_project id (optional) — the plan project keys on it when present
    name            "260078 RUSH DAY SCHOOL …" (falls back to the SL number + title)
    division        "040" | "070" | "080" | …
    pm_id / pm_raw  core_employee id / the typed PM
    hours_union     hours left (the planner's `hoursLeft`; kind 'union' → union hours, else non-union)
    hours_per_day   8
    days            [1,1,1,1,1,0,0]
    start / end     ISO dates or "" — without both the project is `unscheduled` (Needs Dates queue)
    engineer, notes, phase_status, equipment_status   kept in `notes` for the coordinator

Returns the PlanProject. Never touches assignments that already exist on a re-send.
"""

from decimal import Decimal

from django.db import transaction

from apps.core.models import Project

from . import maths as M
from .models import DEFAULT_DAYS, PlanProject
from .services import _date, sort_name_of  # noqa: F401  (sort_name_of re-exported for Phase D convenience)


def _actor(user):
    """Accept an access.Account, a request (with .acc) or None."""
    acc = getattr(user, "acc", None)
    if acc is not None:
        return getattr(acc, "account", None)
    return user if user is not None and hasattr(user, "display_name") else None


def create_plan_project(payload, user=None):
    actor = _actor(user)
    payload = payload or {}
    project = None
    if payload.get("project_id"):
        project = Project.objects.filter(pk=payload["project_id"]).first()
    name = (payload.get("name") or "").strip()
    if not name and project:
        name = "%s %s" % (project.display_number, project.title)
    if not name:
        raise ValueError("A project name (or project_id) is required.")
    start, end = _date(payload.get("start"), "start"), _date(payload.get("end"), "end")
    if start and end and end < start:
        start, end = end, start
    kind = (payload.get("kind") or "union").lower()
    hours = float(payload.get("hours_union") or payload.get("hoursLeft") or 0)
    hours_nonunion = float(payload.get("hours_nonunion") or 0)
    if kind != "union" and not hours_nonunion:
        hours, hours_nonunion = 0.0, hours
    days = payload.get("days") or DEFAULT_DAYS
    days = [1 if x else 0 for x in days] if len(days) == 7 else list(DEFAULT_DAYS)
    note_bits = [("Engineer: %s" % payload["engineer"]) if payload.get("engineer") else "",
                 ("Phase status: %s" % payload["phase_status"]) if payload.get("phase_status") else "",
                 ("Equipment: %s" % payload["equipment_status"]) if payload.get("equipment_status") else "",
                 payload.get("notes") or ""]
    notes = "\n".join(b for b in note_bits if b)
    division = (payload.get("division") or (project.division.code if project and project.division_id else "") or "").strip()
    with transaction.atomic():
        p = None
        if payload.get("status_row_id"):
            p = PlanProject.objects.select_for_update().filter(status_row_id=payload["status_row_id"]).first()
        if p is None:
            p = PlanProject(created_by=actor, source=payload.get("source") or "Project Status", colour=M.PALETTE[PlanProject.objects.count() % len(M.PALETTE)])
        p.updated_by = actor
        p.name, p.division, p.status_row_id = name, division, payload.get("status_row_id")
        p.project = project or p.project
        p.pm_id = payload.get("pm_id") or (project.project_manager_id if project else None) or p.pm_id
        p.pm_raw = (payload.get("pm_raw") or p.pm_raw or "").strip()
        p.start, p.end = start, end
        p.days, p.hours_per_day = days, int(payload.get("hours_per_day") or 8) or 8
        p.hours_union, p.hours_nonunion = Decimal(str(hours)), Decimal(str(hours_nonunion))
        p.short_project = True                         # planner sends totals, not phases: a short project until edited
        p.unscheduled = not (start and end)
        p.notes = notes
        if p.pk:
            p.version = (p.version or 1) + 1
        p.save()
        if start and end and not p.phases.exists():   # give the coordinator a synthetic block per trade with hours
            from .services import build_phase_specs, sync_phases
            specs = build_phase_specs({"short_project": True, "hours_union": hours, "hours_nonunion": hours_nonunion,
                                       "short_union": {"ranges": [{"start": start.isoformat(), "end": end.isoformat(), "weekend": "none"}]},
                                       "short_nonunion": {"ranges": [{"start": start.isoformat(), "end": end.isoformat(), "weekend": "none"}]}}, start, end)
            sync_phases(p, specs)
    return p
