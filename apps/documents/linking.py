"""Pure linking rules: what a folder or file name says about the SL job, bid or customer it belongs to (SharePoint spec
§7.1). No database, no I/O — unit-tested in tests/unit/test_documents_linking.py. The loader resolves the candidate
numbers against the set of canonical SL numbers it holds (`resolve()`), the rules here never decide existence.

Numbers (000000 rule, CLAUDE.md): a 6-digit number may mean itself or itself + '000000' — both are tried, best first,
and a 12-digit number is exact. The AV division's "quote numbers" `YY-NNNN` (SS PM folders: '25-4476 Bulley-Andrews
Timeline Theatre') are SL job numbers with a hyphen after the year — verified 2026-09-10: 25-4476 = 254476 BULLEY
ANDREWS TIMELINE THEATRE AV, 24-1649 = 241649000000. `core_project.quote_reference` is a different thing (SL's own
'SP 7571' / 'HD#1234' proposal numbers, shared by many jobs) and only links when unique.
"""

import re

from apps.bids.rules import job_number_candidates

# rule -> (label, default confidence). Confidence < LOW (0.8) is shown as "check" and can be confirmed / rejected.
RULES = {
    "folder_number":      ("Job number in the folder name", 0.95),
    "folder_number_in":   ("Job number inside the folder name", 0.85),
    "file_number":        ("Job number leads the file name", 0.90),
    "file_number_in":     ("Job number inside the file name", 0.75),
    "quote_number":       ("AV quote number YY-NNNN (= SL job number)", 0.92),
    "portal_id":          ("Project Portal ID leads the folder name (P: drive job folder)", 0.92),
    "sl_quote_reference": ("SL proposal number (SP / HD#) unique to one job", 0.60),
    "planner_folder":     ("Planner attachment folder named after the plan", 0.80),
    "bid_name":           ("Client + project name match a bid (fuzzy)", 0.50),
    "list_attachment":    ("Attachment on the Project List row", 1.00),
    "manual":             ("Set by hand", 1.00),
}
LOW = 0.8

_TWELVE = re.compile(r"(?<![\dA-Za-z])(\d{6})-?(000000)(?![\d])")
_SIX = re.compile(r"(?<![\d.-])(\d{6})(?![\d])")
_QUOTE = re.compile(r"(?<![\d])(\d{2})-(\d{4})(?![\d])")
_TEXTKEY = re.compile(r"(?<![A-Za-z\d])([A-Z]{2,4}\d{4,6})(?![A-Za-z\d])")
_SLREF = re.compile(r"(?<![A-Za-z])(SP|HD)\s*#?\s*(\d{3,5})(?![\d])", re.I)
_LEADING_JUNK = re.compile(r"^[\s_\-#(\[]*")
# P: drive job folders (2022 →) are '<year> Projects/<Client>/<Portal ID> - <name>[ YY-NNNN]': the 3–5 digit Project Portal
# ID leads the name and is followed by a separator (so '2024 Projects' or '01. Pictures' never match).
_PORTAL = re.compile(r"^[\s_#(\[]*(\d{3,5})\s*[-–—_:.]\s*\S")

STOPWORDS = {"the", "of", "and", "a", "an", "at", "in", "for", "to", "inc", "llc", "ltd", "co", "corp", "bid", "bids", "quote", "quotes",
             "proposal", "proposals", "project", "projects", "job", "av", "it", "new", "rev", "revised", "final", "draft", "copy",
             "scope", "pdf", "docx", "xlsx", "budget", "budgetary", "phase", "floor", "room", "building", "st", "ave", "rd", "suite",
             "documents", "document", "drawings", "drawing", "estimating", "estimate", "addendum", "submittal", "submittals", "photos",
             "pictures", "misc", "general", "template", "templates", "archive", "archived", "old", "test", "walkthrough", "site", "walk"}


def _plausible_year(yy):
    return 10 <= int(yy) <= 39


def number_hits(name):
    """Every SL-number-shaped token in a name: [(candidates, matched_text, leading, kind)] best first. kind = 'twelve' |
    'six' | 'quote' | 'text'. `leading` = the token starts the name (after leading punctuation)."""
    s = name or ""
    body = _LEADING_JUNK.sub("", s)
    hits, taken = [], []

    def free(span):
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for m in _TWELVE.finditer(s):
        if free(m.span()):
            taken.append(m.span())
            hits.append(([m.group(1) + m.group(2)], m.group(0), body.startswith(m.group(0)), "twelve"))
    for m in _QUOTE.finditer(s):
        if free(m.span()) and _plausible_year(m.group(1)):
            taken.append(m.span())
            digits = m.group(1) + m.group(2)
            hits.append((job_number_candidates(digits), m.group(0), body.startswith(m.group(0)), "quote"))
    for m in _SIX.finditer(s):
        if free(m.span()):
            taken.append(m.span())
            hits.append((job_number_candidates(m.group(1)), m.group(0), body.startswith(m.group(0)), "six"))
    for m in _TEXTKEY.finditer(s):
        if free(m.span()):
            taken.append(m.span())
            hits.append(([m.group(1).upper()], m.group(0), body.startswith(m.group(0)), "text"))
    return hits


