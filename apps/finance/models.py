"""SL financial models (spec v3 §5.5)."""

from django.db import models

from apps.core.models import HOURS, MONEY, PERCENT, Employee, Project, TimeStampedModel
from apps.ingestion.models import IngestionRun


class CostCategory(models.TextChoices):
    REVENUE = "revenue", "Revenue"
    CONTRACT_VALUE = "contract_value", "Contract Value (budget memo)"
    LABOR_WAGE = "labor_wage", "Labor Wages"
    LABOR_BURDEN = "labor_burden", "Labor Burden"
    MATERIAL = "material", "Material"
    SUBCONTRACT = "subcontract", "Subcontractor"
    OTHER_DIRECT = "other_direct", "Other Direct"
    EXCLUDED_MEMO = "excluded_memo", "Excluded / Memo"
    UNKNOWN = "unknown", "Unknown"


DIRECT_COST_CATEGORIES = (
    CostCategory.LABOR_WAGE,
    CostCategory.LABOR_BURDEN,
    CostCategory.MATERIAL,
    CostCategory.SUBCONTRACT,
    CostCategory.OTHER_DIRECT,
)

# Spec v3 §4.2 — the total mapping of SL account categories. Local decision, never from SL.
SL_ACCT_TO_CATEGORY = {
    "REVENUE": CostCategory.REVENUE,
    "BILLINGS": CostCategory.REVENUE,
    "CONTRACT VALUE": CostCategory.CONTRACT_VALUE,
    "LABOR": CostCategory.LABOR_WAGE,
    "LABORUNION": CostCategory.LABOR_WAGE,
    "BURDEN": CostCategory.LABOR_BURDEN,
    "MATERIALS": CostCategory.MATERIAL,
    "SUBCONTRACT": CostCategory.SUBCONTRACT,
    "ODC": CostCategory.OTHER_DIRECT,
    "TRAVEL": CostCategory.OTHER_DIRECT,
    "PURCHASEVARIANCE": CostCategory.OTHER_DIRECT,
    "BTD": CostCategory.EXCLUDED_MEMO,
    "(OVER)/UNDER": CostCategory.EXCLUDED_MEMO,
    "DEFERRED REVENUE": CostCategory.EXCLUDED_MEMO,
    "PROGRESS BILLING": CostCategory.EXCLUDED_MEMO,
    "RETENTION": CostCategory.EXCLUDED_MEMO,
    "RETENTIONAP": CostCategory.EXCLUDED_MEMO,
    "UBWIP": CostCategory.EXCLUDED_MEMO,
    "UNBILLED": CostCategory.EXCLUDED_MEMO,
}


class AccountCategory(TimeStampedModel):
    sl_acct = models.CharField(max_length=16, unique=True)
    description = models.CharField(max_length=64, blank=True)
    sl_acct_type = models.CharField(max_length=4, blank=True)
    sl_group_cd = models.CharField(max_length=4, blank=True)
    category = models.CharField(max_length=32, choices=CostCategory.choices, default=CostCategory.UNKNOWN)
    sort_num = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_num", "sl_acct"]

    def __str__(self):
        return self.sl_acct


class GLAccount(TimeStampedModel):
    gl_account = models.CharField(max_length=10, unique=True)
    description = models.CharField(max_length=64, blank=True)
    acct_type = models.CharField(max_length=4, blank=True)


class ProjectFinancialTransaction(models.Model):
    """Typed, immutable copy of PJTran."""

    source_key = models.CharField(max_length=64, unique=True)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="financial_transactions")
    task_id = models.CharField(max_length=32, blank=True)
    sl_acct = models.CharField(max_length=16)
    category = models.CharField(max_length=32, choices=CostCategory.choices)
    sub_tag = models.CharField(max_length=32, blank=True)
    system_cd = models.CharField(max_length=2)
    batch_type = models.CharField(max_length=4, blank=True)
    batch_id = models.CharField(max_length=10)
    detail_num = models.IntegerField()
    fiscal_period = models.CharField(max_length=6)
    transaction_date = models.DateField(null=True, blank=True)
    posting_date = models.DateField(null=True, blank=True)
    source_created_at = models.DateTimeField()
    source_created_by = models.CharField(max_length=10, blank=True)
    amount = models.DecimalField(**MONEY)
    units = models.DecimalField(**HOURS, default=0)
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.SET_NULL, related_name="labor_transactions")
    employee_key = models.CharField(max_length=10, blank=True)
    vendor_num = models.CharField(max_length=15, blank=True)
    gl_account = models.CharField(max_length=10, blank=True)
    gl_subaccount = models.CharField(max_length=24, blank=True)
    tr_status = models.CharField(max_length=1, blank=True)
    comment = models.CharField(max_length=40, blank=True)
    pay_period_start = models.DateField(null=True, blank=True)
    pay_period_end = models.DateField(null=True, blank=True)
    voucher_num = models.CharField(max_length=10, blank=True)
    last_seen_run = models.ForeignKey(IngestionRun, on_delete=models.PROTECT)

    class Meta:
        indexes = [
            models.Index(fields=["project", "transaction_date"]),
            models.Index(fields=["project", "category"]),
            models.Index(fields=["category", "transaction_date"]),
            models.Index(fields=["employee", "transaction_date"]),
            models.Index(fields=["source_created_at"]),
            models.Index(fields=["fiscal_period"]),
        ]


class ProjectAccountSummary(models.Model):
    """Copy of PJPTDSUM (current budget/actual/commitment state). Change-log semantics:
    a new row is written only when the content hash changes; is_current marks the latest."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="account_summaries")
    task_id = models.CharField(max_length=32)
    sl_acct = models.CharField(max_length=16)
    category = models.CharField(max_length=32, choices=CostCategory.choices)
    as_of_date = models.DateField()
    is_current = models.BooleanField(default=True)
    actual_amount = models.DecimalField(**MONEY, default=0)
    actual_units = models.DecimalField(**HOURS, default=0)
    committed_amount = models.DecimalField(**MONEY, default=0)
    eac_amount = models.DecimalField(**MONEY, default=0)
    fac_amount = models.DecimalField(**MONEY, default=0)
    budget_amount = models.DecimalField(**MONEY, default=0)
    budget_units = models.DecimalField(**HOURS, default=0)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    source_updated_by = models.CharField(max_length=10, blank=True)
    source_updated_prog = models.CharField(max_length=8, blank=True)
    content_hash = models.CharField(max_length=64)
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "task_id", "sl_acct", "as_of_date"], name="uniq_account_summary_day")]
        indexes = [models.Index(fields=["project", "is_current"]), models.Index(fields=["as_of_date"])]


class ProjectFinancialSnapshot(models.Model):
    """One row per project per refresh day — the historical/current economics table."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="financial_snapshots")
    as_of_date = models.DateField()
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.PROTECT)
    contract_value = models.DecimalField(**MONEY, null=True, blank=True)       # the value the app used that day (see core.Project)
    contract_value_sl = models.DecimalField(**MONEY, null=True, blank=True)    # SL's raw sum that day
    contract_value_basis = models.CharField(max_length=32, null=True, blank=True)
    revenue_budget = models.DecimalField(**MONEY, null=True, blank=True)
    budget_labor = models.DecimalField(**MONEY, null=True, blank=True)
    budget_labor_hours = models.DecimalField(**HOURS, null=True, blank=True)
    budget_material = models.DecimalField(**MONEY, null=True, blank=True)
    budget_subcontract = models.DecimalField(**MONEY, null=True, blank=True)
    budget_other_direct = models.DecimalField(**MONEY, null=True, blank=True)
    budget_direct_cost = models.DecimalField(**MONEY, null=True, blank=True)
    budget_touched_after_setup = models.BooleanField(default=False)
    billed_revenue = models.DecimalField(**MONEY, null=True, blank=True)
    actual_labor_wage = models.DecimalField(**MONEY, null=True, blank=True)
    actual_labor_burden = models.DecimalField(**MONEY, null=True, blank=True)
    actual_labor = models.DecimalField(**MONEY, null=True, blank=True)
    actual_labor_hours_sl = models.DecimalField(**HOURS, null=True, blank=True)
    actual_material = models.DecimalField(**MONEY, null=True, blank=True)
    actual_subcontract = models.DecimalField(**MONEY, null=True, blank=True)
    actual_other_direct = models.DecimalField(**MONEY, null=True, blank=True)          # ODC + TRAVEL (purchase variance split out below)
    actual_purchase_variance = models.DecimalField(**MONEY, null=True, blank=True)     # PURCHASEVARIANCE: voucher vs PO/receipt price on project purchases
    actual_freight = models.DecimalField(**MONEY, null=True, blank=True)
    actual_direct_cost = models.DecimalField(**MONEY, null=True, blank=True)
    open_commitments_material = models.DecimalField(**MONEY, null=True, blank=True)    # open PO lines + UNSHIPPED project-inventory allocations (MATERIALS)
    open_commitments_subcontract = models.DecimalField(**MONEY, null=True, blank=True) # same, SUBCONTRACT
    sl_reported_commitments = models.DecimalField(**MONEY, null=True, blank=True)      # raw PJPTDSUM.com_amount, all accounts, as SL shows it
    phantom_commitments = models.DecimalField(**MONEY, null=True, blank=True)          # part of the SL figure that is allocations already shipped to the job
    last_transaction_date = models.DateField(null=True, blank=True)
    sold_gp_dollars = models.DecimalField(**MONEY, null=True, blank=True)
    sold_gp_percent = models.DecimalField(**PERCENT, null=True, blank=True)
    actual_gp_dollars = models.DecimalField(**MONEY, null=True, blank=True)
    actual_gp_percent = models.DecimalField(**PERCENT, null=True, blank=True)
    effective_loaded_labor_rate = models.DecimalField(**MONEY, null=True, blank=True)
    earned_revenue = models.DecimalField(**MONEY, null=True, blank=True)
    calculation_version = models.CharField(max_length=32, default="v3.0")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "as_of_date"], name="uniq_project_financial_snapshot_day")]
        indexes = [models.Index(fields=["as_of_date", "project"])]


