"""Pure rules for AR billing documents (docs/billings_page_plan.md §2) — shared by the loader that rolls invoice
lines up onto `finance_arinvoice` and by the Billings page. No Django imports, unit-tested in tests/unit/test_billings.py.

SL facts these encode (verified 2026-09-08):
- an invoice's revenue is the sum of its lines on income (3I) accounts; sales tax sits on 20500, customer deposits /
  deferred contract billings on 21000 / 24000 (not revenue), and a handful of lines hit expense accounts (61200 bank
  charges, 50730 rebates);
- ARTran stores credit-memo lines POSITIVE, so every line and roll-up is signed by the document type;
- the revenue line's GL subaccount is the division the revenue posted to (0700 -> 070) — the Divisional P&L basis.
"""

LINE_KINDS = ("rev", "tax", "dep", "oth")
DEPOSIT_ACCOUNTS = ("21000", "24000")
HARDWARE_SO_TYPES = ("SO1",)          # 010 hardware orders — a job-less invoice from a sales order


def ar_line_sign(doc_type):
    """+1 for invoices / debit memos, -1 for credit memos: SL keeps CM lines and totals positive."""
    return -1 if (doc_type or "").strip().upper() == "CM" else 1


def classify_ar_line(acct, acct_type=None):
    """'rev' (income account), 'tax' (accrued sales tax 205xx), 'dep' (customer deposits / deferred billings 21000 / 24000)
    or 'oth'. acct_type = SL Account.AcctType when known; without it a 4xxxx account counts as income."""
    a = (acct or "").strip()
    if a.startswith("205"):
        return "tax"
    if a in DEPOSIT_ACCOUNTS:
        return "dep"
    if (acct_type or "").strip().upper() == "3I" or (acct_type is None and a.startswith("4")):
        return "rev"
    return "oth"


def division_from_subaccount(sub):
    """'0700' / '0701' -> '070'; blank when the subaccount is missing or malformed."""
    s = (sub or "").strip()
    return s[:3] if len(s) >= 3 and s[:3].isdigit() else ""


def rollup_lines(lines, doc_type):
    """Sum a document's lines into the signed roll-up {revenue, tax, deposits, other, top_sub}.
    lines: iterable of (kind, amount, gl_subaccount) with amounts as stored (positive on a CM).
    top_sub = the subaccount carrying the largest absolute revenue — the division the billing posted to."""
    sign = ar_line_sign(doc_type)
    tot = {"rev": 0, "tax": 0, "dep": 0, "oth": 0}
    by_sub = {}
    for kind, amount, sub in lines:
        amt = (amount or 0) * sign
        tot[kind if kind in tot else "oth"] += amt
        if kind == "rev":
            by_sub[sub or ""] = by_sub.get(sub or "", 0) + abs(amount or 0)
    top = max(by_sub.items(), key=lambda kv: kv[1])[0] if by_sub else ""
    return {"revenue": tot["rev"], "tax": tot["tax"], "deposits": tot["dep"], "other": tot["oth"], "top_sub": top}


def invoice_division(project_division, so_type, top_sub):
    """The division a billing belongs to: the job's division when the invoice names a job; 010 for a job-less SO1
    hardware order; else the subaccount prefix of its largest revenue line."""
    if project_division:
        return project_division
    if (so_type or "").strip().upper() in HARDWARE_SO_TYPES:
        return "010"
    return division_from_subaccount(top_sub)


def is_hardware_order(so_type, has_project):
    """A job-less SO1 invoice: the 010 hardware sales stream, folded into one line per group on the Billings page."""
    return (so_type or "").strip().upper() in HARDWARE_SO_TYPES and not has_project


def backdated_days(entered_day, doc_date):
    """Days between the invoice date and the day it was keyed; 0 when same-day or keyed ahead of its date."""
    if not entered_day or not doc_date:
        return 0
    return max((entered_day - doc_date).days, 0)


def period_of(doc_date):
    """The fiscal period a document's date falls in ('YYYYMM'), for the 'posted into another period' flag."""
    return "%04d%02d" % (doc_date.year, doc_date.month) if doc_date else ""
