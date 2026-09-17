"""Estimator and pipeline analytics (SharePoint spec §4.3, §5.2–5.3). Every figure drills to the bids behind it.

Pure helpers are at the top (unit-tested); the query functions below read PCA tables only.
"""

import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.ingestion.bulk import fetch_dict

from . import rules

D0 = Decimal(0)
SIZE_BANDS = [(0, 5000, "< $5k"), (5000, 25000, "$5k–25k"), (25000, 100000, "$25k–100k"), (100000, 500000, "$100k–500k"), (500000, None, "$500k+")]


# ------------------------------------------------------------------------------------------- pure
def size_band(value):
    if not value:
        return "(no value)"
    v = float(value)
    for lo, hi, label in SIZE_BANDS:
        if v >= lo and (hi is None or v < hi):
            return label
    return SIZE_BANDS[0][2]


def hit_rate(won, lost):
    n = won + lost
    return (won / n) if n else None


def weighted(items, key_won, key_val):
    """$-weighted hit rate over dicts: Σ value won ÷ Σ value decided."""
    won = sum(float(i[key_val] or 0) for i in items if i[key_won])
    tot = sum(float(i[key_val] or 0) for i in items)
    return (won / tot) if tot else None


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def calibration(rows):
    """rows: (probability, won) -> [{bucket, n, stated, actual}] — stated probability vs realised win rate."""
    buckets = defaultdict(lambda: {"n": 0, "won": 0, "stated": 0.0})
    for p, w in rows:
        if p is None:
            continue
        b = buckets[int(p)]
        b["n"] += 1; b["won"] += 1 if w else 0; b["stated"] += p / 100.0
    out = []
    for p in sorted(buckets):
        b = buckets[p]
        stated, actual = b["stated"] / b["n"], b["won"] / b["n"]
        out.append({"bucket": p, "n": b["n"], "stated": stated, "actual": actual, "gap": actual - stated})
    return out


def expected_bookings(bids, today, cycle_days, windows=(30, 60, 90), hit_rates=None):
    """bids: dicts with value, probability, submitted_on, bid_due, division, estimator_id.
    cycle_days: {division: median submitted->decision days} (fallback key '' ). hit_rates: {estimator_id: rate} for the
    historical variant. Returns {window: {"stated": $, "historical": $, "count": n}} (SharePoint spec §5.2)."""
    out = {w: {"stated": D0, "historical": D0, "count": 0} for w in windows}
    out["overdue"] = {"stated": D0, "historical": D0, "count": 0, "value": D0}   # past the expected decision date and still open
    for b in bids:
        if b.get("stage") not in rules.OPEN_STAGES or b.get("source", "list") != "list":
            continue
        cyc = cycle_days.get(b.get("division") or "", cycle_days.get("", 21))
        edd = rules.expected_decision_date(b.get("submitted_on"), b.get("bid_due"), cyc)
        if not edd:
            continue
        days = (edd - today).days
        val = Decimal(b.get("value") or 0)
        prob = b.get("probability")
        hr = (hit_rates or {}).get(b.get("estimator_id"))
        for w in list(windows) + (["overdue"] if days < 0 else []):
            if w == "overdue" or days <= w:
                out[w]["count"] += 1
                if prob is not None:
                    out[w]["stated"] += val * Decimal(prob) / 100
                if hr is not None:
                    out[w]["historical"] += val * Decimal(str(round(hr, 4)))
                if w == "overdue":
                    out[w]["value"] += val
    return out


