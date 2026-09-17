"""Source-query guard and static query registry (spec v3 §2.4).

Descends from the repository's db.py: statements are SELECT/WITH only, single
statement, no write/DDL/exec tokens, loaded from reviewed files on disk and
hashed. Nothing in this module opens a connection.
"""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings


class WriteAttemptError(Exception):
    """Raised when a statement is not a plain read."""


class UnregisteredQueryError(Exception):
    """Raised when a query name is not in the allowlist."""


_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|merge|truncate|drop|create|alter|grant|revoke|deny|"
    r"exec|execute|call|backup|restore|shutdown|reconfigure|checkpoint|dbcc|"
    r"sp_\w+|xp_\w+|into|openrowset|openquery|bulk"
    r")\b",
    re.IGNORECASE,
)
_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)


def assert_read_only(sql):
    stripped = _COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    if not stripped:
        raise WriteAttemptError("empty statement")
    if ";" in stripped:
        raise WriteAttemptError("multiple statements in one call")
    if not re.match(r"^(select|with)\b", stripped, re.IGNORECASE):
        raise WriteAttemptError("statement must start with SELECT or WITH, got: %.60s" % stripped)
    found = _FORBIDDEN.search(stripped)
    if found:
        raise WriteAttemptError("forbidden keyword %r in a read-only query" % found.group(0))
    return stripped


# name -> relative path under settings.SOURCE_SQL_DIR
ALLOWED_SOURCE_QUERIES = {
    "ptt.projects": "ptt/projects.sql",
    "ptt.project_tasks": "ptt/project_tasks.sql",
    "ptt.customers": "ptt/customers.sql",
    "ptt.employees": "ptt/employees.sql",
    "ptt.time_entries_since": "ptt/time_entries_since.sql",
    "ptt.remaining_hours_history": "ptt/remaining_hours_history.sql",
    "ptt.form_elements": "ptt/form_elements.sql",
    "sl.projects": "sl/projects.sql",
    "sl.project_tasks": "sl/project_tasks.sql",
    "sl.customers": "sl/customers.sql",
    "sl.employees": "sl/employees.sql",
    "sl.salespersons": "sl/salespersons.sql",
    "sl.account_categories": "sl/account_categories.sql",
    "sl.gl_accounts": "sl/gl_accounts.sql",
    "sl.project_account_summary": "sl/project_account_summary.sql",
    "sl.project_account_rollup": "sl/project_account_rollup.sql",
    "sl.project_budget_view": "sl/project_budget_view.sql",
    "sl.project_commitments": "sl/project_commitments.sql",
    "sl.cnet_sales_orders": "sl/cnet_sales_orders.sql",
    "sl.cnet_shippers": "sl/cnet_shippers.sql",
    "sl.cnet_sales_order_lines": "sl/cnet_sales_order_lines.sql",
    "sl.cnet_shipper_lines": "sl/cnet_shipper_lines.sql",
    "sl.cnet_shipper_serials": "sl/cnet_shipper_serials.sql",
    "sl.gl_010_pnl": "sl/gl_010_pnl.sql",
    "sl.finance_gl_balances": "sl/finance_gl_balances.sql",
    "sl.finance_gl_activity": "sl/finance_gl_activity.sql",
    "sl.finance_ar_open": "sl/finance_ar_open.sql",
    "sl.finance_ap_open": "sl/finance_ap_open.sql",
    "sl.finance_ar_backfill": "sl/finance_ar_backfill.sql",
    "sl.finance_ap_backfill": "sl/finance_ap_backfill.sql",
    "sl.finance_ar_payments": "sl/finance_ar_payments.sql",
    "sl.finance_ar_payment_apps": "sl/finance_ar_payment_apps.sql",
    "sl.finance_ar_invoices": "sl/finance_ar_invoices.sql",
    "sl.finance_ar_invoice_lines": "sl/finance_ar_invoice_lines.sql",
    "sl.salestax_rates": "sl/salestax_rates.sql",
    "sl.salestax_postings": "sl/salestax_postings.sql",
    "sl.finance_ap_checks": "sl/finance_ap_checks.sql",
    "sl.finance_po_lines": "sl/finance_po_lines.sql",
    "sl.finance_po_receipts": "sl/finance_po_receipts.sql",
    "sl.so_demand_lines": "sl/so_demand_lines.sql",
    "sl.project_so_lines": "sl/project_so_lines.sql",
    "sl.project_ship_lines": "sl/project_ship_lines.sql",
    "sl.project_po_vouchers": "sl/project_po_vouchers.sql",
    "sl.finance_ap_check_apps": "sl/finance_ap_check_apps.sql",
    "sl.finance_ap_lines_since": "sl/finance_ap_lines_since.sql",
    "sl.finance_po_receipt_lines_since": "sl/finance_po_receipt_lines_since.sql",
    "sl.finance_ar_open_lines": "sl/finance_ar_open_lines.sql",
    "sl.customer_contacts": "sl/customer_contacts.sql",
    "sl.vendors": "sl/vendors.sql",
    "sl.financial_transactions_since": "sl/financial_transactions_since.sql",
    "sl.financial_transactions_backfill": "sl/financial_transactions_backfill.sql",
    "sl.permissions_audit": "sl/permissions_audit.sql",
    "sl.bank_gl_cash_activity": "sl/bank_gl_cash_activity.sql",
    "sl.bank_ap_checks": "sl/bank_ap_checks.sql",
    "sl.bank_acct_balances": "sl/bank_acct_balances.sql",
    "sl.project_site_addresses": "sl/project_site_addresses.sql",
    "sl.customer_addresses": "sl/customer_addresses.sql",
}


@lru_cache(maxsize=None)
def load_query(name):
    """Return (sql_text, sha256) for a registered query name; fails closed."""
    if name not in ALLOWED_SOURCE_QUERIES:
        raise UnregisteredQueryError(name)
    path = Path(settings.SOURCE_SQL_DIR) / ALLOWED_SOURCE_QUERIES[name]
    text = path.read_text(encoding="utf-8")
    assert_read_only(text)
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def query_versions():
    return {name: load_query(name)[1][:16] for name in ALLOWED_SOURCE_QUERIES}


def redact(text):
    """Strip credential-looking fragments before anything is logged."""
    text = str(text)
    for key in ("PWD=", "password="):
        text = re.sub(re.escape(key) + r"[^;\s]+", key + "***", text, flags=re.IGNORECASE)
    return text
