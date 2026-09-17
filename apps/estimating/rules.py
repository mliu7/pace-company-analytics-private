"""Pure rules of the Estimating workbench (SharePoint spec §8; parity basis: docs/sharepoint_dashboards_inventory.md,
"PACE Pricing Intelligence"). No Django, no I/O — every function here is unit-tested in tests/unit/test_estimating_rules.py.

The dashboard's behaviour is reproduced exactly where it was right (scoring ladder, confidence, compare, line maths,
rate-card defaults, hygiene) and fixed where the inventory documents a defect (field labor is UNION everywhere; the
header aliaser checks specific price columns before the generic "price"; imported labor units survive)."""

import re
from decimal import Decimal, ROUND_HALF_UP

# ----------------------------------------------------------------------------------------------------------------
# Labor types and the rate card
# ----------------------------------------------------------------------------------------------------------------
UNION, NON_UNION = "union", "non_union"
GROUP_LABELS = {UNION: "Union", NON_UNION: "Non-union"}

# builder id, rate-card id, labels, trade group, export code — the dashboard's LABOR_TYPES with the trade fixed by
# construction (field_labor is UNION in the rate card, the header and the export; only its input grid was mislabelled).
LABOR_TYPES = [
    {"id": "union_mobilization", "rate_id": "union_mobilization", "label": "Mobilization", "short": "Mobil.", "group": UNION, "code": "UNION-MOB"},
    {"id": "field_labor", "rate_id": "field_labor", "label": "Field Labor", "short": "Field", "group": UNION, "code": "UNION-FIELD"},
    {"id": "union_rough", "rate_id": "union_rough", "label": "Rough", "short": "Rough", "group": UNION, "code": "UNION-ROUGH"},
    {"id": "union_pull", "rate_id": "union_pull", "label": "Pull", "short": "Pull", "group": UNION, "code": "UNION-PULL"},
    {"id": "union_trim", "rate_id": "union_trim", "label": "Trim", "short": "Trim", "group": UNION, "code": "UNION-TRIM"},
    {"id": "union_test", "rate_id": "union_test", "label": "Test", "short": "Test", "group": UNION, "code": "UNION-TEST"},
    {"id": "engineering", "rate_id": "engineering", "label": "Engineering", "short": "Eng", "group": NON_UNION, "code": "NONUNION-ENG"},
    {"id": "fabrication", "rate_id": "fabrication", "label": "Fabrication", "short": "Fab", "group": NON_UNION, "code": "NONUNION-FAB"},
    {"id": "programming", "rate_id": "programming_non_sub", "label": "Programming", "short": "Prog", "group": NON_UNION, "code": "NONUNION-PROG"},
    {"id": "commissioning", "rate_id": "commissioning", "label": "Commissioning", "short": "Comm.", "group": NON_UNION, "code": "NONUNION-COMM"},
    {"id": "service", "rate_id": "service", "label": "Service", "short": "Svc", "group": NON_UNION, "code": "NONUNION-SVC"},
]
LABOR_IDS = [t["id"] for t in LABOR_TYPES]
LABOR_BY_ID = {t["id"]: t for t in LABOR_TYPES}
LABOR_COLUMN = {t["id"]: "lh_" + t["id"] for t in LABOR_TYPES}     # Line model column per labor type

# rate id → (label, group, cost, sell) — DEFAULT_COSTING of the dashboard (cost / sell per hour)
DEFAULT_RATES = [
    ("union_mobilization", "Mobilization", UNION, Decimal("92.00"), Decimal("125.00")),
    ("field_labor", "Field Labor", UNION, Decimal("92.00"), Decimal("125.00")),
    ("union_rough", "Rough", UNION, Decimal("92.00"), Decimal("125.00")),
    ("union_pull", "Pull", UNION, Decimal("92.00"), Decimal("125.00")),
    ("union_trim", "Trim", UNION, Decimal("92.00"), Decimal("125.00")),
    ("union_test", "Test", UNION, Decimal("92.00"), Decimal("125.00")),
    ("engineering", "Engineering", NON_UNION, Decimal("78.00"), Decimal("135.00")),
    ("fabrication", "Fabrication", NON_UNION, Decimal("45.60"), Decimal("77.00")),
    ("programming_non_sub", "Programming", NON_UNION, Decimal("58.00"), Decimal("135.00")),
    ("commissioning", "Commissioning", NON_UNION, Decimal("58.00"), Decimal("135.00")),
    ("service", "Service", NON_UNION, Decimal("58.00"), Decimal("135.00")),
]
DEFAULT_RATE_MAP = {r[0]: {"label": r[1], "group": r[2], "cost": r[3], "sell": r[4]} for r in DEFAULT_RATES}

MATERIAL_MARKUP_DEFAULT = Decimal("1.265")
CONSUMABLES_MARKUP = Decimal("1.5")
PEER_REVIEW_SELL = Decimal("25000")
PEER_REVIEW_HOURS = Decimal("80")
FIELD_HOURS_PER_1K_CONSUMABLES = Decimal("8")
ENGINEERING_POLICY_RATE = Decimal("155.00")
COMPARE_LIMIT = 6

# The Estimator Notes, verbatim (dashboard lines 1324–1331) — shown on the rate card and enforced as warnings.
ESTIMATOR_NOTES = [
    "Estimates/projects in excess of 25k and/or labor of 80+ hours must have peer review with GE or Field Superintendent.",
    "Unless otherwise dictated by cost+ or similar, minimum markup on material/equipment line items is 1.265.",
    "Engineering costs per hour to increase to 155.00/hour, including drafting services.",
    "Increase all cable pull labor to minimum .010/ft.",
    "Consumable markups to 1.5.",
    "Buffer Miscellaneous increase 12–15%.",
    "Non-union PM time to increase 20%.",
    "For every 1k in consumables, add 8 hours of miscellaneous field union labor.",
]

