"""Production-planning rules (SharePoint spec §6.1–§6.5) — pure functions, no database.

Everything the dashboards computed in the browser lives here so the pages, the exports, the Today counters and the
transition importers agree on one definition. The inventory (docs/sharepoint_dashboards_inventory.md, "Project Status",
"Active Task Dashboard", "Punch Lists", "BOM & Labor Approval") is the parity basis; deviations are deliberate and noted.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

# ------------------------------------------------------------------------------------------------ status vocabulary
# Workflow order of the 21 statuses (plus blank). Both pills — Phase Complete Status and Equipment Complete Status V2 —
# draw from the same list; no transition is enforced (PS-03).
STATUSES = [
    "", "WAITING ON CLIENT", "ONBOARDING IN PROGRESS", "ONBOARDING COMPLETE", "NO ENGINEERING NEEDED",
    "NEEDS ENGINEERING SCHEDULED", "ENGINEERING IN PROGRESS", "ENGINEERING COMPLETE", "PROCUREMENT ONGOING",
    "NEED EQUIPMENT", "EQUIPMENT ALL HERE", "FABRICATION IN PROCESS", "FABRICATION COMPLETE", "NEED TO SCHEDULE",
    "WORK SCHEDULED", "FIELD INSTALL IN PROGRESS", "FIELD INSTALL COMPLETE", "TESTING COMMISSIONING ONGOING",
    "TESTING COMMISSIONING COMPLETE", "CLOSEOUT DOCS REQUIRED", "PROJECT COMPLETE",
]

# Microsoft Planner label colours the PMs use in the Master Schedule (bg, fg).
STATUS_COLORS = {
    "": ("#ffffff", "#606a71"),
    "WAITING ON CLIENT": ("#394146", "#ffffff"),
    "ONBOARDING IN PROGRESS": ("#fbddf0", "#c23988"),
    "ONBOARDING COMPLETE": ("#e9c7cd", "#a4262c"),
    "NO ENGINEERING NEEDED": ("#7160eb", "#ffffff"),
    "NEEDS ENGINEERING SCHEDULED": ("#dbebc7", "#3f6b00"),
    "ENGINEERING IN PROGRESS": ("#dbebc7", "#3f6b00"),
    "ENGINEERING COMPLETE": ("#13a10e", "#000000"),
    "PROCUREMENT ONGOING": ("#77004d", "#ffffff"),
    "NEED EQUIPMENT": ("#eaeeef", "#40545f"),
    "EQUIPMENT ALL HERE": ("#00b7c3", "#000000"),
    "FABRICATION IN PROCESS": ("#f1d9cc", "#c74400"),
    "FABRICATION COMPLETE": ("#ff8c00", "#000000"),
    "NEED TO SCHEDULE": ("#c50f1f", "#ffffff"),
    "WORK SCHEDULED": ("#e2d1cb", "#5c2b1f"),
    "FIELD INSTALL IN PROGRESS": ("#d0e7f8", "#0067b8"),
    "FIELD INSTALL COMPLETE": ("#0078d4", "#ffffff"),
    "TESTING COMMISSIONING ONGOING": ("#fbddf0", "#c23988"),
    "TESTING COMMISSIONING COMPLETE": ("#da3b01", "#ffffff"),
    "CLOSEOUT DOCS REQUIRED": ("#7a7574", "#ffffff"),
    "PROJECT COMPLETE": ("#0b6a0b", "#ffffff"),
}

# Misspellings seen in the Master Schedule, normalised on read and on select.
STATUS_ALIASES = {
    "PROCURMENT ONGOING": "PROCUREMENT ONGOING",
    "FIELD INSTALL IN PROCESS": "FIELD INSTALL IN PROGRESS",
    "TESING COMMISSIONING ONGOING": "TESTING COMMISSIONING ONGOING",
    "CLOSEOUT DOCS NEEDED": "CLOSEOUT DOCS REQUIRED",
    "FABRICATION IN PROGRESS": "FABRICATION IN PROCESS",
    # extra typos in the seed export (the dashboard showed them raw; PCA folds them — PS-03 "vocabulary is data")
    "ENGINEERING IN PROGESS": "ENGINEERING IN PROGRESS",
    "TESTING COMISSIONING ONGOING": "TESTING COMMISSIONING ONGOING",
}

BLANK_STATUS_VALUES = {"", "-", "—", "N/A", "NONE", "NO STATUS", "NO PHASE SELECTED", "NULL", "UNDEFINED"}


def is_blank_status(raw):
    return (raw or "").strip().upper() in BLANK_STATUS_VALUES


def canonical_status(raw, aliases=None):
    """Upper-cased, whitespace-folded, alias-mapped status; blank-ish values → ''. Unknown values stay as typed
    (upper-cased) so nothing is lost — they render with the blank palette."""
    s = re.sub(r"\s+", " ", (raw or "").replace(" ", " ")).strip().upper()
    if s in BLANK_STATUS_VALUES:
        return ""
    if re.fullmatch(r"\d{4,}", s):      # a job number pasted into the status column is not a status
        return ""
    table = STATUS_ALIASES if aliases is None else aliases
    return table.get(s, s)


def status_color(status, colors=None):
    table = STATUS_COLORS if colors is None else colors
    return table.get(status or "", table.get("", ("#ffffff", "#606a71")))


# ------------------------------------------------------------------------------------------------ priority (AT-02)
PRIORITY_LABELS = {"red": "Past critical stop", "yellow": "Due within 14 days", "green": "On track", "none": "No stop date"}
PRIORITY_ORDER = ["red", "yellow", "green", "none"]
DUE_SOON_DAYS = 14


def _as_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def days_until(d, today):
    """Whole days from `today` to `d` (negative = past). Both local calendar dates — the dashboard compared a UTC
    midnight with local midnight, so a stop date of *today* was never 'due'; here today counts as due."""
    d = _as_date(d)
    return None if d is None else (d - today).days


def priority_bucket(end, today):
    """red = critical stop before today · yellow = today … +14 days · green = later · none = no date."""
    n = days_until(end, today)
    if n is None:
        return "none"
    if n < 0:
        return "red"
    if n <= DUE_SOON_DAYS:
        return "yellow"
    return "green"


def starting_soon(start, today):
    n = days_until(start, today)
    return n is not None and 0 <= n <= DUE_SOON_DAYS


def is_active_task(completed, percent):
    """Active-tasks view: not completed and percent complete < 100 (AT-01 / 6.2)."""
    try:
        p = float(percent or 0)
    except (TypeError, ValueError):
        p = 0
    return not completed and p < 100


def task_summary(tasks, today):
    """KPI tiles for a list of task dicts {end, start, hours_left, percent}: the six numbers of AT-03 plus averages."""
    s = {"active": 0, "union_hours": 0.0, "starting_soon": 0, "past_due": 0, "due_soon": 0, "missing": 0, "on_track": 0,
         "urgent_hours": 0.0, "avg_pct": 0.0}
    tot_pct = 0.0
    for t in tasks:
        s["active"] += 1
        h = float(t.get("hours_left") or 0)
        s["union_hours"] += h
        pr = priority_bucket(t.get("end"), today)
        if pr == "red":
            s["past_due"] += 1
        elif pr == "yellow":
            s["due_soon"] += 1
        elif pr == "none":
            s["missing"] += 1
        else:
            s["on_track"] += 1
        if pr in ("red", "yellow"):
            s["urgent_hours"] += h
        if starting_soon(t.get("start"), today):
            s["starting_soon"] += 1
        tot_pct += float(t.get("percent") or 0)
    s["avg_pct"] = round(tot_pct / s["active"]) if s["active"] else 0
    return s


# ------------------------------------------------------------------------------------------------ PM values (AT §7)
_PM_SPLIT = re.compile(r"\s*(?:/|\\|\+|&|,|;|\bAND\b|-|–|—)\s*", re.I)
_PM_JUNK = {"", "?", "N/A", "NA", "NONE", "TBD", "UNASSIGNED", "UNKNOWN"}


def split_pm(raw):
    """'DOBO/CARLSON' → ('DOBO', 'CARLSON'); 'HERRERA/ n/a' → ('HERRERA', ''); '/WALDEN' → ('WALDEN', '');
    'HERRERA/HERRERA' → ('HERRERA', ''). Upper-cased, junk tokens dropped."""
    cleaned = re.sub(r"\bn/a\b", " ", (raw or "").replace("\u00a0", " "), flags=re.I)
    parts = [p.strip().upper() for p in _PM_SPLIT.split(cleaned)]
    parts = [p for p in parts if p not in _PM_JUNK and len(p) >= 2]
    if not parts:
        return "", ""
    first = parts[0]
    second = next((p for p in parts[1:] if p != first), "")
    return first, second


def pm_first_value(raw):
    return split_pm(raw)[0]


# ------------------------------------------------------------------------------------------------ job numbers
def six_digit(text):
    m = re.search(r"\b(\d{6})\b", text or "")
    return m.group(1) if m else ""


def job_key(name, proj_num=""):
    """The dashboards' 6-digit join key: leading 6 digits of the name, else a 6-digit run in proj_num, else a 6-digit
    run anywhere in the name; '26-5219' also counts (job-key patch)."""
    n = (name or "").strip()
    m = re.match(r"^(\d{6})\b", n)
    if m:
        return m.group(1)
    d = re.sub(r"\D", "", proj_num or "")
    if len(d) >= 6:
        return d[:6]
    m = re.search(r"\b(\d{2})-(\d{4})\b", n)
    if m:
        return m.group(1) + m.group(2)
    return six_digit(n)


def project_candidates(key):
    """Canonical-number candidates for a 6-digit key: the number itself and its 12-digit form (never strip 000000;
    both can exist as distinct projects — CLAUDE.md)."""
    d = re.sub(r"\D", "", key or "")
    if not d:
        return []
    out = [d]
    if len(d) == 6:
        out.append(d + "000000")
    return out


def resolve_project(key, known):
    """The canonical project number for a 6-digit key against `known` (set of canonical numbers). The plain 6-digit
    number wins over the 000000 form when both exist."""
    for c in project_candidates(key):
        if c in known:
            return c
    return None


def punch_code(raw):
    """Punch project code: a 6-digit number becomes 'NN-NNNN' (formatPunchProjectCode); anything else is kept."""
    s = (raw or "").strip()
    return s[:2] + "-" + s[2:] if re.fullmatch(r"\d{6}", s) else s


def code_digits(code):
    return re.sub(r"\D", "", code or "")


def division_from(*texts):
    """First 040 / 070 / 080 found as a whole word in the given texts (cell, sheet name, file name), else ''."""
    for t in texts:
        m = re.search(r"\b(040|070|080)\b", str(t or ""))
        if m:
            return m.group(1)
    return ""


def merge_key(div, proj_num, name):
    """Import merge identity: division + project digits, else division + normalised name."""
    d = re.sub(r"\D", "", proj_num or "")
    if d:
        return "%s|PROJECT|%s" % (div, d)
    return "%s|NAME|%s" % (div, re.sub(r"\s+", " ", (name or "").strip().upper()))


# ------------------------------------------------------------------------------------------------ Excel import mapping
# Whole-word alias matching with a fixed claim order (a claimed column is never reused — this fixed the historic
# "pm matched inside Equipment" bug). Exact header = alias length + 100; longest alias wins.
HEADER_ALIASES = {
    "status2": ["equipment complete status v2", "equipment status v2", "status v2", "equipment status",
                "phase complete status v2", "status 2", "status two", "secondary status"],
    "status": ["phase complete status", "phase status", "status", "status v1", "primary status"],
    "lastUpdate": ["last update date", "last updated date", "last update", "last updated", "updated date", "modified date"],
    "pct": ["% complete", "percent complete", "completion", "pct complete", "% done"],
    "hours": ["union hours remaining", "hours remaining", "remaining hours", "hours left", "union hrs", "union hours"],
    "project": ["project number", "project #", "job number", "job #", "project id"],
    "task_num": ["task number", "task #", "task no", "id", "#"],
    "name": ["task name", "project task", "project name", "project description", "description", "name", "title"],
    "pm2": ["pm2", "pm 2", "pm v2", "secondary pm", "project manager 2", "assistant pm", "backup pm"],
    "pm": ["project manager", "pm", "pm1", "pm 1", "primary pm"],
    "engineer": ["engineer", "engineering"],
    "start": ["start date", "start"],
    "finish": ["critical stop date", "critical stop", "due date", "finish date", "finish", "end date"],
    "division": ["division", "div", "schedule"],
    "notes": ["update notes", "update note", "notes", "note", "comments", "comment", "status notes", "project notes"],
}
CLAIM_ORDER = ["status2", "status", "lastUpdate", "pct", "hours", "project", "task_num", "name", "pm2", "pm", "engineer",
               "start", "finish", "division", "notes"]
REQUIRED_HEADER = "name"


def norm_header(h):
    s = str(h if h is not None else "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9%#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _alias_score(header, alias):
    if header == alias:
        return len(alias) + 100
    if (" " + alias + " ") in (" " + header + " "):
        return len(alias)
    return 0


def map_headers(header_row):
    """{key: column index} for one header row. Returns {} when the required 'name' column is absent."""
    headers = [norm_header(h) for h in header_row]
    claimed, out = set(), {}
    for key in CLAIM_ORDER:
        best, best_score = None, 0
        for i, h in enumerate(headers):
            if i in claimed or not h:
                continue
            for alias in HEADER_ALIASES[key]:
                sc = _alias_score(h, alias)
                if sc > best_score:
                    best, best_score = i, sc
        if best is not None:
            out[key] = best
            claimed.add(best)
    if REQUIRED_HEADER not in out:
        return {}
    return out


def find_header_row(rows, max_scan=35):
    """(row index, mapping) of the row in the first `max_scan` rows that resolves the most keys (name required)."""
    best_i, best_map = None, {}
    for i, row in enumerate(rows[:max_scan]):
        m = map_headers(row)
        if len(m) > len(best_map):
            best_i, best_map = i, m
    return best_i, best_map


def parse_pct(raw):
    """0–100 percent from '45%', 0.45, 45, '0.45'. Values ≤ 1 without a % sign are fractions."""
    if raw is None or raw == "":
        return None
    s = str(raw).strip()
    had_pct = "%" in s
    s = s.replace("%", "").replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    if not had_pct and v <= 1:
        v = v * 100
    return max(0.0, min(100.0, v))


def parse_hours(raw):
    """Hours from a cell: numeric text, '3 days' → 24."""
    if raw is None or raw == "":
        return 0.0
    s = str(raw).strip().lower()
    m = re.search(r"-?\d+(\.\d+)?", s.replace(",", ""))
    if not m:
        return 0.0
    v = float(m.group(0))
    if "day" in s:
        v = round(v * 8)
    return v


def parse_date_cell(v):
    """Excel serial (days since 1899-12-30), datetime/date, 'YYYY-MM-DD', 'M/D/YY(YY)' → date or None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 < float(v) < 80000:
            return date(1899, 12, 30) + timedelta(days=int(v))
        return None
    return _as_date(v)


