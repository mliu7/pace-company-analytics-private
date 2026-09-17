"""Change-order / budget-change history per project (finance.ProjectChangeEvent).

SL has no change-order table (PJCO*, PJBUD* are empty; budgets are overwritten in place), but the
history is recoverable from three places, in decreasing certainty:

1. task_added — Pace records change orders as NEW SL TASKS (CO1, CO2, AVCO5, SECCO9, COR15, "Change
   Order 3" ...) or as work-order tasks under a blanket. PJPENT keeps each task's creation stamp and
   user forever, and the task's current budget rows carry the amounts. A task created after the
   project's initial setup window is a scope event: kind change_order when its id/description says so,
   else scope_added. Amounts are exact when none of the task's budget rows were edited after creation.
2. local_history — the app's hash-diffed PJPTDSUM copy (finance_projectaccountsummary keeps a new row
   whenever a budget row changes). Consecutive versions of the same (task, account) row give exact
   before -> after, stamped with SL's lupd_datetime / lupd_user / lupd_prog. Complete since 2026-08-17.
3. sl_last_edit — for in-place edits before local history began, SL's lupd stamp on the budget row
   says WHEN and WHO, but the prior value is gone (confidence unknown_prior).

`derive_events` is pure (dicts in, dicts out) so it is unit-tested; `rebuild_change_events` feeds it
from the local tables and replaces the stored rows. Nothing here reads SL directly.
"""

import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

CO_RE = re.compile(r"(^|[^A-Z])CO\s*-?#?\s*\d+|CHANGE\s*ORDER|\bC\.O\.|COR\s*\d+|^CO\d*$|^[A-Z]{1,4}CO\d+$", re.I)
INITIAL_WINDOW_DAYS = 7        # tasks created within this many days of the project are original scope
EDIT_GRACE = timedelta(days=1)  # lupd within a day of crtd = still initial entry, not an edit
CV_ACCT = "CONTRACT VALUE"
# SL programs that stamp lupd on PJPTDSUM when a PERSON edits a budget. Batch posting programs (PAPOT
# commitments, PAART/PAGLT/PACHG postings) also touch the stamp and must not read as budget edits.
BUDGET_PROGS = ("PAPRJ", "PABSM")
COST_CATEGORIES = ("labor_wage", "labor_burden", "material", "subcontract", "other_direct")
LABOR_HOUR_ACCTS = ("LABOR", "LABORUNION")
D0 = Decimal("0")


def is_change_order_task(task_id, description):
    """True when the task id or description reads like a change order (CO1, AVCO5, COR15, 'Change Order 3')."""
    tid = (task_id or "").strip().upper()
    if tid in ("00", "DEFAULT", "TM", "PREMIUM"):
        return False
    return bool(CO_RE.search(tid) or CO_RE.search(description or ""))


def _fields_for(row):
    """Which event fields a budget row feeds: (field, value) pairs."""
    out = []
    acct, cat = row["sl_acct"], row["category"]
    if acct == CV_ACCT:
        out.append(("contract_value", row["budget_amount"] or D0))
    elif cat in COST_CATEGORIES:
        out.append(("budget_direct_cost", row["budget_amount"] or D0))
        if acct in LABOR_HOUR_ACCTS:
            out.append(("budget_labor_hours", row["budget_units"] or D0))
    return out


