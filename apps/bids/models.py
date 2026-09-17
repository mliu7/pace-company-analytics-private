"""Bids & estimators (SharePoint spec §3): the Project Portal's Project List / Archive mirrored locally, plus the
PCA-owned notes, follow-ups and risks the portal replica had and SharePoint lacks."""

from django.conf import settings
from django.db import models

from apps.core.models import MONEY, Customer, Employee, Project, TimeStampedModel
from apps.ingestion.models import IngestionRun


class Bid(TimeStampedModel):
    class Source(models.TextChoices):
        LIST = "list", "Project List"
        ARCHIVE = "archive", "Project Archive"

    sp_item_id = models.CharField(max_length=32)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.LIST)
    portal_project_id = models.CharField(max_length=32, blank=True)       # the portal's own "Project ID"
    job_number_raw = models.CharField(max_length=64, blank=True)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="bids")
    client_name = models.CharField(max_length=255, blank=True)
    client = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="bids")
    project_name = models.CharField(max_length=255, blank=True)
    stage = models.CharField(max_length=16, default="unknown")             # rules.normalize_status
    stage_flag = models.CharField(max_length=24, blank=True)               # completed / in_progress / budgetary / rebid / needs_assessment
    status_raw = models.CharField(max_length=64, blank=True)
    bid_due = models.DateField(null=True, blank=True)
    submitted_on = models.DateField(null=True, blank=True)
    awarded_on = models.DateField(null=True, blank=True)
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)
    budget = models.DecimalField(**MONEY, null=True, blank=True)
    value = models.DecimalField(**MONEY, null=True, blank=True)
    bidder_raw = models.CharField(max_length=64, blank=True)
    estimator = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="estimated_bids")
    estimator_inferred = models.BooleanField(default=False)
    estimator_rule = models.CharField(max_length=32, blank=True)
    sales_rep_raw = models.CharField(max_length=64, blank=True)
    salesperson = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="rep_bids")
    house_account = models.BooleanField(default=False)
    probability = models.SmallIntegerField(null=True, blank=True)          # Probability of Close (0/25/50/75/100), None = unscored
    pct_close_raw = models.CharField(max_length=24, blank=True)            # % OF CLOSE as typed
    ball_in_court = models.CharField(max_length=64, blank=True)
    bom_status = models.CharField(max_length=32, blank=True)
    walkthrough_date = models.DateField(null=True, blank=True)
    walkthrough_raw = models.CharField(max_length=64, blank=True)
    po_number = models.CharField(max_length=64, blank=True)
    sales_order = models.CharField(max_length=32, blank=True)
    comments = models.TextField(blank=True)
    attachments_flag = models.BooleanField(default=False)
    created_by_raw = models.CharField(max_length=128, blank=True)
    pm_raw = models.CharField(max_length=64, blank=True)                   # portal ProjectManagerv2 / archive PM as typed
    portal_created = models.DateTimeField(null=True, blank=True)
    portal_modified = models.DateTimeField(null=True, blank=True)
    web_url = models.URLField(max_length=500, blank=True)
    division = models.CharField(max_length=8, blank=True)                  # derived, rules.division_for
    won_by_sl = models.BooleanField(default=False)
    versions_synced_at = models.DateTimeField(null=True, blank=True)       # portal_modified when versions were last pulled
    content_hash = models.CharField(max_length=64, blank=True)
    last_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["source", "sp_item_id"], name="uniq_bid_source_item")]
        indexes = [models.Index(fields=["stage"]), models.Index(fields=["estimator", "stage"]), models.Index(fields=["division", "stage"]),
                   models.Index(fields=["bid_due"]), models.Index(fields=["project"])]

    @property
    def is_open(self):
        return self.stage in ("quoting", "submitted")

    @property
    def is_won(self):
        return self.stage == "awarded" or self.won_by_sl

    @property
    def margin(self):
        from . import rules
        return rules.bid_margin(self.value, self.budget)

    def __str__(self):
        return "%s %s" % (self.job_number_raw or self.portal_project_id, self.project_name)


