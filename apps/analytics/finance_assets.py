"""What makes up total assets — the drill-down behind the Daily Financial Snapshot's "Total assets"
figure (docs/06_finance_reports.md, "Liabilities / assets breakdown"). Population = every 1A account
(finance_snapshot.build_daily_snapshot sums the same set), natural sign debit-positive; an asset
*increases* by DrAmt − CrAmt. Shared core: finance_ledger.ledger_breakdown.

Ledger semantics read off the postings (2026-08-28): 12000 inventory is debited by PO receipts and
credited by OM shipments; 12600 "PROJECT DESIGN / MANAGEMENT" is the accountants' jobs-in-process
asset — a month-end "RECORD JOBS IN PROCESS" journal reversed the next month; 15710 takes a flat
"ME DEPRECIATION" journal each month; 10250 is the book balance of the BMO operating account.
"""

from datetime import timedelta
from decimal import Decimal

from apps.ingestion.bulk import fetch_dict

from .finance_ledger import D0, fy_balance, ledger_breakdown

CURRENT_ASSET_MAX = "15000"     # same cut as finance_snapshot.CURRENT_ASSET_MAX

GROUPS = [
    ("cash", "Cash & bank", "Book balances of the bank accounts (bank balance less outstanding checks) — SL does not know the bank's own figure.", "#178a52"),
    ("receivables", "Receivables", "Invoices customers have not paid yet, net of credits and unapplied payments, plus related-party and other receivables.", "#1f5eff"),
    ("inventory", "Inventory & jobs in process", "Stock on hand at cost plus the month-end jobs-in-process asset (costs on open jobs not yet billed).", "#6a3ff0"),
    ("prepaid", "Prepaids & other current", "Paid in advance and expensed over time; clearing and barter balances.", "#b7791f"),
    ("fixed", "Property & equipment, net", "Leasehold improvements, equipment, software and vehicles at cost, less accumulated depreciation.", "#0e9aa7"),
    ("longterm", "Deposits & other long-term", "Security deposits and other non-current balances.", "#8d6e63"),
    ("other", "Other", "Asset accounts not covered by the groups above.", "#546e7a"),
]

ACCOUNTS = {
    "10250": ("cash", "Book balance of the BMO operating account: bank balance less checks written and not yet cleared. A negative figure means outstanding checks exceed what is in the bank until they clear — the Bank reconciliation page ties this to the statement."),
    "10350": ("cash", "BMO money-market account. Matched the finance email to the penny on 7/6/26."),
    "10400": ("cash", "BMO Harris payroll account."),
    "10450": ("cash", "Corporate card (AMEX) charges paid through AP ahead of the statement; clears when the statement settles."),
    "10800": ("cash", "Petty cash on hand."),
    "11000": ("receivables", "Released customer invoices and debit memos not yet paid, less credit memos and unapplied payments — the AR subledger, invoice by invoice. See the Receivables tab."),
    "11011": ("receivables", "Other receivables net of the allowance for doubtful accounts. Prior year-ends carried only the allowance (negative); the 2026 balance is a receivable booked here."),
    "11016": ("receivables", "Unbilled contract revenue (earned, not yet invoiced). Unused in the ledger — the app's WIP position on the snapshot page carries that view."),
    "11035": ("receivables", "Amount owed to Pace by Pace Properties, the related building entity."),
    "12000": ("inventory", "Stock on hand at cost — debited by each PO receipt, credited when orders ship. Subaccounts are divisions; the 0000 and 0200 pair offsets almost exactly (receipts and relief post to different subs), so read the divisional balances and the account total."),
    "12001": ("inventory", "Non-stock items and shipping charges passing through inventory."),
    "12003": ("inventory", "Inventory count and cost adjustments."),
    "12010": ("inventory", "Inventory sub-account."),
    "12600": ("inventory", "Jobs in process — cost on open jobs not yet billed, per the accountants' month-end workbook. Booked by a 'RECORD JOBS IN PROCESS' journal at each close and reversed the next month, so it stays flat intra-month and steps at month end. Excluded from the snapshot's 'Inventory (GL)' figure."),
    "13100": ("prepaid", "Expenses paid in advance and released to the P&L over time (subscriptions, bid fees, licences)."),
    "13125": ("prepaid", "Insurance premiums paid in advance, expensed monthly."),
    "13200": ("prepaid", "Barter-exchange balance."),
    "000000": ("prepaid", "Suspense / clearing account — entries parked here should net to zero."),
    "15600": ("fixed", "Leasehold improvements at cost."),
    "15650": ("fixed", "Computer equipment at cost (subaccounts = divisions)."),
    "15660": ("fixed", "Software at cost."),
    "15700": ("fixed", "Office equipment at cost (subaccounts = divisions)."),
    "15705": ("fixed", "Equipment — introduction."),
    "15710": ("fixed", "Accumulated depreciation (negative) — a flat 'ME DEPRECIATION' journal each month plus year-end adjustments."),
    "15720": ("fixed", "Accumulated depreciation, quarterly schedule (negative; static)."),
    "16500": ("fixed", "Vehicles at cost."),
    "17200": ("longterm", "Security deposits held by others."),
}

