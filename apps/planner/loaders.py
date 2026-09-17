"""Planner loaders (SharePoint spec §9): every unified group -> its plans -> buckets, plan details (labels), tasks;
task details only for tasks whose etag moved (capped per run); history rows on every tracked change; SL links;
board kinds; Data Quality issues. Read-only against Graph (GET only, through apps.ingestion.sources.graph_client);
writes only the apps.planner tables.

Delta: Planner has no delta query, so each refresh pulls every plan's tasks (one paged call per plan) and diffs them
by `@odata.etag` — an unchanged etag means an unchanged task and costs nothing beyond the list call.

    load_all(run)            the whole pipeline (refresh_planner command; refresh_all_step)
    refresh_all_step(run)    what refresh_all calls when the app is present
"""

import logging
import time
from collections import Counter

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.bids.rules import alias_candidates
from apps.core.models import Employee, Project
from apps.ingestion.loaders import issue
from apps.ingestion.sources import graph_client as g

from . import rules
from .models import PlannerBucket, PlannerGroup, PlannerPlan, PlannerTask, PlannerTaskHistory, PlannerUser

log = logging.getLogger(__name__)

DETAILS_CAP = 600           # task-detail calls per run (description / checklist / references); the rest wait for the next run
MATCH_MIN_CONFIDENCE = 0.85  # alias_candidates confidence below which a Planner user stays unmatched (reviewed on DQ)


def _s(v, n=None):
    s = "" if v is None else str(v).strip()
    return s[:n] if n else s


# ------------------------------------------------------------------------------------------- users
def _employee_pool():
    counts = dict(Project.objects.exclude(project_manager=None).values_list("project_manager_id").annotate(n=Count("id")).values_list("project_manager_id", "n"))
    return [{"id": e.id, "name": e.canonical_name, "active": bool(e.active), "role": e.ptt_employee_role, "n_pm": counts.get(e.id, 0)}
            for e in Employee.objects.all()]


def load_users(run):
    """/users (User.ReadBasic.All) -> PlannerUser; each matched to core_employee by display name through
    apps.bids.rules.alias_candidates (exact / name-words / fixed alias / unique surname …), kept only above 0.85."""
    users = g.users_basic()
    g.archive("planner_users", users)
    pool = _employee_pool()
    by_id = {e["id"]: e for e in pool}
    by_name = {e["name"]: e for e in pool}
    existing = {u.user_id: u for u in PlannerUser.objects.all()}
    stats = Counter(read=len(users))
    with transaction.atomic():
        for u in users:
            uid = _s(u.get("id"), 64)
            if not uid:
                continue
            name = _s(u.get("displayName"), 255)
            cands = alias_candidates(name, pool) if name else []
            emp, rule, conf = None, "", None
            if cands and cands[0][1] >= MATCH_MIN_CONFIDENCE:
                emp, conf, rule = by_name.get(cands[0][0]), cands[0][1], cands[0][2]
            row = existing.get(uid) or PlannerUser(user_id=uid)
            changed = row.pk is None
            for f, v in (("display_name", name), ("mail", _s(u.get("mail"), 255)), ("upn", _s(u.get("userPrincipalName"), 255)),
                         ("job_title", _s(u.get("jobTitle"), 255)), ("department", _s(u.get("department"), 255)),
                         ("account_enabled", bool(u.get("accountEnabled", True))), ("employee_id", emp["id"] if emp else None),
                         ("employee_rule", rule), ("employee_confidence", conf)):
                if getattr(row, f) != v:
                    setattr(row, f, v); changed = True
            if changed:
                row.save(); stats["written"] += 1
            if emp:
                stats["matched"] += 1
    stats["employees"] = len(by_id)
    return dict(stats)


