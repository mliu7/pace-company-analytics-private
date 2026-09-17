"""Proposal checks (SharePoint spec §7.3) — pure rules over extracted text, unit-tested on fixture texts under
tests/fixtures/documents/. No database, no network.

What runs on a proposal: template / version fingerprint, the required sections (scope, exclusions, clarifications,
terms, schedule, price, signature block, revision / date), consistency with the linked bid (project name, client,
price vs bid value ±2 %, proposal date vs bid due date), the exclusions / clarifications / terms as written, and the
deviations from the standard clause set in docs/proposal_standard_clauses.md (placeholders until Owner supplies the
real clauses — the file says so). Every finding carries evidence (page, quoted text, expected vs found).

Model-assisted review (Claude API) is OFF: `model_review()` is the hook and raises until Owner approves it explicitly.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

CHECKS = {
    "not_a_proposal":       ("Not recognised as a proposal", "info"),
    "template_fingerprint": ("Template / version fingerprint", "info"),
    "template_unknown":     ("No known proposal template recognised", "warn"),
    "missing_section":      ("Required section missing", "warn"),
    "bid_project_name":     ("Project name does not match the bid", "warn"),
    "bid_client":           ("Client name not found in the proposal", "warn"),
    "bid_price":            ("Price differs from the bid value", "error"),
    "bid_price_missing":    ("No price found to compare with the bid", "warn"),
    "bid_date":             ("Proposal dated after the bid due date", "warn"),
    "clause_missing":       ("Standard clause missing", "warn"),
    "clause_deviation":     ("Standard clause worded differently", "info"),
    "clause_extra":         ("Clause not in the standard set", "info"),
    "payment_terms":        ("Payment terms deviate from the standard", "warn"),
    "warranty":             ("Warranty deviates from the standard", "warn"),
}
PRICE_TOLERANCE = Decimal("0.02")
MODEL_REVIEW_ENABLED = False

# Fingerprint phrases: header / footer text a Pace proposal template carries. PLACEHOLDERS — Owner supplies the real
# phrases (docs/proposal_standard_clauses.md § Template phrases overrides these when present).
TEMPLATES = [
    {"id": "pace_proposal", "label": "Pace proposal (generic)",
     "phrases": ["pace systems", "proposal", "scope of work", "exclusions", "terms and conditions"], "min": 3},
]
PROPOSAL_MARKERS = ["proposal", "scope of work", "exclusions", "clarifications", "terms and conditions", "total price",
                    "we propose", "we are pleased to", "acceptance", "pace systems"]

SECTIONS = {
    "scope":          (r"scope\s+of\s+(work|services|supply)|^\s*scope\s*:?\s*$|project\s+scope|system\s+description", "Scope"),
    "exclusions":     (r"^\s*exclusions?\b|not\s+included|excluded\s+from\s+this\s+proposal", "Exclusions"),
    "clarifications": (r"^\s*clarifications?\b|^\s*assumptions?\b|^\s*qualifications?\b", "Clarifications"),
    "terms":          (r"terms\s+(and|&)\s+conditions|^\s*terms\s*:?\s*$|payment\s+terms", "Terms"),
    "schedule":       (r"^\s*schedule\b|project\s+schedule|lead\s*time|^\s*timeline\b|duration", "Schedule"),
    "price":          (r"^\s*(price|pricing|investment|cost\s+summary|total\s+price|proposal\s+price|contract\s+price|total)\b", "Price"),
    "signature":      (r"signature|accepted\s+by|authorized\s+(signature|representative)|^\s*signed\s*:|^\s*by\s*:\s*_{3,}", "Signature block"),
    "revision_date":  (r"\brev(ision)?\.?\s*[:#]?\s*\d|\bversion\s*[:#]?\s*\d|^\s*date\s*:|\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+\d{4}|\b\d{1,2}/\d{1,2}/\d{2,4}\b", "Revision / date"),
}
_SECTION_RE = {k: re.compile(v[0], re.I | re.M) for k, v in SECTIONS.items()}
_HEADING = re.compile(r"^\s*(?:\d+[.)]?\s*)?([A-Z][A-Za-z &/]{2,40})\s*:?\s*$", re.M)
_MONEY = re.compile(r"\$\s?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?)")
_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b|\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s+(\d{4})\b", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
_WORD = re.compile(r"[a-z0-9]+")
_BULLET = re.compile(r"^\s*(?:[-•*·▪●o]|\d+[.)]|[a-z][.)])\s+", re.I)


# ------------------------------------------------------------------------------------------- helpers
def page_of(offset, page_offsets):
    n = 1
    for i, o in enumerate(page_offsets or []):
        if offset >= o:
            n = i + 1
    return n


def _quote(text, offset, width=90):
    a, b = max(0, offset - width // 3), min(len(text), offset + width)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def words(s):
    return _WORD.findall((s or "").lower())


def similarity(a, b):
    """Word-set Jaccard similarity 0..1 (numbers count as words)."""
    wa, wb = set(words(a)), set(words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def to_decimal(s):
    try:
        return Decimal(str(s).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None


def to_date(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


# ------------------------------------------------------------------------------------------- recognition
def is_proposal(text, name=""):
    """(bool, matched markers) — three markers in the text, or 'proposal' in the file name plus one marker."""
    low = (text or "").lower()
    hits = [m for m in PROPOSAL_MARKERS if m in low]
    named = "proposal" in (name or "").lower()
    return (len(hits) >= 3 or (named and len(hits) >= 1)), hits


def template_fingerprint(text, templates=None):
    """{'template', 'label', 'matched', 'version'} — the best template by matched header / footer phrases."""
    low = (text or "").lower()
    best = {"template": "", "label": "", "matched": [], "version": ""}
    for t in (templates or TEMPLATES):
        matched = [p for p in t["phrases"] if p.lower() in low]
        if len(matched) >= t.get("min", 2) and len(matched) > len(best["matched"]):
            best = {"template": t["id"], "label": t.get("label", t["id"]), "matched": matched, "version": ""}
    m = re.search(r"\b(?:rev(?:ision)?|version|v)\.?\s*[:#]?\s*([0-9]+(?:\.[0-9]+)?)\b", low)
    if m:
        best["version"] = m.group(1)
    return best


def find_sections(text):
    """{section: (offset, matched heading text)} for every required section found."""
    out = {}
    for key, rx in _SECTION_RE.items():
        m = rx.search(text or "")
        if m:
            lead = len(m.group(0)) - len(m.group(0).lstrip())     # `^\s*` under re.M may start on the blank line before
            out[key] = (m.start() + lead, m.group(0).strip())
    return out


def money_amounts(text):
    """[(Decimal, offset)] of every $ amount in the text."""
    out = []
    for m in _MONEY.finditer(text or ""):
        v = to_decimal(m.group(1))
        if v is not None:
            out.append((v, m.start()))
    return out


def proposal_price(text):
    """(amount, offset) — the amount on a 'total / price / investment' line, else the largest amount; None when none."""
    lines_hit = []
    for line_m in re.finditer(r"[^\n]+", text or ""):
        line = line_m.group(0)
        if re.search(r"total|price|investment|sum of|amount|lump", line, re.I):
            for v, off in money_amounts(line):
                lines_hit.append((v, line_m.start() + off))
    if lines_hit:
        return max(lines_hit, key=lambda x: x[0])
    amts = money_amounts(text)
    return max(amts, key=lambda x: x[0]) if amts else None


def dates_in(text, limit_chars=None):
    """[(date, offset)] for every date written in the text (M/D/YYYY or 'Month D, YYYY')."""
    out = []
    for m in _DATE.finditer((text or "")[:limit_chars] if limit_chars else (text or "")):
        try:
            if m.group(1):
                y = int(m.group(3))
                y = y + 2000 if y < 100 else y
                out.append((date(y, int(m.group(1)), int(m.group(2))), m.start()))
            else:
                out.append((date(int(m.group(6)), _MONTHS[m.group(4).lower()], int(m.group(5))), m.start()))
        except ValueError:
            continue
    return out


def proposal_date(text):
    """The date after 'Date:' when present, else the first date in the first 2,500 characters."""
    m = re.search(r"date\s*:\s*([^\n]{4,40})", text or "", re.I)
    if m:
        ds = dates_in(m.group(1))
        if ds:
            return ds[0][0]
    ds = dates_in(text, 2500)
    return ds[0][0] if ds else None


# ------------------------------------------------------------------------------------------- clauses
def _section_body(text, start):
    """Lines after a heading until the next heading-looking line or a blank gap of two lines."""
    body = text[start:]
    body = body.split("\n", 1)[1] if "\n" in body else ""
    out, blank = [], 0
    for line in body.split("\n"):
        if _HEADING.match(line) and not _BULLET.match(line):
            break
        if not line.strip():
            blank += 1
            if blank >= 2 and out:
                break
            continue
        blank = 0
        out.append(line)
    return out


def extract_clauses(text):
    """{'exclusions': [...], 'clarifications': [...], 'terms': [...]} — the bullet / line items under each heading."""
    found = find_sections(text)
    out = {}
    for key in ("exclusions", "clarifications", "terms"):
        items = []
        if key in found:
            lines = _section_body(text, found[key][0])
            bullets = [l for l in lines if _BULLET.match(l)]
            for line in (bullets or lines):               # a bulleted section: the bullets are the clauses, prose is not
                s = _BULLET.sub("", line).strip()
                if len(s) >= 4:
                    items.append(s)
        out[key] = items
    return out


def load_standard_clauses(md_text):
    """Parse docs/proposal_standard_clauses.md: '## <Section>' headings with '- clause' lines ->
    {'exclusions': [...], 'clarifications': [...], 'terms': [...], 'payment_terms': [...], 'warranty': [...],
    'template_phrases': [...]}. Placeholder lines (containing 'PLACEHOLDER') are ignored so nothing fires until the
    real clauses are in."""
    out, current = {}, None
    for line in (md_text or "").splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            current = re.sub(r"[^a-z]+", "_", h.group(1).strip().lower()).strip("_")
            out.setdefault(current, [])
            continue
        b = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if b and current and "PLACEHOLDER" not in b.group(1).upper():
            out[current].append(b.group(1))
    return out


def compare_clauses(found, standard, same=0.9, similar=0.45):
    """found / standard: lists of clause strings -> {'missing': [std], 'deviations': [(std, found, sim)], 'extra': [found]}."""
    missing, deviations, matched_found = [], [], set()
    for s in standard:
        best, best_sim = None, 0.0
        for f in found:
            sim = similarity(s, f)
            if sim > best_sim:
                best, best_sim = f, sim
        if best_sim >= same:
            matched_found.add(best)
        elif best_sim >= similar:
            matched_found.add(best)
            deviations.append((s, best, round(best_sim, 2)))
        else:
            missing.append(s)
    extra = [f for f in found if f not in matched_found]
    return {"missing": missing, "deviations": deviations, "extra": extra}


# ------------------------------------------------------------------------------------------- the run
def _f(check_id, message, **evidence):
    label, severity = CHECKS[check_id]
    return {"check_id": check_id, "severity": severity, "message": message[:500], "evidence": evidence}


def run_checks(text, bid=None, standard=None, name="", page_offsets=None, templates=None):
    """All findings for one document. `bid` is a dict (project_name, client_name, value, bid_due, submitted_on,
    job_number_raw) or None; `standard` the parsed clause set (load_standard_clauses) or None."""
    text = text or ""
    out = []
    ok, markers = is_proposal(text, name)
    if not ok:
        return [_f("not_a_proposal", "Text does not read like a proposal (markers: %s)" % (", ".join(markers) or "none"), markers=markers)]
    tpl_list = templates
    if standard and standard.get("template_phrases"):
        tpl_list = [{"id": "pace_proposal", "label": "Pace proposal", "phrases": standard["template_phrases"], "min": max(2, len(standard["template_phrases"]) // 2)}]
    fp = template_fingerprint(text, tpl_list)
    if fp["template"]:
        out.append(_f("template_fingerprint", "Template %s%s (%d phrases matched)" % (fp["label"], (" rev " + fp["version"]) if fp["version"] else "", len(fp["matched"])), **fp))
    else:
        out.append(_f("template_unknown", "No known proposal template phrases found", **fp))
    found = find_sections(text)
    for key, (_, label) in SECTIONS.items():
        if key not in found:
            out.append(_f("missing_section", "%s section not found" % label, section=key))
    price = proposal_price(text)
    if bid:
        pname = bid.get("project_name") or ""
        if pname and similarity(pname, text[:4000]) == 0 and not any(w in text.lower() for w in words(pname) if len(w) >= 4):
            out.append(_f("bid_project_name", "Bid project name %r not found in the first pages" % pname, expected=pname))
        client = bid.get("client_name") or ""
        if client:
            cw = [w for w in words(client) if len(w) >= 4 and w not in ("company", "corporation", "district", "school", "village", "city")]
            if cw and not any(w in text.lower() for w in cw):
                out.append(_f("bid_client", "Client %r not named in the proposal" % client, expected=client))
        value = to_decimal(bid.get("value")) if bid.get("value") is not None else None
        if value and value > 0:
            if price is None:
                out.append(_f("bid_price_missing", "No price found to compare with the bid value $%s" % f"{value:,.0f}", expected=str(value)))
            else:
                diff = abs(price[0] - value) / value
                if diff > PRICE_TOLERANCE:
                    out.append(_f("bid_price", "Proposal price $%s vs bid value $%s (%.1f%% apart)" % (f"{price[0]:,.0f}", f"{value:,.0f}", diff * 100),
                                  expected=str(value), found=str(price[0]), page=page_of(price[1], page_offsets), quote=_quote(text, price[1])))
        due = to_date(bid.get("bid_due"))
        pdate = proposal_date(text)
        if due and pdate and pdate > due:
            out.append(_f("bid_date", "Proposal dated %s, after the bid due date %s" % (pdate.isoformat(), due.isoformat()), expected=due.isoformat(), found=pdate.isoformat()))
    clauses = extract_clauses(text)
    if standard:
        for key in ("exclusions", "clarifications", "terms"):
            std = standard.get(key) or []
            if not std:
                continue
            cmp = compare_clauses(clauses.get(key) or [], std)
            for s in cmp["missing"]:
                out.append(_f("clause_missing", "Standard %s clause missing: %s" % (key.rstrip("s"), s[:120]), section=key, expected=s))
            for s, f, sim in cmp["deviations"]:
                out.append(_f("clause_deviation", "%s clause worded differently (%.0f%% similar): %s" % (key.title(), sim * 100, f[:120]), section=key, expected=s, found=f, similarity=sim))
            for f in cmp["extra"]:
                out.append(_f("clause_extra", "%s clause not in the standard set: %s" % (key.title(), f[:120]), section=key, found=f))
        for key, check in (("payment_terms", "payment_terms"), ("warranty", "warranty")):
            std = standard.get(key) or []
            if not std:
                continue
            m = re.search(r"(payment\s+terms?|net\s+\d+|progress\s+billing|due\s+upon)[^\n]*" if key == "payment_terms" else r"warrant(y|ies)[^\n]*", text, re.I)
            if m:
                line = m.group(0)
                if max(similarity(line, s) for s in std) < 0.45:
                    out.append(_f(check, "%s: %s" % (CHECKS[check][0], line[:160]), expected=std[0], found=line[:200], page=page_of(m.start(), page_offsets), quote=_quote(text, m.start())))
    return out


def model_review(text, bid=None):
    """Hook for the model-assisted review (Claude API). OFF by design (spec §7.3): no request is ever made from
    here until Owner approves it explicitly, at which point this function becomes the one place that calls out."""
    if not MODEL_REVIEW_ENABLED:
        raise RuntimeError("model-assisted proposal review is off (SharePoint spec §7.3) — rules only")
    raise NotImplementedError("model-assisted review not implemented")
