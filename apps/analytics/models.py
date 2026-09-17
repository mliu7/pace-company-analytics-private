"""Ratings and predictions (spec v3 §5.8, §9, §10)."""

from django.db import models
from django.conf import settings

class PrivateRatingManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        # Shared views can ask for optional rating cards without ever opening a
        # private connection or querying a table that is deliberately absent.
        return qs if settings.PRIVATE_MODE else qs.using("default").none()


from apps.core.models import HOURS, MONEY, PERCENT, Project, TimeStampedModel


class RatingRun(TimeStampedModel):
    objects = PrivateRatingManager()
    as_of_date = models.DateField()
    methodology_version = models.CharField(max_length=32)
    training_cutoff = models.DateField()
    status = models.CharField(max_length=16, default="succeeded")
    metrics = models.JSONField(default=dict)
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ["-as_of_date", "-id"]


class EntityType(models.TextChoices):
    CUSTOMER = "customer", "Customer"
    CUSTOMER_FAMILY = "customer_family", "Customer Family"
    SECTOR = "sector", "Market Sector"
    SOLUTION = "solution", "Solution / Project Type"
    PROJECT_MODE = "project_mode", "Project Mode"
    PROJECT_MANAGER = "project_manager", "Project Manager"
    ESTIMATOR = "estimator", "Estimator"
    SALESPERSON = "salesperson", "Salesperson"
    DIVISION_HEAD_ERA = "division_head_era", "Division Head Era"
    FIELD_EMPLOYEE = "field_employee", "Field Employee"


class EntityRating(models.Model):
    objects = PrivateRatingManager()
    class Publication(models.TextChoices):
        INSUFFICIENT = "insufficient", "Insufficient Data"
        PROVISIONAL = "provisional", "Provisional"
        PUBLISHABLE = "publishable", "Publishable"
        NOT_IDENTIFIABLE = "not_identifiable", "Not Identifiable"

    rating_run = models.ForeignKey(RatingRun, on_delete=models.CASCADE, related_name="ratings")
    entity_type = models.CharField(max_length=32, choices=EntityType.choices)
    entity_key = models.CharField(max_length=128)
    entity_name = models.CharField(max_length=255)
    metric_name = models.CharField(max_length=128)
    raw_effect = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    adjusted_effect = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    interval_low = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    interval_high = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    standard_error = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    project_count = models.PositiveIntegerField(default=0)
    effective_project_count = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    revenue_exposure = models.DecimalField(**MONEY, default=0)
    labor_hours_exposure = models.DecimalField(**HOURS, default=0)
    customer_count = models.PositiveIntegerField(default=0)
    solution_count = models.PositiveIntegerField(default=0)
    shrinkage_factor = models.DecimalField(**PERCENT, null=True, blank=True)
    reliability_score = models.DecimalField(**PERCENT, null=True, blank=True)
    percentile = models.DecimalField(**PERCENT, null=True, blank=True)
    display_index = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    raw_mean_outcome = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    publication_status = models.CharField(max_length=32, choices=Publication.choices)
    limitations = models.JSONField(default=list)
    detail = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rating_run", "entity_type", "entity_key", "metric_name"], name="uniq_entity_rating_metric_run")]
        indexes = [models.Index(fields=["entity_type", "metric_name", "publication_status"])]


class ProjectPrediction(models.Model):
    """Deterministic estimate-at-completion (spec v3 §9.6) plus heuristic risk. ML slots stay NULL until a model is promoted."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="predictions")
    as_of_date = models.DateField()
    generated_at = models.DateTimeField()
    prediction_method = models.CharField(max_length=32, default="deterministic")
    labor_rate_method = models.CharField(max_length=32, blank=True)
    labor_rate_used = models.DecimalField(**MONEY, null=True, blank=True)
    unposted_labor_hours = models.DecimalField(**HOURS, null=True, blank=True)
    unposted_labor_cost = models.DecimalField(**MONEY, null=True, blank=True)
    remaining_labor_hours = models.DecimalField(**HOURS, null=True, blank=True)
    remaining_labor_cost = models.DecimalField(**MONEY, null=True, blank=True)
    eac_labor_hours = models.DecimalField(**HOURS, null=True, blank=True)
    eac_labor_cost = models.DecimalField(**MONEY, null=True, blank=True)
    eac_material = models.DecimalField(**MONEY, null=True, blank=True)
    eac_subcontract = models.DecimalField(**MONEY, null=True, blank=True)
    eac_other_direct = models.DecimalField(**MONEY, null=True, blank=True)
    eac_direct_cost = models.DecimalField(**MONEY, null=True, blank=True)
    eac_revenue = models.DecimalField(**MONEY, null=True, blank=True)
    eac_gp_dollars = models.DecimalField(**MONEY, null=True, blank=True)
    eac_gp_percent = models.DecimalField(**PERCENT, null=True, blank=True)
    projected_margin_change_points = models.DecimalField(**PERCENT, null=True, blank=True)
    projected_gp_shortfall_dollars = models.DecimalField(**MONEY, null=True, blank=True)
    hours_overrun_ratio = models.DecimalField(**PERCENT, null=True, blank=True)
    risk_score = models.PositiveSmallIntegerField(null=True, blank=True)
    risk_level = models.CharField(max_length=16, blank=True)
    risk_reasons = models.JSONField(default=list)
    warnings = models.JSONField(default=list)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "as_of_date"], name="uniq_project_prediction_day")]
        indexes = [models.Index(fields=["as_of_date", "risk_level"])]
