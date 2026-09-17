"""Pure rules for the bid pipeline and estimators (SharePoint spec §4.1–4.2, §5.1–5.2). No database, no I/O — unit-tested
in tests/unit/test_bids_rules.py. Loaders call these; pages never re-derive them.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# ------------------------------------------------------------------------------------------- statuses (§5.1)
QUOTING, SUBMITTED, AWARDED, LOST, DID_NOT_BID, ON_HOLD, NO_DECISION, UNKNOWN = (
    "quoting", "submitted", "awarded", "lost", "did_not_bid", "on_hold", "no_decision", "unknown")
OPEN_STAGES = (QUOTING, SUBMITTED)
DECIDED = (AWARDED, LOST)
STAGE_LABEL = {QUOTING: "Quoting", SUBMITTED: "Submitted", AWARDED: "Awarded", LOST: "Lost", DID_NOT_BID: "Did not bid",
               ON_HOLD: "On hold", NO_DECISION: "No decision", UNKNOWN: "Unknown"}
STAGE_ORDER = {QUOTING: 0, SUBMITTED: 1, ON_HOLD: 2, AWARDED: 3, LOST: 4, DID_NOT_BID: 5, NO_DECISION: 6, UNKNOWN: 7}

_STATUS_MAP = {
    "quoting": (QUOTING, ""), "submitted": (SUBMITTED, ""), "awarded": (AWARDED, ""), "lost": (LOST, ""),
    "did not bid": (DID_NOT_BID, ""), "didnt bid": (DID_NOT_BID, ""), "didn't bid": (DID_NOT_BID, ""), "no bid": (DID_NOT_BID, ""),
    "completed": (AWARDED, "completed"), "complete": (AWARDED, "completed"), "project complete": (AWARDED, "completed"),
    "in progress": (AWARDED, "in_progress"),
    "budget": (QUOTING, "budgetary"), "needs assessment": (QUOTING, "needs_assessment"),
    "re-bid": (SUBMITTED, "rebid"), "rebid": (SUBMITTED, "rebid"), "re bid": (SUBMITTED, "rebid"),
    "on hold": (ON_HOLD, ""), "no decision": (NO_DECISION, ""),
}


def clean_choice(raw):
    """SharePoint multi-choice values arrive as ';#In Progress;#' — strip the delimiters."""
    return re.sub(r"\s+", " ", (raw or "").replace(";#", " ").strip())


def normalize_status(raw):
    """(stage, flag) for a portal Status value. Unknown / blank -> ('unknown', '')."""
    k = clean_choice(raw).lower()
    return _STATUS_MAP.get(k, (UNKNOWN, ""))


def portal_id_text(v):
    """The Project List's Project ID as the text the P: drive uses in folder names: a Number column arrives as 9990.0 —
    '9990', never '999' (the old `rstrip(".0")` ate every trailing zero: 9990 → 999, 9900 → 99; found 2026-09-10 when
    46 open bids had no P: folder link)."""
    if v is None or v == "":
        return ""
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip()
    return re.sub(r"\.0+$", "", s) if re.fullmatch(r"\d+\.0+", s) else s


# ------------------------------------------------------------------------------------------- work type (Pipeline Snapshot)
# The portal has no "type" column; the project name says what the work is. First match wins, most specific first.
WORK_TYPES = [
    ("Access control", ("access control", "access ctrl", "access center", " access", "door", "card reader", "readers", "keyless", "badge", "maglock", "strike", "intercom", " ada ")),
    ("Cameras / CCTV", ("camera", "cctv", "nvr", "surveillance", "video management", "vms", "ptz", "dome", "genetec", "verkada", "avigilon", "milestone")),
    ("Alarm / intrusion", ("alarm", "intrusion", "burglar", "panic", "duress", "motion")),
    ("AV / conferencing", ("av ", " av", "audio", "video wall", "display", "projector", "conference", "boardroom", "board room", "classroom", "auditorium", "sound",
                           "speaker", "paging", "microphone", "digital signage", "signage", "teams room", "zoom", "podium", "lectern", "vocia")),
    ("Network / cabling", ("network", "cabling", "cable", "fiber", "fibre", "cat6", "cat 6", "cat5", "wap", "wireless", "wi-fi", "wifi", "switch", "idf", "mdf",
                           "structured", "backbone", "data drop", "drops", "uplink", "firewall", "router")),
    ("IT / managed services", ("managed", "msp", "help desk", "helpdesk", "it services", "it support", "server", "backup", "m365", "office 365", "software license", "licensing", "hosting", "cloud")),
    ("Hardware / box sale", ("box sale", "box sales", "hardware only", "equipment only", "quote for", "laptop", "laptops", "ups", "monitor", "printer", "toner")),
    ("Service / repair", ("repair", "replace", "replacement", "service call", "maintenance", "troubleshoot", "warranty", "renewal", "support agreement", "annual")),
    ("RFP / bid package", ("rfp", "rfq", "ifb", "bid package", "public bid", "proposal request")),
]


def work_type(project_name, division=""):
    """A coarse, explainable type for a bid from its project name (the Pipeline Snapshot's 'project type' and a
    win-rate factor). Falls back to the division's trade, then 'Other'."""
    n = " " + (project_name or "").lower().replace("_", " ") + " "
    for label, keys in WORK_TYPES:
        if any(k in n for k in keys):
            return label
    return {"070": "Security (other)", "040": "AV (other)", "020": "IT (other)", "080": "Airport / roadway", "030": "Staffing", "060": "Structured cabling"}.get(division or "", "Other")


