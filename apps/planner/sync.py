"""What other apps read from the Planner mirror (SharePoint spec §6.1 "Planner sync", §9).

Stable contract — Phase D's status board (apps.planning) and the project page call these; the keys below do not
change without a note in docs/16_planner.md.

    planner_statuses_for(project_numbers) -> {canonical_project_number: [task_status, ...]}
    open_tasks_for_project(cpn)           -> [task_status, ...]   (active, not complete)

Each task_status dict:
    plan          "Group › Plan title"                      plan_id     Planner plan id
    board_kind    'workflow' | 'job' | 'punch' | 'admin'    group       group display name
    bucket        bucket name ('' when unbucketed)          task_id     Planner task id
    task_title    the task's title                          task_url    link that opens the task in Planner
    labels        [label names] in Planner category order   label_keys  ['category3', …] (rules.label_color gives colours)
    label_colors  [{'label','name','bg','fg'}] aligned with labels
    percent       0-100 (0 not started, 100 complete)       state       'not_started' | 'in_progress' | 'complete'
    priority      0-10                                      priority_label 'Urgent' | 'Important' | 'Medium' | 'Low'
    start / due   ISO dates or None (US-Central calendar)   completed_at ISO datetime or None
    assignees     [display names]                           checklist   {'done': n, 'total': m}
    quote_ref     'YY-NNNN' or ''                            match_rule  why the task links to this project (rules.match_project)
    last_change   ISO datetime of the newest tracked change or None

Ordering: workflow boards first, then per-job, punch, admin; within a kind by plan title, then bucket, then Planner's
order hint. A project with no Planner task is simply absent from the dict (never an empty list), so callers can test
`cpn in statuses`.
"""

from .models import PlannerTask
from . import rules

KIND_ORDER = {rules.WORKFLOW: 0, rules.JOB: 1, rules.PUNCH: 2, rules.ADMIN: 3}


def _status(t):
    labels = rules.plan_labels(t.plan.category_descriptions)
    colors = []
    for key, name in zip(t.label_keys or [], t.labels or []):
        c = labels.get(key) or dict(label=name, **rules.label_color(key))
        colors.append({"label": name, "name": c["name"], "bg": c["bg"], "fg": c["fg"]})
    return {
        "plan": "%s › %s" % (t.plan.group.name, t.plan.title), "plan_id": t.plan.plan_id, "board_kind": t.plan.board_kind,
        "group": t.plan.group.name, "bucket": t.bucket.name if t.bucket else "", "task_id": t.task_id,
        "task_title": t.title, "task_url": rules.task_url(t.task_id, t.plan.group.tenant_domain or "pace-systems.com"),
        "labels": list(t.labels or []), "label_keys": list(t.label_keys or []), "label_colors": colors,
        "percent": t.percent, "state": rules.state_of(t.percent, t.completed_at),
        "priority": t.priority, "priority_label": rules.priority_label(t.priority),
        "start": t.start.isoformat() if t.start else None, "due": t.due.isoformat() if t.due else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "assignees": list(t.assignee_names or []), "checklist": {"done": t.checklist_done, "total": t.checklist_total},
        "quote_ref": t.quote_ref, "match_rule": t.project_rule,
        "last_change": t.last_change.isoformat() if t.last_change else None,
    }


def _sort_key(t):
    return (KIND_ORDER.get(t.plan.board_kind, 9), t.plan.title.lower(), t.bucket.order_hint if t.bucket else "", t.order_hint)


def planner_statuses_for(project_numbers, include_complete=True):
    """{canonical_project_number: [task_status …]} for every active Planner task linked to one of the projects."""
    keys = [k for k in (project_numbers or []) if k]
    if not keys:
        return {}
    qs = (PlannerTask.objects.filter(active=True, project__canonical_project_number__in=keys)
          .select_related("plan__group", "bucket", "project"))
    if not include_complete:
        qs = qs.filter(percent__lt=100)
    out = {}
    for t in sorted(qs, key=_sort_key):
        out.setdefault(t.project.canonical_project_number, []).append(_status(t))
    return out


def open_tasks_for_project(cpn):
    """Active, not-complete Planner tasks linked to one project (same dicts), in the same order."""
    return planner_statuses_for([cpn], include_complete=False).get(cpn, [])
