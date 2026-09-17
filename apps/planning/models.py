"""Production planning (SharePoint spec §3, §6.1, §6.2, §6.4, §6.5) — PCA is the system of record for these tables.

The Resource Scheduler tables (Resource, ResourcePto, PlanProject, PlanPhase, PhaseRange, Assignment) arrive in Phase E
in apps/scheduling. Every edit here stamps who / when, keeps a history row and bumps `version` (optimistic locking: a
stale form gets "changed by X at hh:mm — reload" instead of silently losing the other user's edit).
"""

import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models

from apps.core.models import Employee, Project, TimeStampedModel

from . import rules

USER = settings.AUTH_USER_MODEL


def attachment_storage():
    """Files live in PCA's own store under Application Support, never inline base64 (BA-01)."""
    return FileSystemStorage(location=str(settings.APP_SUPPORT_DIR / "planning_attachments"))

DIVISION_CHOICES = [("040", "040"), ("070", "070"), ("080", "080")]


class Source(models.TextChoices):
    MANUAL = "manual", "Manual"
    PLANNER = "planner", "Planner"
    IMPORT = "import", "Import"


# ------------------------------------------------------------------------------------------------ vocabulary
class StatusVocabulary(models.Model):
    """The 21-value status vocabulary with Planner colours and misspelling aliases — data, editable in the admin
    (PS-03). Seeded from rules.STATUSES on first use; `aliases` are folded into the canonical key on read and select."""

    key = models.CharField(max_length=64, unique=True)
    order = models.PositiveSmallIntegerField(default=0)
    bg = models.CharField(max_length=9, default="#ffffff")
    fg = models.CharField(max_length=9, default="#606a71")
    aliases = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "key"]
        verbose_name_plural = "status vocabulary"

    def __str__(self):
        return self.key or "(blank)"

    @classmethod
    def seed(cls):
        if cls.objects.exists():
            return
        alias_for = {}
        for a, canon in rules.STATUS_ALIASES.items():
            alias_for.setdefault(canon, []).append(a)
        cls.objects.bulk_create([
            cls(key=k, order=i, bg=rules.STATUS_COLORS[k][0], fg=rules.STATUS_COLORS[k][1], aliases=alias_for.get(k, []))
            for i, k in enumerate(rules.STATUSES)])

    @classmethod
    def tables(cls):
        """(ordered keys, colours {key: (bg, fg)}, aliases {alias: key}) from the table, seeded if empty."""
        cls.seed()
        rows = list(cls.objects.filter(active=True))
        keys = [r.key for r in rows]
        colors = {r.key: (r.bg, r.fg) for r in rows}
        aliases = {}
        for r in rows:
            for a in (r.aliases or []):
                aliases[str(a).strip().upper()] = r.key
        return keys, colors, aliases


class PunchDepartment(models.Model):
    """Ball-in-court departments: the nine from the punch workbook plus whatever the PMs add (PU-04)."""

    name = models.CharField(max_length=60, unique=True)
    order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    @classmethod
    def names(cls):
        if not cls.objects.exists():
            cls.objects.bulk_create([cls(name=n, order=i) for i, n in enumerate(rules.BIC_OPTIONS)])
        return list(cls.objects.filter(active=True).values_list("name", flat=True))


# ------------------------------------------------------------------------------------------------ status board (6.1)
class StatusRow(TimeStampedModel):
    """One Master Schedule row: a project / task with two status pills. `project` is the SL project (000000 rule);
    rows that match no SL project keep their free name and `project_number_raw` and are flagged on Data Quality."""

    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="status_rows")
    project_number_raw = models.CharField(max_length=32, blank=True)      # the dashboards' 6-digit job_key, as typed
    name = models.CharField(max_length=300)                                # Project / Task Description
    division = models.CharField(max_length=8, db_index=True)
    task_num = models.IntegerField(null=True, blank=True)
    phase_status = models.CharField(max_length=64, blank=True)             # "Phase Complete Status" (S1)
    equipment_status = models.CharField(max_length=64, blank=True)         # "Equipment Complete Status V2" (S2)
    pm = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="planning_status_rows")
    pm_raw = models.CharField(max_length=120, blank=True)
    pm2 = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="planning_status_rows_pm2")
    pm2_raw = models.CharField(max_length=120, blank=True)
    engineer = models.CharField(max_length=120, blank=True)
    foreman = models.CharField(max_length=120, blank=True)
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)                          # critical stop
    hours_left = models.DecimalField(max_digits=9, decimal_places=2, default=0)   # union hours remaining
    percent = models.DecimalField(max_digits=5, decimal_places=1, default=0)      # 0–100
    notes = models.TextField(blank=True)
    site_contact = models.CharField(max_length=300, blank=True)
    assigned_to = models.CharField(max_length=300, blank=True)
    priority_raw = models.CharField(max_length=32, blank=True)
    last_update_date = models.DateField(null=True, blank=True)             # Planner "Last Update Date" (import) or the edit date
    completed = models.BooleanField(default=False, db_index=True)          # manual only, reversible (PS-05)
    completed_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    source_key = models.CharField(max_length=140, blank=True, db_index=True)   # legacy row id / import merge key
    planner_task_id = models.CharField(max_length=64, blank=True)          # Phase I: the Planner task the statuses follow
    sent_to_scheduler_at = models.DateTimeField(null=True, blank=True)
    plan_project_id = models.IntegerField(null=True, blank=True)           # scheduling.PlanProject pk once Phase E exists
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["division", "task_num", "id"]
        indexes = [models.Index(fields=["division", "completed"]), models.Index(fields=["project"])]

    def __str__(self):
        return "%s %s" % (self.division, self.name[:60])

    @property
    def job_key(self):
        return rules.job_key(self.name, self.project_number_raw)

    @property
    def is_active_task(self):
        return rules.is_active_task(self.completed, self.percent)