def archive_stage(stage, flag):
    """Project Archive rows are the portal's dead file: 2,000 of them still read Quoting / Submitted from 2022 because nobody
    closed them before archiving. An archived row is never open pipeline -- it becomes 'no_decision' with the flag
    'archived_open' so the raw status stays visible on the bid page (build log GAP-4)."""
    if stage in OPEN_STAGES:
        return NO_DECISION, "archived_open"
    return stage, flag


# ------------------------------------------------------------------------------------------- numbers (§2 keys)
_DIGITS = re.compile(r"\d+")


def job_number_candidates(raw):
    """Canonical SL keys a typed Job Number may mean, best first. Never strips a trailing 000000 that was typed;
    '260071' also tries '260071000000' (the portal often omits the suffix for the 000000 jobs)."""
    s = (raw or "").strip().upper().replace(" ", "")
    if not s:
        return []
    digits = "".join(_DIGITS.findall(s))
    out = []
    if s.isalnum() and not s.isdigit():          # text keys like INT21413 stay as typed
        out.append(s)
    if len(digits) == 12:
        out.append(digits)
    elif len(digits) == 6:
        out += [digits, digits + "000000"]
    elif len(digits) == 8 and s.count("-") == 1 and re.match(r"^\d{2}-\d{4}$", s):
        pass                                     # AV quote number YY-NNNN, not a job
    elif digits:
        out.append(digits)
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x); uniq.append(x)
    return uniq


def resolve_job_number(raw, known):
    """The canonical project number for a typed Job Number, or None. `known` = set of canonical numbers."""
    for cand in job_number_candidates(raw):
        if cand in known:
            return cand
    return None


def quote_reference(raw):
    """'YY-NNNN' when the text carries an AV quote number, else None."""
    m = re.search(r"\b(\d{2})-(\d{4})\b", raw or "")
    return "%s-%s" % (m.group(1), m.group(2)) if m else None


# ------------------------------------------------------------------------------------------- people (§4.1)
def norm_name_key(raw):
    """Fold case / whitespace / punctuation: 'Oakridge ' -> 'OAKRIDGE', 'OT ' -> 'OT', 'Van Mapleford, Jim' -> 'VAN MAPLEFORD JIM'."""
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", (raw or ""))
    return re.sub(r"\s+", " ", s).strip().upper()


HOUSE_REPS = {"OT", "OTHER", "HOUSE", "NONE", "N/A", "NA"}
NOT_A_PERSON = {"", "TBD", "?", "UNKNOWN", "NONE", "N/A", "NA", "OT", "OTHER", "HOUSE"}   # OT / Other = house account, never a person

# Explicit disambiguation overrides are deployment data, separate from code.
from apps.core.business_config import get as business_config


PARTICLES = {"VAN", "VON", "DE", "DEL", "DELLA", "LA", "LE", "ST", "MAC", "DI", "DA", "DU"}