class ProjectCommitmentLine(models.Model):
    """SL commitment detail (PJCOMDET — the rows behind PJPTDSUM.com_amount) with project-inventory allocations netted
    against what already shipped to the job. Replaced in full every refresh: SL regenerates PJCOMDET and it has no stable key.

    source_type 'open_po'            open purchase-order line; real future cost (open_amount = amount).
    source_type 'project_inventory'  allocation created when a project PO line was received at the warehouse; at Pace the
                                     allocation is not relieved when the stock ships to the job through Order Management, so
                                     open_amount is the FIFO-netted remainder and is_phantom marks rows whose stock has already
                                     shipped (cost already in actuals via OM/IN)."""

    class SourceType(models.TextChoices):
        OPEN_PO = "open_po", "Open purchase-order line"
        PROJECT_INVENTORY = "project_inventory", "Project-inventory allocation (PO receipt at warehouse)"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="commitment_lines")
    task_id = models.CharField(max_length=32, blank=True)
    sl_acct = models.CharField(max_length=16)
    category = models.CharField(max_length=32, choices=CostCategory.choices)
    source_type = models.CharField(max_length=24, choices=SourceType.choices)
    system_cd = models.CharField(max_length=2, blank=True)
    batch_type = models.CharField(max_length=4, blank=True)
    amount = models.DecimalField(**MONEY, default=0)                 # SL's commitment amount for the row
    units = models.DecimalField(**HOURS, default=0)
    qty_shipped_to_project = models.DecimalField(**HOURS, null=True, blank=True)   # SOLine qty shipped for this project + item (allocations only)
    open_units = models.DecimalField(**HOURS, default=0)
    open_amount = models.DecimalField(**MONEY, default=0)            # what we treat as a real open commitment
    is_phantom = models.BooleanField(default=False)
    # open_po line whose cost was already vouchered directly (same project + vendor + amount) while SL
    # left the PO open — treated as fulfilled (open_amount forced to 0) so it isn't double-counted
    voucher_matched = models.BooleanField(default=False)
    item_id = models.CharField(max_length=32, blank=True)
    po_number = models.CharField(max_length=12, blank=True)          # open PO lines: the PO; allocations: the PO that was received
    vendor_id = models.CharField(max_length=15, blank=True)
    po_date = models.DateField(null=True, blank=True)
    promise_date = models.DateField(null=True, blank=True)
    receipt_number = models.CharField(max_length=12, blank=True)
    receipt_line = models.CharField(max_length=6, blank=True)
    receipt_date = models.DateField(null=True, blank=True)
    comment = models.CharField(max_length=40, blank=True)
    gl_account = models.CharField(max_length=10, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    as_of_date = models.DateField()
    ingestion_run = models.ForeignKey(IngestionRun, on_delete=models.PROTECT)

    class Meta:
        indexes = [models.Index(fields=["project", "sl_acct"]), models.Index(fields=["source_type", "is_phantom"])]


class ProjectCommercialChange(TimeStampedModel):
    class Origin(models.TextChoices):
        DETECTED = "detected", "Detected from Source Snapshot"
        MANUAL = "manual", "Local Manual Entry"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="commercial_changes")
    detected_at = models.DateField()
    field_name = models.CharField(max_length=64)
    prior_value = models.DecimalField(**MONEY, null=True, blank=True)
    new_value = models.DecimalField(**MONEY, null=True, blank=True)
    change_amount = models.DecimalField(**MONEY, null=True, blank=True)
    origin = models.CharField(max_length=16, choices=Origin.choices, default=Origin.DETECTED)
    note = models.TextField(blank=True)
    source_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-detected_at"]