D0 = Decimal("0")
CENTS = Decimal("0.01")
MILLS = Decimal("0.001")
HOURS_Q = Decimal("0.01")


def dec(v, default=D0):
    """Decimal from anything a form, a JSON body or a spreadsheet cell can hold; None/blank/garbage → default."""
    if v is None or v == "":
        return default
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip().replace(",", "").replace("$", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return Decimal(s)
    except Exception:  # noqa
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return Decimal(m.group(0)) if m else default


def q2(v):
    return dec(v).quantize(CENTS, rounding=ROUND_HALF_UP)


def q3(v):
    return dec(v).quantize(MILLS, rounding=ROUND_HALF_UP)


def clean(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


# ----------------------------------------------------------------------------------------------------------------
# Part numbers, manufacturers, source dates
# ----------------------------------------------------------------------------------------------------------------
def norm_part(s):
    """The dashboard's normalized part: upper-case with whitespace - _ . / removed (the search key)."""
    return re.sub(r"[\s\-_./]", "", str(s or "").upper())


MFR_ALIAS = {
    "ATLASIED": "AtlasIED", "BOSE PROFESSIONAL": "Bose Professional", "AVARRO": "Avarro", "NEUTRIK": "Neutrik", "MOGAMI": "Mogami",
    "SONY": "Sony", "SOUNDCRAFT": "Soundcraft", "SWITCHCRAFT": "Switchcraft", "TECHFLEX": "Techflex", "WATTBOX": "WattBox",
    "ZEEVEE": "ZeeVee", "YMAHA": "Yamaha", "MARHSALL": "Marshall", "BTX TECHOLOGIES": "BTX Technologies",
    "BTX TECHNOLOIGES": "BTX Technologies", "WESTPENN": "West Penn", "WEST PENN": "West Penn Wire",
    "BELDEN WIRE AND CABLE": "Belden Wire & Cable", "PANDUIT CORP.": "Panduit", "KRAMER ELECTRONICS": "Kramer",
    "MIDDLE ATLANTIC PRODUCTS": "Middle Atlantic", "LEA PROFESSIONAL": "LEA Professional", "VADDIO": "Vaddio", "CHIEF": "Chief",
    "DA-LITE": "Da-Lite", "LUXUL": "Luxul", "WIREMOLD": "Wiremold",
}
_PART_PREFIXES = re.compile(r"^(ICON-|DIO-|ROOMVIEW-|VIP-|VPX|MAX2-|MAX-|PM-|RF4|RFCC|PUSH-GO-|HTX|HX-|PS-|SX2-|UX-|DCE|EZU$|HCE|HTE-|HUE-|HWCE|USB|DOF|EZD-|EZH|HOF|OLC|TWD|TWH|HDS-|MVS-|UHDS-|HT140|UHD|COLLABORA-|ECOH|HUDDLE-CAM-)")


def looks_like_part(raw):
    """A "manufacturer" value that is really a part number (orphan rows in the vendor files) → grouped as Other."""
    s = clean(raw)
    if not s:
        return True
    compact = re.sub(r"[^A-Za-z0-9]", "", s)
    upper = s.upper()
    if _PART_PREFIXES.search(upper):
        return True
    if re.match(r"^\(?\d", s):
        return True
    if re.search(r"[0-9]", s) and len(s) > 5 and re.search(r"[-_/+.]", s) and re.match(r"^[A-Z0-9\s\-_./+()]+$", s):
        return True
    if re.search(r"[0-9]", s) and len(compact) > 8 and re.match(r"^[A-Z0-9]+$", compact) and s == upper:
        return True
    return False


def clean_manufacturer(name):
    raw = clean(name)
    if not raw:
        return "Other"
    alias = MFR_ALIAS.get(raw.upper())
    if alias:
        return alias
    if looks_like_part(raw):
        return "Other"
    return raw


_MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
           "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11,
           "november": 11, "dec": 12, "december": 12}
_MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MON = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"


def _mk(y, m, d, prec):
    y, m, d = int(y or 0), int(m or 0), int(d or 0)
    if not y or y < 2000 or y > 2099 or m < 0 or m > 12 or d < 0 or d > 31:
        return {"label": "", "key": 0, "precision": ""}
    if prec == "day" and m and d:
        return {"label": "%s %d, %d" % (_MONTH_NAMES[m], d, y), "key": y * 10000 + m * 100 + d, "precision": "day"}
    if prec == "month" and m:
        return {"label": "%s %d" % (_MONTH_NAMES[m], y), "key": y * 10000 + m * 100, "precision": "month"}
    return {"label": str(y), "key": y * 10000, "precision": "year"}


def _month_no(name):
    n = name.lower()
    return _MONTHS.get(n[:3]) or _MONTHS.get(n) or 0


def source_date(src):
    """Date carried by a vendor file name (or an "Effective Date" cell): label like "Aug 18, 2025", numeric key
    y*10000+m*100+d (0 = undated) and precision day/month/year. The dashboard's sourceDate(), pattern for pattern."""
    raw = str(src or "")
    t = re.sub(r"\s+", " ", re.sub(r"_+", " ", raw)).strip()
    m = re.search(r"\b(20\d{2})[-.\s](0?[1-9]|1[0-2])[-.\s](0?[1-9]|[12]\d|3[01])\b", t)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3), "day")
    m = re.search(r"\b(0?[1-9]|1[0-2])[-/.\s](0?[1-9]|[12]\d|3[01])[-/.\s](20\d{2})\b", t)
    if m:
        return _mk(m.group(3), m.group(1), m.group(2), "day")
    m = re.search(r"\b(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])\b", t)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3), "day")
    m = re.search(r"\b(20\d{2})([1-9])([0-2]\d|3[01])\b", t)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3), "day")
    m = re.search(r"\b(" + _MON + r")\s+(0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?,?\s+(20\d{2})\b", t, re.I)
    if m:
        return _mk(m.group(3), _month_no(m.group(1)), m.group(2), "day")
    m = re.search(r"(?:rev)?(0[1-9]|1[0-2])([0-2]\d|3[01])(\d{2})", raw, re.I)
    if m:
        return _mk(2000 + int(m.group(3)), m.group(1), m.group(2), "day")
    m = re.search(r"\b(20\d{2})[-.\s](0?[1-9]|1[0-2])\b", t)
    if m:
        return _mk(m.group(1), m.group(2), 0, "month")
    m = re.search(r"\b(" + _MON + r")\s+(20\d{2})\b", t, re.I)
    if m:
        return _mk(m.group(2), _month_no(m.group(1)), 0, "month")
    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        return _mk(m.group(1), 0, 0, "year")
    return {"label": "", "key": 0, "precision": ""}


