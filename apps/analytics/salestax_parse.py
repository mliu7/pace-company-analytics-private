"""Classifies every GL line on 20500 ACCRUED SALES TAX (pure, no Django; tests/unit/test_salestax.py). What the ledger
says, in its own vocabulary (verified against every distinct description 2012 → 2026-09):

- AR module: the tax charged on an invoice, credit (a credit memo debits). The description is the jurisdiction the
  sales-order module priced ("WA OLYMPIA", "AZ GILA RIVER TRIBAL AREA SP"), "ILLINOIS SALES TAX" for the flat Illinois
  ID (TAX2, the only tax billed before the nationwide ZIP table of July 2022), or a hand-keyed adjustment on an invoice
  ("BEC012 - ADJ SALES TAX 70505", "ACO001-PAYROLL DED-SALES TAX"). The state is best read from the shipper's ZIP-based
  tax ID; the description, the ship-to state and the customer's state are the fallbacks.
- GL module: remittances are debits whose description names the state and says tax ("TN SALES TAX MAR", "IL DEPT REV
  SALES TAX JULY", "MARYLAND SALES TAX MAY", the 2013-2021 "RECORD SALES TAX PAYMENT" of the Illinois-only era); a
  "REV …" credit reverses one, "COR …" corrects one. "PROJECT SUBACCT RECLASS …" and the year-end "RECLASS …" batches
  move amounts inside 20500 and net to zero within their batch (kind reclass — decided per batch, see
  `settle_reclasses`). Return fees / penalties debited here are `fee`. Everything else — accruals of missed tax
  ("RECLASS MISSED SALES TAX SEPT", "LOUISIANA 45330 SALES TAX"), corrections, the 2024-12-31 "Accrual Adj" that moved
  $270,193 from the liability to 40000 SALES — is `adjustment`.
- AP module: vouchers to state agencies ("COM015 COMPTROLLER OF MARYLAND", the 2014 "ILL001 2013 TAX AUDIT") are
  remittances; "EXEMPT" / "NON TAXABLE" / "OUT OF STATE" lines are zero.
"""

import re
from datetime import date
from decimal import Decimal

KINDS = ("collected", "remitted", "fee", "reclass", "adjustment", "zero")

STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD",
    "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT",
    "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC", "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT",
    "NEBRASKA": "NE", "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "N.CAROLINA": "NC", "N CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "S.CAROLINA": "SC", "S CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY",
}
# AP vouchers: the vendor id at the start of the description names the agency
VENDOR_STATES = {"ILL001": "IL", "COM015": "MD", "FLO004": "FL", "STA005": "VT"}
MONTHS = {"JAN": 1, "JANUARY": 1, "FEB": 2, "FEBRUARY": 2, "MAR": 3, "MARCH": 3, "APR": 4, "APRIL": 4, "MAY": 5, "JUN": 6, "JUNE": 6,
          "JUL": 7, "JULY": 7, "AUG": 8, "AUGUST": 8, "SEP": 9, "SEPT": 9, "SEPTEMBER": 9, "OCT": 10, "OCTOBER": 10, "NOV": 11,
          "NOVEMBER": 11, "DEC": 12, "DECEMBER": 12}
NATIONWIDE_TABLE_DATE = date(2022, 7, 19)     # before this only the flat Illinois ID existed: state-less payments are Illinois
TAX_WORDS = ("SALES TAX", "SALE TAX", "SALES TAC", "SALESTAX", "SALTX", "STATE TAX", "TAX PAYMENT", "TAX AUDIT", "DEPT OF REV",
             "DEPT REV", "REVENU", "SALES TX", " TAX")
FEE_WORDS = ("FEE", "PENALTY", "PENALTIES", "INTEREST")
ZERO_AP = ("EXEMPT", "NON TAXABLE", "OUT OF STATE", "NONTAXABLE")
D0 = Decimal(0)