class EmployeeLaborRateObservation(models.Model):
    """Weekly per-employee wage / payroll-tax burden observed in SL charge entry."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="rate_observations")
    check_date = models.DateField()
    labor_account = models.CharField(max_length=16)
    hours = models.DecimalField(**HOURS)
    wage_amount = models.DecimalField(**MONEY)
    payroll_tax_burden = models.DecimalField(**MONEY, default=0)
    wage_rate = models.DecimalField(**MONEY, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "check_date", "labor_account"], name="uniq_employee_rate_week")]
        indexes = [models.Index(fields=["employee", "check_date"])]


# ============================================================================ company-level finance (Financial Reports section)
class GLAccountBalance(models.Model):
    """AcctHist copy: one row per GL account x subaccount x fiscal year, ACTUAL ledger.

    Balances are stored NATURAL-SIGN exactly as SL keeps them (assets/expenses debit-positive,
    liabilities/equity/income credit-positive). Use signed_through() for debit-positive math.
    Account types: 1A asset, 2L liability+equity (accounts 3xxxx are equity; 39999 is SL's
    system-maintained YTD net income), 3I income, 4E expense. Full-replace each refresh.
    """

    acct = models.CharField(max_length=10)
    sub = models.CharField(max_length=24, blank=True)
    fiscal_year = models.CharField(max_length=4)
    acct_type = models.CharField(max_length=2)
    descr = models.CharField(max_length=64, blank=True)
    beg_bal = models.DecimalField(**MONEY, default=0)
    p00 = models.DecimalField(**MONEY, default=0); p01 = models.DecimalField(**MONEY, default=0)
    p02 = models.DecimalField(**MONEY, default=0); p03 = models.DecimalField(**MONEY, default=0)
    p04 = models.DecimalField(**MONEY, default=0); p05 = models.DecimalField(**MONEY, default=0)
    p06 = models.DecimalField(**MONEY, default=0); p07 = models.DecimalField(**MONEY, default=0)
    p08 = models.DecimalField(**MONEY, default=0); p09 = models.DecimalField(**MONEY, default=0)
    p10 = models.DecimalField(**MONEY, default=0); p11 = models.DecimalField(**MONEY, default=0)
    p12 = models.DecimalField(**MONEY, default=0)
    sl_updated_at = models.DateTimeField(null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["acct", "sub", "fiscal_year"], name="uniq_gl_balance")]
        indexes = [models.Index(fields=["fiscal_year", "acct_type"])]


class AROpenDocument(models.Model):
    """Open AR documents (plus unreleased invoices) snapshot — full replace each finance refresh.

    sign: IN/DM/FC/NS/SB +1 (owed to Pace), CM/PA -1. bucket: current/d30/d60/d90/over90 by days
    past DueDate at pull time; credit docs are not aged (bucket=current, days_past_due null).
    book: project / so1 / so2 / rma / credit / pending / other.
    """

    ref_nbr = models.CharField(max_length=10)
    doc_type = models.CharField(max_length=2)
    customer = models.ForeignKey("core.Customer", null=True, blank=True, on_delete=models.SET_NULL)
    customer_id_raw = models.CharField(max_length=15, blank=True)
    customer_name = models.CharField(max_length=64, blank=True)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    project_id_raw = models.CharField(max_length=16, blank=True)
    order_nbr = models.CharField(max_length=15, blank=True)
    so_type = models.CharField(max_length=8, blank=True)
    book = models.CharField(max_length=10)
    doc_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    days_past_due = models.IntegerField(null=True, blank=True)
    bucket = models.CharField(max_length=8)
    sign = models.SmallIntegerField(default=1)
    doc_bal = models.DecimalField(**MONEY)
    orig_amt = models.DecimalField(**MONEY, null=True, blank=True)
    released = models.BooleanField(default=True)
    doc_desc = models.CharField(max_length=32, blank=True)
    slsper_id = models.CharField(max_length=10, blank=True)
    terms = models.CharField(max_length=4, blank=True)
    per_post = models.CharField(max_length=6, blank=True)
    cust_po = models.CharField(max_length=25, blank=True)
    as_of = models.DateTimeField()
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["book", "bucket"]), models.Index(fields=["customer_id_raw"])]


class APVoucherLine(models.Model):
    """APTran copy since 2013 (AP control-account offsets excluded): the expense lines of vouchers (VO) and
    adjustments (AD) — GL account + subaccount (= the division charged), project / task / PO when tied to a job —
    and the cash lines of payments (CK / HC / EP / VC / ZC: the bank or holding account the payment hit).
    Incremental on sl_created_at; record_id is SL's stable key. Powers the vendor page (docs/06)."""
    source_record_id = models.BigIntegerField(unique=True)
    ref_nbr = models.CharField(max_length=10, db_index=True)
    tran_type = models.CharField(max_length=2)              # VO voucher, AD adjustment, CK/HC/EP payment, VC void, ZC zero check, AC
    line_nbr = models.IntegerField(default=0)
    batch_nbr = models.CharField(max_length=10, blank=True)
    vendor_id = models.CharField(max_length=15, blank=True)
    tran_date = models.DateField(null=True, blank=True)
    per_post = models.CharField(max_length=6, blank=True)
    gl_account = models.CharField(max_length=10, blank=True)
    gl_subaccount = models.CharField(max_length=24, blank=True)   # division = first three characters (0700 / 0701 -> 070)
    dr_cr = models.CharField(max_length=1, blank=True)
    amount = models.DecimalField(**MONEY, default=0)               # TranAmt as stored (positive on VO expense lines and AD credits)
    tran_desc = models.CharField(max_length=30, blank=True)
    project_id_raw = models.CharField(max_length=16, blank=True)
    task_id = models.CharField(max_length=32, blank=True)
    po_nbr = models.CharField(max_length=10, blank=True)
    rcpt_nbr = models.CharField(max_length=10, blank=True)
    qty = models.DecimalField(**HOURS, null=True, blank=True)
    unit_price = models.DecimalField(**MONEY, null=True, blank=True)
    invt_id = models.CharField(max_length=30, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    last_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["vendor_id", "tran_date"]), models.Index(fields=["gl_subaccount"]),
                   models.Index(fields=["project_id_raw"]), models.Index(fields=["tran_type", "gl_account"])]


class POReceiptDist(models.Model):
    """POTran receipt lines (type R) since 2013: what each PO receipt debited — inventory 12000 or a project cost
    account — with the subaccount (= division) and project / task. The division of material bought on POs lives
    here (the voucher only clears PO clearing 20001). Incremental on sl_created_at (date precision, 7-day overlap);
    the stable key is receipt number + line reference. Companion to APVoucherLine for the vendor page (docs/06)."""
    source_key = models.CharField(max_length=16, unique=True)      # "<RcptNbr>|<LineRef>"
    rcpt_nbr = models.CharField(max_length=10, db_index=True)
    line_ref = models.CharField(max_length=5, blank=True)
    po_nbr = models.CharField(max_length=10, blank=True)
    vendor_id = models.CharField(max_length=15, blank=True)
    rcpt_date = models.DateField(null=True, blank=True)
    per_post = models.CharField(max_length=6, blank=True)
    gl_account = models.CharField(max_length=10, blank=True)
    gl_subaccount = models.CharField(max_length=24, blank=True)
    project_id_raw = models.CharField(max_length=16, blank=True)
    task_id = models.CharField(max_length=32, blank=True)
    invt_id = models.CharField(max_length=30, blank=True)
    descr = models.CharField(max_length=60, blank=True)
    qty = models.DecimalField(**HOURS, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    ext_cost = models.DecimalField(**MONEY, default=0)
    so_ord_nbr = models.CharField(max_length=15, blank=True)
    site_id = models.CharField(max_length=10, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    last_seen_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["vendor_id", "rcpt_date"]), models.Index(fields=["gl_subaccount"]), models.Index(fields=["project_id_raw"])]


class AROpenInvoiceLine(models.Model):
    """Lines of the open AR documents (ARTran, minus the 11000 control line): what each open invoice billed —
    description, project / task, item, qty x price, amount; tax lines (20500) included. Full replace with the
    open-AR pull. Powers the AR page's per-document detail (docs/06)."""
    ref_nbr = models.CharField(max_length=10, db_index=True)
    doc_type = models.CharField(max_length=2)
    customer_id_raw = models.CharField(max_length=15, blank=True)
    line_nbr = models.IntegerField(default=0)
    gl_account = models.CharField(max_length=10, blank=True)
    gl_subaccount = models.CharField(max_length=24, blank=True)
    tran_desc = models.CharField(max_length=30, blank=True)
    project_id_raw = models.CharField(max_length=16, blank=True)
    task_id = models.CharField(max_length=32, blank=True)
    invt_id = models.CharField(max_length=30, blank=True)
    qty = models.DecimalField(**HOURS, null=True, blank=True)
    unit_price = models.DecimalField(**MONEY, null=True, blank=True)
    amount = models.DecimalField(**MONEY, default=0)
    tran_date = models.DateField(null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)


class SLCustomer(models.Model):
    """SL Customer master contact / billing fields (full replace with the finance refresh): who to call or e-mail
    about an invoice, bill-to name, terms, statement cycle, default salesperson, status, class, credit limit (set
    for a handful of customers), setup date. Companion to core.Customer, which keeps the analytical fields."""
    customer_id = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=64, blank=True)
    attn = models.CharField(max_length=64, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    fax = models.CharField(max_length=32, blank=True)
    email = models.CharField(max_length=128, blank=True)
    bill_name = models.CharField(max_length=64, blank=True)
    bill_attn = models.CharField(max_length=64, blank=True)
    bill_phone = models.CharField(max_length=32, blank=True)
    bill_addr1 = models.CharField(max_length=64, blank=True)
    bill_city = models.CharField(max_length=32, blank=True)
    bill_state = models.CharField(max_length=4, blank=True)
    bill_zip = models.CharField(max_length=16, blank=True)
    terms = models.CharField(max_length=4, blank=True)
    stmt_cycle = models.CharField(max_length=4, blank=True)
    stmt_type = models.CharField(max_length=2, blank=True)
    slsper_id = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=2, blank=True)
    class_id = models.CharField(max_length=12, blank=True)
    credit_limit = models.DecimalField(**MONEY, null=True, blank=True)
    setup_date = models.DateField(null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)


class APOpenDocument(models.Model):
    """Open AP documents snapshot — full replace each finance refresh. VO/PP +1, AD -1; VT excluded."""

    ref_nbr = models.CharField(max_length=10)
    doc_type = models.CharField(max_length=2)
    vendor_id = models.CharField(max_length=15, blank=True)
    vendor_name = models.CharField(max_length=64, blank=True)
    doc_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    days_past_due = models.IntegerField(null=True, blank=True)
    bucket = models.CharField(max_length=8)
    sign = models.SmallIntegerField(default=1)
    doc_bal = models.DecimalField(**MONEY)
    orig_amt = models.DecimalField(**MONEY, null=True, blank=True)
    invoice_nbr = models.CharField(max_length=40, blank=True)
    doc_desc = models.CharField(max_length=32, blank=True)
    po_nbr = models.CharField(max_length=10, blank=True)
    per_post = models.CharField(max_length=6, blank=True)
    as_of = models.DateTimeField()
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["bucket"]), models.Index(fields=["vendor_id"]), models.Index(fields=["due_date"])]