def derive_events(project, tasks, summary_versions):
    """project: {sl_created_at}; tasks: [{task_id, description, sl_created_at, sl_created_by, sl_updated_at, sl_updated_by}];
    summary_versions: every version of every budget row for the project, any order:
        {task_id, sl_acct, category, budget_amount, budget_units, source_created_at, source_updated_at,
         source_updated_by, source_updated_prog, as_of_date, is_current}
    Returns a list of event dicts (one per field)."""
    events = []
    by_key = defaultdict(list)
    for r in summary_versions:
        by_key[(r["task_id"], r["sl_acct"])].append(r)
    for k in by_key:
        by_key[k].sort(key=lambda r: (r["as_of_date"], r["source_updated_at"] or r["as_of_date"]))
    current_by_task = defaultdict(list)
    for (task_id, acct), versions in by_key.items():
        cur = next((r for r in reversed(versions) if r["is_current"]), versions[-1])
        current_by_task[task_id].append(cur)
    proj_created = project.get("sl_created_at")
    task_created = {t["task_id"]: t for t in tasks}
    first_task = min((t["sl_created_at"] for t in tasks if t["sl_created_at"]), default=None)
    origin = proj_created or first_task
    exact_stamps = set()   # (task, acct, stamp) covered by an exact local-history event

    # ---- 1. tasks added after the initial window ----
    for t in tasks:
        created = t["sl_created_at"]
        if not created or not origin or (created - origin) <= timedelta(days=INITIAL_WINDOW_DAYS):
            continue
        rows = current_by_task.get(t["task_id"], [])
        totals = defaultdict(lambda: D0)
        edited_since = False
        for r in rows:
            for field, val in _fields_for(r):
                totals[field] += val
            if (r["source_updated_at"] and r["source_created_at"] and r["source_updated_at"] - r["source_created_at"] > EDIT_GRACE
                    and (r["source_updated_prog"] or "").upper() in BUDGET_PROGS):
                edited_since = True
        if not any(v for v in totals.values()):
            continue  # a task with no budget (work-order shell, "DO NOT USE") is not a scope change
        kind = "change_order" if is_change_order_task(t["task_id"], t["description"]) else "scope_added"
        for field, val in totals.items():
            if not val:
                continue
            events.append({"task_id": t["task_id"], "task_description": t["description"] or "", "event_at": created, "kind": kind,
                           "field": field, "prior_value": D0, "new_value": val, "delta": val, "entered_by": t["sl_created_by"] or "",
                           "source_prog": "", "source": "task_added", "confidence": "approx" if edited_since else "exact",
                           "note": "budget rows edited after the task was created — amount is the current value" if edited_since else ""})
    # ---- 2. exact before -> after from local history ----
    for (task_id, acct), versions in by_key.items():
        for prev, cur in zip(versions, versions[1:]):
            for (field, old), (_f, new) in zip(_fields_for(prev), _fields_for(cur)):
                if abs((new or D0) - (old or D0)) < Decimal("0.005"):
                    continue
                stamp = cur["source_updated_at"] or cur["as_of_date"]
                exact_stamps.add((task_id, acct, cur["source_updated_at"]))
                events.append({"task_id": task_id, "task_description": task_created.get(task_id, {}).get("description", "") or "", "event_at": stamp,
                               "kind": "budget_edited", "field": field, "prior_value": old, "new_value": new, "delta": (new or D0) - (old or D0),
                               "entered_by": cur["source_updated_by"] or "", "source_prog": cur["source_updated_prog"] or "", "source": "local_history",
                               "confidence": "exact", "note": ""})
    # ---- 3. SL's last-edit stamp on the earliest known version (prior value unknown) ----
    for (task_id, acct), versions in by_key.items():
        first = versions[0]
        upd, crt = first["source_updated_at"], first["source_created_at"]
        if not upd or not crt or upd - crt <= EDIT_GRACE:
            continue
        if (first["source_updated_prog"] or "").upper() not in BUDGET_PROGS:
            continue  # stamped by a posting program, not a budget edit
        if not any(val for _f, val in _fields_for(first)):
            continue  # row carries no budget
        if (task_id, acct, upd) in exact_stamps:
            continue
        if origin and (upd - origin) <= timedelta(days=INITIAL_WINDOW_DAYS):
            continue  # budgets entered during the project's first week are setup, not a change
        t = task_created.get(task_id)
        # an edit at the moment a later task was created is that task's own entry, not a change
        if t and t["sl_created_at"] and abs(upd - t["sl_created_at"]) <= EDIT_GRACE and origin and (t["sl_created_at"] - origin) > timedelta(days=INITIAL_WINDOW_DAYS):
            continue
        for field, val in _fields_for(first):
            events.append({"task_id": task_id, "task_description": (t or {}).get("description", "") or "", "event_at": upd, "kind": "budget_edited",
                           "field": field, "prior_value": None, "new_value": val, "delta": None, "entered_by": first["source_updated_by"] or "",
                           "source_prog": first["source_updated_prog"] or "", "source": "sl_last_edit", "confidence": "unknown_prior",
                           "note": "SL keeps only the current value: the amount before this edit is not recorded"})
    events.sort(key=lambda e: (e["event_at"], e["task_id"], e["field"]))
    return events


