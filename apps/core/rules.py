"""Deterministic source-interpretation rules (spec v3 §1.3, §1.4, §6.3).

Pure functions, unit-tested, no database access.
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .models import ProjectMode

# ---- project identity -------------------------------------------------------

_INTERNAL_RE = re.compile(r"^\d\d(9999|8888)")
_TEMPLATE_RE = re.compile(r"^\d\d0000(000000)?$")


def canonical_project_number(raw):
    """Trim + uppercase. Never strips leading zeros or a trailing 000000 (241517 != 241517000000)."""
    if raw is None:
        raise ValueError("project number is None")
    key = str(raw).strip().upper()
    if not key or key == "0000":
        raise ValueError("invalid project number %r" % raw)
    return key


def display_number(key):
    """PTT-style display only: 12-char numeric ids ending in 000000 show their first 6 characters."""
    if len(key) == 12 and key.isdigit() and key.endswith("000000"):
        return key[:6] + "-000000"
    return key


def numbering_style(key, sl_user1=""):
    if _INTERNAL_RE.match(key) or _TEMPLATE_RE.match(key):
        return "internal"
    if len(key) == 6 and key.isdigit():
        return "SO3"
    if len(key) == 12 and key.isdigit() and key.endswith("000000"):
        return "SO2"
    if key.isdigit():
        return "legacy"
    return "alnum"


def is_internal_bucket(key, sl_status=""):
    return bool(_INTERNAL_RE.match(key)) or bool(_TEMPLATE_RE.match(key)) or sl_status == "G"


def is_template_or_void(key, sl_status=""):
    return sl_status in ("G", "M", "T") or bool(_TEMPLATE_RE.match(key))


def project_mode(key, title, sl_status="", customer_po=""):
    t = (title or "").upper().strip()
    po = (customer_po or "").upper().strip()
    if bool(_TEMPLATE_RE.match(key)) or sl_status == "G":
        return ProjectMode.TEMPLATE
    if sl_status in ("M", "T") or "CANCEL" in t or t.startswith("VOID") or " VOID" in t or "DO NOT USE" in t:
        return ProjectMode.CANCELED
    if _INTERNAL_RE.match(key):
        return ProjectMode.INTERNAL
    if t.startswith("TM TICKET") or po == "TM TICKET" or po.startswith("TM TICKET"):
        return ProjectMode.TM_TICKET
    if t.startswith("TM SERVICE") or t.startswith("TM -") or t.startswith("T&M"):
        return ProjectMode.TM_SERVICE
    if re.match(r"^SA\s*-", t) or t.startswith("SA ") or "SERVICE AGREEMENT" in t or "MONITORING" in t and "SA" in t.split():
        return ProjectMode.SERVICE_AGREEMENT
    if "WARRANTY" in t or "WNTY" in key:
        return ProjectMode.WARRANTY
    if "JOC" in t.split() or " JOC " in (" " + t + " ") or re.search(r"\bJOC\b", t):
        return ProjectMode.JOC
    if "STAFFING" in t:
        return ProjectMode.TM_SERVICE
    return ProjectMode.INSTALLATION


_QUOTE_RE = re.compile(r"(?:^|\s)(\d{6})\s*$")


def quote_reference(title, pm_id32=""):
    if pm_id32 and pm_id32.strip():
        return pm_id32.strip()[:32]
    m = _QUOTE_RE.search(title or "")
    return m.group(1) if m else ""


# ---- division ---------------------------------------------------------------

DIVISION_BY_SUBACCOUNT = {
    "0700": ("070", "Premise Security Systems"),
    "0701": ("070", "Premise Security Systems"),
    "0800": ("080", "Airport / Roadway / Municipality"),
    "0400": ("040", "Audio Visual"),
    "0401": ("040", "Audio Visual"),
    "0200": ("020", "IT"),
    "0201": ("020", "IT"),
    "0250": ("025", "Division 025"),
    "0600": ("060", "Electrical"),
    "0601": ("060", "Electrical"),
    "0300": ("030", "Division 030"),
    "0301": ("030", "Division 030"),
    "0100": ("010", "Hardware Sales"),
    "0101": ("010", "Hardware Sales"),
    "0500": ("050", "Division 050"),
    "0000": ("000", "Admin / Overhead"),
}


def division_for_subaccount(sub):
    sub = (sub or "").strip()
    if sub in DIVISION_BY_SUBACCOUNT:
        return DIVISION_BY_SUBACCOUNT[sub]
    if len(sub) >= 3 and sub[:3].isdigit():
        return (sub[:3], "Division %s" % sub[:3])
    return ("000", "Admin / Overhead")


# ---- people -----------------------------------------------------------------

def name_from_sl(last_first):
    """'WILLOWBY~WILLIAM' -> 'William Willowby'; 'REDWOOD II~JAMES' -> 'James Redwood II'."""
    s = (last_first or "").strip()
    if not s:
        return ""
    if "~" in s:
        last, first = s.split("~", 1)
        s = "%s %s" % (first.strip(), last.strip())
    return " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in s.split())


# ---- SL transactions ---------------------------------------------------------

_PAY_RE = re.compile(r"CK\s*DT\s*(\d{1,2}/\d{1,2}/\d{2,4})\s+(\d{1,2}/\d{1,2}/\d{2,4})\s*-\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.IGNORECASE)


def _mdy(s):
    m, d, y = s.split("/")
    y = int(y)
    if y < 100:
        y += 2000
    try:
        return date(y, int(m), int(d))
    except ValueError:
        return None


def parse_pay_period(comment):
    """'CK DT 7/26/2023  7/17/23 -7/23/23' -> (check, start, end) or (None, None, None)."""
    m = _PAY_RE.search(comment or "")
    if not m:
        return None, None, None
    return _mdy(m.group(1)), _mdy(m.group(2)), _mdy(m.group(3))


FREIGHT_GL = {"50750", "50760"}


def transaction_sub_tag(sl_acct, system_cd, batch_type, employee_key, vendor_num, gl_account):
    if sl_acct == "BURDEN":
        if system_cd == "PA" and employee_key:
            return "payroll_tax"
        if system_cd == "AP" and vendor_num:
            return "union_fringe"
        return "burden_other"
    if sl_acct in ("ODC", "TRAVEL", "PURCHASEVARIANCE"):
        if gl_account in FREIGHT_GL:
            return "freight"
        if gl_account == "50717":
            return "contractor_fee"
        return "other"
    if sl_acct == "MATERIALS":
        if system_cd == "AP" and batch_type == "PO":
            return "po_receipt"
        if system_cd == "AP":
            return "voucher"
        if system_cd == "OM":
            return "sales_order_cogs"
        if system_cd == "IN":
            return "inventory_issue"
        if system_cd == "PA" and batch_type == "TFR":
            return "transfer"
        return "material_other"
    if sl_acct in ("LABOR", "LABORUNION"):
        if system_cd == "PA" and batch_type == "TFR":
            return "transfer"
        return "charge_entry" if system_cd == "PA" else system_cd.lower()
    if sl_acct in ("REVENUE", "BILLINGS"):
        if gl_account == "20500":
            return "sales_tax_line"
        return {"IN": "invoice", "CM": "credit_memo", "DM": "debit_memo", "GJ": "journal"}.get(batch_type, batch_type.lower())
    return ""


# ---- SL commitments -----------------------------------------------------------

def fifo_open_units(allocated_units, shipped_units):
    """Net SL project-inventory allocations against what already shipped to the job.

    SL creates an allocation (a "commitment" in PJCOMDET/PJPTDSUM.com_amount) when a project PO line is received at the
    warehouse, but at Pace the allocation is not relieved when the stock ships to the job through Order Management, so the
    same cost appears once as an actual (OM/IN) and again as a commitment. Given the allocation quantities for one
    project + item in receipt order and the quantity already shipped to that project, return the still-open quantity per
    allocation, consuming the earliest receipts first.
    """
    remaining = max(shipped_units or 0, 0)
    out = []
    for u in allocated_units:
        u = u or 0
        consumed = min(u, remaining)
        remaining -= consumed
        out.append(u - consumed)
    return out


# ---- PTT JSON / values --------------------------------------------------------

def parse_ptt_json(value, cap=5):
    """PTT JSON fields may be double-encoded text; unwrap like PTT's json_to_dict."""
    v = value
    n = 0
    while isinstance(v, str) and n < cap:
        try:
            v = json.loads(v)
        except (ValueError, TypeError):
            return {}
        n += 1
    return v if isinstance(v, dict) else {}