NAMES = {
    "10250": "Business checking (BMO, book)", "10350": "Money market (BMO MMA)", "10400": "Payroll account", "10450": "AMEX holding",
    "10800": "Petty cash", "11000": "Accounts receivable", "11011": "Other receivables − allowance", "11016": "Unbilled contract revenue",
    "11035": "Due from Pace Properties", "12000": "Inventory", "12001": "Non-inventory", "12003": "Inventory variance", "12010": "Inventory (12010)",
    "12600": "Jobs in process (project design / management)", "13100": "Prepaid expenses", "13125": "Prepaid insurance", "13200": "Barter account",
    "000000": "Suspense", "15600": "Leasehold improvements", "15650": "Computer equipment", "15660": "Software", "15700": "Office equipment",
    "15705": "Equipment — introduction", "15710": "Accumulated depreciation", "15720": "Accumulated depreciation (quarterly)", "16500": "Vehicles",
    "17200": "Security deposits",
}

LINKS = {
    "10250": [("Bank reconciliation →", "/finance/bank/")],
    "11000": [("Payments received →", "/finance/payments/")],
    "12600": [("WIP by job (the app's own earned-vs-billed view) →", "/finance/wip/")],
}

SPEC = {
    "key": "assets", "label": "assets", "title": "What makes up total assets", "snapshot_field": "assets",
    "where": "acct_type = '1A'", "inc": "dr_amt - cr_amt", "up": "dr_amt", "down": "cr_amt", "inv": False,
    "groups": GROUPS, "accounts": ACCOUNTS, "names": NAMES, "links": LINKS,
    "population": "every 1A ledger account",
    "sign_note": "Balances are natural-sign from AcctHist (debit = owned; a negative figure is a credit balance — accumulated depreciation, an overdrawn book cash balance, or an allowance).",
    "up_word": "added", "down_word": "reduced", "change_label": "Change",
    "neg_note": "a credit balance — for cash, checks written and not yet cleared exceed what is in the bank",
    "sub_tab": {"template": "dashboard/_ledger_tab_ar.html", "label": "Receivables", "acct": "11000",
                "link_label": "Customer-level view → Receivables tab", "amount_key": "ar"},
}


def classify(acct):
    return ACCOUNTS.get(acct, ("other", ""))[0]


def asset_breakdown(snap, prev=None):
    data = ledger_breakdown(SPEC, snap, prev)
    current = sum((a["balance"] for a in data["accounts"] if a["acct"] < CURRENT_ASSET_MAX), D0)
    data["total_sub"] = "current %s · fixed & other %s" % (_money(current), _money(data["total"] - current))
    data["current_assets"] = current
    data["ar"] = ar_breakdown(snap.snapshot_date, data["by_acct"], data["fy"])
    return data


def _money(v):
    return ("-" if v < 0 else "") + "$" + format(int(round(abs(v))), ",")


