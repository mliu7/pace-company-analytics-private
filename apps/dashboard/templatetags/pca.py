"""Display filters: money, percent, hours, deltas — never used for calculation."""

import re
from decimal import Decimal, InvalidOperation

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _num(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


@register.filter
def money(v, places=0):
    n = _num(v)
    if n is None:
        return "—"
    places = int(places)
    if places == 0:
        r = int(round(abs(n)))
        sign = "-" if (n < 0 and r) else ""      # never "-$0" for a value that rounds to zero
        return "%s$%s" % (sign, format(r, ","))
    sign = "-" if n < 0 else ""
    return "%s$%s" % (sign, format(abs(n), ",.%df" % places))


@register.filter
def money_auto(v):
    """Unit prices: cents below $100 ($2.35), whole dollars above ($1,656)."""
    n = _num(v)
    if n is None:
        return "—"
    return money(v, 2) if abs(n) < 100 else money(v, 0)


@register.filter
def money_k(v):
    """Compact: $1.2M / $340k / $850."""
    n = _num(v)
    if n is None:
        return "—"
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1_000_000:
        return "%s$%.2fM" % (sign, a / 1_000_000)
    if a >= 10_000:
        return "%s$%.0fk" % (sign, a / 1_000)
    if a >= 1_000:
        return "%s$%.1fk" % (sign, a / 1_000)
    return "%s$%.0f" % (sign, a)


@register.filter
def pct(v, places=1):
    """0.3043 -> 30.4%"""
    n = _num(v)
    if n is None:
        return "—"
    return ("%." + str(int(places)) + "f%%") % (n * 100)


@register.filter
def pts(v, places=1):
    """0.03 -> +3.0 pts"""
    n = _num(v)
    if n is None:
        return "—"
    return ("%+." + str(int(places)) + "f pts") % (n * 100)


@register.filter
def hours(v, places=0):
    n = _num(v)
    if n is None:
        return "—"
    return format(n, ",.%df" % int(places)) + " h"


@register.filter
def num(v, places=0):
    n = _num(v)
    if n is None:
        return "—"
    return format(n, ",.%df" % int(places))


@register.filter
def sub_money(a, b):
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return "—"
    return money(x - y)


@register.filter
def absolute(v):
    n = _num(v)
    return abs(n) if n is not None else None


@register.filter
def numdash(v, places=1):
    """Like num, but zero renders as a dash — for dense tables where zeros are noise."""
    n = _num(v)
    if n is None or n == 0:
        return "–"
    return format(n, ",.%df" % int(places))


@register.filter
def signed_money(v):
    n = _num(v)
    if n is None:
        return "—"
    s = money(n)
    return ("+" + s) if (n > 0 and s != "$0") else s     # "+$1,200", "-$800", "$0"


@register.filter
def signed_money_auto(v):
    """Unit-price deltas: +$0.01 / -$97.78 below $100, +$1,200 above."""
    n = _num(v)
    if n is None:
        return "—"
    s = money_auto(n)
    return ("+" + s) if (n > 0 and s not in ("$0", "$0.00")) else s


@register.filter
def posneg(v):
    n = _num(v)
    if n is None:
        return ""
    return "pos" if n > 0 else ("neg" if n < 0 else "")


@register.filter
def posneg_inv(v):
    n = _num(v)
    if n is None:
        return ""
    return "neg" if n > 0 else ("pos" if n < 0 else "")


@register.filter
def dcls(v, inverted=False):
    """Delta colour class: posneg, or posneg_inv when `inverted` (liabilities: an increase is red)."""
    return posneg_inv(v) if inverted else posneg(v)


@register.filter
def state_short(v):
    """Compact lifecycle label for narrow columns (the job table's State column); the full label goes in the hover."""
    return {
        "awarded_not_started": "Awarded", "in_progress": "In prog.", "field_complete": "Field done",
        "closed_stabilizing": "Closing", "closed_stabilized": "Closed", "dormant": "Dormant", "canceled": "Canceled",
        "template": "Internal", "unknown": "?",
    }.get(v, (v or "").replace("_", " ").title())


@register.filter
def state_label(v):
    return {
        "awarded_not_started": "Not Started", "in_progress": "In Progress", "field_complete": "Field Complete",
        "closed_stabilizing": "Closed / Stabilizing", "closed_stabilized": "Closed", "dormant": "Dormant", "canceled": "Canceled",
        "template": "Internal / Template", "unknown": "Unknown",
    }.get(v, (v or "").replace("_", " ").title())


@register.filter
def nice(v):
    return (v or "").replace("_", " ").title().replace("Tm ", "T&M ").replace("Joc", "JOC").replace("Ptt", "PTT").replace("Sl ", "SL ").replace("Pm ", "PM ").replace("Gp ", "GP ").replace("It ", "IT ").replace("Ups", "UPS").replace("Pc ", "PC ").replace("Sms", "SMS")


@register.filter
def bar_width(v, maximum):
    """Second arg: a number, or a rating unit name (pts/h1000/d1000) with a sensible scale."""
    unit_max = {"pts": "0.15", "h1000": "150", "d1000": "150"}
    n, m = _num(v), _num(unit_max.get(str(maximum), maximum))
    if n is None or m is None or m == 0:
        return "0"
    w = min(max(abs(n) / abs(m) * 100, 0), 100)
    return "%.1f" % w


@register.filter
def getitem(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def days_ago(dt):
    if not dt:
        return "never"
    from django.utils import timezone
    d = (timezone.now() - dt).days if hasattr(dt, "tzinfo") else (timezone.localdate() - dt).days
    if d <= 0:
        return "today"
    if d == 1:
        return "yesterday"
    return "%d days ago" % d


@register.simple_tag
def sort_link(request, field, label, help_text="", desc_first=False):
    """Sortable column header. desc_first=True (numeric columns): the first click sorts descending."""
    from django.utils.html import escape
    cur = request.GET.get("sort", "")
    desc = cur.startswith("-")
    active = cur.lstrip("-") == field
    nxt = ("-" + field) if ((active and not desc) or (not active and desc_first)) else field
    q = request.GET.copy()
    q["sort"] = nxt
    q.pop("page", None)
    arrow = " ▾" if (active and desc) else (" ▴" if active else "")
    title = ' title="%s"' % escape(help_text) if help_text else ""
    return mark_safe('<a href="?%s"%s>%s%s</a>' % (q.urlencode(), title, label, arrow))


@register.filter
def split(value, sep=","):
    return [v for v in str(value).split(sep) if v]


@register.simple_tag
def qs_without(request, *keys):
    q = request.GET.copy()
    for k in keys:
        q.pop(k, None)
    return q.urlencode()


@register.filter
def effect(v, unit="pts"):
    """Format a rating effect in its metric's unit (pts / h per 1,000 h / $ per $1,000)."""
    n = _num(v)
    if n is None:
        return "—"
    if unit == "pts":
        return pts(n)
    if unit == "h1000":
        return "%+.0f h" % n
    if unit == "d1000":
        return "%+.0f $" % n
    return num(n, 1)


@register.filter
def effect_bar_max(unit):
    return {"pts": "0.15", "h1000": "150", "d1000": "150"}.get(unit, "1")


_NAV_PINNED = {"home", "project-snapshot", "finance-daily", "pipeline-snapshot"}   # always visible at the top of the sidebar, outside any group


@register.filter
def nav_group(nav):
    """Which collapsible sidebar group (base.html) the page's nav key belongs to — that group is forced open so the
    active link is never hidden. '' for the pinned top links and for pages without a sidebar entry."""
    nav = nav or ""
    if nav in _NAV_PINNED:
        return ""
    if nav in ("console", "dq", "about", "finance-alloc"):
        return "system"
    if nav in ("projects", "forecast", "map", "documents"):
        return "projects"
    if nav in ("bids", "production", "schedule", "estimators", "estimating"):
        return "bidsplanning"
    if nav in ("people", "field", "customers", "ratings"):
        return "people"
    if nav.startswith("finance-"):
        return "finance"
    if nav.startswith("sales-"):
        return "sales"
    if nav.startswith("insight"):
        return "insights"
    return ""


@register.filter
def hl(text, q):
    """Wrap every word of the query found in `text` in <mark> (search results). Escapes the text first."""
    from django.utils.html import escape
    s = escape("" if text is None else str(text))
    toks = [t for t in re.split(r"\s+", (q or "").strip().lower()) if t]
    if not toks:
        return mark_safe(s)
    pat = re.compile("(" + "|".join(re.escape(t) for t in toks) + ")", re.I)
    return mark_safe(pat.sub(r"<mark>\1</mark>", s))


@register.filter
def attr_or_blank(obj, name):
    v = getattr(obj, name, None)
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    return v or ""


def _cv_fields(obj):
    """(basis, evidence, effective, sl) from a Project or a row dict (either plain or cv_-prefixed keys)."""
    if obj is None:
        return None, None, None, None
    if isinstance(obj, dict):
        g = obj.get
        basis, ev, eff, sl = (g("contract_value_basis", g("cv_basis")), g("contract_value_evidence", g("cv_evidence")),
                              g("contract_value", g("cv")), g("contract_value_sl", g("cv_sl")))
    else:
        basis, ev, eff, sl = (getattr(obj, "contract_value_basis", None), getattr(obj, "contract_value_evidence", None),
                              getattr(obj, "contract_value", None), getattr(obj, "contract_value_sl", None))
    if isinstance(ev, str):          # raw SQL on the local DB returns jsonb as text (docs/03 conventions)
        import json
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = None
    return basis, ev, eff, sl


@register.filter
def cv_explain(obj):
    """The one-sentence explanation of an adjusted contract value ('' when the app uses SL's figure as-is)."""
    from apps.analytics.contract_value import explain
    basis, ev, eff, _ = _cv_fields(obj)
    if not basis or (basis == "sl" and not (ev or {}).get("confirmed_by")):
        return ""
    return explain(basis, ev or {}, eff)


@register.simple_tag
def cv_mark(obj):
    """A small hover marker after a contract value the app has adjusted away from SL's figure; nothing otherwise."""
    text = cv_explain(obj)
    basis = _cv_fields(obj)[0]
    if not text or basis == "sl":
        return ""
    return mark_safe(' <span class="cvnote" title="%s">ⓘ</span>' % text.replace('"', "&quot;"))