def _tokens(desc):
    return re.findall(r"[A-Z0-9.&/'-]+", (desc or "").upper())


def state_from_text(desc):
    """State code named in a description: a state-code token in the first three tokens (skipping invoice / customer
    codes), else a state name anywhere. None when nothing names a state."""
    u = (desc or "").upper()
    toks = _tokens(u)
    for t in toks[:3]:
        if t in STATE_CODES:
            return t
    for name in sorted(STATE_NAMES, key=len, reverse=True):
        if name in u:
            return STATE_NAMES[name]
    return None


def period_from_text(desc, tran_date):
    """(YYYYMM, inferred): the period a remittance description says it covers — a month name (the previous year when the
    month is later than the payment month, e.g. DEC paid in January), a quarter ('2ND QRT' → the quarter's last month), a bare
    year ('2024' → that December); else the month before the payment, flagged inferred."""
    u = (desc or "").upper()
    toks = _tokens(u)
    for t in toks:
        if t in MONTHS:
            m = MONTHS[t]
            y = tran_date.year - (1 if m > tran_date.month else 0)
            return "%04d%02d" % (y, m), False
    q = re.search(r"(\d)(ST|ND|RD|TH)\s*Q", u)
    if q:
        m = int(q.group(1)) * 3
        y = tran_date.year - (1 if m > tran_date.month else 0)
        return "%04d%02d" % (y, m), False
    yr = re.search(r"\b(20[12]\d)\b", u)
    if yr:
        return "%s12" % yr.group(1), False
    y, m = (tran_date.year, tran_date.month - 1) if tran_date.month > 1 else (tran_date.year - 1, 12)
    return "%04d%02d" % (y, m), True


def _valid_state(s):
    s = (s or "").strip().upper()
    return s if s in STATE_CODES else None


