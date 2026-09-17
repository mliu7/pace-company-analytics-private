"""Resource Scheduler services: models ⇄ maths dicts, the board payload, live PTT / SL enrichment (RS-15) and every
edit action of the `planning_schedule_edit` endpoint (SharePoint spec §2 "PCA-owned planning state": row-level edits with
actor, optimistic `version`, an AuditEvent per write). All maths come from `maths.py` — nothing is computed twice."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Employee, Project
from apps.operations.models import RemainingHoursRevision, TimeEntry

from . import maths as M
from .models import (DEFAULT_DAYS, TRADE_NONUNION, TRADE_UNION, TRADES, Assignment, PhaseRange, PlanPhase, PlanProject, Resource,
                     ResourcePto)

TRADE_VALUES = [t[0] for t in TRADES]


class EditError(Exception):
    def __init__(self, message, reason="", status=400):
        super().__init__(message)
        self.message, self.reason, self.status = message, reason, status


# ---------------------------------------------------------------- helpers
def today():
    return timezone.localdate()


def iso(d):
    return d.isoformat() if d else None


def hms(t):
    return t.strftime("%H:%M") if t else ""


def f(v):
    return float(v) if v is not None else 0.0


def _date(v, field="date"):
    if v in (None, ""):
        return None
    try:
        return M.parse_date(v)
    except Exception:
        raise EditError("%s is not a valid date (%r)." % (field, v))


def _time(v, default):
    if not v:
        return default
    try:
        hh, mm = str(v).split(":")[:2]
        return time(int(hh), int(mm))
    except Exception:
        raise EditError("Not a valid time: %r" % v)


def _hours(v, field="hours", lo=0, hi=24):
    try:
        h = float(v)
    except (TypeError, ValueError):
        raise EditError("%s must be a number." % field)
    if h < lo or h > hi:
        raise EditError("%s must be between %s and %s." % (field, lo, hi))
    return h


def account_of(request):
    acc = getattr(request, "acc", None)
    return getattr(acc, "account", None)


def pm_colour(pm_id, pm_raw):
    key = pm_id if pm_id is not None else (pm_raw or "")
    if not key and key != 0:
        return "#5a6480"
    return M.PM_PALETTE[hash(str(key)) % len(M.PM_PALETTE)] if isinstance(key, str) else M.PM_PALETTE[int(key) % len(M.PM_PALETTE)]


def pm_label(p):
    if p.pm_id and getattr(p, "pm", None):
        return p.pm.canonical_name.split()[-1].upper() if p.pm.canonical_name else p.pm_raw
    return p.pm_raw or ""


# ---------------------------------------------------------------- models → dicts
def resource_dict(r, pto_rows):
    return {"id": r.id, "name": r.display_name, "sort_name": r.sort_name or r.display_name, "trade": r.trade, "max_weekly": r.max_weekly,
            "ot": r.approved_ot, "pto": [(x.start, x.end) for x in pto_rows], "divisions": list(r.divisions or []),
            "employee_id": r.employee_id, "active": r.active, "version": r.version, "source": r.source}


def phase_dict(ph):
    return {"id": ph.id, "project_id": ph.plan_project_id, "name": ph.name, "trade": ph.trade, "start": ph.start, "end": ph.end,
            "weekend": ph.weekend, "hours": f(ph.hours), "dates": [M.parse_date(d) for d in (ph.dates or [])], "order": ph.order,
            "is_short": ph.is_short}


def project_dict(p, phases):
    ce = None
    if p.completed_early and p.completed_early.get("date"):
        ce = dict(p.completed_early)
        ce["date"] = M.parse_date(ce["date"])
    return {"id": p.id, "name": p.name, "start": p.start, "end": p.end, "days": list(p.days or DEFAULT_DAYS), "hours_per_day": p.hours_per_day or 8,
            "hours_union": f(p.hours_union), "hours_nonunion": f(p.hours_nonunion), "phases": phases, "completed_early": ce,
            "division": p.division or "", "pm_id": p.pm_id, "pm": pm_label(p), "pm_raw": p.pm_raw, "colour": p.colour or M.PALETTE[p.id % len(M.PALETTE)],
            "access_start": hms(p.access_start), "access_end": hms(p.access_end), "short": p.short_project, "source": p.source,
            "unscheduled": p.unscheduled, "project_fk": p.project_id, "status_row_id": p.status_row_id, "version": p.version, "notes": p.notes}


def assignment_dict(a, projects_by_id, phases_by_id):
    ph = phases_by_id.get(a.phase_id)
    return {"id": a.id, "resource_id": a.resource_id, "project_id": a.plan_project_id, "date": a.date, "hours": f(a.hours), "phase_id": a.phase_id,
            "phase_name": ph["name"] if ph else None, "note": a.note, "project_name": (projects_by_id.get(a.plan_project_id) or {}).get("name", "?")}


def load_state():
    """Everything the maths need, as dicts. Projects: all plan projects (the set is small; the page filters). Resources:
    active only. Assignments: all rows of those projects (phase progress needs every date, not just the week)."""
    pto = defaultdict(list)
    for x in ResourcePto.objects.all().order_by("start"):
        pto[x.resource_id].append(x)
    resources = [resource_dict(r, pto[r.id]) for r in Resource.objects.filter(active=True)]
    phases_by_project, phases_by_id = defaultdict(list), {}
    for ph in PlanPhase.objects.all().order_by("plan_project_id", "order", "id"):
        d = phase_dict(ph)
        phases_by_project[ph.plan_project_id].append(d)
        phases_by_id[ph.id] = d
    projects = [project_dict(p, phases_by_project[p.id]) for p in PlanProject.objects.select_related("pm").all()]
    projects_by_id = {p["id"]: p for p in projects}
    assignments = [assignment_dict(a, projects_by_id, phases_by_id) for a in Assignment.objects.all()]
    return {"resources": resources, "resources_by_id": {r["id"]: r for r in resources}, "projects": projects, "projects_by_id": projects_by_id,
            "phases_by_id": phases_by_id, "assignments": assignments}


# ---------------------------------------------------------------- enrichment (RS-15)
def enrichment(state, days):
    """PTT hours worked per person per job (lifetime and this week), SL labor budget / PTT remaining per project, and the
    week's planned-vs-worked adherence rows. Only resources with an employee and plan projects with a project FK take part."""
    emp_to_res = {r["employee_id"]: r["id"] for r in state["resources"] if r.get("employee_id")}
    proj_to_plan = defaultdict(list)
    for p in state["projects"]:
        if p.get("project_fk"):
            proj_to_plan[p["project_fk"]].append(p["id"])
    out = {"worked": {}, "worked_week": {}, "projects": {}, "adherence": []}
    if not proj_to_plan:
        return out
    base = TimeEntry.objects.filter(project_id__in=list(proj_to_plan), source_status=1, form_type=1, employee_id__in=list(emp_to_res) or [-1])
    life = base.values("employee_id", "project_id").annotate(h=Sum("hours_total"))
    for row in life:
        rid = emp_to_res.get(row["employee_id"])
        for pid in proj_to_plan[row["project_id"]]:
            out["worked"].setdefault(str(rid), {})[str(pid)] = f(row["h"])
    week = base.filter(work_date__gte=days[0], work_date__lte=days[6]).values("employee_id", "project_id", "work_date").annotate(h=Sum("hours_total"))
    worked_week = defaultdict(float)
    for row in week:
        rid = emp_to_res.get(row["employee_id"])
        for pid in proj_to_plan[row["project_id"]]:
            worked_week[(rid, pid)] += f(row["h"])
            out["worked_week"].setdefault(str(rid), {}).setdefault(str(pid), {})[iso(row["work_date"])] = f(row["h"])
    planned = defaultdict(float)
    for a in state["assignments"]:
        if a["date"] and days[0] <= a["date"] <= days[6]:
            planned[(a["resource_id"], a["project_id"])] += a["hours"]
    # adherence compares only pairs that can be compared: the person has a PTT link and the project an SL link
    comparable_projects = {pid for pids in proj_to_plan.values() for pid in pids}
    comparable_res = set(emp_to_res.values())
    planned = {k: v for k, v in planned.items() if k[0] in comparable_res and k[1] in comparable_projects}
    names = {}
    for k in set(planned) | set(worked_week):
        names[k] = ((state["resources_by_id"].get(k[0]) or {}).get("name", "?"), (state["projects_by_id"].get(k[1]) or {}).get("name", "?"))
    out["adherence"] = [{**r, "resource_id": r["resource_id"], "project_id": r["project_id"]} for r in M.adherence_rows(planned, dict(worked_week), names)]
    # project-level SL / PTT numbers
    projs = {p.id: p for p in Project.objects.filter(id__in=list(proj_to_plan)).select_related("division")}
    remaining = {r.project_id: r for r in RemainingHoursRevision.objects.filter(project_id__in=list(proj_to_plan), is_current=True)}
    for proj_id, plan_ids in proj_to_plan.items():
        pr = projs.get(proj_id)
        if not pr:
            continue
        rem = remaining.get(proj_id)
        info = {"number": pr.display_number, "title": pr.title, "division": pr.division.code if pr.division_id else "", "lifecycle": pr.lifecycle_state,
                "budget_hours": f(pr.budget_labor_hours) if pr.budget_labor_hours is not None else None,
                "ptt_hours": f(pr.ptt_hours_total) if pr.ptt_hours_total is not None else None,
                "remaining": f(rem.remaining_hours_total) if rem and rem.remaining_hours_total is not None else (f(pr.pm_remaining_hours) if pr.pm_remaining_hours is not None else None),
                "remaining_union": f(rem.remaining_hours_union) if rem and rem.remaining_hours_union is not None else None,
                "remaining_nonunion": f(rem.remaining_hours_non_union) if rem and rem.remaining_hours_non_union is not None else None,
                "remaining_at": rem.revised_at.isoformat() if rem and rem.revised_at else None, "url": "/projects/%s/" % pr.canonical_project_number}
        for pid in plan_ids:
            out["projects"][str(pid)] = info
    return out