def date_key_year(key):
    y = int(key or 0) // 10000
    return y if 2000 <= y <= 2099 else 0


def date_key_label(key):
    """Label for a stored date key (day / month / year precision inferred from the zero parts)."""
    key = int(key or 0)
    if not key:
        return ""
    y, m, d = key // 10000, (key // 100) % 100, key % 100
    if m and d:
        return "%s %d, %d" % (_MONTH_NAMES[m], d, y)
    if m:
        return "%s %d" % (_MONTH_NAMES[m], y)
    return str(y)


# ----------------------------------------------------------------------------------------------------------------
# Search scoring, confidence, margin, compare
# ----------------------------------------------------------------------------------------------------------------
def _js_round(x):
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def score(part, part_norm, manufacturer, description, q, qn=None):
    """Match score 0–100: exact normalized 100, exact part 99, prefix 85, contains 70, brand 55, description 45,
    word overlap 25 + hits/words × 20 (words > 2 chars); 0 = excluded. The dashboard's score(), verbatim."""
    q = (q or "").strip()
    if not q:
        return 0
    qn = norm_part(q) if qn is None else qn
    pn = part_norm or norm_part(part)
    dl = (description or "").lower()
    ql = q.lower()
    if pn == qn:
        return 100
    if (part or "").upper() == q.upper():
        return 99
    if len(qn) >= 3 and pn.startswith(qn):
        return 85
    if len(qn) >= 3 and qn in pn:
        return 70
    if ql in (manufacturer or "").lower():
        return 55
    if ql in dl:
        return 45
    ws = [w for w in ql.split() if len(w) > 2]
    if not ws:
        return 0
    pl = pn.lower()
    hits = [w for w in ws if w in dl or w in pl]
    return 25 + _js_round(len(hits) / len(ws) * 20) if hits else 0


def score_label(sc):
    return "Exact" if sc >= 99 else "%d%%" % sc


def score_class(sc):
    return "me" if sc >= 99 else "mh" if sc >= 70 else "mm" if sc >= 40 else "mlo"


def confidence(cost, msrp, description, source_year, map_price):
    """Confidence 0–100 for a catalog row: +30 cost, +15 MSRP, +15 description, +25 source year ≥ 2025 (else +15 for a
    2020s year — the dashboard reads `202\\d` out of the file name, so 2015–2019 files earn nothing), +15 MAP."""
    c = 0
    if cost:
        c += 30
    if msrp:
        c += 15
    if description:
        c += 15
    y = int(source_year or 0)
    if y >= 2025:
        c += 25
    elif y >= 2020:
        c += 15
    if map_price:
        c += 15
    return c


def confidence_label(conf):
    return "High Confidence" if conf >= 75 else "Medium" if conf >= 50 else "Low"


