"""Resource Scheduler — PCA-owned planning state (SharePoint spec §6.3, parity rows RS-01 … RS-15).

Where the P-drive dashboard kept `resource_scheduler_data.json`, PCA keeps these tables: the roster (`Resource`,
one row per PTT / SL employee — plus hand-added people), PTO ranges, the projects being staffed (`PlanProject`,
keyed on `core_project` when the number resolves), their phases (`PlanPhase` + extra date `PhaseRange`s) and the
person × project × day `Assignment`s. Every edit goes through `apps/scheduling/services.py` with the actor recorded
(`created_by` / `updated_by`), a `version` for optimistic concurrency, and an `AuditEvent` (`write_action`).

Nothing here reads or writes PTT / SL / the share; live enrichment (hours worked, remaining hours, labor budgets)
is joined at read time from the tables the refresh already fills.
"""

from datetime import time

from django.db import models

from apps.core.models import Employee, Project, TimeStampedModel

TRADE_UNION, TRADE_SUB, TRADE_NONUNION = "Union", "Subcontracted Union", "Non-Union"
TRADES = [(TRADE_UNION, "Union"), (TRADE_SUB, "Subcontracted Union"), (TRADE_NONUNION, "Non-Union")]
PHASE_TRADES = [(TRADE_UNION, "Union"), (TRADE_NONUNION, "Non-Union")]
WEEKEND_RULES = [("none", "Weekdays only"), ("sat", "+ include Saturdays"), ("sun", "+ include Sundays"), ("both", "+ include Sat & Sun")]
DEFAULT_DAYS = [1, 1, 1, 1, 1, 0, 0]
HOURS = {"max_digits": 8, "decimal_places": 2}


class Resource(TimeStampedModel):
    """A person who can be put on a project-day. The roster is PTT / SL people (`employee`); a resource without an
    employee link is a hand-added or legacy-imported name that matched nobody (flagged on the page)."""
    employee = models.OneToOneField(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="scheduler_resource")
    display_name = models.CharField(max_length=120)
    sort_name = models.CharField(max_length=120, blank=True)          # "Last, First" — the roster is sorted on it
    trade = models.CharField(max_length=24, choices=TRADES, default=TRADE_UNION)
    max_weekly = models.PositiveSmallIntegerField(default=40)         # 0–168 like the dashboard editor
    divisions = models.JSONField(default=list, blank=True)            # ["040", "070"] — home divisions (★ in the assign list)
    approved_ot = models.BooleanField(default=False)                  # "Approved Overtime — ignore hour caps"
    active = models.BooleanField(default=True)
    source = models.CharField(max_length=16, default="manual")        # employee_seed / import / manual
    legacy_id = models.CharField(max_length=40, blank=True)           # dashboard uuid (one-time import)
    created_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["sort_name", "display_name"]

    def __str__(self):
        return self.display_name

    @property
    def is_union(self):
        return self.trade != TRADE_NONUNION


class ResourcePto(models.Model):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="pto")
    start = models.DateField()
    end = models.DateField()

    class Meta:
        ordering = ["start"]


class PlanProject(TimeStampedModel):
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="plan_projects")
    name = models.CharField(max_length=200)
    pm = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    pm_raw = models.CharField(max_length=64, blank=True)             # what the dashboard / status board typed ("PATTON")
    division = models.CharField(max_length=8, blank=True)
    start = models.DateField(null=True, blank=True)                  # earliest on-site date
    end = models.DateField(null=True, blank=True)                    # latest allowed date
    access_start = models.TimeField(default=time(7, 0))
    access_end = models.TimeField(default=time(15, 0))
    days = models.JSONField(default=list, blank=True)                # 7 × 0/1 Mon..Sun, "days needed on site"
    hours_per_day = models.PositiveSmallIntegerField(default=8)      # the shift used by the crew-plan maths
    hours_union = models.DecimalField(**HOURS, default=0)
    hours_nonunion = models.DecimalField(**HOURS, default=0)
    colour = models.CharField(max_length=9, blank=True)
    short_project = models.BooleanField(default=False)
    completed_early = models.JSONField(null=True, blank=True)        # {date, scope, at, hours, count, by}
    source = models.CharField(max_length=32, default="manual")       # manual / Project Status / import
    status_row_id = models.IntegerField(null=True, blank=True)       # the status-board row that sent it (Phase D)
    unscheduled = models.BooleanField(default=False)                 # sent by the planner without dates
    notes = models.TextField(blank=True)
    legacy_id = models.CharField(max_length=40, blank=True)
    created_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PlanPhase(models.Model):
    plan_project = models.ForeignKey(PlanProject, on_delete=models.CASCADE, related_name="phases")
    name = models.CharField(max_length=32)                           # one of the 11 phases, or Union / Non-Union (short project)
    trade = models.CharField(max_length=16, choices=PHASE_TRADES, default=TRADE_UNION)
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)
    weekend = models.CharField(max_length=8, choices=WEEKEND_RULES, default="none")
    hours = models.DecimalField(**HOURS, default=0)                  # total man-hours for the phase
    dates = models.JSONField(default=list, blank=True)               # explicit working days (multi-range / short project)
    order = models.PositiveSmallIntegerField(default=0)
    is_short = models.BooleanField(default=False)

    class Meta:
        ordering = ["plan_project_id", "order", "id"]

    def __str__(self):
        return "%s · %s" % (self.plan_project_id, self.name)


class PhaseRange(models.Model):
    phase = models.ForeignKey(PlanPhase, on_delete=models.CASCADE, related_name="ranges")
    start = models.DateField()
    end = models.DateField()
    weekend = models.CharField(max_length=8, choices=WEEKEND_RULES, default="none")
    hours = models.DecimalField(**HOURS, null=True, blank=True)      # per-range hours (summed into the phase); None on short projects
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["phase_id", "order", "id"]


class Assignment(TimeStampedModel):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name="assignments")
    plan_project = models.ForeignKey(PlanProject, on_delete=models.CASCADE, related_name="assignments")
    date = models.DateField(db_index=True)
    hours = models.DecimalField(max_digits=4, decimal_places=1)
    phase = models.ForeignKey(PlanPhase, null=True, blank=True, on_delete=models.SET_NULL, related_name="assignments")
    note = models.CharField(max_length=200, blank=True)
    legacy_id = models.CharField(max_length=40, blank=True)
    created_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    updated_by = models.ForeignKey("access.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["date", "resource_id"]
        indexes = [models.Index(fields=["resource", "date"]), models.Index(fields=["plan_project", "date"])]

    def __str__(self):
        return "%s · %s · %s · %sh" % (self.resource_id, self.plan_project_id, self.date, self.hours)