# ---------------------------------------------------------------- the board payload
def board_payload(monday, state=None, today_=None):
    state = state or load_state()
    today_ = today_ or today()
    days = M.week_days(monday)
    R, P, A = state["resources"], state["projects"], state["assignments"]
    rs_by = state["resources_by_id"]
    shown = [d for d in days if M.show_weekend_day(d, [a for a in A if a["date"] and days[0] <= a["date"] <= days[6]], P)]
    week_a = [a for a in A if a["date"] and days[0] <= a["date"] <= days[6]]
    a_by_res_day = defaultdict(list)
    for a in week_a:
        a_by_res_day[(a["resource_id"], a["date"])].append(a)
    a_by_proj = defaultdict(list)
    for a in A:
        a_by_proj[a["project_id"]].append(a)

    # projects lane + per-project facts (needs, cards)
    lane, cards, needs = [], [], []
    for p in sorted(P, key=lambda x: _natural(x["name"])):
        ce, shift = p["completed_early"], p["hours_per_day"]
        blocks = {}
        for d in shown:
            active = [ph for ph in p["phases"] if M.phase_covers(ph, d, ce)]
            if not active:
                continue
            assigned = sum(a["hours"] for a in a_by_proj[p["id"]] if a["date"] == d)
            tags = []
            for ph in active:
                rem = M.phase_remaining(ph, A, shift, ce)
                got_all = M.phase_hours_assigned(ph, A)
                got_day = sum(a["hours"] for a in a_by_proj[p["id"]] if a["date"] == d and a["phase_id"] == ph["id"])
                state_ = "ok" if (got_all + 1e-9 >= ph["hours"] and ph["hours"] > 0) or d in rem["completed"] else ("part" if got_day > 0 else "none")
                tags.append({"id": ph["id"], "name": ph["name"], "colour": M.phase_colour(ph["name"]), "state": state_, "got": got_day,
                             "planned": M.phase_schedule(ph, shift, ce).get(d, {}).get("hours", 0)})
            blocks[iso(d)] = {"assigned": assigned, "tags": tags}
        if blocks:
            lane.append({"id": p["id"], "name": p["name"], "division": p["division"], "pm": p["pm"], "pm_colour": pm_colour(p["pm_id"], p["pm_raw"]),
                         "div_colour": M.division_colour(p["division"]), "access": "%s–%s" % (M.fmt_time(p["access_start"]), M.fmt_time(p["access_end"])),
                         "completed_early": bool(ce), "blocks": blocks})
        gn = M.project_got_need(p, A, rs_by)
        need = M.staffing_need(p, A)
        has_dates = bool(p["start"] and p["end"]) and not p["unscheduled"]
        phase_dates = any(M.phase_days(ph, ce) for ph in p["phases"])
        chips = []
        for ph in p["phases"]:
            pd = M.phase_days(ph, ce)
            chips.append({"id": ph["id"], "name": ph["name"], "trade": ph["trade"], "hours": ph["hours"], "colour": M.phase_colour(ph["name"]),
                          "start": iso(pd[0]) if pd else iso(ph["start"]), "end": iso(pd[-1]) if pd else iso(ph["end"]), "days": len(pd),
                          "staffed": M.phase_staffed(ph, A, shift, ce), "got": M.phase_hours_assigned(ph, A), "plan": M.phase_plan_text(ph, A, shift, ce),
                          "weekend": ph["weekend"], "is_short": ph["is_short"]})
        cards.append({"id": p["id"], "name": p["name"], "division": p["division"], "pm": p["pm"], "pm_id": p["pm_id"], "pm_colour": pm_colour(p["pm_id"], p["pm_raw"]),
                      "div_colour": M.division_colour(p["division"]), "colour": p["colour"], "start": iso(p["start"]), "end": iso(p["end"]), "days": p["days"],
                      "hours_per_day": shift, "hours_union": p["hours_union"], "hours_nonunion": p["hours_nonunion"], "short": p["short"],
                      "access": "%s–%s" % (M.fmt_time(p["access_start"]), M.fmt_time(p["access_end"])), "unscheduled": p["unscheduled"], "source": p["source"],
                      "completed_early": _ce_out(ce), "got_need": gn, "phases": chips, "fully_staffed": need is None and bool(p["phases"]),
                      "met_days": [iso(d) for d in M.allocation_met_days(p, A)],
                      "needs_dates": not has_dates or not phase_dates, "project_fk": p["project_fk"], "status_row_id": p["status_row_id"],
                      "assigned": [{"id": a["id"], "resource_id": a["resource_id"], "date": iso(a["date"]), "hours": a["hours"], "phase_id": a["phase_id"],
                                    "phase_name": a["phase_name"], "note": a["note"]} for a in sorted(a_by_proj[p["id"]], key=lambda a: (a["date"] or date.min, a["resource_id"]))]})
        if need is not None or not has_dates:
            first_date = min([pd[0] for ph in p["phases"] for pd in [M.phase_days(ph, ce)] if pd] or [p["start"] or today_])
            item = {"id": p["id"], "name": p["name"], "division": p["division"], "colour": p["colour"], "kind": "dates" if (not has_dates or not phase_dates) else "resources",
                    "got_need": gn, "first_date": iso(first_date), "trades": {}}
            if need:
                for key in ("union", "nonunion"):
                    g = need.get(key)
                    if g is None:
                        continue
                    item["trades"][key] = {"no_data": g.get("no_data", False), "need": g.get("need", 0), "assigned": g.get("assigned", 0), "done": g.get("done", False),
                                           "rows": [{**r, "days_need": [{"date": iso(x["date"]), "text": x["text"], "hours": x["hours"]} for x in r["days_need"]]}
                                                    for r in g.get("rows", [])]}
            needs.append(item)

    rows = []
    for r in sorted(R, key=lambda x: x["sort_name"].lower()):
        util = M.resource_week(r, A, days)
        cells = {}
        for d in shown:
            items = a_by_res_day.get((r["id"], d), [])
            by_proj = defaultdict(list)
            for a in items:
                by_proj[a["project_id"]].append(a)
            chips = []
            for pid, arr in by_proj.items():
                p = state["projects_by_id"].get(pid) or {}
                chips.append({"project_id": pid, "name": p.get("name", "?"), "hours": sum(a["hours"] for a in arr), "note": any(a["note"] for a in arr),
                              "pm_colour": pm_colour(p.get("pm_id"), p.get("pm_raw")), "div_colour": M.division_colour(p.get("division", "")),
                              "division": p.get("division", ""), "access": "%s–%s" % (M.fmt_time(p.get("access_start")), M.fmt_time(p.get("access_end"))),
                              "completed_early": bool(p.get("completed_early")),
                              "items": [{"id": a["id"], "phase_id": a["phase_id"], "phase": a["phase_name"], "hours": a["hours"], "note": a["note"],
                                         "colour": M.phase_colour(a["phase_name"]) if a["phase_name"] else "#5a6480"} for a in arr]})
            cells[iso(d)] = {"pto": M.on_pto(r, d), "chips": chips, "room": M.room(r, A, d), "used": sum(a["hours"] for a in items)}
        rows.append({"id": r["id"], "name": r["name"], "trade": r["trade"], "divisions": r["divisions"], "ot": r["ot"], "max_weekly": r["max_weekly"],
                     "employee_id": r["employee_id"], "util": util, "cells": cells, "pto": [[iso(s), iso(e)] for s, e in r["pto"]], "version": r["version"],
                     "source": r["source"]})

    u, n = M.week_tally(R, A, days, True), M.week_tally(R, A, days, False)
    enr = enrichment(state, days)
    lo, hi = M.gantt_window([p for p in P if p["start"] and p["end"]], today_)
    pms = sorted({(p["pm_id"], p["pm"], p["pm_raw"]) for p in P if p["pm"] or p["pm_raw"]}, key=lambda x: x[1] or x[2])
    divisions = sorted({p["division"] for p in P if p["division"]} | {d for r in R for d in r["divisions"]})
    return {
        "week": {"monday": iso(days[0]), "label": M.week_label(days[0]), "prev": iso(days[0] - timedelta(days=7)), "next": iso(days[0] + timedelta(days=7)),
                 "today": iso(today_), "this_monday": iso(M.monday_of(today_)),
                 "days": [{"date": iso(d), "dow": d.strftime("%a"), "day": d.day, "weekend": d.weekday() >= 5, "free": M.day_free(R, A, d, days)} for d in shown]},
        "summary": {"union": u, "nonunion": n, "shifts": len(week_a), "hours": sum(a["hours"] for a in week_a), "resources": len(R), "projects": len(P)},
        "lane": lane, "rows": rows, "footer": {iso(d): M.daily_load(R, A, d) for d in shown}, "needs": needs, "cards": cards,
        "gantt": {"start": iso(lo), "end": iso(hi), "px_per_day": 9,
                  "bars": [{"id": p["id"], "name": p["name"], "start": iso(p["start"]), "end": iso(p["end"]), "division": p["division"], "pm": p["pm"],
                            "pm_colour": pm_colour(p["pm_id"], p["pm_raw"]), "div_colour": M.division_colour(p["division"]), "days": (p["end"] - p["start"]).days + 1,
                            "completed_early": bool(p["completed_early"])} for p in sorted(P, key=lambda x: _natural(x["name"])) if p["start"] and p["end"]]},
        "enrich": enr, "pms": [{"id": i, "label": lbl or raw, "raw": raw} for i, lbl, raw in pms], "divisions": divisions,
        "phases": M.PHASES, "phase_type": M.PHASE_TYPE, "phase_colours": M.PHASE_COLORS, "trades": TRADE_VALUES,
    }


