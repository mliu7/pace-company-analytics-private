"""Microsoft Planner mirror (SharePoint spec §3, §9): every plan of every Microsoft 365 group the reader app can see —
groups, plans (with their label names), buckets, tasks, a change history diffed by etag, and the Planner users
behind assignments. Read-only: the loader only GETs; nothing here is ever written back to Planner."""

from django.db import models

from apps.core.models import Employee, Project, TimeStampedModel
from apps.ingestion.models import IngestionRun


class PlannerGroup(TimeStampedModel):
    group_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    mail = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    visibility = models.CharField(max_length=16, blank=True)
    site_path = models.CharField(max_length=255, blank=True)        # /sites/<mailNickname> — by convention (rules.site_path_from_mail)
    tenant_domain = models.CharField(max_length=128, blank=True)    # from the group mail; feeds the Planner links
    group_created = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)                      # False once a pull no longer lists the group
    last_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PlannerPlan(TimeStampedModel):
    class Kind(models.TextChoices):
        WORKFLOW = "workflow", "Workflow board"
        JOB = "job", "Per-job plan"
        PUNCH = "punch", "Punch list"
        ADMIN = "admin", "Team & admin"

    plan_id = models.CharField(max_length=64, unique=True)
    group = models.ForeignKey(PlannerGroup, on_delete=models.CASCADE, related_name="plans")
    title = models.CharField(max_length=255)
    owner_id = models.CharField(max_length=64, blank=True)          # the owning group id (Planner's `owner`)
    plan_created = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=128, blank=True)
    category_descriptions = models.JSONField(default=dict, blank=True)   # {category1..25: label name or null}
    board_kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.ADMIN)
    board_kind_auto = models.CharField(max_length=12, choices=Kind.choices, default=Kind.ADMIN)   # what the rule derived
    board_kind_manual = models.BooleanField(default=False)          # True once edited: the rule stops overwriting board_kind
    av_stage = models.CharField(max_length=24, blank=True)          # rules.AV_SEQUENCE key when the plan is an AV production stage
    task_count = models.IntegerField(default=0)
    open_count = models.IntegerField(default=0)
    overdue_count = models.IntegerField(default=0)
    last_change = models.DateTimeField(null=True, blank=True)       # newest tracked change on any of its tasks
    last_synced = models.DateTimeField(null=True, blank=True)
    active = models.BooleanField(default=True)
    last_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["group__name", "title"]

    def __str__(self):
        return "%s › %s" % (self.group.name, self.title)


class PlannerBucket(TimeStampedModel):
    plan = models.ForeignKey(PlannerPlan, on_delete=models.CASCADE, related_name="buckets")
    bucket_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    order_hint = models.CharField(max_length=64, blank=True)
    etag = models.CharField(max_length=128, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["plan_id", "order_hint"]

    def __str__(self):
        return self.name


class PlannerTask(TimeStampedModel):
    plan = models.ForeignKey(PlannerPlan, on_delete=models.CASCADE, related_name="tasks")
    bucket = models.ForeignKey(PlannerBucket, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    task_id = models.CharField(max_length=64, unique=True)
    title = models.CharField(max_length=512)
    percent = models.SmallIntegerField(default=0)                   # 0 / 50 / 100 in the UI; any 0-100 via the API
    priority = models.SmallIntegerField(default=5)                  # 0-10: ≤1 urgent, ≤4 important, ≤7 medium, else low
    start = models.DateField(null=True, blank=True)                 # Central calendar date of startDateTime
    due = models.DateField(null=True, blank=True)                   # Central calendar date of dueDateTime
    completed_at = models.DateTimeField(null=True, blank=True)
    task_created = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=128, blank=True)
    labels = models.JSONField(default=list, blank=True)             # label names, in category order
    label_keys = models.JSONField(default=list, blank=True)         # category keys behind them (colours)
    assignee_ids = models.JSONField(default=list, blank=True)
    assignee_names = models.JSONField(default=list, blank=True)
    checklist_done = models.SmallIntegerField(default=0)
    checklist_total = models.SmallIntegerField(default=0)
    has_description = models.BooleanField(default=False)
    reference_count = models.SmallIntegerField(default=0)
    order_hint = models.CharField(max_length=64, blank=True)
    created_by_id = models.CharField(max_length=64, blank=True)
    completed_by_id = models.CharField(max_length=64, blank=True)
    # task details (a second call per task — only after the task's etag moved, capped per run)
    description = models.TextField(blank=True)
    checklist = models.JSONField(default=list, blank=True)          # [{title, done}] in order
    references = models.JSONField(default=list, blank=True)         # [{alias, type, url}]
    details_etag = models.CharField(max_length=128, blank=True)
    details_pending = models.BooleanField(default=False)
    # SL link (rules.match_project) and the AV quote number
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="planner_tasks")
    project_rule = models.CharField(max_length=32, blank=True)
    quote_ref = models.CharField(max_length=12, blank=True)
    last_change = models.DateTimeField(null=True, blank=True)       # when a tracked field last changed (history)
    active = models.BooleanField(default=True)                      # False once Planner no longer returns the task
    first_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["plan_id", "bucket_id", "order_hint"]
        indexes = [models.Index(fields=["project", "active"]), models.Index(fields=["plan", "active"])]

    def __str__(self):
        return self.title


class PlannerTaskHistory(models.Model):
    task = models.ForeignKey(PlannerTask, on_delete=models.CASCADE, related_name="history")
    changed_at = models.DateTimeField(db_index=True)
    field = models.CharField(max_length=24)                         # rules.TRACKED + 'created' / 'deleted'
    old = models.TextField(blank=True)
    new = models.TextField(blank=True)
    run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self):
        return "%s: %s %r -> %r" % (self.task_id, self.field, self.old, self.new)


class PlannerUser(TimeStampedModel):
    user_id = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=255)
    mail = models.CharField(max_length=255, blank=True)
    upn = models.CharField(max_length=255, blank=True)
    job_title = models.CharField(max_length=255, blank=True)
    department = models.CharField(max_length=255, blank=True)
    account_enabled = models.BooleanField(default=True)
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="planner_users")
    employee_rule = models.CharField(max_length=32, blank=True)     # apps.bids.rules.alias_candidates rule name
    employee_confidence = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name