class GLRecentPosting(models.Model):
    """Posted GL rows on income/expense accounts, trailing window — full replace each finance refresh.

    revenue = cr - dr on 3I accounts; cost = dr - cr on 4E. Powers MTD / prior-month P&L,
    the previous-business-day split (by sl_created_at) and posting drill-downs.
    """

    acct = models.CharField(max_length=10)
    sub = models.CharField(max_length=24, blank=True)
    acct_type = models.CharField(max_length=2)
    acct_descr = models.CharField(max_length=64, blank=True)
    tran_date = models.DateField(null=True, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    per_post = models.CharField(max_length=6)
    dr_amt = models.DecimalField(**MONEY, default=0)
    cr_amt = models.DecimalField(**MONEY, default=0)
    module = models.CharField(max_length=2, blank=True)
    batch_nbr = models.CharField(max_length=10, blank=True)
    ref_nbr = models.CharField(max_length=10, blank=True)
    tran_desc = models.CharField(max_length=30, blank=True)
    jrnl_type = models.CharField(max_length=3, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["per_post", "acct_type"]), models.Index(fields=["sl_created_at"])]


class DailyFinanceSnapshot(models.Model):
    """One row per day: every headline number of the daily finance report. Live rows are written by
    each finance refresh (same-day rerun overwrites); reconstructed=True rows come from the
    historical backfill (AR/AP/P&L only — balance-sheet fields stay null there)."""

    snapshot_date = models.DateField(unique=True)
    as_of = models.DateTimeField()
    reconstructed = models.BooleanField(default=False)
    # AR aging (net; credits parked in current) and books
    ar_total = models.DecimalField(**MONEY, null=True, blank=True)
    ar_current = models.DecimalField(**MONEY, null=True, blank=True)
    ar_d30 = models.DecimalField(**MONEY, null=True, blank=True)
    ar_d60 = models.DecimalField(**MONEY, null=True, blank=True)
    ar_d90 = models.DecimalField(**MONEY, null=True, blank=True)
    ar_over90 = models.DecimalField(**MONEY, null=True, blank=True)
    ar_credits = models.DecimalField(**MONEY, null=True, blank=True)
    ar_pending = models.DecimalField(**MONEY, null=True, blank=True)
    # AP aging
    ap_total = models.DecimalField(**MONEY, null=True, blank=True)
    ap_current = models.DecimalField(**MONEY, null=True, blank=True)
    ap_d30 = models.DecimalField(**MONEY, null=True, blank=True)
    ap_d60 = models.DecimalField(**MONEY, null=True, blank=True)
    ap_d90 = models.DecimalField(**MONEY, null=True, blank=True)
    ap_over90 = models.DecimalField(**MONEY, null=True, blank=True)
    # balance sheet (live only)
    assets = models.DecimalField(**MONEY, null=True, blank=True)
    liabilities = models.DecimalField(**MONEY, null=True, blank=True)
    equity = models.DecimalField(**MONEY, null=True, blank=True)
    net_income_ytd = models.DecimalField(**MONEY, null=True, blank=True)
    working_capital = models.DecimalField(**MONEY, null=True, blank=True)  # true NWC: current assets - current liabilities
    # cash & credit, GL book basis (live only)
    cash_checking = models.DecimalField(**MONEY, null=True, blank=True)
    cash_payroll = models.DecimalField(**MONEY, null=True, blank=True)
    cash_mma = models.DecimalField(**MONEY, null=True, blank=True)
    cash_petty = models.DecimalField(**MONEY, null=True, blank=True)
    credit_line_gl = models.DecimalField(**MONEY, null=True, blank=True)
    inventory_gl = models.DecimalField(**MONEY, null=True, blank=True)
    # P&L
    revenue_mtd = models.DecimalField(**MONEY, null=True, blank=True)
    cogs_mtd = models.DecimalField(**MONEY, null=True, blank=True)
    overhead_mtd = models.DecimalField(**MONEY, null=True, blank=True)
    revenue_prior_month = models.DecimalField(**MONEY, null=True, blank=True)
    cogs_prior_month = models.DecimalField(**MONEY, null=True, blank=True)
    overhead_prior_month = models.DecimalField(**MONEY, null=True, blank=True)
    # WIP / billing position on open jobs — earned basis = the company WIP workbook formula
    # (CV x PTT % complete - billed; net POSITIVE = underbilled). See docs/finance_wip_plan.md.
    wip_overbilled = models.DecimalField(**MONEY, null=True, blank=True)
    wip_underbilled = models.DecimalField(**MONEY, null=True, blank=True)
    wip_net = models.DecimalField(**MONEY, null=True, blank=True)          # under - over; positive = underbilled
    wip_overbilled_cost = models.DecimalField(**MONEY, null=True, blank=True)   # secondary: PTT report cost basis
    wip_underbilled_cost = models.DecimalField(**MONEY, null=True, blank=True)
    prev_business_day = models.DateField(null=True, blank=True)
    revenue_prev_day_cur = models.DecimalField(**MONEY, null=True, blank=True)
    cost_prev_day_cur = models.DecimalField(**MONEY, null=True, blank=True)
    revenue_prev_day_prior = models.DecimalField(**MONEY, null=True, blank=True)
    cost_prev_day_prior = models.DecimalField(**MONEY, null=True, blank=True)
    detail = models.JSONField(default=dict, blank=True)  # {"matrix": {book: {bucket: amt}}, "pnl_days": [...]}
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-snapshot_date"]


class BankFigureEntry(models.Model):
    """Manually entered bank-portal figures (not available in SL): one row per entry date.
    Purely optional; the page shows GL book balances regardless."""

    entry_date = models.DateField(unique=True)
    checking_bank_balance = models.DecimalField(**MONEY, null=True, blank=True)
    float_amount = models.DecimalField(**MONEY, null=True, blank=True)
    borrowing_base_available = models.DecimalField(**MONEY, null=True, blank=True)
    credit_line_used = models.DecimalField(**MONEY, null=True, blank=True)
    payroll_note = models.TextField(blank=True)
    entered_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-entry_date"]


class DivisionOverheadShare(models.Model):
    """Share of the corporate overhead pool (GL subaccount 0000, accounts 6xxxx/7xxxx excluding
    60000/60005) charged to each division in the P&L-by-year view (docs/07_pnl_and_wip.md).

    Seeded from the accountants' own allocation — the "000" pivot columns of the division tabs in
    the Income Statement Tracker (FY2024) and the Jan-Dec 2025 PL Full workbook. Editable at
    /finance/allocations/. share = fraction of the pool (0..1); a fiscal year with no rows
    inherits the latest earlier year that has rows, so future years follow the newest allocation
    until Owner changes it. Shares need not sum to 1 — the workbooks themselves leave 7-13% of the
    pool unallocated at corporate.
    """

    fiscal_year = models.CharField(max_length=4)
    division_code = models.CharField(max_length=3)
    share = models.DecimalField(max_digits=8, decimal_places=6, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["fiscal_year", "division_code"], name="uniq_overhead_share")]
        ordering = ["fiscal_year", "division_code"]


class ARPayment(models.Model):
    """AR payment documents (ARDoc type PA), full history since AR_HISTORY_FIRST_YEAR (2013) — full replace each finance refresh.
    Wire batches share one RefNbr across customers, so identity = (ref_nbr, customer_id_raw).
    Powers /finance/payments/ (the daily 'Payment Applications' email, SL report 08820)."""

    ref_nbr = models.CharField(max_length=10)
    customer = models.ForeignKey("core.Customer", null=True, blank=True, on_delete=models.SET_NULL)
    customer_id_raw = models.CharField(max_length=15, blank=True)
    customer_name = models.CharField(max_length=64, blank=True)
    doc_date = models.DateField(null=True, blank=True)
    batch_nbr = models.CharField(max_length=10, blank=True)
    orig_amt = models.DecimalField(**MONEY, default=0)
    balance = models.DecimalField(**MONEY, default=0)
    per_post = models.CharField(max_length=6, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    doc_desc = models.CharField(max_length=32, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["doc_date"]), models.Index(fields=["ref_nbr", "customer_id_raw"])]