def _ce_out(ce):
    if not ce:
        return None
    return {**ce, "date": iso(ce["date"])}


def _natural(s):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]


# ---------------------------------------------------------------- audit + versions
def audit(request, action, **meta):
    try:
        from apps.access.audit import log
        log("write_action", request, action=action, **meta)
    except Exception:   # audit must never sink an edit
        pass


def _check_version(obj, payload):
    v = payload.get("version")
    if v is None:
        return
    try:
        v = int(v)
    except (TypeError, ValueError):
        return
    if v != obj.version:
        who = obj.updated_by.display_name if getattr(obj, "updated_by", None) else "someone else"
        at = timezone.localtime(obj.updated_at).strftime("%H:%M") if getattr(obj, "updated_at", None) else "?"
        raise EditError("Changed by %s at %s — reload to see the latest version." % (who, at), reason="version", status=409)


def _bump(obj, actor):
    obj.version = (obj.version or 1) + 1
    obj.updated_by = actor


# ---------------------------------------------------------------- resources
def resource_save(payload, actor):
    name = (payload.get("display_name") or payload.get("name") or "").strip()
    if not name:
        raise EditError("A name is required.")
    trade = payload.get("trade") or TRADE_UNION
    if trade not in TRADE_VALUES:
        raise EditError("Unknown role %r." % trade)
    max_weekly = int(_hours(payload.get("max_weekly", 40), "Max hrs / week", 0, 168))
    divisions = [str(d) for d in (payload.get("divisions") or []) if d]
    if trade == TRADE_NONUNION:
        divisions = []                                   # non-union people are never division-scoped
    pto = []
    for rng in payload.get("pto") or []:
        s, e = _date(rng.get("start"), "PTO start"), _date(rng.get("end") or rng.get("start"), "PTO end")
        if s and e:
            pto.append((min(s, e), max(s, e)))
    pto.sort()
    with transaction.atomic():
        if payload.get("id"):
            r = Resource.objects.select_for_update().filter(pk=payload["id"]).first()
            if not r:
                raise EditError("Resource not found.", status=404)
            _check_version(r, payload)
            _bump(r, actor)
        else:
            r = Resource(created_by=actor, updated_by=actor, source="manual")
            emp_id = payload.get("employee_id")
            if emp_id:
                if Resource.objects.filter(employee_id=emp_id).exists():
                    raise EditError("That employee is already on the roster.")
                r.employee_id = emp_id
        r.display_name, r.trade, r.max_weekly, r.divisions, r.approved_ot = name, trade, max_weekly, divisions, bool(payload.get("approved_ot", payload.get("ot", False)))
        r.sort_name = payload.get("sort_name") or sort_name_of(name)
        r.active = bool(payload.get("active", True))
        r.save()
        r.pto.all().delete()
        ResourcePto.objects.bulk_create([ResourcePto(resource=r, start=s, end=e) for s, e in pto])
    return r