# ------------------------------------------------------------------------------------------- groups + plans
def load_groups_and_plans(run):
    """Every Microsoft 365 (unified) group the app can read, and the plans each one owns. Groups / plans that a pull
    no longer lists are flagged inactive (never deleted — their history stays)."""
    groups = g.unified_groups()
    g.archive("planner_groups", groups)
    stats = Counter(groups=len(groups))
    seen_groups, seen_plans = set(), set()
    now = timezone.now()
    for grp in groups:
        gid = _s(grp.get("id"), 64)
        if not gid:
            continue
        plans = g.group_plans(gid)
        stats["plan_calls"] += 1
        row, created = PlannerGroup.objects.get_or_create(group_id=gid, defaults={"name": _s(grp.get("displayName"), 255)})
        row.name = _s(grp.get("displayName"), 255) or row.name
        row.mail = _s(grp.get("mail"), 255)
        row.description = _s(grp.get("description"))
        row.visibility = _s(grp.get("visibility"), 16)
        row.site_path = rules.site_path_from_mail(row.mail)
        row.tenant_domain = rules.tenant_domain_from_mail(row.mail)
        row.group_created = rules.to_datetime(grp.get("createdDateTime"))
        row.active = True
        row.last_seen_run = run
        row.save()
        seen_groups.add(gid)
        for p in plans:
            pid = _s(p.get("id"), 64)
            if not pid:
                continue
            plan, pcreated = PlannerPlan.objects.get_or_create(plan_id=pid, defaults={"group": row, "title": _s(p.get("title"), 255)})
            plan.group = row
            plan.title = _s(p.get("title"), 255) or plan.title
            plan.owner_id = _s(p.get("owner"), 64)
            plan.plan_created = rules.to_datetime(p.get("createdDateTime"))
            plan.etag = _s(p.get("@odata.etag"), 128)
            plan.active = True
            plan.last_seen_run = run
            plan.save()
            seen_plans.add(pid)
            stats["plans_new" if pcreated else "plans"] += 1
    stats["groups_deactivated"] = PlannerGroup.objects.filter(active=True).exclude(group_id__in=seen_groups).update(active=False)
    stats["plans_deactivated"] = PlannerPlan.objects.filter(active=True).exclude(plan_id__in=seen_plans).update(active=False)
    stats["plans_total"] = len(seen_plans)
    stats["at"] = now.isoformat()
    return dict(stats)


# ------------------------------------------------------------------------------------------- one plan
def _task_dict(t, labels, bucket_names, user_names):
    names, keys = rules.applied_labels(t.get("appliedCategories"), labels)
    aids = list((t.get("assignments") or {}).keys())
    return {
        "title": _s(t.get("title"), 512), "percent": int(t.get("percentComplete") or 0), "priority": int(t.get("priority") if t.get("priority") is not None else 5),
        "start": rules.to_central_date(t.get("startDateTime")), "due": rules.to_central_date(t.get("dueDateTime")),
        "completed": rules.to_datetime(t.get("completedDateTime")), "created": rules.to_datetime(t.get("createdDateTime")),
        "etag": _s(t.get("@odata.etag"), 128), "labels": names, "label_keys": keys,
        "assignee_ids": aids, "assignees": [user_names.get(a, "Unknown user") for a in aids],
        "checklist_total": int(t.get("checklistItemCount") or 0),
        "checklist_done": int(t.get("checklistItemCount") or 0) - int(t.get("activeChecklistItemCount") or 0),
        "has_description": bool(t.get("hasDescription")), "reference_count": int(t.get("referenceCount") or 0),
        "order_hint": _s(t.get("orderHint"), 64), "bucket_id": _s(t.get("bucketId"), 64), "bucket": bucket_names.get(_s(t.get("bucketId"), 64), ""),
        "created_by_id": _s(((t.get("createdBy") or {}).get("user") or {}).get("id"), 64),
        "completed_by_id": _s(((t.get("completedBy") or {}).get("user") or {}).get("id"), 64),
    }


def _row_dict(row):
    return {"title": row.title, "percent": row.percent, "priority": row.priority, "start": row.start, "due": row.due,
            "completed": row.completed_at, "labels": list(row.labels or []), "assignees": list(row.assignee_names or []),
            "bucket": row.bucket.name if row.bucket else ""}


