"""Ingestion lineage and data-quality models (spec v3 §5.2)."""

from django.conf import settings
from django.db import models

from apps.core.models import SourceSystem, TimeStampedModel


class IngestionRun(TimeStampedModel):
    class Trigger(models.TextChoices):
        NIGHTLY = "nightly", "Nightly"
        MANUAL = "manual", "Manual"
        BACKFILL = "backfill", "Backfill"
        RETRY = "retry", "Retry"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    source_system = models.CharField(max_length=16, choices=SourceSystem.choices)
    trigger = models.CharField(max_length=16, choices=Trigger.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    watermark_start = models.JSONField(default=dict, blank=True)
    watermark_end = models.JSONField(default=dict, blank=True)
    rows_read = models.BigIntegerField(default=0)
    rows_inserted = models.BigIntegerField(default=0)
    rows_updated = models.BigIntegerField(default=0)
    rows_unchanged = models.BigIntegerField(default=0)
    rows_rejected = models.BigIntegerField(default=0)
    query_versions = models.JSONField(default=dict, blank=True)
    permissions_audit = models.JSONField(default=dict, blank=True)
    steps = models.JSONField(default=list, blank=True)
    code_commit = models.CharField(max_length=64, blank=True)
    error_summary = models.TextField(blank=True)
    log_path = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return "%s %s %s" % (self.source_system, self.trigger, self.status)

    def add_step(self, name, **info):
        info["step"] = name
        self.steps.append(info)


class SourceWatermark(TimeStampedModel):
    source_system = models.CharField(max_length=16, choices=SourceSystem.choices)
    query_name = models.CharField(max_length=128)
    watermark = models.JSONField(default=dict)
    last_successful_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source_system", "query_name"], name="uniq_source_query_watermark")]


class SourceRecordVersion(models.Model):
    """Append-only raw payload versions for slowly-changing source rows."""

    source_system = models.CharField(max_length=16, choices=SourceSystem.choices)
    entity_type = models.CharField(max_length=64)
    source_key = models.CharField(max_length=255)
    content_hash = models.CharField(max_length=64)
    payload = models.JSONField()
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    first_seen_run = models.ForeignKey(IngestionRun, related_name="first_seen_records", on_delete=models.PROTECT)
    last_seen_run = models.ForeignKey(IngestionRun, related_name="last_seen_records", on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source_system", "entity_type", "source_key", "content_hash"], name="uniq_source_record_version")]
        indexes = [models.Index(fields=["source_system", "entity_type", "source_key"]), models.Index(fields=["last_seen_at"])]


class DataQualityIssue(TimeStampedModel):
    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"
        BLOCKING = "blocking", "Blocking"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACKNOWLEDGED = "acknowledged", "Acknowledged"
        RESOLVED = "resolved", "Resolved"
        ACCEPTED = "accepted", "Accepted Limitation"

    code = models.CharField(max_length=64)
    severity = models.CharField(max_length=16, choices=Severity.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    project = models.ForeignKey("core.Project", null=True, blank=True, on_delete=models.CASCADE, related_name="data_quality_issues")
    source_system = models.CharField(max_length=16, choices=SourceSystem.choices, blank=True)
    source_key = models.CharField(max_length=255, blank=True)
    field_name = models.CharField(max_length=128, blank=True)
    details = models.JSONField(default=dict)
    detected_run = models.ForeignKey(IngestionRun, on_delete=models.PROTECT)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["code", "status"]), models.Index(fields=["severity", "status"])]
        constraints = [models.UniqueConstraint(fields=["code", "project", "source_key", "field_name"], name="uniq_open_issue_key")]


class RefreshRequest(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"

    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    scope = models.CharField(max_length=32, default="all")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)
    pid = models.IntegerField(null=True, blank=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