def resource_ot(payload, actor):
    r = Resource.objects.filter(pk=payload.get("id")).first()
    if not r:
        raise EditError("Resource not found.", status=404)
    r.approved_ot = bool(payload.get("ot"))
    _bump(r, actor)
    r.save(update_fields=["approved_ot", "version", "updated_by", "updated_at"])
    return r


def resource_delete(payload, actor):
    r = Resource.objects.filter(pk=payload.get("id")).first()
    if not r:
        raise EditError("Resource not found.", status=404)
    n = r.assignments.count()
    r.delete()                                           # cascades PTO + assignments ("Remove X and all their assignments?")
    return n


def sort_name_of(name):
    """'Mike Pinebrook' → 'Pinebrook, Mike'; 'Pinebrook, Mike' stays."""
    name = (name or "").strip()
    if "," in name:
        return name
    parts = name.split()
    if len(parts) < 2:
        return name
    suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
    last = parts[-1]
    if last.lower() in suffixes and len(parts) > 2:
        return "%s %s, %s" % (parts[-2], last, " ".join(parts[:-2]))
    return "%s, %s" % (last, " ".join(parts[:-1]))


# ---------------------------------------------------------------- projects
def _clamp(d, lo, hi):
    if d is None:
        return None
    if lo and d < lo:
        d = lo
    if hi and d > hi:
        d = hi
    return d