def _needs_details(d):
    return d["has_description"] or d["checklist_total"] > 0 or d["reference_count"] > 0


def load_plan(plan, run, user_names, details_budget):
    """Buckets, labels and tasks of one plan; returns (stats, details_used). Tasks are diffed by etag; every tracked
    change writes PlannerTaskHistory rows; details are fetched for changed tasks that have a description / checklist /
    references while the run's budget lasts (the rest are flagged details_pending)."""
    stats = Counter()
    now = timezone.now()
    det = g.plan_details(plan.plan_id) or {}
    if det:
        g.archive("planner_plan_%s_details" % plan.plan_id, det)
    labels = rules.plan_labels(det.get("categoryDescriptions") or {})
    plan.category_descriptions = {k: v for k, v in (det.get("categoryDescriptions") or {}).items() if v}
    buckets = g.plan_buckets(plan.plan_id)
    g.archive("planner_plan_%s_buckets" % plan.plan_id, buckets)
    tasks = g.plan_tasks(plan.plan_id)
    g.archive("planner_plan_%s_tasks" % plan.plan_id, tasks)
    stats["buckets"], stats["tasks_read"] = len(buckets), len(tasks)

    with transaction.atomic():
        seen_b = set()
        bucket_rows = {b.bucket_id: b for b in plan.buckets.all()}
        for b in buckets:
            bid = _s(b.get("id"), 64)
            row = bucket_rows.get(bid) or PlannerBucket(plan=plan, bucket_id=bid)
            row.name, row.order_hint, row.etag, row.active = _s(b.get("name"), 255), _s(b.get("orderHint"), 64), _s(b.get("@odata.etag"), 128), True
            row.save(); bucket_rows[bid] = row; seen_b.add(bid)
        for bid, row in bucket_rows.items():
            if bid not in seen_b and row.active:
                row.active = False; row.save(update_fields=["active"])
        bucket_names = {bid: r.name for bid, r in bucket_rows.items()}

        existing = {t.task_id: t for t in plan.tasks.select_related("bucket")}
        seen_t, history, to_detail = set(), [], []
        for t in tasks:
            tid = _s(t.get("id"), 64)
            if not tid:
                continue
            seen_t.add(tid)
            d = _task_dict(t, labels, bucket_names, user_names)
            row = existing.get(tid)
            if row is not None and row.etag == d["etag"] and row.active:
                stats["tasks_unchanged"] += 1
                if row.details_pending and _needs_details(d):
                    to_detail.append((row, d))
                continue
            old = _row_dict(row) if row is not None else None
            if row is None:
                row = PlannerTask(plan=plan, task_id=tid, first_seen_run=run)
                stats["tasks_new"] += 1
            else:
                stats["tasks_changed" if row.active else "tasks_revived"] += 1
            row.bucket = bucket_rows.get(d["bucket_id"])
            row.title, row.percent, row.priority, row.start, row.due = d["title"], d["percent"], d["priority"], d["start"], d["due"]
            row.completed_at, row.task_created, row.etag = d["completed"], d["created"], d["etag"]
            row.labels, row.label_keys, row.assignee_ids, row.assignee_names = d["labels"], d["label_keys"], d["assignee_ids"], d["assignees"]
            row.checklist_done, row.checklist_total = d["checklist_done"], d["checklist_total"]
            row.has_description, row.reference_count, row.order_hint = d["has_description"], d["reference_count"], d["order_hint"]
            row.created_by_id, row.completed_by_id = d["created_by_id"], d["completed_by_id"]
            row.active, row.last_seen_run = True, run
            changes = rules.diff_task(old, d)
            if changes:
                row.last_change = d["created"] if old is None and d["created"] else now
            row.details_pending = _needs_details(d)
            row.save()
            for field, a, b in changes:
                when = now
                if field == "created" and d["created"]:
                    when = d["created"]
                elif field == "completed" and d["completed"] and b:
                    when = d["completed"]
                history.append(PlannerTaskHistory(task=row, changed_at=when, field=field, old=a, new=b, run=run))
            if row.details_pending:
                to_detail.append((row, d))
        # tasks Planner no longer returns: keep them (history) but mark inactive
        for tid, row in existing.items():
            if tid not in seen_t and row.active:
                row.active = False; row.last_change = now; row.save(update_fields=["active", "last_change"])
                history.append(PlannerTaskHistory(task=row, changed_at=now, field="deleted", old=row.title, new="", run=run))
                stats["tasks_removed"] += 1
        # unchanged rows still record the run that saw them (one statement)
        PlannerTask.objects.filter(plan=plan, task_id__in=seen_t).exclude(last_seen_run=run).update(last_seen_run=run)
        PlannerTaskHistory.objects.bulk_create(history)
        stats["history"] = len(history)

        # task details — bounded
        fetched = []
        for row, d in to_detail:
            if details_budget <= 0:
                stats["details_deferred"] += 1
                continue
            td = g.task_details(row.task_id)
            details_budget -= 1
            stats["details_fetched"] += 1
            if td is None:
                row.details_pending = False; row.save(update_fields=["details_pending"])
                continue
            fetched.append(td)
            cl = td.get("checklist") or {}
            items = sorted(cl.values(), key=lambda x: (x or {}).get("orderHint") or "")
            row.description = _s(td.get("description"))
            row.checklist = [{"title": _s((i or {}).get("title"), 255), "done": bool((i or {}).get("isChecked"))} for i in items]
            row.references = [{"alias": _s((r or {}).get("alias"), 255), "type": _s((r or {}).get("type"), 32), "url": k}
                              for k, r in (td.get("references") or {}).items()]
            row.details_etag = _s(td.get("@odata.etag"), 128)
            row.details_pending = False
            row.save(update_fields=["description", "checklist", "references", "details_etag", "details_pending"])
        if fetched:
            g.archive("planner_plan_%s_task_details" % plan.plan_id, fetched)

        plan.last_synced = now
        plan.save(update_fields=["category_descriptions", "last_synced"])
    return dict(stats), details_budget


