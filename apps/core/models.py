"""Canonical identity models (spec v3 §5.3).

Deviation from spec: integer primary keys instead of UUIDs, for bulk-load speed
and simpler joins. Every row still carries its raw source keys.
"""

from django.db import models

MONEY = {"max_digits": 20, "decimal_places": 4}
PERCENT = {"max_digits": 12, "decimal_places": 6}
HOURS = {"max_digits": 14, "decimal_places": 4}


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SourceSystem(models.TextChoices):
    PTT = "ptt", "PTT"
    SL = "sl", "Microsoft Dynamics SL"
    LOCAL = "local", "Pace Company Analytics"
    SHAREPOINT = "sharepoint", "SharePoint (Graph)"
    PLANNER = "planner", "Microsoft Planner"
    SHARE = "share", "Network share (P:)"
    CNET = "cnet", "ChannelOnline"


class ProjectLifecycle(models.TextChoices):
    AWARDED_NOT_STARTED = "awarded_not_started", "Awarded / Not Started"
    IN_PROGRESS = "in_progress", "In Progress"
    FIELD_COMPLETE = "field_complete", "Field Complete / Financially Open"
    CLOSED_STABILIZING = "closed_stabilizing", "Closed / Stabilizing"
    CLOSED_STABILIZED = "closed_stabilized", "Closed / Stabilized"
    DORMANT = "dormant", "Dormant / Stalled"
    CANCELED = "canceled", "Canceled / Void"
    TEMPLATE = "template", "Template / Internal Bucket"
    UNKNOWN = "unknown", "Unknown"


OPEN_LIFECYCLES = (
    ProjectLifecycle.AWARDED_NOT_STARTED,
    ProjectLifecycle.IN_PROGRESS,
    ProjectLifecycle.FIELD_COMPLETE,
    ProjectLifecycle.DORMANT,
)
CLOSED_LIFECYCLES = (ProjectLifecycle.CLOSED_STABILIZING, ProjectLifecycle.CLOSED_STABILIZED)


class ProjectMode(models.TextChoices):
    INSTALLATION = "installation", "Installation / Project"
    TM_TICKET = "tm_ticket", "T&M Ticket"
    TM_SERVICE = "tm_service", "T&M Service Blanket"
    SERVICE_AGREEMENT = "service_agreement", "Service Agreement"
    JOC = "joc", "JOC Work"
    WARRANTY = "warranty", "Warranty"
    INTERNAL = "internal", "Internal / Overhead"
    CANCELED = "canceled", "Canceled / Void"
    TEMPLATE = "template", "Template"
    UNKNOWN = "unknown", "Unknown"


class RoleType(models.TextChoices):
    SALESPERSON = "salesperson", "Salesperson"
    ESTIMATOR = "estimator", "Estimator"
    PROJECT_MANAGER = "project_manager", "Project Manager"
    DIVISION_HEAD = "division_head", "Division Head"
    FIELD = "field", "Field"
    CREW_LEAD = "crew_lead", "Crew Lead (inferred)"
    OTHER = "other", "Other"


class Division(TimeStampedModel):
    code = models.CharField(max_length=8, unique=True)
    name = models.CharField(max_length=128)
    sl_subaccounts = models.JSONField(default=list)
    active = models.BooleanField(default=True)
    modelled = models.BooleanField(default=False)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return "%s %s" % (self.code, self.name)


class CustomerFamily(TimeStampedModel):
    name = models.CharField(max_length=255, unique=True)
    market_sector = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)

    def __str__(self):
        return self.name