_NUM_RE = re.compile(r"[^0-9.\-]")


def parse_hours(text):
    """PTT stores hours as text ('8.0'); returns (Decimal, warning_flag)."""
    if text is None:
        return Decimal("0"), False
    s = _NUM_RE.sub("", str(text))
    if not s or s in ("-", "."):
        return Decimal("0"), bool(str(text).strip())
    try:
        d = Decimal(s)
    except InvalidOperation:
        return Decimal("0"), True
    if d < 0 or d > 48:
        return Decimal("0"), True
    return d, False


def utc_from_tuple(t):
    try:
        parts = [int(x) for x in t][:6]
        while len(parts) < 6:
            parts.append(0)
        return datetime(*parts)
    except Exception:
        return None


def none_if_1900(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime) and dt.year <= 1900:
        return None
    if isinstance(dt, date) and dt.year <= 1900:
        return None
    return dt


def to_dec(v, places=4):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    return Decimal(str(round(float(v), places)))


PCT_MIN, PCT_MAX = Decimal(0), Decimal(1)


def pct_fraction(raw_percent, places=6):
    """PTT `estimated_percent_complete` (0-100) -> Decimal fraction, or None when PTT's derived value is
    outside 0-100. PTT computes the % from the PM's typed remaining costs against the job's cost basis, so a
    remaining figure larger than that basis produces nonsense (265304 on 2026-09-02: -538.82 %, which the
    Project Snapshot turned into a -$65K "earned" swing). Out of range = *no valid update* everywhere: the
    last valid % stands (finance_wip.pct_series skips the point; the loader leaves the project header on
    the last valid observation) and the loader raises the `ptt_pct_out_of_range` data-quality issue."""
    if raw_percent is None:
        return None
    pc = to_dec(Decimal(str(raw_percent)) / 100, places)
    return pc if PCT_MIN <= pc <= PCT_MAX else None