# ------------------------------------------------------------------------------------------- queries
BID_SQL = """
    SELECT b.id, b.source, b.stage, b.stage_flag, b.status_raw, b.value, b.budget, b.probability, b.bid_due, b.submitted_on, b.awarded_on,
           b.portal_created, b.portal_modified, b.division, b.estimator_id, b.estimator_inferred, b.salesperson_id, b.house_account,
           b.client_id, b.client_name, b.project_name, b.project_id, b.won_by_sl, b.bom_status, b.walkthrough_raw, b.ball_in_court,
           b.job_number_raw, b.web_url, b.sp_item_id, b.portal_project_id, b.walkthrough_date, b.start, b."end" bid_end,
           e.canonical_name estimator, e.employee_key est_key, sp.canonical_name rep, sp.employee_key rep_key, c.canonical_name customer, c.sl_customer_id cust_key, c.market_sector sector,
           p.display_number pdisp, p.title ptitle, p.lifecycle_state pstate, p.contract_value cv, p.billed_revenue billed,
           p.actual_gp_percent gp, p.actual_direct_cost bdc, p.close_date, p.canonical_project_number cpn
    FROM bids_bid b
    LEFT JOIN core_employee e ON e.id = b.estimator_id
    LEFT JOIN core_employee sp ON sp.id = b.salesperson_id
    LEFT JOIN core_customer c ON c.id = b.client_id
    LEFT JOIN core_project p ON p.id = b.project_id
"""


def bids(where="TRUE", params=None, order="b.bid_due DESC NULLS LAST, b.portal_modified DESC"):
    rows = fetch_dict(BID_SQL + " WHERE %s ORDER BY %s" % (where, order), params or [])
    for r in rows:
        decorate(r)
    return rows


def decorate(r):
    r["won"] = r["stage"] == rules.AWARDED or bool(r["won_by_sl"])
    r["decided"] = r["won"] or r["stage"] == rules.LOST
    r["open"] = r["stage"] in rules.OPEN_STAGES and r["source"] == "list"   # archived rows are never live pipeline
    r["margin"] = rules.bid_margin(r["value"], r["budget"])
    r["stage_label"] = rules.STAGE_LABEL.get(r["stage"], r["stage"]) + (" (archived)" if r["stage_flag"] == "archived_open" else "")
    r["size_band"] = size_band(r["value"])
    r["realised_gp"] = float(r["gp"]) if r["gp"] is not None else None
    r["accuracy"] = (r["realised_gp"] - float(r["margin"])) if (r["margin"] is not None and r["realised_gp"] is not None and r["pstate"] == "closed_stabilized") else None
    r["cost_vs_budget"] = (float(r["bdc"]) / float(r["budget"])) if (r["budget"] and r["bdc"] and r["pstate"] == "closed_stabilized") else None
    r["cycle_days"] = (r["awarded_on"] - r["submitted_on"]).days if (r["awarded_on"] and r["submitted_on"] and r["awarded_on"] >= r["submitted_on"]) else None
    r["late_days"] = (r["submitted_on"] - r["bid_due"]).days if (r["submitted_on"] and r["bid_due"]) else None
    r["year"] = (r["bid_due"] or r["submitted_on"] or (r["portal_created"].date() if r["portal_created"] else None) or date(1900, 1, 1)).year
    r["client_label"] = r["customer"] or r["client_name"]
    return r


def decision_cycle_days():
    """Median submitted -> awarded days per division (and overall '') from decided bids with both dates."""
    per = defaultdict(list)
    for r in fetch_dict("SELECT division, submitted_on, awarded_on FROM bids_bid WHERE submitted_on IS NOT NULL AND awarded_on IS NOT NULL AND awarded_on >= submitted_on AND awarded_on - submitted_on <= 400"):
        d = (r["awarded_on"] - r["submitted_on"]).days
        per[r["division"] or ""].append(d)
        per[""].append(d)
    return {k: (statistics.median(v) if v else 21) for k, v in per.items()} or {"": 21}


def estimator_hit_rates(since=None):
    """{estimator_id: $-weighted hit rate over decided bids (trailing 24 months by default)}."""
    since = since or (timezone.localdate() - timedelta(days=730))
    rows = fetch_dict("""SELECT estimator_id, stage, won_by_sl, value FROM bids_bid WHERE estimator_id IS NOT NULL AND NOT estimator_inferred
                         AND (stage IN ('awarded','lost') OR won_by_sl) AND COALESCE(awarded_on, submitted_on, bid_due) >= %s""", [since])
    by = defaultdict(list)
    for r in rows:
        by[r["estimator_id"]].append({"won": r["stage"] == "awarded" or r["won_by_sl"], "value": r["value"]})
    return {k: weighted(v, "won", "value") for k, v in by.items() if len(v) >= 5}


