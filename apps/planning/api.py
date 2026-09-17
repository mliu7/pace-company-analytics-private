"""Programmatic entry points other apps use (SharePoint spec §6.5 approvals ↔ estimating §8; §6.1 ↔ scheduler §6.3;
§9 Planner labels). Everything here is a plain function; the views are thin wrappers around the same helpers."""

from django.utils import timezone

from . import rules
from .models import ApprovalEvent, ApprovalRequest, StatusHistory, StatusRow


def create_approval_request(project_id, kind, requested_by_user, needed_by, department, notes, estimate_id=None,
                            project_name="", approver_id=None, bid_id=None, project_number_raw=""):
    """A new Pending request (BA-01). `kind` = 'BOM' | 'Labor'; `project_id` may be None with a free `project_name`
    until an SL number exists. Returns the ApprovalRequest."""
    from apps.core.models import Project
    kind = "Labor" if str(kind).lower().startswith("l") else "BOM"
    name, number = project_name, project_number_raw
    if project_id:
        p = Project.objects.filter(pk=project_id).first()
        if p is not None:
            name = name or ("%s %s" % (p.display_number, p.title)).strip()
            number = number or p.canonical_project_number
    if not name:
        raise ValueError("project_name is required when no project is linked")
    req = ApprovalRequest.objects.create(
        project_id=project_id or None, project_name=name[:150], project_number_raw=(number or "")[:60], kind=kind,
        requested_by=requested_by_user if getattr(requested_by_user, "pk", None) else None,
        approver_id=approver_id, needed_by=needed_by, department=(department or "")[:100], notes=notes or "",
        status=ApprovalRequest.Status.PENDING, submitted_at=timezone.now(), estimate_id=estimate_id, bid_id=bid_id,
        updated_by=requested_by_user if getattr(requested_by_user, "pk", None) else None)
    ApprovalEvent.objects.create(request=req, by=req.requested_by, kind="created",
                                 detail={"kind": kind, "estimate_id": estimate_id, "via": "api"})
    return req


def status_rows_for_project(project):
    """Every status row on an SL project (any division), active first."""
    return StatusRow.objects.filter(project=project).select_related("pm", "pm2").order_by("completed", "division", "task_num", "id")


def planner_labels(project_numbers):
    """{canonical number: {'phase': ..., 'equipment': ..., 'task_id': ...}} from the Planner mirror when Phase I has
    shipped `apps.planner.sync.planner_statuses_for`; {} until then."""
    try:
        from apps.planner.sync import planner_statuses_for
    except ImportError:
        return {}
    try:
        return planner_statuses_for(list(project_numbers)) or {}
    except Exception:  # noqa — a Planner outage never breaks the status board
        return {}


def send_to_scheduler(row, actor=None):
    """PS-07: hand a status row to the Resource Scheduler. Calls apps.scheduling.api.create_plan_project(payload, actor)
    when Phase E has shipped it; otherwise reports that the scheduler is not built yet. `actor` is the request (its
    .acc names the account for the scheduler, .user stamps the row) or a Django user. Returns (ok, message, payload)."""
    payload = rules.scheduler_payload(row)
    user = getattr(actor, "user", actor)
    if not getattr(user, "pk", None) or not getattr(user, "is_authenticated", True):
        user = None
    try:
        from apps.scheduling.api import create_plan_project
    except ImportError:
        return False, "Resource Scheduler not built yet (Phase E) — nothing was sent.", payload
    try:
        plan = create_plan_project(payload, actor)
    except Exception as e:  # noqa
        return False, "Resource Scheduler refused the hand-off: %s" % e, payload
    row.sent_to_scheduler_at = timezone.now()
    row.plan_project_id = getattr(plan, "pk", None) or (plan.get("id") if isinstance(plan, dict) else None)
    row.updated_by = user
    row.version += 1
    row.save(update_fields=["sent_to_scheduler_at", "plan_project_id", "updated_by", "version", "updated_at"])
    StatusHistory.objects.create(row=row, changed_by=row.updated_by, field="sent_to_scheduler", old="", new=str(row.plan_project_id or "sent"))
    return True, "Sent to the Resource Scheduler as a project.", payload