def margin_pct(cost, msrp):
    """Margin Cost→MSRP as a percentage (1 dp) or None when either side is missing."""
    cost, msrp = dec(cost), dec(msrp)
    if cost > 0 and msrp > 0:
        return float(((msrp - cost) / msrp * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    return None


def compare_best(items):
    """items: dicts with cost / msrp. Marks best_cost (lowest cost), best_margin (highest cost→MSRP margin) and
    best (either) — the compare grid's "Best Value" rule."""
    costs = [dec(i.get("cost")) for i in items if dec(i.get("cost")) > 0]
    margins = [(dec(i["msrp"]) - dec(i["cost"])) / dec(i["msrp"]) for i in items if dec(i.get("cost")) > 0 and dec(i.get("msrp")) > 0]
    min_c = min(costs) if costs else None
    max_m = max(margins) if margins else None
    out = []
    for i in items:
        c, m = dec(i.get("cost")), dec(i.get("msrp"))
        mar = (m - c) / m if c > 0 and m > 0 else None
        best_cost = bool(c > 0 and min_c is not None and c == min_c)
        best_margin = bool(mar is not None and max_m is not None and abs(mar - max_m) < Decimal("0.0001"))
        out.append({**i, "best_cost": best_cost, "best_margin": best_margin, "best": best_cost or best_margin})
    return out


# ----------------------------------------------------------------------------------------------------------------
# Line maths — sell and markup are coupled; labor hours are per line, never multiplied by qty
# ----------------------------------------------------------------------------------------------------------------
def line_qty(qty):
    try:
        n = int(dec(qty))
    except Exception:  # noqa
        n = 1
    return max(1, n or 1)


def line_markup(markup):
    m = dec(markup, MATERIAL_MARKUP_DEFAULT)
    return m if m > 0 else MATERIAL_MARKUP_DEFAULT


def sell_from_markup(cost, markup):
    """updMarkup: sell = cost × markup, 2 dp."""
    return q2(dec(cost) * line_markup(markup))


def markup_from_sell(cost, sell):
    """updSell: markup = sell ÷ cost, 3 dp (None when the line has no cost — markup is then informational only)."""
    cost = dec(cost)
    if cost <= 0:
        return None
    return q3(dec(sell) / cost)


def default_sell(cost, msrp, markup=MATERIAL_MARKUP_DEFAULT):
    """On add: sell = cost × 1.265 when the item has a cost, else its MSRP (or 0)."""
    cost = dec(cost)
    if cost > 0:
        return q2(cost * dec(markup, MATERIAL_MARKUP_DEFAULT))
    return q2(dec(msrp))


def rate_lookup(rates, rate_id):
    r = (rates or {}).get(rate_id) or DEFAULT_RATE_MAP.get(rate_id) or {"cost": D0, "sell": D0}
    return dec(r.get("cost")), dec(r.get("sell"))


def labor_breakdown(hours, rates):
    """hours: {labor id: hours}; rates: {rate id: {cost, sell}}. One row per labor type — hours are the line's total
    billable hours (NOT × qty: 0 hides the group, 1 exports exactly one hour)."""
    out = []
    for t in LABOR_TYPES:
        h = dec((hours or {}).get(t["id"]))
        h = h if h > 0 else D0
        rc, rs = rate_lookup(rates, t["rate_id"])
        out.append({"id": t["id"], "label": t["label"], "group": t["group"], "hours": h, "rate_cost": rc, "rate_sell": rs,
                    "cost": h * rc, "sell": h * rs})
    return out


def line_totals(cost, qty, sell, hours, rates, markup=None):
    """Everything a line shows: equipment ext (× qty), labor (Σ hours × rate), total cost / sell / profit / margin."""
    q = line_qty(qty)
    c = dec(cost)
    c = c if c > 0 else D0
    s = dec(sell)
    s = s if s > 0 else D0
    parts = labor_breakdown(hours, rates)
    lc = sum((p["cost"] for p in parts), D0)
    ls = sum((p["sell"] for p in parts), D0)
    lh = sum((p["hours"] for p in parts), D0)
    ec, es = c * q, s * q
    tc, ts = ec + lc, es + ls
    mk = dec(markup) if markup is not None else (markup_from_sell(c, s) if c > 0 else None)
    return {"qty": q, "cost": c, "sell": s, "markup": mk, "equipment_cost_ext": ec, "equipment_sell_ext": es,
            "labor_cost": lc, "labor_sell": ls, "labor_hours": lh, "labor": parts,
            "total_cost": tc, "total_sell": ts, "profit": ts - tc,
            "margin": float(((ts - tc) / ts * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) if ts > 0 else None}


def sum_totals(rows):
    """Room totals from line totals, or grand totals from room totals (same keys)."""
    keys = ("equipment_cost_ext", "equipment_sell_ext", "labor_cost", "labor_sell", "labor_hours", "total_cost", "total_sell")
    out = {k: sum((dec(r.get(k)) for r in rows), D0) for k in keys}
    out["lines"] = sum(int(r.get("lines", 1)) for r in rows)
    out["profit"] = out["total_sell"] - out["total_cost"]
    out["margin"] = float((out["profit"] / out["total_sell"] * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) if out["total_sell"] > 0 else None
    hours = {}
    for r in rows:
        for k, v in (r.get("hours_by_type") or {}).items():
            hours[k] = hours.get(k, D0) + dec(v)
        for p in r.get("labor") or []:
            hours[p["id"]] = hours.get(p["id"], D0) + dec(p["hours"])
    out["hours_by_type"] = hours
    return out


def rate_margin(cost, sell):
    """Costing-table margin: (sell − cost) ÷ sell as whole-percent text, blank when sell is 0."""
    cost, sell = dec(cost), dec(sell)
    return "%d%%" % _js_round((sell - cost) / sell * 100) if sell > 0 else ""


# ----------------------------------------------------------------------------------------------------------------
# Line classification (misc-like consumables, export "Item" label)
# ----------------------------------------------------------------------------------------------------------------
_MISC_RE = re.compile(r"cable|wire|connector|adapter|plate|bulk|misc|material|consumable|hardware|freight|cat6|cat5|hdmi|hdbaset|db9|patch|sleeving|tie holder|j-hook|name plate|trim")
_NOT_MISC_RE = re.compile(r"display|camera|processor|amplifier|speaker|touchpanel|microphone|switcher|dsp|rack$")


def misc_like(category, manufacturer, part, description):
    """Consumables / miscellaneous material — the policy's 1.5 markup applies (the export's miscLike rule)."""
    txt = " ".join(str(x or "") for x in (category, manufacturer, part, description, norm_part(part))).lower()
    return bool(_MISC_RE.search(txt)) and not _NOT_MISC_RE.search(txt)


_ITEM_RULES = [
    ("Display", r"display|monitor|projector|screen|tv\b|led wall|videowall|video wall"),
    ("Camera", r"camera|ptz|cam\b"),
    ("Microphone", r"microphone|\bmic\b|mics\b|beamforming"),
    ("Speaker", r"speaker|loudspeaker|subwoofer|soundbar|pendant"),
    ("Audio", r"amplifier|\bamp\b|\bdsp\b|mixer|audio|tesira|q-sys|qsys"),
    ("Control", r"control|touch ?panel|keypad|processor|crestron|extron ip"),
    ("Video", r"switcher|scaler|matrix|hdmi|hdbaset|encoder|decoder|extender|video"),
    ("Rack", r"\brack\b|enclosure|shelf|blank panel|vent panel|power distribution|pdu\b|ups\b"),
    ("Mount", r"mount|bracket|cart|stand\b|arm\b"),
    ("Network", r"switch\b|router|access point|network|poe\b|wifi|wi-fi"),
    ("Software / Service", r"software|license|licence|subscription|service|warranty|support|cloud"),
    ("Misc. Materials", r"cable|wire|connector|adapter|plate|bulk|misc|material|consumable|hardware|freight|cat6|cat5|patch|sleeving|j-hook"),
]


def item_label(category, manufacturer, part, description):
    """The export's "Item" column: a category word inferred from what the line says (default Equipment)."""
    txt = " ".join(str(x or "") for x in (category, manufacturer, part, description)).lower()
    for label, pat in _ITEM_RULES:
        if re.search(pat, txt):
            return label
    return "Equipment"


# ----------------------------------------------------------------------------------------------------------------
# Catalog hygiene — the V100 "current-only catalog" as a loader rule with a report
# ----------------------------------------------------------------------------------------------------------------
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
_VERSION_PATTERNS = [
    ("V", re.compile(r"(?:[-_\s]?)(?:V|VER)(\d{1,2})$"), lambda m: int(m.group(1))),
    ("MK", re.compile(r"(?:[-_\s]?)MK(?:\s|-|_)?(\d{1,2}|II|III|IV|V|VI|VII|VIII|IX|X)$"), lambda m: _ROMAN.get(m.group(1)) or int(m.group(1))),
    ("GEN", re.compile(r"(?:[-_\s]?)(?:GEN|GENERATION)(?:\s|-|_)?(\d{1,2})$"), lambda m: int(m.group(1))),
    ("GEN", re.compile(r"(?:[-_\s]?)(\d{1,2})(?:ST|ND|RD|TH)?(?:\s|-|_)?GEN$"), lambda m: int(m.group(1))),
    ("REV", re.compile(r"(?:[-_\s]?)REV(?:\s|-|_)?([A-Z]|\d{1,2})$"), lambda m: (ord(m.group(1)) - 64) if re.match(r"^[A-Z]$", m.group(1)) else int(m.group(1))),
]


def version_info(part):
    """Explicit version suffix on a part number (V2, MK II, GEN 3, 3RD GEN, REV B) → base family, rank."""
    p = clean(part).upper()
    if not p:
        return None
    for family, rx, rank in _VERSION_PATTERNS:
        m = rx.search(p)
        if m:
            base = re.sub(r"[-_\s]+$", "", p[:m.start()])
            base_c = re.sub(r"[^A-Z0-9]", "", base)
            if len(base_c) >= 4:
                return {"base": base_c, "rank": rank(m), "family": family, "explicit": True}
    return {"base": re.sub(r"[^A-Z0-9]", "", p), "rank": None, "family": None, "explicit": False}


def is_pre_2023(year):
    y = int(year or 0)
    return 0 < y < 2023


def _quality(r):
    return (4 if dec(r.get("cost")) > 0 else 0) + (2 if dec(r.get("msrp")) > 0 else 0) + (1 if r.get("description") else 0)


def choose_better(a, b):
    """Of two rows with the same normalized part: not-stale beats stale, later source year, later date key,
    then cost (4) + MSRP (2) + description (1). Ties keep the first (a)."""
    sa, sb = is_pre_2023(a.get("year")), is_pre_2023(b.get("year"))
    if sa != sb:
        return b if sa else a
    ya, yb = int(a.get("year") or 0), int(b.get("year") or 0)
    if ya != yb:
        return a if ya > yb else b
    da, db = int(a.get("date_key") or 0), int(b.get("date_key") or 0)
    if da != db:
        return a if da > db else b
    return a if _quality(a) >= _quality(b) else b


def hygiene(rows):
    """rows: dicts {id, part, part_norm, manufacturer, year, date_key, cost, msrp, description}.
    Returns {"current": [ids], "duplicate": [(loser, winner)], "pre2023": [ids], "version": [(loser, winner)],
             "counts": {...}} — step 1 collapses duplicate part numbers (keeping the better row), step 2 removes rows
    priced before 2023 (undated rows stay), step 3 archives older explicit versions of the same family."""
    exact = {}
    for i, r in enumerate(rows):
        k = r.get("part_norm") or norm_part(r.get("part")) or ("__BLANK__%d" % i)
        exact[k] = choose_better(exact[k], r) if k in exact else r
    winner_by_key = {k: v["id"] for k, v in exact.items()}
    duplicate = []
    for i, r in enumerate(rows):
        k = r.get("part_norm") or norm_part(r.get("part")) or ("__BLANK__%d" % i)
        if winner_by_key[k] != r["id"]:
            duplicate.append((r["id"], winner_by_key[k]))
    deduped = list(exact.values())
    pre2023 = [r["id"] for r in deduped if is_pre_2023(r.get("year"))]
    stale = set(pre2023)
    current = [r for r in deduped if r["id"] not in stale]
    groups = {}
    for r in current:
        vi = version_info(r.get("part"))
        if not vi or not vi["explicit"]:
            continue
        key = (clean(r.get("manufacturer")).upper(), vi["base"], vi["family"])
        groups.setdefault(key, []).append((vi["rank"], r))
    version = []
    for items in groups.values():
        ranks = sorted({rk for rk, _ in items if rk is not None})
        if len(ranks) < 2:
            continue
        top = ranks[-1]
        winner = next(r for rk, r in items if rk == top)
        for rk, r in items:
            if rk is not None and rk < top:
                version.append((r["id"], winner["id"]))
    archived_v = {a for a, _ in version}
    final = [r["id"] for r in current if r["id"] not in archived_v]
    return {"current": final, "duplicate": duplicate, "pre2023": pre2023, "version": version,
            "counts": {"total": len(rows), "duplicates": len(duplicate), "pre2023": len(pre2023), "versions": len(version),
                       "current": len(final)}}


# ----------------------------------------------------------------------------------------------------------------
# Spreadsheet header detection (vendor catalogs / BOMs) and the estimate importer's column mapping
# ----------------------------------------------------------------------------------------------------------------
# alias lists per field; checked in this order, exact match first, then containment (msrp / map / sourceDate before the
# generic "price" of sell — the dashboard checked sell first, so its own template's "MSRP / List Price" and "MAP Price"
# both landed on sell and MAP was lost).
HEADER_ALIASES = [
    ("mfr", ["manufacturer", "mfr", "brand", "vendor", "make"]),
    ("part", ["part number", "part no", "part", "model number", "model no", "model", "sku", "catalog number", "catalog no", "item number", "item no"]),
    ("desc", ["description", "product description", "item description", "product name", "item name", "name"]),
    ("qty", ["qty", "quantity", "count", "total count", "total counts"]),
    ("cost", ["dealer cost", "unit cost", "cost ea", "cost each", "material cost", "cost"]),
    ("msrp", ["msrp", "list price", "manufacturer list", "list", "retail price"]),
    ("map", ["map price", "minimum advertised price", "map"]),
    ("sourceDate", ["effective date", "price effective date", "catalog date", "price date", "source date", "date updated", "updated date"]),
    ("area", ["area room", "area", "room name", "room", "location"]),
    ("unionFab", ["union fabrication units", "union fab units", "fabrication units", "fab units"]),
    ("unionField", ["union field labor units", "union field units", "field labor units", "field units"]),
    ("unionProg", ["union programming units", "programming commissioning units", "programming units", "commissioning units"]),
    ("unionPm", ["union project management units", "project management units", "pm units"]),
    ("nonFab", ["non union fabrication units", "nonunion fabrication units", "non union fab units"]),
    ("nonField", ["non union field labor units", "nonunion field labor units", "non union field units"]),
    ("nonProg", ["non union programming units", "nonunion programming units"]),
    ("nonPm", ["non union project management units", "nonunion project management units"]),
    ("sell", ["unit sell", "sell ea", "sell each", "sell price", "price", "sell"]),
]
HEADER_WEIGHTS = {"part": 5, "desc": 3, "mfr": 2}
# imported labor-unit columns → builder labor ids (the dashboard dropped these because the keys never matched)
IMPORT_LABOR_MAP = {"unionFab": "fabrication", "unionField": "field_labor", "unionProg": "programming", "unionPm": "engineering",
                    "nonFab": "fabrication", "nonField": "field_labor", "nonProg": "programming", "nonPm": "engineering"}


def norm_header(v):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(v or "").lower())).strip()


def header_key(v):
    h = norm_header(v)
    if not h:
        return ""
    for key, aliases in HEADER_ALIASES:
        if h in aliases:
            return key
    for key, aliases in HEADER_ALIASES:
        if any(a in h for a in aliases):
            return key
    return ""


def find_header(matrix, limit=35):
    """Best header row among the first 35: score = Σ weights of the distinct keys found (part 5, desc 3, mfr 2, else 1).
    Returns {row, map: {key: column index}, score} (score −1 when nothing matched)."""
    best = {"row": 0, "map": {}, "score": -1}
    for r, row in enumerate(matrix[:limit]):
        m, sc = {}, 0
        for c, v in enumerate(row or []):
            k = header_key(v)
            if k and k not in m:
                m[k] = c
                sc += HEADER_WEIGHTS.get(k, 1)
        if sc > best["score"]:
            best = {"row": r, "map": m, "score": sc}
    return best


def cell(row, mp, key):
    i = mp.get(key)
    if i is None or i >= len(row):
        return ""
    return row[i]


def catalog_rows(matrix, file_name="", sheet_name=""):
    """Rows of a vendor price list / BOM sheet after header detection (matrixRows): needs part or description; missing
    part → IMPORTED-n; qty ≥ 1; has_* flags distinguish blank from zero; labor units carried through."""
    h = find_header(matrix)
    mp = h["map"]
    out = []
    if "part" not in mp and "desc" not in mp:
        return out
    for row in matrix[h["row"] + 1:]:
        row = row or []
        if not any(clean(v) for v in row):
            continue
        part, desc = clean(cell(row, mp, "part")), clean(cell(row, mp, "desc"))
        if not part and not desc:
            continue
        if not part:
            part = "IMPORTED-%d" % (len(out) + 1)
        raw = {k: cell(row, mp, k) for k in ("cost", "sell", "msrp", "map")}
        has = {k: (k in mp and clean(raw[k]) != "") for k in raw}
        vals = {k: max(D0, dec(raw[k])) for k in raw}
        qty = max(1, _js_round(dec(cell(row, mp, "qty")) or 1))
        labor = {}
        for key, lid in IMPORT_LABOR_MAP.items():
            v = dec(cell(row, mp, key))
            if v > 0:
                labor[lid] = labor.get(lid, D0) + v
        out.append({"mfr": clean(cell(row, mp, "mfr")) or "Imported", "part": part, "desc": desc or part, "qty": qty,
                    "cost": vals["cost"], "sell": vals["sell"], "msrp": vals["msrp"], "map": vals["map"],
                    "has_cost": has["cost"], "has_sell": has["sell"], "has_msrp": has["msrp"], "has_map": has["map"],
                    "source_date": clean(cell(row, mp, "sourceDate")), "area": clean(cell(row, mp, "area")),
                    "labor": labor, "file": file_name, "sheet": sheet_name})
    return out


def dedupe_catalog_rows(rows):
    """Same normalized part in the same room is one line: qty summed, later non-blank prices win, blank/Imported
    manufacturer filled from a later row."""
    seen = {}
    for r in rows:
        k = norm_part(r["part"]) + "|" + clean(r.get("area")).upper()
        if k in seen:
            x = seen[k]
            x["qty"] += r["qty"]
            for f in ("cost", "sell", "msrp", "map"):
                if r.get("has_" + f):
                    x[f] = r[f]
                    x["has_" + f] = True
            if r.get("desc") and r["desc"] != r["part"]:
                x["desc"] = r["desc"]
            if (not x.get("mfr") or x["mfr"] == "Imported") and r.get("mfr"):
                x["mfr"] = r["mfr"]
            if r.get("source_date"):
                x["source_date"] = r["source_date"]
            for lid, v in (r.get("labor") or {}).items():
                x.setdefault("labor", {})[lid] = x["labor"].get(lid, D0) + v
        else:
            seen[k] = dict(r, labor=dict(r.get("labor") or {}))
    return list(seen.values())


def imported_product(row):
    """A vendor-catalog row as a catalog record: blank prices stay NULL (never 0), sell stands in for MSRP,
    effective date from the cell or the file name, category "Vendor Catalog"."""
    info = source_date(row.get("source_date") or row.get("file") or "")
    return {"manufacturer_raw": row.get("mfr") or "Imported", "manufacturer": clean_manufacturer(row.get("mfr") or "Imported"),
            "part": row["part"], "part_norm": norm_part(row["part"]), "description": row.get("desc") or row["part"],
            "cost": row["cost"] if row.get("has_cost") else None,
            "msrp": row["msrp"] if row.get("has_msrp") else (row["sell"] if row.get("has_sell") else None),
            "map": row["map"] if row.get("has_map") else None, "category": "Vendor Catalog",
            "source": row.get("file") or "Imported Catalog", "date_key": info["key"], "date_label": info["label"] or clean(row.get("source_date"))}


def map_estimate_header(header):
    """The estimate importer's simpler mapping (mapHeader): keys item / mfr / qty / model / desc / cost / markup / sell / room."""
    m = {}
    for i, v in enumerate(header or []):
        k = re.sub(r"[^a-z0-9]", "", clean(v).lower())
        if not k:
            continue
        if k in ("item", "type", "category"):
            m.setdefault("item", i)
        elif k in ("manufacturer", "brand", "vendor"):
            m.setdefault("mfr", i)
        elif k in ("qty", "quantity"):
            m.setdefault("qty", i)
        elif "model" in k or "part" in k:
            m.setdefault("model", i)
        elif "description" in k or k == "desc":
            m.setdefault("desc", i)
        elif k in ("cost", "equipmentcost", "dealercost", "unitcost"):
            m.setdefault("cost", i)
        elif "markup" in k:
            m.setdefault("markup", i)
        elif k in ("sell", "sellprice", "equipmentsell", "sellea"):
            m.setdefault("sell", i)
        elif k in ("room", "area"):
            m.setdefault("room", i)
        else:
            lk = header_key(v)
            if lk in IMPORT_LABOR_MAP:
                m.setdefault("labor:" + lk, i)
    return m


def find_estimate_header(matrix, limit=60):
    """First row (of 60) whose mapping has a model/part column plus qty or description."""
    for r, row in enumerate(matrix[:limit]):
        m = map_estimate_header(row)
        if "model" in m and ("qty" in m or "desc" in m):
            return r
    return -1


def estimate_rows_from_sheet(name, matrix):
    """Sheet-per-room strategy (parseSheet): room = title cell above the header (B else A, < 80 chars, minus
    " and Total Counts") or the sheet name; skips blanks, subtotal / total-count rows, qty ≤ 0 rows with no model;
    qty ≤ 0 + description-only rows become NOTE lines. Returns {name, lines: [raw line dicts]} or None."""
    hrow = find_estimate_header(matrix)
    if hrow < 0:
        return None
    mp = map_estimate_header(matrix[hrow])
    room = clean(name) or "Room"
    above = matrix[hrow - 1] if hrow > 0 else []
    title = clean((above[1] if len(above) > 1 else "") or (above[0] if above else ""))
    if title and len(title) < 80:
        room = re.sub(r"\s+and\s+total\s+counts", "", title, flags=re.I)
    lines = []
    for row in matrix[hrow + 1:]:
        row = row or []
        cat, mf, model, desc = (clean(cell(row, mp, k)) for k in ("item", "mfr", "model", "desc"))
        qty = dec(cell(row, mp, "qty"))
        low = " ".join((cat, mf, model, desc)).lower()
        if not (cat or mf or model or desc):
            continue
        if "subtotal" in low or "total counts" in low:
            continue
        if qty <= 0 and desc and not (mf or model or cat):
            lines.append(_raw_line(row, mp, room, note=True))
            continue
        if qty <= 0 and not model and not mf:
            continue
        lines.append(_raw_line(row, mp, room))
    return {"name": room, "lines": lines} if lines else None


def estimate_rows_by_room_column(sheets):
    """Room-column strategy (parseRoomColumn): sheets = [(name, matrix)]; any sheet whose header has a Room/Area
    column groups its rows by that value. Returns [{name, lines}] (empty when no sheet has a room column)."""
    rooms = {}
    for name, matrix in sheets:
        hrow = find_estimate_header(matrix)
        if hrow < 0:
            continue
        mp = map_estimate_header(matrix[hrow])
        if "room" not in mp:
            continue
        for row in matrix[hrow + 1:]:
            row = row or []
            rn = clean(cell(row, mp, "room")) or clean(name) or "Room"
            line = _raw_line(row, mp, rn)
            if line["model"] or line["desc"] or line["mfr"]:
                rooms.setdefault(rn, {"name": rn, "lines": []})["lines"].append(line)
    return [r for r in rooms.values() if r["lines"]]


def _raw_line(row, mp, room, note=False):
    labor = {}
    for k, i in mp.items():
        if k.startswith("labor:"):
            v = dec(row[i] if i < len(row) else "")
            if v > 0:
                lid = IMPORT_LABOR_MAP[k[6:]]
                labor[lid] = labor.get(lid, D0) + v
    return {"room": room, "note": note, "item": clean(cell(row, mp, "item")), "mfr": clean(cell(row, mp, "mfr")),
            "model": clean(cell(row, mp, "model")), "desc": clean(cell(row, mp, "desc")), "qty": dec(cell(row, mp, "qty")),
            "cost": dec(cell(row, mp, "cost")), "markup": dec(cell(row, mp, "markup")), "sell": dec(cell(row, mp, "sell")),
            "labor": labor}


# ----------------------------------------------------------------------------------------------------------------
# Estimator Notes policy → warnings (never blocking)
# ----------------------------------------------------------------------------------------------------------------
def policy_warnings(grand, lines):
    """grand: sum_totals() of the estimate; lines: dicts with kind, misc (bool), cost, markup, qty, hours{}.
    Returns a list of {code, level, text} — shown on the builder; saving is never blocked."""
    out = []
    sell, hours = dec(grand.get("total_sell")), dec(grand.get("labor_hours"))
    if sell > PEER_REVIEW_SELL or hours > PEER_REVIEW_HOURS:
        why = []
        if sell > PEER_REVIEW_SELL:
            why.append("sell $%s > $25,000" % "{:,.0f}".format(sell))
        if hours > PEER_REVIEW_HOURS:
            why.append("labor %s h > 80 h" % "{:,.1f}".format(hours))
        out.append({"code": "peer_review", "level": "warn",
                    "text": "Peer review with GE or Field Superintendent required (%s)." % ", ".join(why)})
    low, low_misc, consumables_cost, field_hours = [], [], D0, D0
    for ln in lines:
        if ln.get("kind") == "note":
            continue
        cost = dec(ln.get("cost"))
        mk = dec(ln.get("markup"))
        h = ln.get("hours") or {}
        field_hours += dec(h.get("field_labor"))
        if ln.get("misc"):
            consumables_cost += cost * line_qty(ln.get("qty"))
            if cost > 0 and mk < CONSUMABLES_MARKUP:
                low_misc.append(ln)
        elif cost > 0 and mk < MATERIAL_MARKUP_DEFAULT:
            low.append(ln)
    if low:
        out.append({"code": "min_markup", "level": "warn",
                    "text": "%d equipment line%s below the 1.265 minimum markup: %s." % (len(low), "s" if len(low) != 1 else "",
                                                                                          ", ".join(_line_ref(x) for x in low[:6]) + (" …" if len(low) > 6 else ""))})
    if low_misc:
        out.append({"code": "consumables_markup", "level": "warn",
                    "text": "%d consumable line%s below the 1.5 markup: %s." % (len(low_misc), "s" if len(low_misc) != 1 else "",
                                                                                ", ".join(_line_ref(x) for x in low_misc[:6]) + (" …" if len(low_misc) > 6 else ""))})
    if consumables_cost > 0:
        need = (consumables_cost / Decimal("1000") * FIELD_HOURS_PER_1K_CONSUMABLES).quantize(Decimal("0.1"))
        if field_hours < need:
            out.append({"code": "field_per_consumables", "level": "warn",
                        "text": "Consumables cost $%s call for %s h of miscellaneous union field labor; the estimate carries %s h." % (
                            "{:,.0f}".format(consumables_cost), need, field_hours.quantize(Decimal("0.1")))})
    return out


def _line_ref(ln):
    return (ln.get("part") or ln.get("description") or "line")[:24] + (" ×%s" % line_qty(ln.get("qty")) if line_qty(ln.get("qty")) > 1 else "")


def rate_card_notes(rates):
    """Informational notes about the current rate card against the Estimator Notes."""
    out = []
    eng = (rates or {}).get("engineering") or {}
    if dec(eng.get("sell")) and dec(eng.get("sell")) < ENGINEERING_POLICY_RATE:
        out.append({"code": "engineering_rate", "level": "info",
                    "text": "Estimator Notes say engineering bills at $155.00/h (incl. drafting); the card sells engineering at $%s/h." % q2(eng.get("sell"))})
    return out


# ----------------------------------------------------------------------------------------------------------------
# Exports
# ----------------------------------------------------------------------------------------------------------------
def sheet_name(name, used=None, limit=31):
    """Excel sheet title: no []:*?/\\, ≤ 31 chars, unique within the workbook (suffix " (2)", " (3)" …)."""
    base = re.sub(r"[\[\]:*?/\\]", " ", clean(name)) or "Sheet"
    base = base[:limit].strip() or "Sheet"
    used = used if used is not None else set()
    cand, n = base, 2
    while cand.lower() in {u.lower() for u in used}:
        suf = " (%d)" % n
        cand = base[:limit - len(suf)].rstrip() + suf
        n += 1
    used.add(cand)
    return cand


def default_area(lines, title):
    """defaultAreaName: the last non-empty area on the current room, else the estimate title, else "PACE Estimate"."""
    for ln in reversed(lines or []):
        if clean(ln.get("area")):
            return clean(ln.get("area"))
    return clean(title) or "PACE Estimate"


def estimate_title(title, client, when):
    return clean(title) or clean(client) or ("Estimate %s" % when)