def match_vouchered_pos(po_lines, vouchers, tolerance=Decimal("0.01"), grace_days=30):
    """Greedy one-to-one match of open PO lines against AP vouchers already posted to the same
    project: same vendor, same amount (within tolerance), voucher dated no earlier than ~a month
    before the PO. Returns the set of po_line ids considered already vouchered — SL kept the PO
    open, but the cost is in actuals, so counting the PO as a commitment double-counts it
    (found 2026-08-31 on 264932: PO 069273/OME001 $72,675 open + the identical voucher 163307).
    po_lines: iterable of (id, project_id, vendor_id, amount, po_date)
    vouchers: iterable of (project_id, vendor_id, amount, date)"""
    from collections import defaultdict
    pool = defaultdict(list)
    for pid, vend, amt, dt in vouchers:
        pool[(pid, (vend or "").strip())].append([amt, dt, False])
    matched = set()
    for lid, pid, vend, amt, po_date in sorted(po_lines, key=lambda l: (str(l[4] or ""), str(l[0]))):
        for v in pool.get((pid, (vend or "").strip()), ()):
            if v[2] or v[0] is None or amt is None:
                continue
            if abs(Decimal(v[0]) - Decimal(amt)) <= tolerance and (po_date is None or v[1] is None or (v[1] - po_date).days >= -grace_days):
                v[2] = True
                matched.add(lid)
                break
    return matched