def portal_id_hit(name):
    """The Project Portal ID a job folder name starts with ('9955 - NEIU EL Centro WAP Install' -> '9955'), else None.
    The loader resolves it against the Project List's `portal_project_id` (unique per list row)."""
    m = _PORTAL.match(name or "")
    return m.group(1).lstrip("0") or m.group(1) if m else None


def folder_rules(name):
    """[(candidates, rule, confidence, evidence)] a folder name yields. 'quote' hits carry rule quote_number."""
    out = []
    for cands, text, leading, kind in number_hits(name):
        if not cands:
            continue
        if kind == "quote":
            rule, conf = "quote_number", RULES["quote_number"][1] if leading else RULES["quote_number"][1] - 0.1
        elif leading:
            rule, conf = "folder_number", RULES["folder_number"][1]
        else:
            rule, conf = "folder_number_in", RULES["folder_number_in"][1]
        out.append((cands, rule, round(conf, 2), {"matched": text, "text": name}))
    return out


def file_rules(name):
    out = []
    for cands, text, leading, kind in number_hits(name):
        if not cands:
            continue
        if kind == "quote":
            rule, conf = "quote_number", RULES["quote_number"][1] - (0 if leading else 0.15)
        elif leading:
            rule, conf = "file_number", RULES["file_number"][1]
        else:
            rule, conf = "file_number_in", RULES["file_number_in"][1]
        out.append((cands, rule, round(conf, 2), {"matched": text, "text": name}))
    return out


def sl_quote_refs(name):
    """SL proposal numbers written in a name ('SP 6199', 'HD#1234') -> ['SP6199', 'HD1234'] (normalised: letters + digits)."""
    return ["%s%s" % (m.group(1).upper(), m.group(2)) for m in _SLREF.finditer(name or "")]


def normalize_quote_reference(raw):
    """core_project.quote_reference as the same normalised key ('SP  4476' -> 'SP4476'); '' when it is not one number."""
    s = (raw or "").strip().upper()
    m = re.fullmatch(r"(SP|HD)\s*#?\s*(\d{3,5})", s)
    return "%s%s" % (m.group(1), m.group(2)) if m else ""


def planner_plan_name(path):
    """'Microsoft Planner/AV Fabrication_abc123XYZ/…' -> 'AV Fabrication'; None when the path is not a Planner folder."""
    parts = [p for p in (path or "").split("/") if p]
    for i, p in enumerate(parts[:-1] if len(parts) > 1 else parts):
        if p.lower() == "microsoft planner" and i + 1 < len(parts):
            seg = parts[i + 1]
            if "_" in seg:
                base, tail = seg.rsplit("_", 1)
                if len(tail) >= 8 and re.fullmatch(r"[A-Za-z0-9_-]+", tail):
                    return base
            return seg
    return None


def words(text):
    """Meaningful lower-case words of a name (numbers, stop words and 1–2 letter tokens dropped)."""
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z']+", text or ""):
        w = w.lower().strip("'")
        if len(w) >= 3 and w not in STOPWORDS:
            out.append(w)
    return out


def bid_name_score(name, client_name, project_name):
    """0..1 — how much of the folder / file name's vocabulary the bid's client + project name covers. A link is only
    proposed at ≥ 0.6 and never on fewer than two shared meaningful words (one word — "documents", "drawings",
    "Washington" — matched far too much on the real libraries)."""
    fw = set(words(name))
    if len(fw) < 2:
        return 0.0
    bw = set(words("%s %s" % (client_name or "", project_name or "")))
    common = fw & bw
    if len(common) < 2:
        return 0.0
    return round(len(common) / len(fw), 2)


def bid_name_matches(name, bids, threshold=0.6, limit=3):
    """bids: iterable of (bid_id, client_name, project_name) -> [(bid_id, score)] best first, at or above the threshold."""
    scored = []
    for bid_id, client, pname in bids:
        s = bid_name_score(name, client, pname)
        if s >= threshold:
            scored.append((bid_id, s))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:limit]


def resolve(candidates, known):
    """First candidate that is a known canonical number, or None."""
    for c in candidates:
        if c in known:
            return c
    return None


# ------------------------------------------------------------------------------------------- folder-naming hygiene
STYLES = {
    "number_dash_name": "265092 - Name",
    "number_name": "265092 Name",
    "number_only": "265092",
    "quote_number": "25-4476 Name",
    "name_number": "Name … 265092",
    "name_only": "Name only (no job number)",
}


def folder_style(name):
    """Which naming convention a project folder follows (per-division hygiene report)."""
    s = (name or "").strip()
    if re.match(r"^\d{6}(-?000000)?\s*$", s):
        return "number_only"
    if re.match(r"^\d{6}(-?000000)?\s*[-–—_:]\s*\S", s):
        return "number_dash_name"
    if re.match(r"^\d{6}(-?000000)?\S*\s+\S", s) or re.match(r"^\d{6}[A-Za-z]", s):
        return "number_name"
    if re.match(r"^\d{2}-\d{4}\b", s):
        return "quote_number"
    if number_hits(s):
        return "name_number"
    return "name_only"


def confidence_label(conf):
    c = float(conf or 0)
    if c >= 0.9:
        return "high"
    if c >= LOW:
        return "good"
    return "check"