class ARPaymentApplication(models.Model):
    """One payment->invoice application (ARAdjust, adjusting doc = PA), full history since AR_HISTORY_FIRST_YEAR (2013),
    full replace. date_appl is the report day of the daily 'Payment Applications' email; the payment's own date is on
    ARPayment (ref_nbr, customer_id_raw)."""

    payment_ref = models.CharField(max_length=10)
    customer = models.ForeignKey("core.Customer", null=True, blank=True, on_delete=models.SET_NULL)
    customer_id_raw = models.CharField(max_length=15, blank=True)
    customer_name = models.CharField(max_length=64, blank=True)
    date_appl = models.DateField(null=True, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    batch_nbr = models.CharField(max_length=10, blank=True)
    per_appl = models.CharField(max_length=6, blank=True)
    applied = models.DecimalField(**MONEY, default=0)
    discount = models.DecimalField(**MONEY, default=0)
    invoice_ref = models.CharField(max_length=10, blank=True)
    invoice_type = models.CharField(max_length=2, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    terms = models.CharField(max_length=4, blank=True)
    invoice_amt = models.DecimalField(**MONEY, null=True, blank=True)
    invoice_balance = models.DecimalField(**MONEY, null=True, blank=True)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    project_id_raw = models.CharField(max_length=16, blank=True)
    invoice_desc = models.CharField(max_length=32, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["date_appl"]), models.Index(fields=["payment_ref", "customer_id_raw"])]


class BankStatement(models.Model):
    """One monthly bank statement PDF (dropped in settings.BANK_STATEMENTS_DIR), parsed locally and
    reconciled against SL's cash GL account. The statement is the only input in the app that is
    neither PTT nor SL; SL is read (guarded queries) and never written. `result` holds the whole
    reconciliation (bridge, flags, outstanding checks, payroll compare, unmatched lists) so the page
    renders from local data only."""

    gl_account = models.CharField(max_length=10, default="10250")
    bank_account = models.CharField(max_length=32, blank=True)   # as printed, e.g. 000-000-000-0
    account_title = models.CharField(max_length=64, blank=True)  # e.g. OPERATING ACCOUNT
    bank_name = models.CharField(max_length=64, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    file_name = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64, unique=True)
    previous_balance = models.DecimalField(**MONEY, default=0)
    ending_balance = models.DecimalField(**MONEY, default=0)
    deposits_count = models.PositiveIntegerField(default=0)
    deposits_total = models.DecimalField(**MONEY, default=0)
    withdrawals_count = models.PositiveIntegerField(default=0)
    withdrawals_total = models.DecimalField(**MONEY, default=0)
    checks_count = models.PositiveIntegerField(default=0)
    checks_total = models.DecimalField(**MONEY, default=0)
    parse_ok = models.BooleanField(default=False)
    parse_notes = models.JSONField(default=list, blank=True)
    imported_at = models.DateTimeField(auto_now=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)
    book_balance_prev = models.DecimalField(**MONEY, null=True, blank=True)   # AcctHist at prior period end
    book_balance = models.DecimalField(**MONEY, null=True, blank=True)        # AcctHist at period end
    residual = models.DecimalField(**MONEY, null=True, blank=True)            # adjusted bank - book
    result = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-period_end", "gl_account"]
        constraints = [models.UniqueConstraint(fields=["gl_account", "period_end"], name="uniq_bank_statement_period")]

    def __str__(self):
        return "%s %s..%s" % (self.gl_account, self.period_start, self.period_end)


class BankStatementLine(models.Model):
    """One statement line: deposit, withdrawal (ACH / wire / sweep / fee / payroll), cleared check,
    or a check the bank reversed. Filled in by the reconciliation with what it matched in SL."""

    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name="lines")
    line_no = models.PositiveIntegerField()
    posted_date = models.DateField()
    kind = models.CharField(max_length=16)          # dep | wd | check | check_reversal
    bucket = models.CharField(max_length=16, blank=True)  # deposit, ach_in, wire_in, loan_draw, ach, ach_batch, wire, sweep, fee, payroll, check, check_reversal
    description = models.CharField(max_length=160, blank=True)
    amount = models.DecimalField(**MONEY)
    check_number = models.CharField(max_length=16, blank=True)   # as printed by the bank
    sl_ref = models.CharField(max_length=16, blank=True)         # SL check RefNbr once matched
    payee = models.CharField(max_length=64, blank=True)
    match_kind = models.CharField(max_length=64, blank=True)     # '' = unmatched
    match_detail = models.JSONField(default=list, blank=True)    # [{dt, m, ref, descr, amt}, ...]

    class Meta:
        ordering = ["statement", "line_no"]
        indexes = [models.Index(fields=["statement", "kind"])]


class ARInvoice(models.Model):
    """AR invoices / credit memos / debit memos issued — full history since AR_HISTORY_FIRST_YEAR (2013), full replace
    each finance refresh — regardless of open/paid (the open-docs table can't see same-week-paid invoices). Powers the
    Project Snapshot's 'billing out' section (docs/project_snapshot_spec.md D3) and, with ARPaymentApplication, the
    customer page's payment history (docs/06 "Customer payment history")."""

    ref_nbr = models.CharField(max_length=10)
    doc_type = models.CharField(max_length=2)
    customer = models.ForeignKey("core.Customer", null=True, blank=True, on_delete=models.SET_NULL)
    customer_id_raw = models.CharField(max_length=15, blank=True)
    customer_name = models.CharField(max_length=64, blank=True)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    project_id_raw = models.CharField(max_length=16, blank=True)
    order_nbr = models.CharField(max_length=15, blank=True)
    doc_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    terms = models.CharField(max_length=4, blank=True)
    cust_po = models.CharField(max_length=32, blank=True)
    amount = models.DecimalField(**MONEY, default=0)
    balance = models.DecimalField(**MONEY, default=0)
    doc_desc = models.CharField(max_length=32, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    # Billings page (2026-09-08, docs/billings_page_plan.md): who keyed the document, when and on which screen
    # (crtd_prog 40690 = sales-order invoicing, 08010 = AR Invoice & Memo entry), the fiscal period it posted into,
    # the sales-order type behind it, and the line roll-up. The roll-up is SIGNED (negative on a credit memo) — unlike
    # amount / balance, which keep SL's positive-on-CM convention for the AR page and the Project Snapshot — and is
    # filled only for documents inside the lines window (finance_loaders.BILLING_LINES_WINDOW_DAYS); older rows hold NULL.
    task_id = models.CharField(max_length=32, blank=True, default="")
    so_type = models.CharField(max_length=8, blank=True, default="")
    per_post = models.CharField(max_length=6, blank=True, default="")
    batch_nbr = models.CharField(max_length=10, blank=True, default="")
    slsper_id = models.CharField(max_length=10, blank=True, default="")
    crtd_user = models.CharField(max_length=16, blank=True, default="")
    crtd_prog = models.CharField(max_length=8, blank=True, default="")
    lupd_user = models.CharField(max_length=16, blank=True, default="")
    lupd_at = models.DateTimeField(null=True, blank=True)
    revenue = models.DecimalField(**MONEY, null=True, blank=True)     # Σ lines on income (3I) accounts — the GL revenue this document posted
    tax = models.DecimalField(**MONEY, null=True, blank=True)         # Σ 205xx sales-tax lines
    deposits = models.DecimalField(**MONEY, null=True, blank=True)    # Σ 21000 / 24000 customer-deposit lines (billed ahead of service — not revenue)
    other = models.DecimalField(**MONEY, null=True, blank=True)       # everything else (bank charges, rebates …)
    division = models.CharField(max_length=3, blank=True, default="")  # the job's division; SO1 hardware -> 010; else the largest revenue line's subaccount prefix
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["doc_date"]), models.Index(fields=["project"]), models.Index(fields=["customer_id_raw", "doc_date"]),
                   models.Index(fields=["sl_created_at"]), models.Index(fields=["per_post"])]