def _edit_distance(a, b):
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def split_joint(raw):
    """A joint entry ('MIKE E CHRIS S', 'CEDARWELL / NORTHWOOD', 'Redwood-Ivybridge') -> its name tokens, first first.
    Tokens are surnames, or 'FIRST L' first-name-plus-initial pairs."""
    key = norm_name_key(re.sub(r"[/+&,;-]|\bAND\b", " ", (raw or ""), flags=re.I))
    words = key.split()
    out, i = [], 0
    while i < len(words):
        if words[i] in PARTICLES and i + 1 < len(words):            # 'VAN MAPLEFORD', 'DE LA CRUZ' are one surname
            j = i + 1
            while j < len(words) and words[j] in PARTICLES:
                j += 1
            if j < len(words):
                out.append(" ".join(words[i:j + 1])); i = j + 1; continue
        if i + 1 < len(words) and len(words[i + 1]) == 1:
            out.append(words[i] + " " + words[i + 1]); i += 2
        else:
            out.append(words[i]); i += 1
    return out


def _weight(e):
    """Evidence that a person is the one who bids: jobs managed + jobs sold (reps bid too)."""
    return (e.get("n_pm") or 0) + (e.get("n_sales") or 0)


def _rank_hits(hits):
    hits = sorted(hits, key=lambda e: (-_weight(e), not e.get("active"), e.get("role") not in ("pm", "head_pm"), e["name"]))
    if len(hits) == 1:
        return hits, "unique"
    top, second = hits[0], hits[1]
    stems = {norm_name_key(e["name"]).split()[0][:3] for e in hits}
    if len(stems) == 1:                                   # Dave / David Hazelton, Phil / Phillip Ivybridge: one person, two rows
        return hits, "dominant"
    if _weight(top) >= 10 and _weight(second) <= 2:      # one person carries the jobs, the others (almost) none
        return hits, "dominant"
    pms = [e for e in hits if e.get("role") in ("pm", "head_pm")]
    if len(pms) == 1 and all(_weight(e) == 0 for e in hits if e is not pms[0]):
        return [pms[0]] + [e for e in hits if e is not pms[0]], "dominant"   # the only PM among people who never ran a job
    return hits, "ambiguous"


def _surname_keys(name):
    """Last word, and the last two words joined ('Jim Van Mapleford' -> {'DETTUM', 'VANDETTUM'})."""
    w = norm_name_key(name).split()
    if not w:
        return set()
    keys = {w[-1]}
    if len(w) >= 3:
        keys.add(w[-2] + w[-1])
    return keys