def _range_days(s, e, weekend):
    return [d for d in M.daterange(s, e) if M.phase_covers({"start": s, "end": e, "weekend": weekend, "dates": []}, d)]


def build_phase_specs(payload, start, end):
    """Turn the editor's phase rows into phase specs, exactly as the dashboard's save did: dates clamped to the project
    window; multi-range phases get explicit dates[] + ranges; Test is split per trade; a short project becomes synthetic
    Union / Non-Union blocks only when the respective total > 0."""
    specs = []
    if payload.get("short_project"):
        for trade, key, total in ((TRADE_UNION, "short_union", payload.get("hours_union")), (TRADE_NONUNION, "short_nonunion", payload.get("hours_nonunion"))):
            if f(total) <= 0:
                continue
            ranges = _ranges(payload.get(key) or {}, start, end, with_hours=False)
            if not ranges:
                continue
            dates = sorted({d for r in ranges for d in _range_days(r["start"], r["end"], r["weekend"])})
            specs.append({"name": trade, "trade": trade, "start": min(r["start"] for r in ranges), "end": max(r["end"] for r in ranges),
                          "weekend": ranges[0]["weekend"], "hours": f(total), "dates": dates, "ranges": ranges, "is_short": True, "order": len(specs)})
        return specs
    for row in payload.get("phases") or []:
        name = row.get("name")
        if name not in M.PHASES or not row.get("enabled", True):
            continue
        ranges = _ranges(row, start, end, with_hours=True)
        if not ranges:
            continue
        by_trade = defaultdict(list)
        for r in ranges:
            by_trade[(r.get("trade") if name == "Test" else None) or M.phase_trade(name)].append(r)
        for trade, rs in by_trade.items():
            hours = sum(f(r.get("hours")) for r in rs) or f(row.get("hours"))
            spec = {"name": name, "trade": trade, "start": min(r["start"] for r in rs), "end": max(r["end"] for r in rs), "weekend": rs[0]["weekend"],
                    "hours": hours, "dates": [], "ranges": rs, "is_short": False, "order": len(specs)}
            if len(rs) > 1:
                spec["dates"] = sorted({d for r in rs for d in _range_days(r["start"], r["end"], r["weekend"])})
            specs.append(spec)
    return specs


def _ranges(row, start, end, with_hours):
    out = []
    raw = row.get("ranges")
    if raw is None:
        raw = [{"start": row.get("start"), "end": row.get("end"), "weekend": row.get("weekend"), "hours": row.get("hours"), "trade": row.get("trade")}]
    for i, r in enumerate(raw):
        s, e = _date(r.get("start"), "Phase start"), _date(r.get("end") or r.get("start"), "Phase end")
        if not s or not e:
            continue
        if e < s:
            s, e = e, s
        s, e = _clamp(s, start, end), _clamp(e, start, end)
        if s is None or e is None or e < s:
            continue
        wk = r.get("weekend") or "none"
        if wk not in M.WEEKEND_RULES:
            wk = "none"
        out.append({"start": s, "end": e, "weekend": wk, "hours": _hours(r.get("hours") or 0, "Phase hours", 0, 100000) if with_hours else None,
                    "trade": r.get("trade"), "order": i})
    return out


def project_save(payload, actor):
    name = (payload.get("name") or "").strip()
    if not name:
        raise EditError("A project name is required.")
    start, end = _date(payload.get("start"), "Earliest on-site date"), _date(payload.get("end"), "Latest allowed date")
    if start and end and end < start:
        start, end = end, start
    days = payload.get("days") or DEFAULT_DAYS
    if len(days) != 7:
        raise EditError("days must have 7 entries.")
    days = [1 if x else 0 for x in days]
    specs = build_phase_specs(payload, start, end)
    with transaction.atomic():
        if payload.get("id"):
            p = PlanProject.objects.select_for_update().filter(pk=payload["id"]).first()
            if not p:
                raise EditError("Project not found.", status=404)
            _check_version(p, payload)
            _bump(p, actor)
        else:
            p = PlanProject(created_by=actor, updated_by=actor, source=payload.get("source") or "manual")
        p.name = name
        if "project_id" in payload:
            p.project_id = payload.get("project_id") or None
        if "project_number" in payload:
            p.project_id = resolve_project(payload.get("project_number"))
        p.pm_id = payload.get("pm_id") or None
        p.pm_raw = (payload.get("pm_raw") or "").strip()
        if p.pm_id and not p.pm_raw:
            emp = Employee.objects.filter(pk=p.pm_id).first()
            p.pm_raw = emp.canonical_name.split()[-1].upper() if emp else ""
        p.division = (payload.get("division") or "").strip()
        p.start, p.end = start, end
        p.access_start, p.access_end = _time(payload.get("access_start"), time(7, 0)), _time(payload.get("access_end"), time(15, 0))
        p.days, p.hours_per_day = days, 8
        p.hours_union, p.hours_nonunion = Decimal(str(_hours(payload.get("hours_union") or 0, "Total Union hours", 0, 100000))), Decimal(str(_hours(payload.get("hours_nonunion") or 0, "Total Non-Union hours", 0, 100000)))
        if payload.get("colour"):
            p.colour = payload["colour"]
        elif not p.colour:
            p.colour = M.PALETTE[(PlanProject.objects.count()) % len(M.PALETTE)]
        p.short_project = bool(payload.get("short_project"))
        p.notes = payload.get("notes", p.notes) or ""
        if p.source == "Project Status":              # a manual edit takes the project out of planner management
            p.unscheduled = not (start and end)
        else:
            p.unscheduled = False
        p.save()
        sync_phases(p, specs)
    return p