class ARInvoiceLine(models.Model):
    """Lines of the AR documents issued in the trailing BILLING_LINES_WINDOW_DAYS (ARTran without the 11000 AR-control
    line), full replace each finance refresh. `amount` is SIGNED (negated on a credit memo); `kind` = rev (income account)
    / tax (205xx) / dep (21000 / 24000 customer deposits) / oth. The Billings page's detail panel ("what was billed",
    with the GL account + subaccount = division each line posted to) and the per-document roll-up on ARInvoice."""

    ref_nbr = models.CharField(max_length=10, db_index=True)
    doc_type = models.CharField(max_length=2)
    customer_id_raw = models.CharField(max_length=15, blank=True)
    line_nbr = models.IntegerField(default=0)
    kind = models.CharField(max_length=3, default="oth")
    gl_account = models.CharField(max_length=10, blank=True)
    gl_subaccount = models.CharField(max_length=24, blank=True)
    tran_desc = models.CharField(max_length=30, blank=True)
    project_id_raw = models.CharField(max_length=16, blank=True)
    task_id = models.CharField(max_length=32, blank=True)
    invt_id = models.CharField(max_length=30, blank=True)
    qty = models.DecimalField(**HOURS, null=True, blank=True)
    unit_price = models.DecimalField(**MONEY, null=True, blank=True)
    amount = models.DecimalField(**MONEY, default=0)
    tran_date = models.DateField(null=True, blank=True)
    shipper_id = models.CharField(max_length=15, blank=True, default="")
    order_nbr = models.CharField(max_length=15, blank=True, default="")
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)


# A payment document's APDoc.Acct is the cash account it was drawn on. Pace's checking account is 10250;
# 10450 "AMEX HOLDING ACCOUNT" is the clearing account for every payable settled with a company credit card
# (Amex or Capital One — the payment itself does not say which card): the vendor's vouchers are "paid" against
# 10450 and a voucher to the card issuer for the same amount takes their place, so no cash leaves the bank
# until the card bill is paid. Found 2026-09-03 (ADI: 47 of 47 payments in 12 months). docs/06 Vendors.
CARD_CLEARING_ACCOUNTS = ("10450",)


def settled_by_card(cash_acct):
    """True when an AP payment was drawn on a credit-card holding account rather than a bank account."""
    return (cash_acct or "").strip() in CARD_CLEARING_ACCOUNTS


class APCheck(models.Model):
    """AP payments cut (CK/HC/EP + VC voids), full history since 2013, full replace. Powers the Project
    Snapshot's 'AP paid out' section (spec D4) and the Vendor pages. cash_acct = the cash or holding account the
    payment was drawn on (settled_by_card)."""

    ref_nbr = models.CharField(max_length=10)
    doc_type = models.CharField(max_length=2)
    vendor_id = models.CharField(max_length=15, blank=True)
    vendor_name = models.CharField(max_length=64, blank=True)
    doc_date = models.DateField(null=True, blank=True)
    amount = models.DecimalField(**MONEY, default=0)
    doc_desc = models.CharField(max_length=32, blank=True)
    cash_acct = models.CharField(max_length=10, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["doc_date"])]

    @property
    def settled_by_card(self):
        return settled_by_card(self.cash_acct)


class SLVendor(models.Model):
    """Vendor master, full replace each finance refresh. Powers /finance/vendors/."""

    vendor_id = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=2, blank=True)
    terms = models.CharField(max_length=4, blank=True)
    class_id = models.CharField(max_length=12, blank=True)
    city = models.CharField(max_length=32, blank=True)
    state = models.CharField(max_length=4, blank=True)
    phone = models.CharField(max_length=24, blank=True)
    email = models.CharField(max_length=64, blank=True)
    pmt_method = models.CharField(max_length=2, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    def __str__(self):
        return "%s %s" % (self.vendor_id, self.name)


class APCheckApplication(models.Model):
    """What each AP payment paid (APAdjust + the voucher's APDoc), trailing window, full
    replace. invoice_date -> check_date is how long Pace held the vendor's invoice."""

    check_ref = models.CharField(max_length=10)
    check_type = models.CharField(max_length=2)
    check_date = models.DateField(null=True, blank=True)
    date_appl = models.DateField(null=True, blank=True)
    vendor_id = models.CharField(max_length=15, blank=True)
    adj_amount = models.DecimalField(**MONEY, default=0)
    disc_amount = models.DecimalField(**MONEY, default=0)
    voucher_ref = models.CharField(max_length=10, blank=True)
    voucher_type = models.CharField(max_length=2, blank=True)
    voucher_date = models.DateField(null=True, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    invoice_nbr = models.CharField(max_length=40, blank=True)
    doc_desc = models.CharField(max_length=32, blank=True)
    voucher_amount = models.DecimalField(**MONEY, null=True, blank=True)
    po_nbr = models.CharField(max_length=10, blank=True)
    terms = models.CharField(max_length=4, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["check_ref"]), models.Index(fields=["vendor_id"]),
                   models.Index(fields=["check_date"])]


class POLine(models.Model):
    """Purchase-order lines for POs dated in the trailing window (PurchOrd + PurOrdDet), full
    replace. 'Material ordered' with line-item detail (spec D5)."""

    po_nbr = models.CharField(max_length=10)
    po_date = models.DateField(null=True, blank=True)
    vendor_id = models.CharField(max_length=15, blank=True)
    vendor_name = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=2, blank=True)
    buyer = models.CharField(max_length=16, blank=True)
    line_ref = models.CharField(max_length=8, blank=True)
    item_id = models.CharField(max_length=32, blank=True)
    descr = models.CharField(max_length=64, blank=True)
    qty_ord = models.DecimalField(**HOURS, null=True, blank=True)
    qty_rcvd = models.DecimalField(**HOURS, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    ext_cost = models.DecimalField(**MONEY, default=0)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    project_id_raw = models.CharField(max_length=16, blank=True)
    task_id = models.CharField(max_length=32, blank=True)
    site_id = models.CharField(max_length=10, blank=True)
    # Materials section (2026-09-03): order status + who entered the PO. Buyer is usually blank at Pace, so the
    # SL login that created the PO (PurchOrd.Crtd_User) is the "ordered by". PO status: O open, P printed/placed
    # (nothing received), M fully received ("matched"), X cancelled. Header fields repeat on every line.
    crtd_user = models.CharField(max_length=16, blank=True, default="")
    po_type = models.CharField(max_length=4, blank=True, default="")             # OR regular · DP drop-ship · BL blanket
    purchase_type = models.CharField(max_length=4, blank=True, default="")       # PurOrdDet.PurchaseType (PS project stock, GP goods for project …)
    prom_date = models.DateField(null=True, blank=True)                          # vendor promise date
    reqd_date = models.DateField(null=True, blank=True)
    open_line = models.BooleanField(default=False)
    qty_vouched = models.DecimalField(**HOURS, null=True, blank=True)
    cost_vouched = models.DecimalField(**MONEY, null=True, blank=True)
    po_freight = models.DecimalField(**MONEY, null=True, blank=True)             # PurchOrd.Freight (header)
    last_rcpt_date = models.DateField(null=True, blank=True)                     # PurchOrd.LastRcptDate (header)
    ship_via = models.CharField(max_length=16, blank=True, default="")
    # Deduced tie for "stock" lines (blank ProjectID in SL): filled by the loader from
    # sales-order demand for the same item (docs/02 SS3b flow; validated 2026-08-28 at 94.8%
    # agreement, 98.8% by dollars, on lines that DO carry a project). basis: 'so' = single
    # project in demand; 'so_qty' = exact-qty tiebreak; 'so_cust' = customer resale order, no
    # SL project. Always labeled as deduced in the UI -- never presented as SL fact.
    deduced_project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    deduced_so = models.CharField(max_length=15, null=True, blank=True)
    deduced_customer = models.CharField(max_length=64, null=True, blank=True)
    deduce_basis = models.CharField(max_length=10, null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["po_date"]), models.Index(fields=["project"]), models.Index(fields=["po_nbr"])]