def alias_candidates(raw, employees):
    """Rank employees for a raw bidder / rep value.

    employees: iterable of dicts {name, active, role, n_pm} (role = ptt_employee_role, n_pm = SL jobs managed).
    Returns [(name, confidence, rule)] best first. exact 1.0 · name_words 0.98 · fixed_alias 0.95 · surname 0.9 ·
    first_initial 0.9 · surname_dominant 0.85 · surname_fuzzy 0.8 (typo within 2 edits) · surname_ambiguous 0.6 (review) ·
    joint entries resolve their first name and carry rule suffix '+joint'."""
    key = norm_name_key(raw)
    if not key or key in NOT_A_PERSON:
        return []
    by_name = {norm_name_key(e["name"]): e for e in employees}
    if key in by_name:
        return [(by_name[key]["name"], 1.0, "exact")]
    parts = key.split()
    if len(parts) >= 2:
        for e in employees:
            ek = norm_name_key(e["name"]).split()
            if ek and set(parts) == set(ek):
                return [(e["name"], 0.98, "name_words")]
    fixed_aliases = business_config("bid_fixed_aliases", {})
    if key in fixed_aliases:
        fixed = fixed_aliases[key]
        if norm_name_key(fixed) in by_name:
            return [(fixed, 0.95, "fixed_alias")]
        if " " not in fixed:                      # alias to a surname: fall through with the surname
            key, raw = fixed, fixed
    tokens = split_joint(raw)
    if len(tokens) > 1:
        first = alias_candidates(tokens[0], employees)
        return [(n, c, r + "+joint") for n, c, r in first]
    token = tokens[0] if tokens else key
    tp = token.split()
    if len(tp) == 2 and len(tp[1]) == 1:          # FIRST L
        hits = [e for e in employees if (lambda w: len(w) >= 2 and w[0] == tp[0] and w[-1].startswith(tp[1]))(norm_name_key(e["name"]).split())]
        if hits:
            ranked, kind = _rank_hits(hits)
            conf = {"unique": 0.9, "dominant": 0.85, "ambiguous": 0.6}[kind]
            return [(e["name"], conf if i == 0 else 0.3, "first_initial" if kind != "ambiguous" else "first_initial_ambiguous") for i, e in enumerate(ranked)]
        return []
    surname = "".join(tp) if len(tp) >= 2 and all(w in PARTICLES for w in tp[:-1]) else tp[-1]     # 'VAN MAPLE' -> 'VANMAPLE'
    hits = [e for e in employees if surname in _surname_keys(e["name"])]
    if not hits and len(surname) >= 5:
        near = [(e, min(_edit_distance(surname, k) for k in _surname_keys(e["name"]))) for e in employees if _surname_keys(e["name"])]
        hits = [e for e, d in near if d <= 2]
        if hits:
            ranked, kind = _rank_hits(hits)
            conf = {"unique": 0.8, "dominant": 0.8, "ambiguous": 0.5}[kind]
            return [(e["name"], conf if i == 0 else 0.3, "surname_fuzzy" if kind != "ambiguous" else "surname_fuzzy_ambiguous") for i, e in enumerate(ranked)]
        return []
    if not hits:
        return []
    ranked, kind = _rank_hits(hits)
    if kind == "unique":
        return [(ranked[0]["name"], 0.9, "surname")]
    if kind == "dominant":
        return [(e["name"], 0.85 if i == 0 else 0.3, "surname_dominant") for i, e in enumerate(ranked)]
    return [(e["name"], 0.6 if i == 0 else 0.3, "surname_ambiguous") for i, e in enumerate(ranked)]


def is_house_rep(raw):
    return norm_name_key(raw) in HOUSE_REPS


# ------------------------------------------------------------------------------------------- values
def to_decimal(v):
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def to_date(v):
    """Graph dateTime strings ('2026-08-19T05:00:00Z') or dates -> date; None when blank / unparsable."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%?")


def probability_from(raw):
    """'25%' -> 25; 0.25 -> 25; 'Quoting' / blank -> None (unscored — never 0, SharePoint spec §5.2)."""
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float, Decimal)):
        f = float(raw)
        return int(round(f * 100)) if 0 < f <= 1 else int(round(f))
    m = _PCT.fullmatch(str(raw).strip())
    if not m:
        return None
    f = float(m.group(1))
    return int(round(f * 100)) if ("%" not in str(raw) and 0 < f <= 1) else int(round(f))


def bid_margin(value, budget):
    """(value - budget) / value, or None."""
    if value in (None, 0) or budget is None:
        return None
    try:
        return (Decimal(value) - Decimal(budget)) / Decimal(value)
    except (InvalidOperation, ZeroDivisionError):
        return None


def division_for(project_division, estimator_top_division, home_subaccount, sub2div):
    """SharePoint spec §4.2: SL job's division -> estimator's dominant PM division -> home subaccount -> ''."""
    if project_division:
        return project_division
    if estimator_top_division:
        return estimator_top_division
    sub = (home_subaccount or "").strip()
    if sub in sub2div:
        return sub2div[sub]
    if sub[:3] in {v for v in sub2div.values()}:
        return sub[:3]
    return ""


# ------------------------------------------------------------------------------------------- stage timing (§5.1)
def stage_entries(versions):
    """versions: [(modified_at, stage)] in any order -> {stage: first datetime the row entered that stage}."""
    out, prev = {}, None
    for when, stage in sorted(versions, key=lambda x: x[0]):
        if stage != prev and stage not in out:
            out[stage] = when
        prev = stage
    return out


def won(stage, sl_billed):
    """D4: SL is the truth for won — a linked job with billings is won whatever the portal says."""
    return stage == AWARDED or (sl_billed or 0) > 0


def expected_decision_date(submitted_on, bid_due, median_cycle_days):
    base = submitted_on or bid_due
    if not base:
        return None
    from datetime import timedelta
    return base + timedelta(days=int(median_cycle_days or 0))
