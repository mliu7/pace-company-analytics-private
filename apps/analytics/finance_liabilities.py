"""What makes up total liabilities — the drill-down behind the Daily Financial Snapshot's
"Total liabilities" figure (docs/06_finance_reports.md, "Liabilities / assets breakdown").

Population = every 2L account below 30000 (finance_snapshot.build_daily_snapshot sums the same set;
3xxxx are equity, 39999 the system YTD net income), natural sign credit-positive; a liability
*increases* by CrAmt − DrAmt. Shared core: finance_ledger.ledger_breakdown; the groups below
partition the population exactly, so group totals reconcile to the headline by construction.

Ledger semantics were read off the postings themselves (2026-08-28, docs/06): PO clearing is
credited by PO receipts and debited when the voucher lands in AP; 24000 is credited by invoices for
contracts billed ahead of the service period and relieved by a monthly journal; 21000 carries prepaid
service-hour blocks; the credit line moves only by "COMM LOAN BORROW / PAYMENT" journals.
"""

from datetime import timedelta
from decimal import Decimal

from apps.ingestion.bulk import fetch_dict

from .finance_ledger import D0, fy_balance, ledger_breakdown

LIABILITY_MAX_ACCT = "30000"      # 2L accounts at or above this are equity (3xxxx) / YTD NI (39999)

# (key, label, blurb, color) — order = display order. Colors follow the app palette (PCA.colors).
GROUPS = [
    ("trade", "Trade payables", "What Pace owes vendors: vouchered invoices, goods received but not yet invoiced, and vendor-side clearing accounts.", "#1f5eff"),
    ("bank", "Bank debt", "The BMO line of credit plus lease and interest obligations.", "#c62828"),
    ("deposits", "Customer prepayments & deferred", "Money customers paid or were billed before the work or service period — owed back as work and released to revenue as it is performed.", "#6a3ff0"),
    ("tax", "Sales & other taxes", "Taxes collected or accrued and not yet remitted: sales tax by jurisdiction, property tax, income tax.", "#b7791f"),
    ("payroll", "Payroll, benefits & union", "Withholdings and accruals that leave with each payroll and are remitted afterwards: 401(k), union dues and benefits, payroll taxes, accrued wages and vacation.", "#0e9aa7"),
    ("other", "Other", "Liability accounts not covered by the groups above.", "#8d6e63"),
]

# acct -> (group, plain-English meaning). Anything not listed falls into "other" with the GL description only.
ACCOUNTS = {
    "20000": ("trade", "Vendor invoices (vouchers) entered and released in SL but not yet paid. The AP subledger lists every open voucher by vendor — see the Accounts payable tab."),
    "20001": ("trade", "Goods received on purchase orders whose vendor invoice has not been vouchered yet — 'received, not invoiced'. Credited by each PO receipt, debited when the voucher lands in AP; the cost is already on the job or in inventory, so this is AP-in-waiting."),
    "20002": ("trade", "Payables booked by journal entry rather than voucher (payroll accruals, cost-entry reversals). A negative balance means reversals have run ahead of the accruals."),
    "20003": ("trade", "Vendor returns (RMAs) in process — credit expected back from the vendor."),
    "20006": ("trade", "Corporate card charges parked until the statement is paid."),
    "20005": ("bank", "Drawn balance on the BMO commercial line of credit per the ledger — moved only by the 'COMM LOAN BORROW' / 'COMM LOAN PAYMENT' journals. The bank portal's live figure can differ by a day of float."),
    "20007": ("bank", "Second credit-line account."),
    "20060": ("bank", "Remaining principal on capital (finance) leases."),
    "20650": ("bank", "Interest on the credit line accrued but not yet paid."),
    "24000": ("deposits", "Contracts billed ahead of the service period (UIH help-desk support, Abridge, staffing) plus customer down payments. Each advance invoice credits this account; a monthly journal releases the earned portion to revenue. Not yet earned — it is owed back as service."),
    "21000": ("deposits", "Prepaid blocks of service hours sold on invoices (e.g. '100 IT HRS'); relieved by journal as hours are used or a block expires."),
    "20500": ("tax", "Sales tax charged on invoices and not yet remitted. Credited by each taxable invoice (the posting description names the jurisdiction), debited when the state is paid. Subaccounts = the billing division."),
    "20800": ("tax", "Accrued property tax on the building."),
    "20700": ("tax", "Accrued state income / replacement tax. A negative balance = overpaid, refund due."),
    "20100": ("payroll", "Federal withholding plus FICA/Medicare accrued with each payroll and cleared when the 941 deposit is made — normally nets to zero between payrolls."),
    "20300": ("payroll", "Federal unemployment tax (FUTA) accrued from payroll, pending deposit."),
    "20320": ("payroll", "State unemployment tax (SUTA) accrued from payroll, pending deposit."),
    "20323": ("payroll", "Colorado SUTA base-rate accrual."),
    "20325": ("payroll", "Lake County, Indiana income tax withheld for work performed there, pending remittance."),
    "20326": ("payroll", "Oklahoma / Minnesota income tax withheld for work performed there, pending remittance."),
    "20327": ("payroll", "Oklahoma / Minnesota unemployment tax accrued, pending remittance."),
    "20350": ("payroll", "Illinois state income tax withheld from pay, cleared with each deposit."),
    "20400": ("payroll", "Wages earned but unpaid at a period end — accrued at month or year end and reversed in the next period, so it is normally zero mid-month."),
    "20420": ("payroll", "Vacation pay accrued for union employees."),
    "20010": ("payroll", "Employee 401(k) deferrals withheld from pay until remitted to the plan. Negative = remitted ahead of the withholdings."),
    "20011": ("payroll", "401(k) loan repayments withheld from pay, pending remittance."),
    "20020": ("payroll", "AFLAC premiums withheld from pay, pending remittance."),
    "20050": ("payroll", "Employee advances and reimbursements in transit."),
    "20206": ("payroll", "Union dues withheld from 'A card' members, remitted to the local monthly."),
    "20211": ("payroll", "Union dues withheld from 'C card' members, remitted to the local monthly."),
    "20213": ("payroll", "Local 461A employer benefit contributions accrued, paid monthly."),
    "20215": ("payroll", "Local 701 employer benefit contributions accrued, paid monthly."),
    "20216": ("payroll", "Local 701 (Indiana) union dues withheld, remitted monthly."),
    "20340": ("payroll", "Other payroll deductions pending remittance."),
    "20550": ("payroll", "Workers' compensation premium accrual (annual audit true-up), cleared when paid."),
}