def decimal_or_none(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ punch lists (PU-05)
BIC_OPTIONS = ["PM", "Engineering", "Field Foreman", "Client", "Account Manager", "AV Closeout", "PM/Install",
               "PM/Engineering", "Client/Engineering"]
CRITICAL_LABELS = {1: "Lowest", 2: "Low", 3: "Medium", 4: "High", 5: "Highest"}
CRITICAL_COLORS = {1: "#d9ead3", 2: "#93c47d", 3: "#ffd966", 4: "#f6b26b", 5: "#e06666"}
PUNCH_ITEM_DEFAULTS = {"active": False, "bic": "PM", "critical": 3}


def punch_item_status(item, today):
    """completed ⇐ Date Completed set · overdue ⇐ open and due strictly before today · else open. The Active checkbox
    is visual only. `item` is any mapping / object with date_completed and due_by."""
    get = item.get if isinstance(item, dict) else (lambda k, d=None: getattr(item, k, d))
    if get("date_completed"):
        return "completed"
    due = _as_date(get("due_by"))
    if due is not None and due < today:
        return "overdue"
    return "open"


def is_critical_open(item, today=None):
    get = item.get if isinstance(item, dict) else (lambda k, d=None: getattr(item, k, d))
    if get("date_completed"):
        return False
    try:
        return int(get("critical") or 0) in (4, 5)
    except (TypeError, ValueError):
        return False


def punch_stats(items, today):
    s = {"total": 0, "completed": 0, "open": 0, "overdue": 0, "critical": 0, "crit5": 0, "pct": 0}
    for it in items:
        s["total"] += 1
        st = punch_item_status(it, today)
        if st == "completed":
            s["completed"] += 1
            continue
        s["open"] += 1
        if st == "overdue":
            s["overdue"] += 1
        if is_critical_open(it):
            s["critical"] += 1
            get = it.get if isinstance(it, dict) else (lambda k, d=None: getattr(it, k, d))
            if int(get("critical") or 0) == 5:
                s["crit5"] += 1
    s["pct"] = round(100 * s["completed"] / s["total"]) if s["total"] else 0
    return s


def days_overdue(item, today):
    get = item.get if isinstance(item, dict) else (lambda k, d=None: getattr(item, k, d))
    due = _as_date(get("due_by"))
    if get("date_completed") or due is None:
        return 0
    return max(0, (today - due).days)


# ------------------------------------------------------------------------------------------------ approvals (BA-02)
APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_REOPENED = "pending", "approved", "reopened"
APPROVAL_LABELS = {APPROVAL_PENDING: "Pending Approval", APPROVAL_APPROVED: "Approved", APPROVAL_REOPENED: "Reopened"}
LEGACY_APPROVAL_STATUS = {"PENDING APPROVAL": APPROVAL_PENDING, "NEEDS APPROVAL": APPROVAL_PENDING,
                          "APPROVED": APPROVAL_APPROVED, "REOPENED": APPROVAL_REOPENED, "PENDING": APPROVAL_PENDING}


def normalize_approval_status(raw):
    return LEGACY_APPROVAL_STATUS.get((raw or "").strip().upper(), APPROVAL_PENDING)


def expected_decision(current, action):
    """Workflow: pending/reopened —approve→ approved; approved —reopen→ reopened. Anything else is a no-op error."""
    if action == "approve" and current in (APPROVAL_PENDING, APPROVAL_REOPENED):
        return APPROVAL_APPROVED
    if action == "reopen" and current == APPROVAL_APPROVED:
        return APPROVAL_REOPENED
    raise ValueError("cannot %s a request that is %s" % (action, APPROVAL_LABELS.get(current, current)))


def is_awaiting(status):
    return status in (APPROVAL_PENDING, APPROVAL_REOPENED)


def approval_overdue(needed_by, status, today):
    """Overdue on Needed By: still awaiting a decision and the needed-by date is before today (BA-08)."""
    d = _as_date(needed_by)
    return is_awaiting(status) and d is not None and d < today


# ------------------------------------------------------------------------------------------------ scheduler hand-off (PS-07)
def scheduler_payload(row):
    """The complete hand-off the original never delivered: name, division, PM, hours as union, 8 h/day, Mon–Fri,
    statuses, project, start / end, engineer, notes. `row` = StatusRow or a dict with the same attributes."""
    get = row.get if isinstance(row, dict) else (lambda k, d=None: getattr(row, k, d))
    iso = lambda v: v.isoformat() if isinstance(v, (date, datetime)) else (v or None)   # noqa: E731
    return {
        "source": "status_row",
        "status_row_id": get("id"),
        "project_id": get("project_id"),
        "name": get("name") or "",
        "division": get("division") or "",
        "pm_id": get("pm_id"),
        "pm_raw": get("pm_raw") or "",
        "hours_union": float(get("hours_left") or 0),
        "hours_per_day": 8,
        "days": [1, 1, 1, 1, 1, 0, 0],
        "start": iso(get("start")),
        "end": iso(get("end")),
        "engineer": get("engineer") or "",
        "notes": get("notes") or "",
        "phase_status": get("phase_status") or "",
        "equipment_status": get("equipment_status") or "",
    }