class Customer(TimeStampedModel):
    sl_customer_id = models.CharField(max_length=15, unique=True)
    canonical_name = models.CharField(max_length=255)
    sl_class_id = models.CharField(max_length=10, blank=True)
    market_sector_source = models.CharField(max_length=64, blank=True)
    market_sector = models.CharField(max_length=64, blank=True)
    city = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=8, blank=True)
    sl_status = models.CharField(max_length=4, blank=True)
    default_salesperson_code = models.CharField(max_length=10, blank=True)
    family = models.ForeignKey(CustomerFamily, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers")
    is_general_contractor = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    source_payload_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["canonical_name"]

    def __str__(self):
        return "%s (%s)" % (self.canonical_name, self.sl_customer_id)


class Salesperson(TimeStampedModel):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=128)
    non_commission = models.BooleanField(default=False)
    is_rma_or_split = models.BooleanField(default=False)
    employee = models.ForeignKey("Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="salesperson_codes")

    def __str__(self):
        return "%s %s" % (self.code, self.name)


class Employee(TimeStampedModel):
    employee_key = models.CharField(max_length=10, unique=True)
    canonical_name = models.CharField(max_length=255)
    ptt_person_id = models.IntegerField(null=True, blank=True, unique=True)
    ptt_employee_type = models.CharField(max_length=16, blank=True)   # non_union / union
    ptt_employee_role = models.CharField(max_length=16, blank=True)   # head_pm / pm / regular
    ptt_active = models.BooleanField(null=True)
    sl_status = models.CharField(max_length=4, blank=True)
    home_subaccount = models.CharField(max_length=8, blank=True)
    union_code = models.CharField(max_length=10, blank=True)
    labor_class = models.CharField(max_length=10, blank=True)
    hire_date = models.DateField(null=True, blank=True)
    termination_date = models.DateField(null=True, blank=True)
    ptt_loaded_rate_estimate = models.DecimalField(**MONEY, null=True, blank=True)
    ptt_base_wage = models.DecimalField(**MONEY, null=True, blank=True)
    salesperson_ids = models.JSONField(default=list)
    default_role = models.CharField(max_length=32, choices=RoleType.choices, blank=True)
    active = models.BooleanField(default=True)
    # Derived classification (apps/analytics/field_ratings.derive_classifications):
    # exact from labor_class when present, else inferred (≈) from wage vs the union's journeyman anchor.
    classification = models.CharField(max_length=48, blank=True)
    classification_code = models.CharField(max_length=20, blank=True)   # apprentice/journeyman/foreman/general_foreman/tech/admin/unknown
    classification_inferred = models.BooleanField(default=False)
    current_wage_rate = models.DecimalField(**MONEY, null=True, blank=True)   # latest observed SL wage rate
    wage_rate_observed_at = models.DateField(null=True, blank=True)
    # Access Spec v1 §7.2: True only for field workers who submit PTT time (trailing 24 months) and are not
    # PM/office/admin — the ONLY people whose $/h may be shown below superadmin. Conservative default False.
    is_field_hourly = models.BooleanField(default=False)

    class Meta:
        ordering = ["canonical_name"]

    def __str__(self):
        return "%s (%s)" % (self.canonical_name, self.employee_key)


class Project(TimeStampedModel):
    # identity
    canonical_project_number = models.CharField(max_length=32, unique=True)
    display_number = models.CharField(max_length=32)
    sl_project_key_raw = models.CharField(max_length=32, blank=True)
    ptt_project_pk = models.IntegerField(null=True, blank=True, unique=True)
    ptt_project_id_raw = models.CharField(max_length=64, blank=True)
    division = models.ForeignKey(Division, on_delete=models.PROTECT, related_name="projects")
    sl_subaccount = models.CharField(max_length=8, blank=True)
    numbering_style = models.CharField(max_length=8, blank=True)
    # descriptive
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name="projects")
    title = models.CharField(max_length=120, blank=True)
    quote_reference = models.CharField(max_length=32, blank=True)
    customer_po = models.CharField(max_length=32, blank=True)
    contract_type = models.CharField(max_length=4, blank=True)
    project_mode_rule = models.CharField(max_length=32, choices=ProjectMode.choices, default=ProjectMode.UNKNOWN)
    solution_class = models.CharField(max_length=32, blank=True)
    system_mix = models.JSONField(default=dict)
    work_type_mix = models.JSONField(default=dict)
    # lifecycle
    lifecycle_state = models.CharField(max_length=32, choices=ProjectLifecycle.choices, default=ProjectLifecycle.UNKNOWN)
    lifecycle_rule_version = models.CharField(max_length=16, blank=True)
    lifecycle_evidence = models.JSONField(default=dict)
    sl_status = models.CharField(max_length=2, blank=True)
    ptt_status = models.SmallIntegerField(null=True, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    sl_last_updated_at = models.DateTimeField(null=True, blank=True)
    sl_planned_start = models.DateField(null=True, blank=True)
    sl_planned_end = models.DateField(null=True, blank=True)
    ptt_inactivation_date = models.DateField(null=True, blank=True)
    close_date = models.DateField(null=True, blank=True)
    close_date_method = models.CharField(max_length=32, blank=True)
    first_work_date = models.DateField(null=True, blank=True)
    last_work_date = models.DateField(null=True, blank=True)
    last_transaction_date = models.DateField(null=True, blank=True)
    last_transaction_created_at = models.DateTimeField(null=True, blank=True)
    financially_stabilized_at = models.DateField(null=True, blank=True)
    # people
    project_manager = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_projects")
    division_head = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="headed_projects")
    salesperson_code = models.CharField(max_length=10, blank=True)
    salesperson = models.ForeignKey(Salesperson, null=True, blank=True, on_delete=models.SET_NULL, related_name="projects")
    salesperson_non_commission = models.BooleanField(default=False)
    estimator = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="estimated_projects")
    task_count = models.PositiveIntegerField(default=1)
    is_internal_bucket = models.BooleanField(default=False)
    is_template_or_void = models.BooleanField(default=False)
    # current-state economics (denormalised from the latest snapshot for fast lists)
    # contract_value = the value the app uses everywhere. SL's raw sum of task CONTRACT VALUE budgets is kept in
    # contract_value_sl; when the two differ, contract_value_basis names the rule (apps/analytics/contract_value.py)
    # and contract_value_evidence holds the numbers behind it. Nullable on purpose: loaders insert with explicit column lists.
    contract_value = models.DecimalField(**MONEY, null=True, blank=True)
    contract_value_sl = models.DecimalField(**MONEY, null=True, blank=True)
    contract_value_basis = models.CharField(max_length=32, null=True, blank=True)
    contract_value_evidence = models.JSONField(null=True, blank=True)
    revenue_budget = models.DecimalField(**MONEY, null=True, blank=True)
    budget_labor = models.DecimalField(**MONEY, null=True, blank=True)
    budget_labor_hours = models.DecimalField(**HOURS, null=True, blank=True)
    budget_material = models.DecimalField(**MONEY, null=True, blank=True)
    budget_subcontract = models.DecimalField(**MONEY, null=True, blank=True)
    budget_other_direct = models.DecimalField(**MONEY, null=True, blank=True)
    budget_direct_cost = models.DecimalField(**MONEY, null=True, blank=True)
    budget_touched_after_setup = models.BooleanField(default=False)
    contract_value_row_last_edited_at = models.DateTimeField(null=True, blank=True)
    billed_revenue = models.DecimalField(**MONEY, null=True, blank=True)
    actual_labor_wage = models.DecimalField(**MONEY, null=True, blank=True)
    actual_labor_burden = models.DecimalField(**MONEY, null=True, blank=True)
    actual_labor = models.DecimalField(**MONEY, null=True, blank=True)
    actual_labor_hours_sl = models.DecimalField(**HOURS, null=True, blank=True)
    actual_material = models.DecimalField(**MONEY, null=True, blank=True)
    actual_subcontract = models.DecimalField(**MONEY, null=True, blank=True)
    actual_other_direct = models.DecimalField(**MONEY, null=True, blank=True)          # ODC + TRAVEL only
    actual_purchase_variance = models.DecimalField(**MONEY, null=True, blank=True)     # PURCHASEVARIANCE (material price correction)
    actual_freight = models.DecimalField(**MONEY, null=True, blank=True)
    actual_direct_cost = models.DecimalField(**MONEY, null=True, blank=True)
    open_commitments_material = models.DecimalField(**MONEY, null=True, blank=True)    # real: open PO lines + unshipped warehouse allocations
    open_commitments_subcontract = models.DecimalField(**MONEY, null=True, blank=True)
    sl_reported_commitments = models.DecimalField(**MONEY, null=True, blank=True)      # raw SL com_amount (all accounts)
    phantom_commitments = models.DecimalField(**MONEY, null=True, blank=True)          # SL commitments for stock already shipped to the job
    sold_gp_dollars = models.DecimalField(**MONEY, null=True, blank=True)
    sold_gp_percent = models.DecimalField(**PERCENT, null=True, blank=True)
    actual_gp_dollars = models.DecimalField(**MONEY, null=True, blank=True)
    actual_gp_percent = models.DecimalField(**PERCENT, null=True, blank=True)
    effective_loaded_labor_rate = models.DecimalField(**MONEY, null=True, blank=True)
    budget_labor_rate = models.DecimalField(**MONEY, null=True, blank=True)
    earned_revenue = models.DecimalField(**MONEY, null=True, blank=True)
    # current-state operations
    ptt_hours_total = models.DecimalField(**HOURS, null=True, blank=True)
    ptt_hours_onsite = models.DecimalField(**HOURS, null=True, blank=True)
    ptt_hours_ot = models.DecimalField(**HOURS, null=True, blank=True)
    ptt_hours_offsite = models.DecimalField(**HOURS, null=True, blank=True)
    hours_ptt_minus_sl = models.DecimalField(**HOURS, null=True, blank=True)
    hours_last_30_days = models.DecimalField(**HOURS, null=True, blank=True)
    distinct_workers = models.PositiveIntegerField(default=0)
    pm_remaining_hours = models.DecimalField(**HOURS, null=True, blank=True)
    pm_remaining_hours_updated_at = models.DateTimeField(null=True, blank=True)
    pm_percent_complete = models.DecimalField(**PERCENT, null=True, blank=True)
    pm_percent_complete_updated_at = models.DateTimeField(null=True, blank=True)
    labor_percent_complete_calc = models.DecimalField(**PERCENT, null=True, blank=True)
    # eligibility
    descriptive_eligible = models.BooleanField(default=False)
    closed_model_eligible = models.BooleanField(default=False)
    award_model_eligible = models.BooleanField(default=False)
    rating_eligible = models.BooleanField(default=False)
    data_quality_flags = models.JSONField(default=list)
    latest_source_observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-sl_created_at"]
        indexes = [
            models.Index(fields=["division", "lifecycle_state"]),
            models.Index(fields=["customer"]),
            models.Index(fields=["project_manager"]),
            models.Index(fields=["close_date"]),
            models.Index(fields=["sl_created_at"]),
            models.Index(fields=["project_mode_rule"]),
            models.Index(fields=["sl_subaccount"]),
        ]

    def __str__(self):
        return "%s %s" % (self.display_number, self.title)

    @property
    def is_open(self):
        return self.lifecycle_state in OPEN_LIFECYCLES

    @property
    def is_closed(self):
        return self.lifecycle_state in CLOSED_LIFECYCLES