# Display names (the GL descriptions are shouted upper-case and abbreviated); fallback = GL description.
NAMES = {
    "20000": "Accounts payable", "20001": "PO clearing — received, not invoiced", "20002": "AP other — journal payables",
    "20003": "RMA clearing", "20006": "AMEX holding", "20005": "Credit line (BMO)", "20007": "Credit line 2",
    "20060": "Capital lease obligation", "20650": "Accrued interest",
    "24000": "Customer deposits — billed in advance", "21000": "Customer deposits — prepaid hour blocks",
    "20500": "Accrued sales tax", "20800": "Accrued real estate taxes", "20700": "Accrued income taxes",
    "20100": "FICA / Medicare / federal withholding (941)", "20300": "Accrued FUTA", "20320": "Accrued SUTA", "20323": "Accrued CO SUTA",
    "20325": "Lake County IN income tax", "20326": "OK / MN income tax", "20327": "OK / MN unemployment", "20350": "Illinois withholding",
    "20400": "Accrued salaries & wages", "20420": "Accrued union vacation", "20010": "401(k) payable", "20011": "401(k) loan repayments",
    "20020": "AFLAC deductions", "20050": "Employee clearing", "20206": "Union dues — A card", "20211": "Union dues — C card",
    "20213": "Local 461A contributions", "20215": "Local 701 contributions", "20216": "Local 701 IN dues", "20340": "Other deductions",
    "20550": "Accrued WC insurance",
}


LINKS = {
    "20005": [("Bank reconciliation →", "/finance/bank/")],
}

SPEC = {
    "key": "liabilities", "label": "liabilities", "title": "What makes up total liabilities", "snapshot_field": "liabilities",
    "where": "acct_type = '2L' AND acct < '%s'" % LIABILITY_MAX_ACCT, "inc": "cr_amt - dr_amt", "up": "cr_amt", "down": "dr_amt", "inv": True,
    "groups": GROUPS, "accounts": ACCOUNTS, "names": NAMES, "links": LINKS,
    "population": "every 2L ledger account below 30000",
    "sign_note": "Balances are natural-sign from AcctHist (credit = owed; a negative figure is a debit balance — paid or relieved ahead of the accrual).",
    "up_word": "added", "down_word": "relieved", "change_label": "Change owed",
    "neg_note": "a debit balance — paid or relieved ahead of the accrual",
    "sub_tab": {"template": "dashboard/_ledger_tab_ap.html", "label": "Accounts payable", "acct": "20000",
                "link_label": "Vendor-level view → Accounts payable tab", "amount_key": "ap"},
}