# ------------------------------------------------------------------------------------------- links, kinds, counts
def link_projects(run=None):
    """rules.match_project over every active task: SL number in the title, then the bucket name, then the YY-NNNN
    quote against core_project.quote_reference. Only rows whose link changed are written."""
    known = set(Project.objects.values_list("canonical_project_number", flat=True))
    by_key = dict(Project.objects.values_list("canonical_project_number", "id"))
    quotes = {}
    for q, cpn in Project.objects.exclude(quote_reference="").values_list("quote_reference", "canonical_project_number"):
        q = rules.quote_ref_in(q)
        if q:
            quotes.setdefault(q, cpn)
    stats = Counter()
    changed = []
    for t in PlannerTask.objects.filter(active=True).select_related("bucket"):
        cpn, quote, rule = rules.match_project(t.title, t.bucket.name if t.bucket else "", known, quotes)
        pid = by_key.get(cpn) if cpn else None
        if pid:
            stats["linked"] += 1
        if quote:
            stats["with_quote"] += 1
        if t.project_id != pid or t.quote_ref != quote or t.project_rule != rule:
            t.project_id, t.quote_ref, t.project_rule = pid, quote, rule
            changed.append(t)
    PlannerTask.objects.bulk_update(changed, ["project", "quote_ref", "project_rule"], batch_size=500)
    stats["written"] = len(changed)
    stats["tasks"] = PlannerTask.objects.filter(active=True).count()
    return dict(stats)