def ar_breakdown(today, by_acct, fy):
    """The AR subledger behind GL 11000: customers, ages, books, largest invoices, bridge to GL."""
    tot = fetch_dict("""SELECT COUNT(*) FILTER (WHERE released) n, COUNT(DISTINCT customer_id_raw) FILTER (WHERE released) customers,
                               COALESCE(SUM(sign*doc_bal) FILTER (WHERE released),0) total,
                               COALESCE(SUM(sign*doc_bal) FILTER (WHERE released AND days_past_due > 0),0) past_due,
                               COUNT(*) FILTER (WHERE released AND days_past_due > 0) past_due_n,
                               COALESCE(SUM(sign*doc_bal) FILTER (WHERE released AND bucket = 'over90'),0) over90,
                               COALESCE(SUM(sign*doc_bal) FILTER (WHERE released AND sign < 0),0) credits,
                               COALESCE(SUM(orig_amt) FILTER (WHERE NOT released),0) pending, COUNT(*) FILTER (WHERE NOT released) pending_n,
                               MAX(as_of) as_of FROM finance_aropendocument""")[0]
    customers = fetch_dict("""SELECT d.customer_id_raw, MAX(d.customer_name) name, COUNT(*) n, SUM(d.sign*d.doc_bal) amt,
                                     COALESCE(SUM(CASE WHEN d.days_past_due > 0 THEN d.sign*d.doc_bal END),0) past_due,
                                     COALESCE(SUM(CASE WHEN d.bucket = 'over90' THEN d.sign*d.doc_bal END),0) over90,
                                     MIN(d.due_date) oldest, MAX(c.sl_customer_id) link_id
                              FROM finance_aropendocument d LEFT JOIN core_customer c ON c.id = d.customer_id
                              WHERE d.released GROUP BY d.customer_id_raw ORDER BY amt DESC""")
    top, rest = customers[:20], customers[20:]
    others = {"n": sum(v["n"] for v in rest), "amt": sum((v["amt"] or D0 for v in rest), D0),
              "past_due": sum((v["past_due"] or D0 for v in rest), D0), "customers": len(rest)} if rest else None
    for v in top:
        v["share"] = float(v["amt"] / tot["total"]) if tot["total"] else 0.0
    buckets = {r["bucket"]: r for r in fetch_dict("SELECT bucket, SUM(sign*doc_bal) amt, COUNT(*) n FROM finance_aropendocument WHERE released GROUP BY bucket")}
    books = fetch_dict("SELECT book, COUNT(*) n, SUM(sign*doc_bal) amt FROM finance_aropendocument WHERE released GROUP BY book ORDER BY amt DESC")
    largest = fetch_dict("""SELECT d.ref_nbr, d.customer_name, d.customer_id_raw, d.project_id_raw, d.book, d.doc_date, d.due_date, d.days_past_due, d.sign*d.doc_bal amt, d.doc_desc,
                                   (SELECT sl_customer_id FROM core_customer c WHERE c.id = d.customer_id) link_id,
                                   (SELECT canonical_project_number FROM core_project p WHERE p.id = d.project_id) cpn
                            FROM finance_aropendocument d WHERE d.released AND d.sign > 0 ORDER BY d.doc_bal DESC LIMIT 10""")
    gl_bal = fy_balance(by_acct, "11000", fy)
    return {"total": tot["total"], "n": tot["n"], "customers": tot["customers"], "past_due": tot["past_due"], "past_due_n": tot["past_due_n"],
            "past_due_pct": float(tot["past_due"] / tot["total"]) if tot["total"] else 0.0, "over90": tot["over90"],
            "over90_pct": float(tot["over90"] / tot["total"]) if tot["total"] else 0.0, "credits": tot["credits"],
            "pending": tot["pending"], "pending_n": tot["pending_n"], "as_of": tot["as_of"],
            "top_customers": top, "others": others, "buckets": buckets, "books": books, "largest": largest,
            "gl_balance": gl_bal, "bridge": tot["total"] - gl_bal,
            "top_share": float(sum((v["amt"] for v in top[:3]), D0) / tot["total"]) if tot["total"] else 0.0}