def classify(acct):
    """Group key for a liability account; unknown accounts are 'other'."""
    return ACCOUNTS.get(acct, ("other", ""))[0]


def explain(acct):
    return ACCOUNTS.get(acct, ("other", ""))[1]


def name(acct, descr=""):
    return NAMES.get(acct) or (descr or acct).lower().capitalize()


def liability_breakdown(snap, prev=None):
    data = ledger_breakdown(SPEC, snap, prev)
    data["total_sub"] = "%d ledger accounts · all current — no long-term debt" % len(data["accounts"])
    # sales tax collected by jurisdiction (the OM/AR posting description is the tax-ID name)
    data["tax_by_jurisdiction"] = fetch_dict("""
        SELECT tran_desc, COUNT(*) n, SUM(cr_amt - dr_amt) amt FROM finance_glrecentposting
        WHERE acct = '20500' AND module = 'AR' AND sl_created_at >= %s GROUP BY tran_desc ORDER BY amt DESC LIMIT 12""", [data["activity_since"]])
    data["ap"] = ap_breakdown(snap.snapshot_date, data["by_acct"], data["fy"])
    return data


def ap_breakdown(today, by_acct, fy):
    """The AP subledger behind GL 20000: vendors, ages, posting months, largest vouchers, bridge to GL."""
    tot = fetch_dict("SELECT COUNT(*) n, COUNT(DISTINCT vendor_id) vendors, COALESCE(SUM(sign*doc_bal),0) total, "
                     "COALESCE(SUM(CASE WHEN days_past_due > 0 THEN sign*doc_bal END),0) past_due, "
                     "COUNT(CASE WHEN days_past_due > 0 THEN 1 END) past_due_n, MAX(as_of) as_of FROM finance_apopendocument")[0]
    due14 = fetch_dict("SELECT COALESCE(SUM(sign*doc_bal),0) s, COUNT(*) n FROM finance_apopendocument WHERE due_date BETWEEN %s AND %s",
                       [today, today + timedelta(days=14)])[0]
    vendors = fetch_dict("""SELECT vendor_id, MAX(vendor_name) name, COUNT(*) n, SUM(sign*doc_bal) amt,
                                   COALESCE(SUM(CASE WHEN days_past_due > 0 THEN sign*doc_bal END),0) past_due, MIN(due_date) oldest
                            FROM finance_apopendocument GROUP BY vendor_id ORDER BY amt DESC""")
    top, rest = vendors[:20], vendors[20:]
    others = {"n": sum(v["n"] for v in rest), "amt": sum((v["amt"] or D0 for v in rest), D0),
              "past_due": sum((v["past_due"] or D0 for v in rest), D0), "vendors": len(rest)} if rest else None
    for v in top:
        v["share"] = float(v["amt"] / tot["total"]) if tot["total"] else 0.0
    buckets = {r["bucket"]: r for r in fetch_dict("SELECT bucket, SUM(sign*doc_bal) amt, COUNT(*) n FROM finance_apopendocument GROUP BY bucket")}
    periods = fetch_dict("SELECT per_post, COUNT(*) n, SUM(sign*doc_bal) amt FROM finance_apopendocument GROUP BY per_post ORDER BY per_post DESC")
    recent, older = periods[:4], periods[4:]
    if older:
        recent.append({"per_post": "older", "n": sum(p["n"] for p in older), "amt": sum((p["amt"] or D0 for p in older), D0)})
    largest = fetch_dict("""SELECT ref_nbr, vendor_id, vendor_name, invoice_nbr, po_nbr, doc_date, due_date, days_past_due, sign*doc_bal amt, doc_desc
                            FROM finance_apopendocument WHERE sign > 0 ORDER BY doc_bal DESC LIMIT 10""")
    gl_bal = fy_balance(by_acct, "20000", fy)
    return {"total": tot["total"], "n": tot["n"], "vendors": tot["vendors"], "past_due": tot["past_due"], "past_due_n": tot["past_due_n"],
            "past_due_pct": float(tot["past_due"] / tot["total"]) if tot["total"] else 0.0, "as_of": tot["as_of"],
            "due14": due14["s"], "due14_n": due14["n"], "top_vendors": top, "others": others, "buckets": buckets,
            "periods": recent, "largest": largest, "gl_balance": gl_bal, "bridge": tot["total"] - gl_bal,
            "top_share": float(sum((v["amt"] for v in top[:3]), D0) / tot["total"]) if tot["total"] else 0.0}
