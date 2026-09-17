"""PTT operational models (spec v3 §5.4)."""

from django.db import models

from apps.core.models import HOURS, MONEY, PERCENT, Employee, Project, ProjectTask, TimeStampedModel
from apps.ingestion.models import IngestionRun


class TimeEntry(models.Model):
    """One row per PTT TTFormResponse with the Job Report elements pivoted."""

    source_key = models.IntegerField(unique=True)   # ttformresponse.id
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.CASCADE, related_name="time_entries")
    task = models.ForeignKey(ProjectTask, null=True, blank=True, on_delete=models.SET_NULL, related_name="time_entries")
    task_id_text = models.CharField(max_length=32, blank=True)
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="time_entries")
    ptt_person_pk = models.IntegerField()
    submitted_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name="submitted_time_entries")
    ptt_submitted_by_pk = models.IntegerField(null=True, blank=True)
    form_type = models.SmallIntegerField()   # PTT form_type: 1 job report, 2 time off, 3 other, 4 check-in
    work_date = models.DateField()
    hours_onsite = models.DecimalField(**HOURS, default=0)
    hours_ot = models.DecimalField(**HOURS, default=0)
    hours_offsite = models.DecimalField(**HOURS, default=0)
    hours_total = models.DecimalField(**HOURS, default=0)
    hours_time_off = models.DecimalField(**HOURS, default=0)
    system_choice = models.CharField(max_length=32, blank=True)
    work_type_choice = models.CharField(max_length=32, blank=True)
    completed_flag = models.BooleanField(null=True)
    activity_note = models.TextField(blank=True)
    open_issues_note = models.TextField(blank=True)
    shift_code = models.CharField(max_length=16, blank=True)
    shift_multiplier = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    submitted_at = models.DateTimeField()
    last_edited_at = models.DateTimeField(null=True, blank=True)
    source_status = models.SmallIntegerField(default=1)   # 1 live, 2 removed
    removed_at = models.DateTimeField(null=True, blank=True)
    hours_parse_warning = models.BooleanField(default=False)
    content_hash = models.CharField(max_length=64)
    last_seen_run = models.ForeignKey(IngestionRun, on_delete=models.PROTECT)

    class Meta:
        indexes = [
            models.Index(fields=["project", "work_date"]),
            models.Index(fields=["employee", "work_date"]),
            models.Index(fields=["work_date"]),
            models.Index(fields=["source_status"]),
        ]

    @property
    def is_labor(self):
        return self.source_status == 1 and self.form_type == 1 and self.project_id is not None


class RemainingHoursRevision(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="remaining_hours_revisions")
    revised_at = models.DateTimeField()
    revised_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    ptt_person_pk = models.IntegerField(null=True, blank=True)
    remaining_hours_non_union = models.DecimalField(**HOURS, null=True, blank=True)
    remaining_hours_union = models.DecimalField(**HOURS, null=True, blank=True)
    remaining_hours_total = models.DecimalField(**HOURS, null=True, blank=True)
    sequence = models.PositiveIntegerField()
    is_current = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "revised_at", "sequence"], name="uniq_remaining_hours_revision")]
        indexes = [models.Index(fields=["project", "revised_at"])]
        ordering = ["revised_at", "sequence"]


class PercentCompleteObservation(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="percent_complete_observations")
    observed_at = models.DateTimeField()
    ptt_percent_complete = models.DecimalField(**PERCENT, null=True, blank=True)
    ptt_last_updated_at = models.DateTimeField(null=True, blank=True)
    ptt_last_updated_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    sl_pm_id26_percent = models.DecimalField(**PERCENT, null=True, blank=True)
    ptt_remaining_expense_costs = models.DecimalField(**MONEY, null=True, blank=True)
    ptt_remaining_labor_costs = models.DecimalField(**MONEY, null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "observed_at"], name="uniq_pc_observation")]


class ProjectOperationalSnapshot(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="operational_snapshots")
    as_of_date = models.DateField()
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.PROTECT)
    ptt_hours_to_date = models.DecimalField(**HOURS, null=True, blank=True)
    ptt_hours_onsite_to_date = models.DecimalField(**HOURS, null=True, blank=True)
    ptt_hours_ot_to_date = models.DecimalField(**HOURS, null=True, blank=True)
    ptt_hours_offsite_to_date = models.DecimalField(**HOURS, null=True, blank=True)
    sl_labor_hours_to_date = models.DecimalField(**HOURS, null=True, blank=True)
    hours_ptt_minus_sl = models.DecimalField(**HOURS, null=True, blank=True)
    pm_remaining_hours = models.DecimalField(**HOURS, null=True, blank=True)
    pm_remaining_hours_updated_at = models.DateTimeField(null=True, blank=True)
    budget_hours = models.DecimalField(**HOURS, null=True, blank=True)
    pm_percent_complete = models.DecimalField(**PERCENT, null=True, blank=True)
    pm_percent_complete_updated_at = models.DateTimeField(null=True, blank=True)
    labor_percent_complete_calc = models.DecimalField(**PERCENT, null=True, blank=True)
    hours_last_7_days = models.DecimalField(**HOURS, default=0)
    hours_last_30_days = models.DecimalField(**HOURS, default=0)
    hours_last_90_days = models.DecimalField(**HOURS, default=0)
    active_workers_last_30_days = models.PositiveIntegerField(default=0)
    distinct_workers_to_date = models.PositiveIntegerField(default=0)
    days_since_last_work = models.IntegerField(null=True, blank=True)
    first_work_date = models.DateField(null=True, blank=True)
    last_work_date = models.DateField(null=True, blank=True)
    system_mix = models.JSONField(default=dict)
    work_type_mix = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "as_of_date"], name="uniq_project_operational_snapshot_day")]
        indexes = [models.Index(fields=["as_of_date", "project"])]