class StatusHistory(models.Model):
    row = models.ForeignKey(StatusRow, on_delete=models.CASCADE, related_name="history")
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    changed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    field = models.CharField(max_length=40)
    old = models.TextField(blank=True)
    new = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]


# ------------------------------------------------------------------------------------------------ punch lists (6.4)
class PunchProject(TimeStampedModel):
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="punch_projects")
    division = models.CharField(max_length=8, db_index=True)
    code = models.CharField(max_length=40)                                 # 'NN-NNNN' (the seed also carries longer legacy codes)
    title = models.CharField(max_length=300, blank=True)
    pm = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="punch_projects")
    pm_raw = models.CharField(max_length=120, blank=True)                  # 'Unassigned' = triage
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("division", "code")]
        ordering = ["division", "code"]

    def __str__(self):
        return "%s %s" % (self.division, self.code)


class PunchItem(TimeStampedModel):
    punch_project = models.ForeignKey(PunchProject, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=False)                            # the spreadsheet tick — visual only
    date_entered = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    bic = models.CharField(max_length=60, blank=True)                      # ball in court
    critical = models.PositiveSmallIntegerField(null=True, blank=True)     # 1–5
    due_by = models.DateField(null=True, blank=True)
    date_completed = models.DateField(null=True, blank=True)               # the only "finished" signal
    assigned = models.CharField(max_length=200, blank=True)
    engineer_signoff = models.CharField(max_length=200, blank=True)
    verified = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["punch_project", "position", "id"]

    def status(self, today):
        return rules.punch_item_status(self, today)


class PunchHistory(models.Model):
    punch_project = models.ForeignKey(PunchProject, on_delete=models.CASCADE, related_name="history")
    item = models.ForeignKey(PunchItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="history")
    changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    changed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    field = models.CharField(max_length=40)
    old = models.TextField(blank=True)
    new = models.TextField(blank=True)

    class Meta:
        ordering = ["-changed_at", "-id"]


class PunchAttachment(models.Model):
    """Photos / files on a punch item, in PCA's own store (PU-04 "+ photos / attachments")."""

    item = models.ForeignKey(PunchItem, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="punch/%Y/%m/", storage=attachment_storage)
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=120, blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)


# ------------------------------------------------------------------------------------------------ approvals (6.5)
def _attachment_path(instance, filename):
    base, ext = os.path.splitext(filename)
    return "approvals/%s%s" % (uuid.uuid4().hex, ext.lower()[:12])


class ApprovalRequest(TimeStampedModel):
    class Kind(models.TextChoices):
        BOM = "BOM", "BOM"
        LABOR = "Labor", "Labor"

    class Status(models.TextChoices):
        PENDING = rules.APPROVAL_PENDING, "Pending Approval"
        APPROVED = rules.APPROVAL_APPROVED, "Approved"
        REOPENED = rules.APPROVAL_REOPENED, "Reopened"

    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="approval_requests")
    project_name = models.CharField(max_length=150)                        # free text until linked
    project_number_raw = models.CharField(max_length=60, blank=True)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="planning_requests")
    requested_by_raw = models.CharField(max_length=100, blank=True)       # imports only (the dashboard typed names)
    approver = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="approval_requests_to_approve")
    approver_raw = models.CharField(max_length=100, blank=True)
    needed_by = models.DateField(null=True, blank=True)
    department = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    submitted_at = models.DateTimeField(db_index=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="planning_decisions")
    decided_by_raw = models.CharField(max_length=100, blank=True)         # imports only
    decided_at = models.DateTimeField(null=True, blank=True)
    estimate_id = models.IntegerField(null=True, blank=True)               # estimating.Estimate pk (Phase G)
    bid = models.ForeignKey("bids.Bid", null=True, blank=True, on_delete=models.SET_NULL, related_name="approval_requests")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    source_key = models.CharField(max_length=80, blank=True)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-submitted_at", "-id"]

    def __str__(self):
        return "%s · %s" % (self.kind, self.project_name)

    @property
    def awaiting(self):
        return rules.is_awaiting(self.status)


class ApprovalAttachment(models.Model):
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=_attachment_path, storage=attachment_storage)
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=120, blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class ApprovalEvent(models.Model):
    KINDS = [(k, k) for k in ("created", "edited", "approved", "reopened", "deleted", "attachment_added", "attachment_removed", "imported")]
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name="events")
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=24, choices=KINDS)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-at", "-id"]