def summarize(events, current_cv):
    """Headline numbers for the project page."""
    co = [e for e in events if e["kind"] == "change_order" and e["field"] == "contract_value"]
    added = [e for e in events if e["kind"] == "scope_added" and e["field"] == "contract_value"]
    edits = [e for e in events if e["kind"] == "budget_edited"]
    cv_known = [e for e in events if e["field"] == "contract_value" and e["delta"] is not None]
    known_delta = sum((e["delta"] for e in cv_known), D0)
    unknown = [e for e in events if e["confidence"] == "unknown_prior" and e["field"] == "contract_value"]
    return {"change_orders": len(co), "change_orders_amount": sum((e["delta"] for e in co), D0),
            "scope_added": len(added), "scope_added_amount": sum((e["delta"] for e in added), D0),
            "edits": len({(e["event_at"], e["task_id"], e["entered_by"]) for e in edits}),
            "edits_exact": len({(e["event_at"], e["task_id"]) for e in edits if e["confidence"] == "exact"}),
            "cv_unknown_edits": len(unknown),
            "original_cv": (current_cv - known_delta) if (current_cv is not None and not unknown) else None,
            "current_cv": current_cv}


# ---------------------------------------------------------------- DB runner
def rebuild_change_events(run=None, project_ids=None):
    """Recompute ProjectChangeEvent for all projects (or the given ids) from local tables."""
    from django.db import connection, transaction
    from apps.ingestion.bulk import fetch_dict
    from apps.finance.models import ProjectChangeEvent
    where, params = ("WHERE p.id = ANY(%s)", [list(project_ids)]) if project_ids else ("", [])
    projects = {r["id"]: r for r in fetch_dict("SELECT p.id, p.sl_created_at FROM core_project p " + where, params)}
    tasks = defaultdict(list)
    for r in fetch_dict("SELECT t.project_id, t.task_id, t.description, t.sl_created_at, t.sl_created_by, t.sl_updated_at, t.sl_updated_by FROM core_projecttask t "
                        + ("WHERE t.project_id = ANY(%s)" if project_ids else ""), params):
        tasks[r["project_id"]].append(r)
    versions = defaultdict(list)
    for r in fetch_dict("""SELECT s.project_id, s.task_id, s.sl_acct, s.category, s.budget_amount, s.budget_units, s.source_created_at, s.source_updated_at,
                                  s.source_updated_by, s.source_updated_prog, s.as_of_date, s.is_current
                           FROM finance_projectaccountsummary s """ + ("WHERE s.project_id = ANY(%s)" if project_ids else ""), params):
        versions[r["project_id"]].append(r)
    rows = []
    for pid, p in projects.items():
        for e in derive_events(p, tasks.get(pid, []), versions.get(pid, [])):
            rows.append(ProjectChangeEvent(project_id=pid, task_id=e["task_id"][:32], task_description=e["task_description"][:60], event_at=e["event_at"],
                                           kind=e["kind"], field=e["field"], prior_value=e["prior_value"], new_value=e["new_value"], delta=e["delta"],
                                           entered_by=e["entered_by"][:10], source_prog=e["source_prog"][:8], source=e["source"], confidence=e["confidence"], note=e["note"][:160]))
    with transaction.atomic():
        qs = ProjectChangeEvent.objects.all() if not project_ids else ProjectChangeEvent.objects.filter(project_id__in=list(project_ids))
        qs.delete()
        ProjectChangeEvent.objects.bulk_create(rows, batch_size=2000)
    kinds = defaultdict(int)
    for r in rows:
        kinds[r.kind] += 1
    return {"projects": len(projects), "events": len(rows), **dict(kinds)}