def classify(module, tran_desc, dr_amt, cr_amt, tran_date, tax_id="", ship_state="", cust_state=""):
    """One posting → {"kind", "state", "state_source", "period_covered", "period_inferred", "note"}. GL-module lines with
    RECLASS wording come back as kind 'reclass?' for `settle_reclasses` to confirm per batch."""
    dr, cr = Decimal(str(dr_amt or 0)), Decimal(str(cr_amt or 0))
    u = (tran_desc or "").strip().upper()
    out = {"kind": "adjustment", "state": "", "state_source": "", "period_covered": "", "period_inferred": False, "note": ""}
    if dr == 0 and cr == 0:
        out["kind"] = "zero"
        return out
    if module == "AR":
        out["kind"] = "collected"
        tid = (tax_id or "").strip().upper()
        if tid == "TAX2":
            out.update(state="IL", state_source="tax id (Illinois flat)")
        elif len(tid) >= 3 and tid[:2] in STATE_CODES and tid[2:].isdigit():
            out.update(state=tid[:2], state_source="tax id")
        else:
            st = state_from_text(u)
            if st:
                out.update(state=st, state_source="description")
            elif _valid_state(ship_state):
                out.update(state=_valid_state(ship_state), state_source="ship-to")
            elif _valid_state(cust_state):
                out.update(state=_valid_state(cust_state), state_source="customer")
        if not any(w in u for w in ("SALES TAX", "TAX")) and not (u[:2] in STATE_CODES and " " in u):
            out["note"] = "hand-keyed on the invoice"
        elif any(w in u for w in ("ADJ", "CANCEL", "KILL", "REMOV", "PAYROLL DED", "SHORT PD", "UNPAID", "BILLING ADJ")):
            out["note"] = "hand-keyed on the invoice"
        return out
    if module == "AP":
        if any(w in u for w in ZERO_AP) and dr == 0 and cr == 0:
            out["kind"] = "zero"
            return out
        vendor = _tokens(u)[:1]
        st = VENDOR_STATES.get(vendor[0]) if vendor else None
        st = st or state_from_text(u)
        out.update(kind="remitted", state=st or "", state_source="vendor" if st else "")
        if "AUDIT" in u:
            out["note"] = "audit assessment"
        out["period_covered"], out["period_inferred"] = period_from_text(u, tran_date)
        return out
    # ---- GL journals ----
    st = state_from_text(u)
    if "PROJECT SUBACC" in u:
        out.update(kind="reclass", note="project subaccount reclass")
        return out
    if any(w in u for w in FEE_WORDS) and dr > 0 and "RECLASS" not in u:
        out.update(kind="fee", state=st or "", state_source="description" if st else "", note="fee / penalty debited to the liability")
        return out
    if "RECLASS" in u or "RECLA " in u or u.endswith("RECLA"):
        if st and dr > 0 and any(w in u for w in TAX_WORDS) and ("MISSED" not in u):
            # "IL DEPT REV SALESTAX APR RECLA" (2022-10-31): payments recorded late; settle_reclasses keeps them as
            # remittances unless the batch nets to zero
            out.update(kind="reclass?", state=st, state_source="description", note="payment recorded as a reclass")
            out["period_covered"], out["period_inferred"] = period_from_text(u, tran_date)
            return out
        out.update(kind="reclass?", state=st or "", state_source="description" if st else "")
        return out
    if u.startswith("REV ") and cr > 0 and any(w in u for w in TAX_WORDS):
        out.update(kind="remitted", state=st or "", state_source="description" if st else "", note="reversal of a remittance")
        out["period_covered"], out["period_inferred"] = period_from_text(u, tran_date)
        return out
    if dr > 0 and any(w in u for w in TAX_WORDS):
        if u.startswith("COR "):
            out["note"] = "correction of a remittance"
        if not st and ("PAYMENT" in u or "RECORD SALES TAX" in u) and tran_date < NATIONWIDE_TABLE_DATE:
            st, src = "IL", "assumed: Illinois-only"
        elif not st and "SALES TAX PAYMENT" in u:
            st, src = "IL", "assumed: Illinois-only"
        else:
            src = "description" if st else ""
        out.update(kind="remitted", state=st or "", state_source=src)
        out["period_covered"], out["period_inferred"] = period_from_text(u, tran_date)
        return out
    # credits (accruals of tax not charged, corrections) and unlabelled debits
    out.update(kind="adjustment", state=st or "", state_source="description" if st else "")
    if "ACCRUAL" in u or "MISSED" in u:
        out["note"] = "accrual"
    elif "CORRECT" in u or "ERROR" in u:
        out["note"] = "correction"
    elif "CLOSING" in u or u.startswith("YE"):
        out["note"] = "year-end"
    return out


def settle_reclasses(rows):
    """Second pass over classified rows (dicts with batch_nbr, kind, dr_amt, cr_amt): a 'reclass?' line stays a reclass when
    its batch's reclass-candidate lines net to zero (a move inside 20500), otherwise it is a remittance (when it carried a
    state and a debit) or an adjustment. Mutates and returns rows."""
    from collections import defaultdict
    net = defaultdict(lambda: D0)
    for r in rows:
        if r["kind"] == "reclass?":
            net[r["batch_nbr"]] += Decimal(str(r["cr_amt"] or 0)) - Decimal(str(r["dr_amt"] or 0))
    for r in rows:
        if r["kind"] != "reclass?":
            continue
        if abs(net[r["batch_nbr"]]) < Decimal("0.01"):
            r["kind"] = "reclass"
            r["period_covered"], r["period_inferred"] = "", False
        elif r.get("state") and Decimal(str(r["dr_amt"] or 0)) > 0 and r.get("note") == "payment recorded as a reclass":
            r["kind"] = "remitted"
        else:
            r["kind"] = "adjustment"
            r["period_covered"], r["period_inferred"] = "", False
            if not r.get("note"):
                r["note"] = "reclass that did not net to zero"
    return rows