class BidVersion(models.Model):
    """One SharePoint version of a Project List row — stage timing, re-pricing and who changed what (§3)."""

    bid = models.ForeignKey(Bid, on_delete=models.CASCADE, related_name="versions")
    version_no = models.CharField(max_length=16)
    modified_at = models.DateTimeField()
    modified_by = models.CharField(max_length=128, blank=True)
    stage = models.CharField(max_length=16, default="unknown")
    status_raw = models.CharField(max_length=64, blank=True)
    value = models.DecimalField(**MONEY, null=True, blank=True)
    budget = models.DecimalField(**MONEY, null=True, blank=True)
    bidder_raw = models.CharField(max_length=64, blank=True)
    probability = models.SmallIntegerField(null=True, blank=True)
    job_number_raw = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["bid", "version_no"], name="uniq_bid_version")]
        indexes = [models.Index(fields=["bid", "modified_at"])]


class BidderAlias(TimeStampedModel):
    """Raw portal name -> employee, with the rule that made the link and its confidence (§4.1)."""

    class Role(models.TextChoices):
        ESTIMATOR = "estimator", "Estimator (Bidder)"
        SALES_REP = "sales_rep", "Sales Rep"
        WALKTHROUGH = "walkthrough", "Walkthrough personnel"
        PM = "pm", "Project Manager"

    raw = models.CharField(max_length=64)                                  # normalised key (rules.norm_name_key)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.ESTIMATOR)
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="bid_aliases")
    confidence = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    rule = models.CharField(max_length=32, blank=True)                     # exact / name_words / fixed_alias / surname / surname_ambiguous / manual / none
    manual = models.BooleanField(default=False)                            # set by hand on the Data Quality page; never overwritten by rules
    candidates = models.JSONField(default=list)                            # [(name, confidence, rule)] for review
    uses = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["raw", "role"], name="uniq_alias_raw_role")]

    def __str__(self):
        return "%s (%s) -> %s" % (self.raw, self.role, self.employee or "?")


class BidClientAlias(TimeStampedModel):
    raw = models.CharField(max_length=255, unique=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="bid_aliases")
    confidence = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    rule = models.CharField(max_length=32, blank=True)                     # linked_jobs / name / name_ambiguous / similar / similar_candidate / manual / none
    manual = models.BooleanField(default=False)
    candidates = models.JSONField(default=list)                            # [(customer_id, name, similarity)] for review
    uses = models.PositiveIntegerField(default=0)


class BidSnapshot(models.Model):
    """Daily pipeline history (§3): one row per date x division x estimator x stage."""

    snapshot_date = models.DateField()
    division = models.CharField(max_length=8, blank=True)
    estimator = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    stage = models.CharField(max_length=16)
    count = models.PositiveIntegerField(default=0)
    value = models.DecimalField(**MONEY, default=0)
    weighted_value = models.DecimalField(**MONEY, default=0)
    aged_30 = models.PositiveIntegerField(default=0)                       # open rows with no movement for 30+ days
    aged_60 = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["snapshot_date", "division", "estimator", "stage"], name="uniq_bid_snapshot")]
        indexes = [models.Index(fields=["snapshot_date"])]


class BidNote(TimeStampedModel):
    """PCA-owned conversation on a bid (the portal replica's conversations / notebook, §5.5)."""

    class Kind(models.TextChoices):
        GENERAL = "general", "General"
        CLIENT = "client", "Client follow-up"
        INTERNAL = "internal", "Internal note"
        PRICING = "pricing", "Pricing question"
        RISK = "risk", "Risk / blocker"

    bid = models.ForeignKey(Bid, on_delete=models.CASCADE, related_name="notes")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.GENERAL)
    body = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    author_name = models.CharField(max_length=128, blank=True)

    class Meta:
        ordering = ["-created_at"]


class BidFollowup(TimeStampedModel):
    bid = models.ForeignKey(Bid, on_delete=models.CASCADE, related_name="followups")
    text = models.CharField(max_length=500)
    owner = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    due = models.DateField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["done_at", "due", "-created_at"]


class BidRisk(TimeStampedModel):
    class Severity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    bid = models.ForeignKey(Bid, on_delete=models.CASCADE, related_name="risks")
    text = models.CharField(max_length=500)
    severity = models.CharField(max_length=8, choices=Severity.choices, default=Severity.MEDIUM)
    mitigation = models.TextField(blank=True)
    owner = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL)
    closed_at = models.DateTimeField(null=True, blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["closed_at", "-severity", "-created_at"]