class POReceiptLine(models.Model):
    """PO receipt lines (POTran type R) in the trailing window, full replace. 'Material received'
    with line-item detail (spec D5)."""

    rcpt_nbr = models.CharField(max_length=10)
    rcpt_date = models.DateField(null=True, blank=True)
    po_nbr = models.CharField(max_length=10, blank=True)
    vendor_id = models.CharField(max_length=15, blank=True)
    vendor_name = models.CharField(max_length=64, blank=True)
    item_id = models.CharField(max_length=32, blank=True)
    descr = models.CharField(max_length=64, blank=True)
    qty = models.DecimalField(**HOURS, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    ext_cost = models.DecimalField(**MONEY, default=0)
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    project_id_raw = models.CharField(max_length=16, blank=True)
    task_id = models.CharField(max_length=32, blank=True)
    crtd_user = models.CharField(max_length=16, blank=True, default="")          # warehouse login that received it
    po_line_ref = models.CharField(max_length=8, blank=True, default="")
    qty_vouched = models.DecimalField(**HOURS, null=True, blank=True)
    vend_invc_nbr = models.CharField(max_length=40, blank=True, default="")      # vendor packing slip / invoice on the receipt
    # Deduced tie for "stock" lines (blank ProjectID in SL): filled by the loader from
    # sales-order demand for the same item (docs/02 SS3b flow; validated 2026-08-28 at 94.8%
    # agreement, 98.8% by dollars, on lines that DO carry a project). basis: 'so' = single
    # project in demand; 'so_qty' = exact-qty tiebreak; 'so_cust' = customer resale order, no
    # SL project. Always labeled as deduced in the UI -- never presented as SL fact.
    deduced_project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    deduced_so = models.CharField(max_length=15, null=True, blank=True)
    deduced_customer = models.CharField(max_length=64, null=True, blank=True)
    deduce_basis = models.CharField(max_length=10, null=True, blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["rcpt_date"]), models.Index(fields=["project"]), models.Index(fields=["po_nbr"])]


class ProjectSalesOrderLine(models.Model):
    """SL Order Management lines that name a project (SOLine.ProjectID) — the warehouse → job leg of Pace's material
    path (docs/02 §3b): what was ordered for the job from stock, how much has shipped or is back-ordered, at what
    cost and sale price, and the ChannelOnline document (SOHeader.User2) the order came from. Trailing
    MATERIALS_DAYS_BACK window, full replace each refresh_all. Returns (RM*) and credits carry negative quantities."""

    so_nbr = models.CharField(max_length=15)
    line_ref = models.CharField(max_length=8, blank=True, default="")
    ord_date = models.DateField(null=True, blank=True)
    so_type = models.CharField(max_length=6, blank=True, default="")             # SO2, RM1, CM …
    behavior = models.CharField(max_length=6, blank=True, default="")            # SOType.Behavior: SO, RMA, CM, Q …
    status = models.CharField(max_length=2, blank=True, default="")              # header: O open · C completed · X cancelled
    line_status = models.CharField(max_length=2, blank=True, default="")
    cnet_quote = models.CharField(max_length=16, blank=True, default="", db_index=True)   # SOHeader.User2 = ChannelOnline document number
    crtd_user = models.CharField(max_length=16, blank=True, default="")          # SYSADMIN = imported from ChannelOnline
    slsper_id = models.CharField(max_length=10, blank=True, default="")
    cust_ord_nbr = models.CharField(max_length=48, blank=True, default="")
    ship_name = models.CharField(max_length=64, blank=True, default="")
    tot_frt = models.DecimalField(**MONEY, null=True, blank=True)                # header freight charged to the customer
    item_id = models.CharField(max_length=32, blank=True, default="")
    descr = models.CharField(max_length=64, blank=True, default="")
    qty_ord = models.DecimalField(**HOURS, null=True, blank=True)
    qty_ship = models.DecimalField(**HOURS, null=True, blank=True)
    qty_bo = models.DecimalField(**HOURS, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    tot_cost = models.DecimalField(**MONEY, null=True, blank=True)
    sls_price = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    tot_ord = models.DecimalField(**MONEY, null=True, blank=True)
    task_id = models.CharField(max_length=32, blank=True, default="")
    site_id = models.CharField(max_length=10, blank=True, default="")
    prom_date = models.DateField(null=True, blank=True)
    req_date = models.DateField(null=True, blank=True)
    drop_ship = models.BooleanField(default=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="material_so_lines")
    project_id_raw = models.CharField(max_length=16, blank=True, default="")
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["project"]), models.Index(fields=["so_nbr"])]


class ProjectShipmentLine(models.Model):
    """Shipper lines that name a project (SOShipLine.ProjectID): the stock actually sent from the warehouse to the
    job — ship date, who shipped it, the AR invoice it produced (which is what posts MATERIALS OM/IN to the project),
    freight cost and freight billed. Same window/replace as ProjectSalesOrderLine."""

    shipper_id = models.CharField(max_length=15)
    ship_date = models.DateField(null=True, blank=True)                          # SOShipHeader.ShipDateAct (None until shipped)
    ship_date_plan = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=2, blank=True, default="")
    so_nbr = models.CharField(max_length=15, blank=True, default="")
    invc_nbr = models.CharField(max_length=15, blank=True, default="")
    invc_date = models.DateField(null=True, blank=True)
    crtd_user = models.CharField(max_length=16, blank=True, default="")
    ship_via = models.CharField(max_length=16, blank=True, default="")
    tracking_nbr = models.CharField(max_length=40, blank=True, default="")
    tot_frt_cost = models.DecimalField(**MONEY, null=True, blank=True)           # header, repeated per line
    tot_frt_invc = models.DecimalField(**MONEY, null=True, blank=True)
    line_ref = models.CharField(max_length=8, blank=True, default="")
    ord_line_ref = models.CharField(max_length=8, blank=True, default="")
    item_id = models.CharField(max_length=32, blank=True, default="")
    descr = models.CharField(max_length=64, blank=True, default="")
    qty_ship = models.DecimalField(**HOURS, null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    tot_cost = models.DecimalField(**MONEY, null=True, blank=True)
    sls_price = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    tot_invc = models.DecimalField(**MONEY, null=True, blank=True)
    task_id = models.CharField(max_length=32, blank=True, default="")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="material_shipments")
    project_id_raw = models.CharField(max_length=16, blank=True, default="")
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["project"]), models.Index(fields=["so_nbr"])]


class POVoucher(models.Model):
    """AP documents that reference a purchase order (APDoc.PONbr): what the vendor billed against the PO and the
    freight it charged (FreightAmt). Joined to the project's PO lines by po_nbr for the Materials section's extra
    costs. doc_type VO voucher (+), AD adjustment/credit (−). Same window/replace as ProjectSalesOrderLine."""

    ref_nbr = models.CharField(max_length=10)
    doc_type = models.CharField(max_length=2, blank=True, default="")
    doc_date = models.DateField(null=True, blank=True)
    vendor_id = models.CharField(max_length=15, blank=True, default="")
    vendor_name = models.CharField(max_length=64, blank=True, default="")
    amount = models.DecimalField(**MONEY, default=0)
    freight_amt = models.DecimalField(**MONEY, default=0)
    po_nbr = models.CharField(max_length=10, db_index=True)
    status = models.CharField(max_length=2, blank=True, default="")
    crtd_user = models.CharField(max_length=16, blank=True, default="")
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)


class ProjectChangeEvent(models.Model):
    """Change-order / budget-change history per project, rebuilt every refresh from three sources
    (apps/analytics/change_orders.py). SL keeps no change-order table, but:
      task_added     Pace records change orders as NEW SL TASKS (CO1, AVCO5, COR15 ...): the task's PJPENT
                     creation stamp is the date/user, its current budgets the amounts (exact if never edited since).
      local_history  the app's hash-diffed PJPTDSUM copy: exact before -> after for every budget edit since
                     local history began (2026-08-17), with SL's edit stamp and user.
      sl_last_edit   for in-place edits before that: SL's lupd stamp on the budget row says when and who,
                     but the prior value is gone (confidence 'unknown_prior').
    One row per (event, field); the page groups rows that share a date, task and user."""

    class Kind(models.TextChoices):
        CHANGE_ORDER = "change_order", "Change order"
        SCOPE_ADDED = "scope_added", "Task / scope added"
        BUDGET_EDITED = "budget_edited", "Budget edited in place"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="change_events")
    task_id = models.CharField(max_length=32, blank=True)
    task_description = models.CharField(max_length=60, blank=True)
    event_at = models.DateTimeField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    field = models.CharField(max_length=32)            # contract_value | budget_direct_cost | budget_labor_hours | budget_<category>
    prior_value = models.DecimalField(**MONEY, null=True, blank=True)
    new_value = models.DecimalField(**MONEY, null=True, blank=True)
    delta = models.DecimalField(**MONEY, null=True, blank=True)
    entered_by = models.CharField(max_length=10, blank=True)
    source_prog = models.CharField(max_length=8, blank=True)
    source = models.CharField(max_length=16)           # task_added | local_history | sl_last_edit
    confidence = models.CharField(max_length=16)       # exact | approx | unknown_prior
    note = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["project", "event_at", "task_id", "field"]
        indexes = [models.Index(fields=["project", "event_at"])]