# ------------------------------------------------------------------------------------------- estimators (§4.3)
def estimator_roster(division=None, since=None, until=None):
    """One row per estimator with output, hit rate, margin, accuracy, cycle and hygiene over the window."""
    where, params = ["b.estimator_id IS NOT NULL"], []
    if division:
        where.append("b.division = %s"); params.append(division)
    if since:
        where.append("COALESCE(b.bid_due, b.submitted_on, b.portal_created::date) >= %s"); params.append(since)
    if until:
        where.append("COALESCE(b.bid_due, b.submitted_on, b.portal_created::date) <= %s"); params.append(until)
    rows = bids(" AND ".join(where), params)
    by = defaultdict(list)
    for r in rows:
        by[r["estimator_id"]].append(r)
    meta = {e["id"]: e for e in fetch_dict("""SELECT e.id, e.canonical_name name, e.active, e.ptt_employee_role role, e.employee_key key
                                             FROM core_employee e WHERE e.id = ANY(%s)""", [list(by)])}
    out = []
    for eid, items in by.items():
        real = [r for r in items if not r["estimator_inferred"]]
        decided = [r for r in real if r["decided"]]
        won = [r for r in decided if r["won"]]
        submitted = [r for r in real if r["stage"] in (rules.SUBMITTED, rules.AWARDED, rules.LOST) or r["submitted_on"]]
        divs = Counter(r["division"] or "?" for r in items)
        out.append({
            "id": eid, "name": meta.get(eid, {}).get("name", "?"), "key": meta.get(eid, {}).get("key", ""), "active": meta.get(eid, {}).get("active"),
            "role": meta.get(eid, {}).get("role", ""), "division": divs.most_common(1)[0][0] if divs else "", "divisions": dict(divs),
            "bids": len(items), "inferred": sum(1 for r in items if r["estimator_inferred"]), "open": sum(1 for r in items if r["open"]),
            "submitted_value": sum((r["value"] or D0) for r in submitted), "submitted": len(submitted),
            "median_value": median([float(r["value"]) for r in submitted if r["value"]]),
            "won": len(won), "lost": len(decided) - len(won),
            "hit_rate": hit_rate(len(won), len(decided) - len(won)), "hit_rate_w": weighted(decided, "won", "value"),
            "dnb": sum(1 for r in real if r["stage"] == rules.DID_NOT_BID),
            "margin": median([float(r["margin"]) for r in real if r["margin"] is not None]),
            "accuracy": median([r["accuracy"] for r in real if r["accuracy"] is not None]), "accuracy_n": sum(1 for r in real if r["accuracy"] is not None),
            "cost_vs_budget": median([r["cost_vs_budget"] for r in real if r["cost_vs_budget"] is not None]),
            "cycle": median([r["cycle_days"] for r in real if r["cycle_days"] is not None]),
            "late": sum(1 for r in real if r["late_days"] is not None and r["late_days"] > 0), "late_n": sum(1 for r in real if r["late_days"] is not None),
            "leaks": sum(1 for r in real if r["stage"] == rules.AWARDED and not r["project_id"]),
            "hygiene": _hygiene(real),
            "house_share": (sum(1 for r in real if r["house_account"]) / len(real)) if real else None,
        })
    out.sort(key=lambda x: -x["bids"])
    return out


def _hygiene(rows):
    """Share of the estimator's submitted/awarded bids with budget, value, dates and probability all filled."""
    pool = [r for r in rows if r["stage"] in (rules.SUBMITTED, rules.AWARDED, rules.LOST)]
    if not pool:
        return None
    ok = sum(1 for r in pool if r["budget"] and r["value"] and r["bid_due"] and r["probability"] is not None)
    return ok / len(pool)