def sync_phases(p, specs):
    """Match existing phases by (name, trade) so assignments keep their phase; drop the rest."""
    existing = {(ph.name, ph.trade): ph for ph in p.phases.all()}
    keep = set()
    for spec in specs:
        ph = existing.get((spec["name"], spec["trade"]))
        if ph is None:
            ph = PlanPhase(plan_project=p, name=spec["name"], trade=spec["trade"])
        ph.start, ph.end, ph.weekend, ph.hours = spec["start"], spec["end"], spec["weekend"], Decimal(str(spec["hours"]))
        ph.dates, ph.order, ph.is_short = [iso(d) for d in spec["dates"]], spec["order"], spec["is_short"]
        ph.save()
        keep.add(ph.id)
        ph.ranges.all().delete()
        PhaseRange.objects.bulk_create([PhaseRange(phase=ph, start=r["start"], end=r["end"], weekend=r["weekend"],
                                                   hours=None if r.get("hours") is None else Decimal(str(r["hours"])), order=i) for i, r in enumerate(spec["ranges"])])
    p.phases.exclude(id__in=keep).delete()


def resolve_project(number):
    """The 000000 rule (CLAUDE.md): '260078' matches core_project 260078 or 260078000000 (a real job over a template)."""
    number = (number or "").strip().upper()
    if not number:
        return None
    cands = list(Project.objects.filter(canonical_project_number__in=[number, number + "000000"]))
    if not cands:
        return None
    real = [c for c in cands if not c.is_template_or_void and c.lifecycle_state not in ("template",)]
    pool = real or cands
    exact = [c for c in pool if c.canonical_project_number == number]
    return (exact or pool)[0].id


def project_delete(payload, actor):
    p = PlanProject.objects.filter(pk=payload.get("id")).first()
    if not p:
        raise EditError("Project not found.", status=404)
    n = p.assignments.count()
    p.delete()
    return n


def project_editor_payload(p):
    """Everything the editor needs for one project, including per-range rows."""
    phases = []
    for ph in p.phases.all().order_by("order", "id"):
        phases.append({"id": ph.id, "name": ph.name, "trade": ph.trade, "start": iso(ph.start), "end": iso(ph.end), "weekend": ph.weekend, "hours": f(ph.hours),
                       "dates": list(ph.dates or []), "is_short": ph.is_short,
                       "ranges": [{"start": iso(r.start), "end": iso(r.end), "weekend": r.weekend, "hours": f(r.hours) if r.hours is not None else None} for r in ph.ranges.all()]})
    return {"id": p.id, "name": p.name, "project_number": p.project.display_number if p.project_id else "", "pm_id": p.pm_id, "pm_raw": p.pm_raw,
            "division": p.division, "start": iso(p.start), "end": iso(p.end), "access_start": hms(p.access_start), "access_end": hms(p.access_end),
            "days": list(p.days or DEFAULT_DAYS), "hours_union": f(p.hours_union), "hours_nonunion": f(p.hours_nonunion), "colour": p.colour,
            "short_project": p.short_project, "source": p.source, "unscheduled": p.unscheduled, "notes": p.notes, "version": p.version,
            "completed_early": p.completed_early, "phases": phases}


# ---------------------------------------------------------------- assignments (the ONE capacity rule)
def _resource_and_state(resource_id):
    state = load_state()
    r = state["resources_by_id"].get(int(resource_id)) if resource_id is not None else None
    if not r:
        raise EditError("Resource not found.", status=404)
    return r, state


def assignment_save(payload, actor):
    """Create / update one assignment (person-day modal, project modal Assign, hour edits). hours 0 deletes."""
    existing = Assignment.objects.filter(pk=payload["id"]).first() if payload.get("id") else None
    if payload.get("id") and not existing:
        raise EditError("Assignment not found.", status=404)
    d = _date(payload.get("date")) or (existing.date if existing else None)
    if not d:
        raise EditError("A date is required.")
    hours = _hours(payload.get("hours"), "Hours", 0, 24)
    if hours * 2 != int(hours * 2):
        raise EditError("Hours go in half-hour steps.")
    if hours == 0:
        if existing:
            existing.delete()
        return None
    resource_id = payload.get("resource_id") or (existing.resource_id if existing else None)
    project_id = payload.get("project_id") or (existing.plan_project_id if existing else None)
    r, state = _resource_and_state(resource_id)
    p = state["projects_by_id"].get(int(project_id)) if project_id else None
    if not p:
        raise EditError("Project not found.", status=404)
    phase_id = payload.get("phase_id")
    phase = None
    if phase_id not in (None, "", "null"):
        phase = state["phases_by_id"].get(int(phase_id))
        if not phase or phase["project_id"] != p["id"]:
            raise EditError("That phase does not belong to this project.")
    ok, reason, msg = M.capacity_check(r, state["assignments"], d, hours, exclude_ids=[existing.id] if existing else ())
    if not ok and not payload.get("force_ot"):
        raise EditError(msg, reason=reason)
    if existing:
        a = existing
        a.updated_by = actor
    else:
        a = Assignment(created_by=actor, updated_by=actor)
    a.resource_id, a.plan_project_id, a.date, a.hours, a.phase_id, a.note = r["id"], p["id"], d, Decimal(str(hours)), phase["id"] if phase else None, (payload.get("note") or "")[:200]
    a.save()
    return a


def assignment_delete(payload, actor):
    a = Assignment.objects.filter(pk=payload.get("id")).first()
    if not a:
        raise EditError("Assignment not found.", status=404)
    a.delete()