class ContractValueAdjustment(models.Model):
    """The contract-value log (docs/contract_value_reconciliation_plan.md): one row every time a project's basis or
    value changes — a rule firing, a person overriding, or the value reverting to SL. The active row per project has
    superseded_at NULL; a basis of 'sl' on the active row records a reversal. Never edited, only superseded."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="contract_value_adjustments")
    effective_from = models.DateField()
    basis = models.CharField(max_length=32)
    sl_value = models.DecimalField(**MONEY, null=True, blank=True)
    effective_value = models.DecimalField(**MONEY, null=True, blank=True)
    evidence = models.JSONField(null=True, blank=True)
    explanation = models.TextField(blank=True)
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField()
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["project", "superseded_at"]), models.Index(fields=["effective_from"])]
        ordering = ["-created_at"]


class ContractValueOverride(models.Model):
    """A person's decision about one project's contract value (finance.write). value = use this figure everywhere;
    confirm_sl = SL is right, stop flagging it. One row per project; replaced, not appended (the adjustment log keeps history)."""

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="contract_value_override")
    value = models.DecimalField(**MONEY, null=True, blank=True)
    confirm_sl = models.BooleanField(default=False)
    reason = models.CharField(max_length=300)
    set_by = models.CharField(max_length=120, blank=True)
    set_at = models.DateTimeField()


class SalesTaxRate(models.Model):
    """The SL sales-tax master (SalesTax): ZIP-based tax IDs (state code + ZIP, description = state code + locality,
    combined rate %), loaded nationwide 2022-07-19 and refreshed 2025-10-14, plus the hand-made TAX2 ("ILLINOIS SALES TAX",
    7.75 %, Pace's flat Illinois rate since 2016) and NONE. Full replace each finance refresh (~41k rows). Sales Tax page
    (superadmin only): the rate applied on a shipment is checked against this master."""

    tax_id = models.CharField(max_length=10, unique=True)
    state = models.CharField(max_length=2, blank=True, default="", db_index=True)
    descr = models.CharField(max_length=32, blank=True)
    rate = models.DecimalField(max_digits=9, decimal_places=4, default=0)
    tax_type = models.CharField(max_length=2, blank=True)
    gl_account = models.CharField(max_length=10, blank=True)
    gl_subaccount = models.CharField(max_length=24, blank=True)
    old_rate = models.DecimalField(max_digits=9, decimal_places=4, null=True, blank=True)
    new_rate = models.DecimalField(max_digits=9, decimal_places=4, null=True, blank=True)
    new_rate_date = models.DateField(null=True, blank=True)
    revised_date = models.DateField(null=True, blank=True)
    sl_created_at = models.DateTimeField(null=True, blank=True)
    sl_updated_at = models.DateTimeField(null=True, blank=True)
    sl_updated_by = models.CharField(max_length=16, blank=True, default="")
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)


class SalesTaxPosting(models.Model):
    """Every posted GL line on 20500 ACCRUED SALES TAX since 2012 (GLTran, ~6.8k rows), classified at load by
    apps/analytics/salestax_parse.py: `kind` = collected (tax charged on an invoice, credit; a credit memo is a negative
    collection) · remitted (a payment to a state — general-journal debits such as "TN SALES TAX MAR", AP vouchers to state
    agencies; a reversal is negative) · fee (return fees / penalties debited to the liability) · reclass (moves inside 20500
    that net to zero within their batch) · adjustment (everything else: accruals of missed tax, corrections, year-end
    true-ups such as the 2024-12-31 "Accrual Adj" that moved $270K to Sales) · zero. `amount` = credit − debit (positive
    raises the liability). `state` from the shipper's ZIP-based tax ID, else the posting description, else the ship-to or
    customer state (`state_source` says which); remittances carry the period they cover (`period_covered`, YYYYMM; parsed
    from the description, else the month before the payment with `period_inferred`). Full replace each finance refresh.
    Sales Tax page (superadmin only)."""

    tran_date = models.DateField(null=True, blank=True, db_index=True)
    per_post = models.CharField(max_length=6, blank=True, db_index=True)
    module = models.CharField(max_length=2, blank=True)
    jrnl_type = models.CharField(max_length=3, blank=True)
    batch_nbr = models.CharField(max_length=10, blank=True)
    ref_nbr = models.CharField(max_length=10, blank=True, db_index=True)
    gl_subaccount = models.CharField(max_length=24, blank=True)
    tran_desc = models.CharField(max_length=30, blank=True)
    dr_amt = models.DecimalField(**MONEY, default=0)
    cr_amt = models.DecimalField(**MONEY, default=0)
    amount = models.DecimalField(**MONEY, default=0)                      # credit − debit: + raises the liability
    crtd_user = models.CharField(max_length=16, blank=True, default="")
    sl_created_at = models.DateTimeField(null=True, blank=True)
    kind = models.CharField(max_length=12, default="adjustment", db_index=True)
    state = models.CharField(max_length=2, blank=True, default="", db_index=True)
    state_source = models.CharField(max_length=24, blank=True, default="")
    period_covered = models.CharField(max_length=6, blank=True, default="")
    period_inferred = models.BooleanField(default=False)
    note = models.CharField(max_length=64, blank=True, default="")
    # the invoice behind an AR-module line
    doc_type = models.CharField(max_length=2, blank=True, default="")
    customer_id_raw = models.CharField(max_length=15, blank=True, default="")
    customer = models.ForeignKey("core.Customer", null=True, blank=True, on_delete=models.SET_NULL)
    order_nbr = models.CharField(max_length=15, blank=True, default="")
    project_id_raw = models.CharField(max_length=16, blank=True, default="")
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL)
    doc_date = models.DateField(null=True, blank=True)
    doc_amount = models.DecimalField(**MONEY, null=True, blank=True)
    cust_state = models.CharField(max_length=4, blank=True, default="")
    cust_exempt_nbr = models.CharField(max_length=32, blank=True, default="")
    shipper_id = models.CharField(max_length=15, blank=True, default="")
    ship_state = models.CharField(max_length=4, blank=True, default="")
    ship_zip = models.CharField(max_length=12, blank=True, default="")
    ship_city = models.CharField(max_length=32, blank=True, default="")
    tax_id = models.CharField(max_length=10, blank=True, default="")
    tax_rate = models.DecimalField(max_digits=9, decimal_places=4, null=True, blank=True)   # the rate applied on the shipment
    taxable = models.DecimalField(**MONEY, null=True, blank=True)                            # the shipment's taxable base
    ship_tax = models.DecimalField(**MONEY, null=True, blank=True)                           # the shipment's computed tax
    ingestion_run = models.ForeignKey(IngestionRun, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        indexes = [models.Index(fields=["kind", "state"]), models.Index(fields=["state", "per_post"])]


class BankUpload(models.Model):
    """Original PDFs survive parser failures and can be viewed by finance readers.

    Storage is not publicly served. Keeping upload identity separate from parsed
    statements also preserves prior versions when a corrected statement arrives.
    """
    sha256 = models.CharField(max_length=64, unique=True)
    storage_name = models.CharField(max_length=80)
    original_name = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey("access.Account", null=True, on_delete=models.SET_NULL)
    statement = models.ForeignKey(BankStatement, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploads")
    parse_error = models.TextField(blank=True)
    class Meta: ordering = ["-uploaded_at"]
