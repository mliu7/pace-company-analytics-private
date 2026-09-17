"""Template tags for the bids app: the project page's bid card (SharePoint spec §4.5)."""

from django import template

from apps.bids.views_estimators import project_bid_card

register = template.Library()


@register.inclusion_tag("bids/_project_bid_card_inner.html", takes_context=True)
def project_bid_card_tag(context, project):
    ctx = project_bid_card(project) if project is not None else {}
    ctx["acc"] = context.get("acc")
    ctx["p"] = project
    return ctx


@register.filter
def stage_class(stage):
    return {"quoting": "warn", "submitted": "info", "awarded": "good", "lost": "bad", "did_not_bid": "", "on_hold": "", "no_decision": "", "unknown": ""}.get(stage or "", "")


@register.filter
def pts(v, places=1):
    """A margin-points delta as '+3.2 pts' / '−1.0 pts'."""
    if v is None:
        return "—"
    try:
        f = float(v) * 100
    except (TypeError, ValueError):
        return "—"
    return ("+" if f >= 0 else "−") + ("%%.%df pts" % places) % abs(f)


@register.filter
def index(seq, i):
    """seq[i] for the pivot header/month lookups."""
    try:
        return seq[int(i)]
    except (IndexError, TypeError, ValueError):
        return ""


# ------------------------------------------------------------------------------------------- hooks on other pages
@register.inclusion_tag("bids/_cc_tile.html", takes_context=True)
def bids_cc_tile(context, div_code=""):
    """Command Center KPI: open pipeline + expected bookings for the division scope (spec §5.3 hook)."""
    from apps.bids import analytics as A
    acc = context.get("acc")
    if acc is not None and not getattr(acc, "bids", False):
        return {"hide": True}
    try:
        o = A.overview(division=div_code or None)
    except Exception:  # noqa - never break the Command Center over a bids error
        return {"hide": True}
    kp = o["kp"]
    return {"kp": kp, "eb": kp["expected"][90], "div_code": div_code}


@register.inclusion_tag("bids/_week_card.html", takes_context=True)
def bids_week_card(context, div_code, start, end):
    """Project Snapshot card: what the estimators did in the window — submitted, awarded, lost, due (spec §5.5 hook)."""
    from apps.bids import analytics as A
    acc = context.get("acc")
    if acc is not None and not getattr(acc, "bids", False):
        return {"hide": True}
    where, params = ["b.source = 'list'"], []
    if div_code:
        where.append("b.division = %s"); params.append(div_code)
    where.append("((b.submitted_on BETWEEN %s AND %s) OR (b.awarded_on BETWEEN %s AND %s) OR (b.bid_due BETWEEN %s AND %s) OR (b.portal_modified::date BETWEEN %s AND %s AND b.stage = 'lost'))")
    params += [start, end] * 4
    try:
        rows = A.bids(" AND ".join(where), params, order="b.portal_modified DESC NULLS LAST")
    except Exception:  # noqa
        return {"hide": True}
    groups = {"submitted": [], "awarded": [], "lost": [], "due": []}
    for r in rows:
        if r["awarded_on"] and start <= r["awarded_on"] <= end:
            groups["awarded"].append(r)
        elif r["stage"] == "lost" and r["portal_modified"] and start <= r["portal_modified"].date() <= end:
            groups["lost"].append(r)
        elif r["submitted_on"] and start <= r["submitted_on"] <= end:
            groups["submitted"].append(r)
        elif r["bid_due"] and start <= r["bid_due"] <= end:
            groups["due"].append(r)
    tot = {k: sum((r["value"] or 0) for r in v) for k, v in groups.items()}
    return {"groups": groups, "tot": tot, "n": len(rows), "start": start, "end": end, "div_code": div_code}


@register.inclusion_tag("bids/_dq_section.html", takes_context=True)
def bids_dq_section(context):
    """Data Quality page: the unresolved bidder / rep / client aliases with a manual-fix form (spec §13.2)."""
    from apps.bids.models import BidClientAlias, BidderAlias
    from apps.core.models import Customer, Employee
    acc = context.get("acc")
    if acc is not None and not getattr(acc, "bids", False):
        return {"hide": True}
    people = list(BidderAlias.objects.filter(employee=None).exclude(role="walkthrough").order_by("-uses")[:60])
    clients = list(BidClientAlias.objects.filter(customer=None).order_by("-uses")[:80])
    for c in clients:
        c.cands = [x for x in (c.candidates or [])][:3]
    for p in people:
        p.cands = [x for x in (p.candidates or [])][:3]
    manual = list(BidderAlias.objects.filter(manual=True).select_related("employee").order_by("raw")) + list(BidClientAlias.objects.filter(manual=True).select_related("customer").order_by("raw"))
    return {"people": people, "clients": clients, "manual": manual, "csrf_token": context.get("csrf_token"), "request": context.get("request"),
            "employees": Employee.objects.filter(active=True).order_by("canonical_name").values("id", "canonical_name"),
            "n_people": BidderAlias.objects.filter(employee=None).exclude(role="walkthrough").count(), "n_clients": BidClientAlias.objects.filter(customer=None).count(),
            "customers_url": "/api/customers/"}


@register.filter
def split_pairs(s):
    """'a:A,b:B' -> [('a','A'), ('b','B')] for the week card's fixed group order."""
    return [tuple(x.split(":", 1)) for x in s.split(",")]