def assignment_move(payload, actor):
    """Drag-and-drop of a chip to another day (RS-05 'new'): every assignment of that person on that project-day moves;
    the capacity rule is checked at the target and the phase must cover the target day."""
    d = _date(payload.get("date"))
    resource_id, project_id, src = payload.get("resource_id"), payload.get("project_id"), _date(payload.get("from"))
    if not (d and resource_id and project_id and src):
        raise EditError("resource, project, from and date are required.")
    if d == src:
        return 0
    r, state = _resource_and_state(resource_id)
    p = state["projects_by_id"].get(int(project_id))
    if not p:
        raise EditError("Project not found.", status=404)
    items = list(Assignment.objects.filter(resource_id=r["id"], plan_project_id=p["id"], date=src))
    if not items:
        raise EditError("Nothing to move.")
    total = sum(f(a.hours) for a in items)
    for a in items:
        ph = state["phases_by_id"].get(a.phase_id) if a.phase_id else None
        if ph and not M.phase_covers(ph, d, p["completed_early"]):
            raise EditError("%s does not cover %s on this project." % (ph["name"], M.short_date(d)), reason="phase")
    if Assignment.objects.filter(resource_id=r["id"], plan_project_id=p["id"], date=d).exists():
        raise EditError("%s is already on this project on %s." % (r["name"], M.short_date(d)), reason="dup")
    ok, reason, msg = M.capacity_check(r, state["assignments"], d, total, exclude_ids=[a.id for a in items])
    if not ok:
        raise EditError(msg, reason=reason)
    for a in items:
        a.date, a.updated_by = d, actor
        a.save(update_fields=["date", "updated_by", "updated_at"])
    return len(items)


def phase_panel(payload, actor):
    """Per-person phase panel: replaces ALL of the person's assignments on this project-day with the ticked phases."""
    d = _date(payload.get("date"))
    r, state = _resource_and_state(payload.get("resource_id"))
    p = state["projects_by_id"].get(int(payload.get("project_id") or 0))
    if not (d and p):
        raise EditError("Project and date are required.")
    items = []
    for it in payload.get("items") or []:
        h = _hours(it.get("hours") or 0, "Hours", 0, 24)
        if h <= 0:
            continue
        ph = state["phases_by_id"].get(int(it.get("phase_id")))
        if not ph or ph["project_id"] != p["id"]:
            raise EditError("Unknown phase.")
        items.append((ph, h))
    current = list(Assignment.objects.filter(resource_id=r["id"], plan_project_id=p["id"], date=d))
    total = sum(h for _, h in items)
    if total > 0:
        ok, reason, msg = M.capacity_check(r, state["assignments"], d, total, exclude_ids=[a.id for a in current])
        if not ok:
            raise EditError(msg, reason=reason)
    with transaction.atomic():
        for a in current:
            a.delete()
        for ph, h in items:
            Assignment.objects.create(resource_id=r["id"], plan_project_id=p["id"], date=d, hours=Decimal(str(h)), phase_id=ph["id"], created_by=actor, updated_by=actor)
    return len(items)


def copy_preview(payload):
    state = load_state()
    p = state["projects_by_id"].get(int(payload.get("project_id") or 0))
    src = _date(payload.get("source_date"))
    targets = [x for x in (_date(t) for t in payload.get("dates") or []) if x]
    phase_id = int(payload["phase_id"]) if payload.get("phase_id") not in (None, "") else None
    if not (p and src and targets):
        raise EditError("Project, source day and target days are required.")
    source = [a for a in state["assignments"] if a["project_id"] == p["id"] and a["date"] == src and a["phase_id"] == phase_id]
    conflicts = M.copy_conflicts(p["id"], phase_id, source, targets, state["assignments"], state["resources_by_id"])
    return {"source": [{"resource_id": a["resource_id"], "name": state["resources_by_id"][a["resource_id"]]["name"], "hours": a["hours"]} for a in source],
            "conflicts": [{**c, "date": iso(c["date"])} for c in conflicts], "targets": [iso(t) for t in targets]}


def copy_apply(payload, actor):
    """Copy the source day's people (for that phase) to the selected days, replacing what was there and skipping conflicts."""
    prev = copy_preview(payload)
    state = load_state()
    p = state["projects_by_id"][int(payload["project_id"])]
    src = _date(payload["source_date"])
    phase_id = int(payload["phase_id"]) if payload.get("phase_id") not in (None, "") else None
    skip = {(c["resource_id"], c["date"]) for c in prev["conflicts"]}
    source = list(Assignment.objects.filter(plan_project_id=p["id"], date=src, phase_id=phase_id))
    copied = 0
    with transaction.atomic():
        for t in (M.parse_date(x) for x in prev["targets"]):
            if t == src:
                continue
            Assignment.objects.filter(plan_project_id=p["id"], date=t, phase_id=phase_id).exclude(resource_id__in=[c["resource_id"] for c in prev["conflicts"] if c["date"] == iso(t)]).delete()
            for sa in source:
                if (sa.resource_id, iso(t)) in skip:
                    continue
                Assignment.objects.create(resource_id=sa.resource_id, plan_project_id=p["id"], date=t, hours=sa.hours, phase_id=phase_id, note=sa.note, created_by=actor, updated_by=actor)
                copied += 1
    return {"copied": copied, "skipped": len(prev["conflicts"])}


# ---------------------------------------------------------------- completed early (RS-11)
def ce_preview(payload):
    state = load_state()
    p = state["projects_by_id"].get(int(payload.get("project_id") or 0))
    d = _date(payload.get("date"))
    if not (p and d):
        raise EditError("Project and date are required.")
    scope = payload.get("scope") or "all"
    r = M.completed_early_affected(p, state["assignments"], d, scope, state["resources_by_id"])
    return {"count": r["count"], "hours": r["hours"], "people": r["people"], "options": M.scope_options(p, state["assignments"], state["resources_by_id"])}


def completed_early(payload, actor):
    state = load_state()
    p = state["projects_by_id"].get(int(payload.get("project_id") or 0))
    d = _date(payload.get("date"))
    if not (p and d):
        raise EditError("Project and date are required.")
    if p["start"] and d < p["start"]:
        raise EditError("The completion date can't be before the project's start.")
    scope = payload.get("scope") or "all"
    opts = [o["value"] for o in M.scope_options(p, state["assignments"], state["resources_by_id"])]
    if len(opts) > 1 and scope not in opts:
        raise EditError("Choose which part was completed.")
    r = M.completed_early_affected(p, state["assignments"], d, scope, state["resources_by_id"])
    with transaction.atomic():
        obj = PlanProject.objects.select_for_update().get(pk=p["id"])
        Assignment.objects.filter(id__in=r["ids"]).delete()
        obj.completed_early = {"date": iso(d), "scope": scope, "at": timezone.now().isoformat(), "hours": r["hours"], "count": r["count"],
                               "by": actor.display_name if actor else ""}
        _bump(obj, actor)
        obj.save()
    return r


