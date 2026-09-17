"""Access-suite fixtures (Access Spec v1 §13): one account per principal + SENTINEL data.

Sentinels are values that exist nowhere else, seeded once and asserted ABSENT from every rendered page
(HTML + embedded JSON) for every principal that must not see them:
  RATING_NAME / RATING_RENDER      — superadmin only, everywhere
  SALARIED_WAGE_RENDER             — superadmin only (salaried pay), everywhere
  FIELD_WAGE_RENDER                — requires rates.field.view
  GP_RENDER                        — requires margins.view
  AR_RENDER                        — requires finance.view (open AR balance on the customer page)
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone

from apps.access.models import Account, RoleAssignment
from apps.analytics.models import ProjectPrediction
from apps.core.models import Customer, Division, Employee, Project, ProjectRoleAssignment
from apps.finance.models import AROpenDocument, EmployeeLaborRateObservation, ProjectFinancialTransaction
from apps.ingestion.models import IngestionRun
from apps.operations.models import TimeEntry

RATING_NAME = "ZZRATSENTINEL"
RATING_EFFECT = Decimal("0.987654")        # renders ~ "+98.8 pts" / "+987.7"
RATING_RENDERS = ("ZZRATSENTINEL", "+98.8", "+987.7", "98.77")
SALARIED_WAGE = Decimal("111222.33")       # renders "111,222"
SALARIED_WAGE_RENDER = "111,222"
FIELD_WAGE = Decimal("87654.32")           # renders "87,654"
FIELD_WAGE_RENDER = "87,654"
GP_DOLLARS = Decimal("7654321.00")         # renders "7,654,321"
GP_RENDER = "7,654,321"
AR_BALANCE = Decimal("3141592.00")         # renders "3,141,592" (customer page: Outstanding invoices card)
AR_RENDER = "3,141,592"

PRINCIPALS = ["superadmin", "executive", "dm070", "finance", "pm", "estimator", "hradmin", "norole", "disabled", "sales", "fieldworker"]


def build():
    out = {}
    run = IngestionRun.objects.create(source_system="local", trigger="manual", status="succeeded", started_at=timezone.now(), finished_at=timezone.now())
    d070 = Division.objects.create(code="070", name="Premise Security", active=True, modelled=True)
    d020 = Division.objects.create(code="020", name="IT", active=True)
    cust = Customer.objects.create(sl_customer_id="TST001", canonical_name="Test Customer")
    field_emp = Employee.objects.create(employee_key="FLD001", canonical_name="Frank Field", ptt_employee_type="union",
                                        ptt_employee_role="regular", union_code="134A", is_field_hourly=True, active=True,
                                        ptt_base_wage=Decimal("87.65"), ptt_person_id=90001)
    sal_emp = Employee.objects.create(employee_key="SAL001", canonical_name="Sally Salaried", ptt_employee_type="non_union",
                                      ptt_employee_role="pm", is_field_hourly=False, active=True, ptt_person_id=90002)
    proj = Project.objects.create(
        canonical_project_number="990001", display_number="990001", division=d070, customer=cust, title="SENTINEL TEST PROJECT",
        lifecycle_state="in_progress", project_mode_rule="installation", project_manager=sal_emp,
        contract_value=Decimal("9000000"), billed_revenue=Decimal("8000000"), actual_direct_cost=Decimal("345679.00"),
        actual_gp_dollars=GP_DOLLARS, actual_gp_percent=Decimal("0.5"), sold_gp_dollars=GP_DOLLARS, sold_gp_percent=Decimal("0.5"),
        budget_direct_cost=Decimal("400000"), actual_labor=Decimal("200000"), actual_labor_wage=Decimal("180000"),
        budget_labor_hours=Decimal("1000"), ptt_hours_total=Decimal("900"), actual_labor_hours_sl=Decimal("880"),
        pm_percent_complete=Decimal("0.5"), task_count=1)
    p020 = Project.objects.create(canonical_project_number="990002", display_number="990002", division=d020, customer=cust,
                                  title="OTHER DIVISION PROJECT", lifecycle_state="in_progress", project_mode_rule="installation",
                                  billed_revenue=Decimal("1000"), task_count=1)
    ProjectPrediction.objects.create(project=proj, as_of_date=timezone.localdate(), generated_at=timezone.now(),
                                     eac_direct_cost=Decimal("400000"), eac_revenue=Decimal("9000000"), eac_gp_dollars=GP_DOLLARS,
                                     eac_gp_percent=Decimal("0.5"), risk_score=10, risk_level="low")
    ProjectRoleAssignment.objects.create(project=proj, employee=field_emp, role="field", actual_hours=Decimal("500"),
                                         actual_labor_cost=FIELD_WAGE, share_of_project_hours=Decimal("0.6"),
                                         first_date=date(2026, 1, 5), last_date=date(2026, 8, 1), assignment_method="derived")
    ProjectRoleAssignment.objects.create(project=proj, employee=sal_emp, role="field", actual_hours=Decimal("10"),
                                         actual_labor_cost=SALARIED_WAGE, share_of_project_hours=Decimal("0.01"),
                                         first_date=date(2026, 1, 5), last_date=date(2026, 8, 1), assignment_method="derived")
    for i, (emp, amt) in enumerate([(field_emp, FIELD_WAGE), (sal_emp, SALARIED_WAGE)]):
        ProjectFinancialTransaction.objects.create(
            source_key="TST%05d" % i, project=proj, sl_acct="LABOR", category="labor_wage", system_cd="PA", batch_type="CHRG",
            batch_id="B%d" % i, detail_num=i, fiscal_period="202607", transaction_date=date(2026, 7, 1),
            source_created_at=timezone.now(), amount=amt, units=Decimal("40"), employee=emp, employee_key=emp.employee_key, last_seen_run=run)
    EmployeeLaborRateObservation.objects.create(employee=field_emp, check_date=date(2026, 7, 8), labor_account="LABORUNION",
                                                hours=Decimal("40"), wage_amount=FIELD_WAGE, wage_rate=Decimal("87.65"))
    EmployeeLaborRateObservation.objects.create(employee=sal_emp, check_date=date(2026, 7, 8), labor_account="LABOR",
                                                hours=Decimal("40"), wage_amount=SALARIED_WAGE, wage_rate=Decimal("99.99"))
    TimeEntry.objects.create(source_key=1, project=proj, employee=field_emp, ptt_person_pk=90001,
                             work_date=timezone.localdate() - timedelta(days=10),
                             hours_onsite=Decimal("8"), hours_total=Decimal("8"), source_status=1, form_type=1,
                             submitted_at=timezone.now(), content_hash="x", last_seen_run=run)
    # ---- 010 sales fixtures (010 Sales Spec §7): one converted quote + one SO with full SL econ + GL rows.
    # Values deliberately unlike the wage/GP sentinels; sales pages show cost/margin to every sales010 holder by design.
    from apps.sales.models import (CnetDocument, CnetDocumentLine, CnetLineSerial, Gl010Period,
                                   SlCnetOrder, SlCnetOrderLine, SlCnetShipper)
    so = CnetDocument.objects.create(
        document_id=900100, document_number="S900100", doc_type="sales_order", status="Shipped",
        created_at=timezone.now() - timedelta(days=40), ordered_at=timezone.now() - timedelta(days=39),
        created_by_name="Cathy Creator", ordered_by_name="Cathy Creator",
        customer_sl_id="TST001", customer=cust, customer_company="Test Customer", salesperson_name="Sam Sales",
        total_item_cost=Decimal("4000"), subtotal=Decimal("5000"), tax=Decimal("0"),
        shipping_handling=Decimal("25"), total=Decimal("5025"), line_count=1,
        sl_ord_nbr="0099001", sl_so_type="SO1", sl_status="C", sl_ord_date=date(2026, 7, 12),
        realized_revenue=Decimal("5025"), realized_cost=Decimal("4100"), realized_freight_charged=Decimal("25"),
        invoice_count=1, first_ship_date=date(2026, 7, 20), last_ship_date=date(2026, 7, 20), fully_shipped=True)
    CnetDocument.objects.create(
        document_id=900050, document_number="Q900050", doc_type="quote", status="Ordered",
        created_at=timezone.now() - timedelta(days=45), customer_sl_id="TST001", customer=cust,
        customer_company="Test Customer", salesperson_name="Sam Sales", total=Decimal("5025"),
        total_item_cost=Decimal("4000"), line_count=1, converted_document=so, conversion_method="note_ref",
        converted_at=timezone.now() - timedelta(days=39))
    CnetDocument.objects.create(
        document_id=900051, document_number="Q900051", doc_type="quote", status="Open",
        created_at=timezone.now() - timedelta(days=5), customer_sl_id="TST001", customer=cust,
        customer_company="Test Customer", salesperson_name="Sam Sales", total=Decimal("1200"),
        total_item_cost=Decimal("950"), line_count=1)
    sline = CnetDocumentLine.objects.create(
        document=so, line_item_id=1, line_number=1, line_order=1, manufacturer="TestMfr", part_number="TM-100",
        description="Test widget", unit_cost=Decimal("40"), unit_price=Decimal("50"), qty=Decimal("100"),
        ext_cost=Decimal("4000"), ext_price=Decimal("5000"), sl_line_cost=Decimal("41"), sl_line_matched=True)
    CnetLineSerial.objects.create(line=sline, serial="SER123456")
    SlCnetOrder.objects.create(ord_nbr="0099001", cnet_number="S900100", so_type="SO1", cust_id="TST001",
                               ord_date=date(2026, 7, 12), status="C", tot_ord=Decimal("5025"),
                               tot_merch=Decimal("5000"), tot_frt=Decimal("25"), tot_tax=Decimal("0"))
    SlCnetShipper.objects.create(shipper_id="0099001A", ord_nbr="0099001", ship_date=date(2026, 7, 20),
                                 invc_nbr="INV991", invc_date=date(2026, 7, 21), status="C", tot_invc=Decimal("5025"),
                                 tot_cost=Decimal("4100"), tot_frt=Decimal("25"), tot_merch=Decimal("5000"))
    SlCnetOrderLine.objects.create(ord_nbr="0099001", line_ref="0001", invt_id="TM-100", qty_ord=Decimal("100"),
                                   qty_ship=Decimal("100"), qty_bo=Decimal("0"), unit_cost=Decimal("41"),
                                   tot_cost=Decimal("4100"), sls_price=Decimal("50.25"), tot_ord=Decimal("5025"))
    Gl010Period.objects.create(fiscal_year="2026", period=7, sub="0100", acct="4000", acct_class="revenue", amount=Decimal("5025"))
    Gl010Period.objects.create(fiscal_year="2026", period=7, sub="0100", acct="5000", acct_class="cogs", amount=Decimal("4100"))
    # ---- one open AR invoice for the test customer (finance data on /customers/TST001/ — finance.view only)
    AROpenDocument.objects.create(ref_nbr="900777", doc_type="IN", customer=cust, customer_id_raw="TST001", customer_name="Test Customer",
                                  project=proj, project_id_raw="990001", book="project", doc_date=date.today() - timedelta(days=40),
                                  due_date=date.today() - timedelta(days=10), days_past_due=10, bucket="d30", sign=1, doc_bal=AR_BALANCE,
                                  orig_amt=AR_BALANCE, released=True, terms="30", as_of=timezone.now(), ingestion_run=run)
    out.update(run=run, d070=d070, d020=d020, project=proj, p020=p020, field_emp=field_emp, sal_emp=sal_emp, customer=cust,
               cnet_so=so)

    def mk(email, name, role=None, divisions=None, superadmin=False, status="active", employee=None):
        user = User.objects.create_user(email, email=email); user.set_unusable_password(); user.save()
        a = Account.objects.create(email=email, display_name=name, user=user, is_superadmin=superadmin, status=status, employee=employee)
        if role:
            RoleAssignment.objects.create(account=a, role=role, division_codes=divisions)
        return a
    out["accounts"] = {
        "superadmin": mk("t-sup@t.local", "T Owner", superadmin=True),
        "executive": mk("t-exec@t.local", "T Exec", "executive"),
        "dm070": mk("t-dm@t.local", "T DM070", "division_manager", ["070"]),
        "finance": mk("t-fin@t.local", "T Finance", "finance"),
        "pm": mk("t-pm@t.local", "T PM", "project_manager"),
        "estimator": mk("t-est@t.local", "T Estimator", "estimator"),
        "hradmin": mk("t-hr@t.local", "T HRAdmin", "permission_admin"),
        "norole": mk("t-none@t.local", "T NoRole"),
        "disabled": mk("t-dis@t.local", "T Disabled", "executive", status="disabled"),
        "sales": mk("t-sales@t.local", "T Sales", "sales"),
        # a field-hourly employee with a content role: the compensation rule strips rates.field.view from HIM,
        # however generous the role is (Access Spec §7.2; Owner on the Permissions page 2026-09-11)
        "fieldworker": mk("t-field@t.local", "T Field Worker", "project_manager", employee=field_emp),
    }
    return out


# Every named URL with a representative kwargs instance (meta-test T1 asserts coverage of the URLconf).
def url_instances(fx):
    return {
        "command_center": "/?div=070", "command_center_jobs": "/cc/jobs/?div=070&keys=990001,990002",
        "project_list": "/projects/", "project_detail": "/projects/990001/",
        "project_account_drill": "/projects/990001/drill/?acct=MATERIALS", "project_entries": "/projects/990001/entries/",
        "project_transactions": "/projects/990001/transactions/",
        "forecast": "/forecast/?div=070", "people": "/people/", "person_detail": "/people/FLD001/",
        "field": "/field/?div=070", "customers": "/customers/", "customer_detail": "/customers/TST001/",
        "ratings": "/ratings/", "insights": "/insights/2026-08-24-070/",
        "finance_daily": "/finance/daily/", "finance_wip": "/finance/wip/", "finance_payments": "/finance/payments/",
        "finance_bank": "/finance/bank/", "finance_bank_detail": None, "finance_allocations": "/finance/allocations/",
        "finance_drill": "/finance/drill/?kind=ar_open", "finance_bank_figures": None, "finance_bank_reconcile": None,
        "finance_refresh": None, "data_quality": "/data-quality/", "refresh": None, "refresh_status": "/refresh/status/",
        "about": "/about/",
        "login": "/access/login/", "denied": "/access/denied/", "logout": None,
        "console_people": "/access/console/people/", "console_person": None, "console_create": "/access/console/people/new/",
        "console_audit": "/access/console/audit/", "console_audit_all": "/access/console/audit/all/",
        "console_usage": "/access/console/usage/", "console_grants": None,
        "view_as_start": None, "view_as_stop": None,
        "permissions": "/access/permissions/", "permissions_update": None,
        "sales010_overview": "/sales/010/", "sales010_snapshot": "/sales/010/snapshot/", "sales010_pipeline": "/sales/010/pipeline/",
        "sales010_quote_json": "/sales/010/pipeline/quote/Q900051/",
        "sales010_orders": "/sales/010/orders/", "sales010_order_detail": "/sales/010/orders/S900100/",
        "sales010_products": "/sales/010/products/", "sales010_serials": "/sales/010/serials/?q=SER123",
        "sales010_hygiene": "/sales/010/hygiene/",
    }