class ProjectTask(TimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    task_id = models.CharField(max_length=32)
    description = models.CharField(max_length=60, blank=True)
    sl_status = models.CharField(max_length=2, blank=True)
    task_manager_key = models.CharField(max_length=10, blank=True)
    ptt_phase_pk = models.IntegerField(null=True, blank=True)
    # SL audit stamps on PJPENT: Pace records change orders as NEW TASKS (CO1, AVCO5, ...), so a task's
    # creation date/user is the change-order date/user (apps/analytics/change_orders.py)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    sl_updated_at = models.DateTimeField(null=True, blank=True)
    sl_created_by = models.CharField(max_length=10, blank=True)
    sl_updated_by = models.CharField(max_length=10, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "task_id"], name="uniq_project_task")]
        ordering = ["task_id"]

    def __str__(self):
        return "%s/%s" % (self.project.display_number, self.task_id)


class ProjectRoleAssignment(TimeStampedModel):
    class Method(models.TextChoices):
        EXPLICIT = "explicit", "Explicit Source Field"
        WORK_LOG = "work_log", "Inferred from PTT hours"
        SUBMITTED_BY = "submitted_by", "Inferred from PTT submitter"
        MANUAL = "manual", "Local Manual Assignment"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="assignments")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="assignments")
    role = models.CharField(max_length=32, choices=RoleType.choices)
    assignment_method = models.CharField(max_length=32, choices=Method.choices)
    confidence = models.DecimalField(**PERCENT, default=1)
    actual_hours = models.DecimalField(**HOURS, null=True, blank=True)
    actual_labor_cost = models.DecimalField(**MONEY, null=True, blank=True)
    share_of_project_hours = models.DecimalField(**PERCENT, null=True, blank=True)
    first_date = models.DateField(null=True, blank=True)
    last_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "employee", "role"], name="uniq_project_employee_role")]
        indexes = [models.Index(fields=["project", "role"]), models.Index(fields=["employee", "role"])]


class BusinessConfiguration(models.Model):
    """Deployment-specific identifiers belong in backed-up app data, never Git.

    Configuration contains no credentials. The mirror copies it one way along
    with company data, so classification and bank parsing use the same policy.
    """
    key = models.CharField(max_length=80, primary_key=True)
    value = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)