def derive_board_kinds(run=None):
    """Board kind (unless edited by hand), AV stage and the per-plan counts the picker shows."""
    today = timezone.localdate()
    stats, av = Counter(), []
    for plan in PlannerPlan.objects.select_related("group"):
        tasks = list(plan.tasks.filter(active=True).values_list("title", "percent", "due", "last_change"))
        buckets = list(plan.buckets.filter(active=True).values_list("name", flat=True))
        kind = rules.board_kind(plan.title, plan.group.name, buckets, [t[0] for t in tasks])
        stage = rules.av_stage(plan.title, plan.group.name) or ""
        plan.board_kind_auto = kind
        if not plan.board_kind_manual:
            plan.board_kind = kind
        plan.av_stage = stage
        plan.task_count = len(tasks)
        plan.open_count = sum(1 for t in tasks if (t[1] or 0) < 100)
        plan.overdue_count = sum(1 for t in tasks if rules.is_overdue(t[2], t[1], today))
        changes = [t[3] for t in tasks if t[3]]
        plan.last_change = max(changes) if changes else None
        plan.save(update_fields=["board_kind_auto", "board_kind", "av_stage", "task_count", "open_count", "overdue_count", "last_change"])
        stats[plan.board_kind] += 1
        if stage and plan.active:
            av.append("%s › %s = %s" % (plan.group.name, plan.title, stage))
    stats["av_stage_plans"] = len(av)
    log.info("planner AV sequence plans: %s", av)
    return dict(stats, av=av)


def check_quality(run):
    """Data Quality (spec §12): workflow / punch tasks with no project number (one issue per plan with the count and a
    sample), Planner users with assignments but no employee match, and the permission audit."""
    stats = Counter()
    for plan in PlannerPlan.objects.filter(active=True, board_kind__in=(rules.WORKFLOW, rules.PUNCH)).select_related("group"):
        missing = list(plan.tasks.filter(active=True, project=None, percent__lt=100).values_list("title", flat=True))
        if missing:
            issue(run, "planner_task_no_project", "info", source_system="planner", source_key=plan.plan_id,
                  plan="%s › %s" % (plan.group.name, plan.title), count=len(missing), sample=missing[:8])
            stats["plans_with_unlinked"] += 1
    assigned = Counter()
    for ids in PlannerTask.objects.filter(active=True).values_list("assignee_ids", flat=True):
        assigned.update(ids or [])
    unmatched = [u for u in PlannerUser.objects.filter(user_id__in=list(assigned.keys()), employee=None)]
    if unmatched:
        issue(run, "planner_user_unmatched", "warning", source_system="planner", source_key="users",
              count=len(unmatched), users=[{"name": u.display_name, "tasks": assigned[u.user_id]} for u in unmatched[:40]])
        stats["users_unmatched"] = len(unmatched)
    return dict(stats)


# ------------------------------------------------------------------------------------------- orchestration
def load_all(run, details_cap=DETAILS_CAP, plan_ids=None):
    """The whole Planner pull. Returns the stats dict recorded on the run's step."""
    t0 = time.time()
    audit = g.permissions_audit()
    out = {"audit": audit}
    out["users"] = load_users(run)
    out["groups"] = load_groups_and_plans(run)
    user_names = dict(PlannerUser.objects.values_list("user_id", "display_name"))
    plans = PlannerPlan.objects.filter(active=True).select_related("group").order_by("group__name", "title")
    if plan_ids:
        plans = plans.filter(plan_id__in=plan_ids)
    budget, totals, failed = details_cap, Counter(), []
    for plan in plans:
        try:
            st, budget = load_plan(plan, run, user_names, budget)
            totals.update(st)
        except Exception as e:  # noqa - one plan must not sink the others
            log.warning("planner plan %s failed: %s", plan.plan_id, e)
            failed.append("%s: %s" % (plan.title, str(e)[:120]))
    out["plans"] = dict(totals, count=plans.count(), failed=failed)
    out["links"] = link_projects(run)
    out["kinds"] = derive_board_kinds(run)
    out["quality"] = check_quality(run)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def refresh_all_step(run):
    """Called by refresh_all (best effort — an outage never sinks the refresh)."""
    return load_all(run)