def estimator_detail(eid):
    rows = bids("b.estimator_id = %s", [eid], order="COALESCE(b.bid_due, b.submitted_on, b.portal_created::date) DESC NULLS LAST")
    real = [r for r in rows if not r["estimator_inferred"]]
    quarters = defaultdict(lambda: {"bids": 0, "won": 0, "lost": 0, "value": D0, "margins": [], "gp": [], "acc": []})
    for r in real:
        d = r["bid_due"] or r["submitted_on"] or (r["portal_created"].date() if r["portal_created"] else None)
        if not d:
            continue
        q = "%dQ%d" % (d.year, (d.month - 1) // 3 + 1)
        qq = quarters[q]
        qq["bids"] += 1
        if r["decided"]:
            qq["won" if r["won"] else "lost"] += 1
        qq["value"] += r["value"] or D0
        if r["margin"] is not None:
            qq["margins"].append(float(r["margin"]))
        if r["realised_gp"] is not None and r["won"]:
            qq["gp"].append(r["realised_gp"])
        if r["accuracy"] is not None:
            qq["acc"].append(r["accuracy"])
    trend = [{"q": q, "bids": v["bids"], "won": v["won"], "lost": v["lost"], "hit": hit_rate(v["won"], v["lost"]), "value": v["value"],
              "margin": median(v["margins"]), "gp": median(v["gp"]), "accuracy": median(v["acc"])} for q, v in sorted(quarters.items())]
    by_client = _group(real, lambda r: r["client_label"] or "(no client)")
    by_sector = _group(real, lambda r: r["sector"] or "(no sector)")
    by_size = _group(real, lambda r: r["size_band"])
    calib = calibration([(r["probability"], r["won"]) for r in real if r["decided"]])
    return {"rows": rows, "trend": trend, "by_client": by_client, "by_sector": by_sector, "by_size": by_size, "calibration": calib,
            "open": [r for r in rows if r["open"]], "decided": [r for r in rows if r["decided"]]}


def _group(rows, keyf):
    by = defaultdict(list)
    for r in rows:
        by[keyf(r)].append(r)
    out = []
    for k, items in by.items():
        decided = [r for r in items if r["decided"]]
        won = [r for r in decided if r["won"]]
        out.append({"key": k, "bids": len(items), "won": len(won), "lost": len(decided) - len(won), "hit": hit_rate(len(won), len(decided) - len(won)),
                    "value": sum((r["value"] or D0) for r in items), "margin": median([float(r["margin"]) for r in items if r["margin"] is not None]),
                    "accuracy": median([r["accuracy"] for r in items if r["accuracy"] is not None])})
    out.sort(key=lambda x: -x["bids"])
    return out


# ------------------------------------------------------------------------------------------- pipeline overview (§5.3)
def overview(division=None, estimator=None, rep=None, client=None, year=None, funnel_months=24):
    where, params = ["TRUE"], []
    if division:
        where.append("b.division = %s"); params.append(division)
    if estimator:
        where.append("b.estimator_id = %s"); params.append(estimator)
    if rep:
        where.append("b.salesperson_id = %s"); params.append(rep)
    if client:
        where.append("b.client_id = %s"); params.append(client)
    rows = bids(" AND ".join(where), params)
    today = timezone.localdate()
    open_rows = [r for r in rows if r["open"]]
    submitted = [r for r in open_rows if r["stage"] == rules.SUBMITTED]
    quoting = [r for r in open_rows if r["stage"] == rules.QUOTING]
    scored = [r for r in open_rows if r["probability"] is not None]
    pipeline = sum((r["value"] or D0) for r in open_rows)
    sub_val = sum((r["value"] or D0) for r in submitted)
    wtd = sum((r["value"] or D0) * Decimal(r["probability"]) / 100 for r in scored)
    wtd_sub = sum((r["value"] or D0) * Decimal(r["probability"]) / 100 for r in scored if r["stage"] == rules.SUBMITTED)
    m0 = today.replace(day=1)
    this_month_sub = [r for r in rows if r["submitted_on"] and r["submitted_on"] >= m0]
    this_month_won = [r for r in rows if r["won"] and (r["awarded_on"] or date(1900, 1, 1)) >= m0]
    t12 = today - timedelta(days=365)
    decided12 = [r for r in rows if r["decided"] and (r["awarded_on"] or r["submitted_on"] or r["bid_due"] or date(1900, 1, 1)) >= t12]
    won12 = [r for r in decided12 if r["won"]]
    cycles = decision_cycle_days()
    hr = estimator_hit_rates()
    eb = expected_bookings(open_rows, today, cycles, hit_rates=hr)
    kp = {
        "open_n": len(open_rows), "open_value": pipeline, "submitted_n": len(submitted), "submitted_value": sub_val, "quoting_n": len(quoting),
        "quoting_value": pipeline - sub_val, "weighted": wtd, "weighted_submitted": wtd_sub, "weighted_pct": (float(wtd) / float(pipeline)) if pipeline else None,
        "scored_n": len(scored), "unscored_n": len(open_rows) - len(scored),
        "avg_close": (sum(r["probability"] for r in scored) / len(scored)) if scored else None,
        "avg_value": (float(sub_val) / len(submitted)) if submitted else None,
        "bidders": len({r["estimator_id"] for r in open_rows if r["estimator_id"]}),
        "month_submitted_n": len(this_month_sub), "month_submitted_value": sum((r["value"] or D0) for r in this_month_sub),
        "month_won_n": len(this_month_won), "month_won_value": sum((r["value"] or D0) for r in this_month_won),
        "month_won_margin": median([float(r["margin"]) for r in this_month_won if r["margin"] is not None]),
        "hit12": hit_rate(len(won12), len(decided12) - len(won12)), "hit12_w": weighted(decided12, "won", "value"), "decided12": len(decided12),
        "cycle_median": cycles.get(division or "", cycles.get("")), "expected": eb,
        "leaks": [r for r in rows if r["stage"] == rules.AWARDED and not r["project_id"] and (r["awarded_on"] or (r["portal_modified"].date() if r["portal_modified"] else today)) <= today - timedelta(days=14) and (r["awarded_on"] or today) >= today - timedelta(days=730)],
        "stale": [r for r in open_rows if r["portal_modified"] and (timezone.now() - r["portal_modified"]).days >= 30],
        "attention": needs_attention(rows, today),
    }
    f_since = today - timedelta(days=30 * funnel_months) if funnel_months and not year else None
    frows = [r for r in rows if not f_since or (r["bid_due"] or r["submitted_on"] or (r["portal_created"].date() if r["portal_created"] else None) or date(1900, 1, 1)) >= f_since]
    charts = {
        "by_bidder": _bar(open_rows, lambda r: r["estimator"] or "(no bidder)", 8),
        "by_probability": _bar([r for r in open_rows if r["probability"] is not None], lambda r: "%d%%" % r["probability"], 12, sort_key=lambda k: -int(k["key"].rstrip("%"))),
        "by_ball": _bar(open_rows, lambda r: r["ball_in_court"] or "(blank)", 8),
        "funnel": {s: {"n": sum(1 for r in frows if r["stage"] == s), "value": sum((r["value"] or D0) for r in frows if r["stage"] == s)} for s in rules.STAGE_ORDER},
        "funnel_by_division": _funnel_by_division(frows),
        "win_trend": _win_trend(rows),
    }
    return {"rows": rows, "kp": kp, "charts": charts, "cycles": cycles}


def attention_reasons(r, today):
    """The portal replica's hygiene rules for one decorated bid row (Appendix A.2 PL-16) — [] when nothing is wrong."""
    reasons = []
    st = r["stage"]
    if not r["estimator_id"] and st in (rules.QUOTING, rules.SUBMITTED, rules.AWARDED):
        reasons.append("Missing bidder")
    if not r["bid_due"] and st in rules.OPEN_STAGES:
        reasons.append("Missing due date")
    if st == rules.AWARDED and not r["job_number_raw"]:
        reasons.append("Awarded, no job #")
    if st == rules.SUBMITTED and not r["value"]:
        reasons.append("Submitted, no value")
    if st == rules.SUBMITTED and r["value"] and r["budget"] is None:
        reasons.append("No budget")
    if (r["bom_status"] or "").upper() == "NEEDED" and st in rules.OPEN_STAGES:
        reasons.append("BOM needed")
    # a submitted proposal is past its bid-due date by definition; "past due" is only news while it is still being priced
    if r["bid_due"] and r["bid_due"] < today and st in (rules.QUOTING, rules.ON_HOLD):
        reasons.append("Past due")
    if st in rules.OPEN_STAGES and r["probability"] is None:
        reasons.append("Unscored")
    if r["portal_modified"]:
        idle = (timezone.now() - r["portal_modified"]).days
        if st in (rules.QUOTING, rules.ON_HOLD) and idle >= 100:
            reasons.append("Stale 100d+")          # red: a quote nobody has touched in over three months
        elif st in (rules.QUOTING, rules.ON_HOLD) and idle >= 60:
            reasons.append("Stale 60d+")
        elif st == rules.SUBMITTED and idle >= 120:
            reasons.append("No update 120d+")
    return reasons


def needs_attention(rows, today):
    """The queue: every live (list) row with at least one hygiene reason, worst first."""
    out = []
    for r in rows:
        if r["source"] != "list":
            continue
        reasons = attention_reasons(r, today)
        if reasons:
            r["reasons"] = reasons
            out.append(r)
    out.sort(key=lambda r: (-len(r["reasons"]), r["bid_due"] or date(2999, 1, 1)))
    return out


def _bar(rows, keyf, top, sort_key=None):
    by = defaultdict(lambda: {"n": 0, "value": D0})
    for r in rows:
        k = keyf(r)
        by[k]["n"] += 1
        by[k]["value"] += r["value"] or D0
    items = [{"key": k, "n": v["n"], "value": v["value"]} for k, v in by.items()]
    items.sort(key=sort_key or (lambda x: -float(x["value"])))
    mx = max((float(x["value"]) for x in items), default=0) or 1
    for x in items:
        x["pct"] = float(x["value"]) / mx
    return items[:top]


def _funnel_by_division(rows):
    by = defaultdict(lambda: defaultdict(lambda: {"n": 0, "value": D0}))
    for r in rows:
        d = by[r["division"] or "?"][r["stage"]]
        d["n"] += 1; d["value"] += r["value"] or D0
    return {div: dict(stages) for div, stages in sorted(by.items())}


def _win_trend(rows):
    q = defaultdict(lambda: {"won": 0, "lost": 0, "value_won": D0, "value": D0})
    for r in rows:
        if not r["decided"]:
            continue
        d = r["awarded_on"] or r["submitted_on"] or r["bid_due"]
        if not d:
            continue
        k = "%dQ%d" % (d.year, (d.month - 1) // 3 + 1)
        q[k]["won" if r["won"] else "lost"] += 1
        q[k]["value"] += r["value"] or D0
        if r["won"]:
            q[k]["value_won"] += r["value"] or D0
    return [{"q": k, "won": v["won"], "lost": v["lost"], "hit": hit_rate(v["won"], v["lost"]), "hit_w": (float(v["value_won"]) / float(v["value"])) if v["value"] else None}
            for k, v in sorted(q.items())][-12:]


def quoting_pivot(rows, axis="estimator", value="value", months=12):
    """Rows = estimator | rep | pm; columns = the last `months` months of bid due date; cells = $ submitted or count."""
    today = timezone.localdate()
    cols = []
    y, m = today.year, today.month
    for _ in range(months):
        cols.append("%04d-%02d" % (y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    cols.reverse()
    keyf = {"estimator": lambda r: r["estimator"] or "(no bidder)", "rep": lambda r: r["rep"] or ("House" if r["house_account"] else "(no rep)"),
            "division": lambda r: r["division"] or "?", "client": lambda r: r["client_label"] or "(no client)"}[axis]
    table = defaultdict(lambda: defaultdict(lambda: D0))
    for r in rows:
        d = r["bid_due"] or r["submitted_on"]
        if not d or r["stage"] not in (rules.SUBMITTED, rules.AWARDED, rules.LOST, rules.QUOTING):
            continue
        k = "%04d-%02d" % (d.year, d.month)
        if k in cols:
            table[keyf(r)][k] += (r["value"] or D0) if value == "value" else 1
    out = [{"key": k, "cells": [v.get(c, D0) for c in cols], "total": sum(v.values(), D0)} for k, v in table.items()]
    out.sort(key=lambda x: -float(x["total"]))
    totals = [sum((x["cells"][i] for x in out), D0) for i in range(len(cols))]
    return {"cols": cols, "rows": out, "totals": totals, "grand": sum(totals, D0)}