def completed_early_clear(payload, actor):
    obj = PlanProject.objects.filter(pk=payload.get("project_id")).first()
    if not obj:
        raise EditError("Project not found.", status=404)
    obj.completed_early = None
    _bump(obj, actor)
    obj.save()


# ---------------------------------------------------------------- assistant (RS-12)
def assist(payload):
    state = load_state()
    exclude = int(payload["project_id"]) if payload.get("project_id") else None
    groups = []
    for g in payload.get("groups") or []:
        dates = sorted({x for x in (_date(d) for d in g.get("dates") or []) if x})
        for rng in g.get("ranges") or []:                    # the editor sends every date range of the phase row
            s, e = _date(rng.get("start")), _date(rng.get("end") or rng.get("start"))
            if s and e:
                if e < s:
                    s, e = e, s
                dates = sorted(set(dates) | set(_range_days(s, e, rng.get("weekend") or "none")))
        if not dates and g.get("start") and g.get("end"):
            s, e = _date(g["start"]), _date(g["end"])
            if s and e:
                if e < s:
                    s, e = e, s
                dates = _range_days(s, e, g.get("weekend") or "none")
        groups.append({"key": g.get("key"), "label": g.get("label") or g.get("key"), "trade": g.get("trade") or "Union", "hours": f(g.get("hours")), "dates": dates})
    out = M.assistant(groups, state["resources"], state["assignments"], exclude)
    for g in out["groups"]:
        for c in g["chips"]:
            c["date"] = iso(c["date"])
    return out


# ---------------------------------------------------------------- lookups for the editors
def project_search(q, limit=12):
    q = (q or "").strip()
    if not q:
        return []
    qs = Project.objects.filter(is_template_or_void=False).exclude(lifecycle_state="template").select_related("division", "project_manager")
    if q.isdigit():
        qs = qs.filter(canonical_project_number__startswith=q)
    else:
        qs = qs.filter(title__icontains=q)
    out = []
    for pr in qs.order_by("-canonical_project_number")[:limit]:
        out.append({"id": pr.id, "number": pr.display_number, "canonical": pr.canonical_project_number, "title": pr.title, "division": pr.division.code if pr.division_id else "",
                    "pm_id": pr.project_manager_id, "pm": pr.project_manager.canonical_name if pr.project_manager_id else "", "lifecycle": pr.lifecycle_state,
                    "budget_hours": f(pr.budget_labor_hours) if pr.budget_labor_hours is not None else None,
                    "remaining": f(pr.pm_remaining_hours) if pr.pm_remaining_hours is not None else None,
                    "start": iso(pr.sl_planned_start), "end": iso(pr.sl_planned_end)})
    return out


def employee_search(q, limit=12):
    q = (q or "").strip()
    if not q:
        return []
    taken = set(Resource.objects.exclude(employee_id=None).values_list("employee_id", flat=True))
    out = []
    for e in Employee.objects.filter(canonical_name__icontains=q, active=True).order_by("canonical_name")[:limit * 2]:
        out.append({"id": e.id, "name": e.canonical_name, "key": e.employee_key, "trade": TRADE_UNION if employee_is_union(e) else TRADE_NONUNION,
                    "division": home_division(e), "on_roster": e.id in taken, "role": e.ptt_employee_role, "classification": e.classification})
        if len(out) >= limit:
            break
    return out


def pm_choices():
    ids = set(PlanProject.objects.exclude(pm_id=None).values_list("pm_id", flat=True)) | set(Project.objects.filter(lifecycle_state__in=["in_progress", "awarded_not_started"]).exclude(project_manager_id=None).values_list("project_manager_id", flat=True))
    return [{"id": e.id, "name": e.canonical_name, "label": e.canonical_name.split()[-1].upper()} for e in Employee.objects.filter(id__in=ids).order_by("canonical_name")]


# ---------------------------------------------------------------- roster seed from core_employee (RS-01)
IBEW_CLASSES = ("journeyman", "apprentice", "foreman", "general_foreman")


def employee_is_union(e):
    return bool(e.union_code) or e.ptt_employee_type == "union" or (e.classification_code in IBEW_CLASSES) or ("ibew" in (e.classification or "").lower())


def home_division(e):
    sub = (e.home_subaccount or "").strip()
    return sub[:3] if len(sub) >= 3 and sub[:3] != "000" else ""


def roster_candidates():
    """Field-hourly people plus PMs / head PMs and non-union technicians (engineers / programmers) who are active in PTT."""
    qs = Employee.objects.filter(active=True).order_by("canonical_name")
    out = []
    for e in qs:
        field = e.is_field_hourly
        pm = e.ptt_employee_role in ("pm", "head_pm") and e.ptt_active is not False
        tech = e.ptt_employee_type == "non_union" and e.classification_code == "tech" and e.ptt_active is True
        if field or pm or tech:
            out.append(e)
    return out


def seed_resources(apply=False):
    """Create a Resource for every candidate employee not yet on the roster. Returns the report rows."""
    existing = set(Resource.objects.exclude(employee_id=None).values_list("employee_id", flat=True))
    rows = []
    for e in roster_candidates():
        if e.id in existing:
            continue
        trade = TRADE_UNION if employee_is_union(e) else TRADE_NONUNION
        div = home_division(e)
        rows.append({"employee_id": e.id, "key": e.employee_key, "name": e.canonical_name, "trade": trade, "division": div, "why": "field" if e.is_field_hourly else ("pm" if e.ptt_employee_role in ("pm", "head_pm") else "tech")})
        if apply:
            Resource.objects.create(employee=e, display_name=e.canonical_name, sort_name=sort_name_of(e.canonical_name), trade=trade,
                                    divisions=[div] if (div and trade != TRADE_NONUNION) else [], source="employee_seed")
    return rows
