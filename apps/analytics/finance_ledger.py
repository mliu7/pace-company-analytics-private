"""Balance-sheet side breakdowns — the shared core behind the "what makes up total liabilities /
total assets" modals on the Daily Financial Snapshot (docs/06_finance_reports.md).

A *spec* (see finance_liabilities.SPEC / finance_assets.SPEC) names the population of ledger
accounts, the sign that counts as an increase, the display groups and the plain-English notes.
`ledger_breakdown()` then reads the local copies only (finance_glaccountbalance,
finance_glrecentposting, finance_dailyfinancesnapshot) and returns everything the generic
template `finance_ledger_breakdown.html` renders: per-account balances, the time ladder
(day / MTD / since Dec 31 / vs year ago / year-ends), 20-month month-end series, ledger activity,
groups that partition the population exactly, movers and the chart payload.

Balances are AcctHist natural-sign (assets debit-positive, liabilities credit-positive), so the
same arithmetic serves both sides; only the posting sign that means "more" differs.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from apps.ingestion.bulk import fetch_dict

D0 = Decimal("0")
ACTIVITY_DAYS = 90                # ledger-activity window shown per account (loader keeps 95)
SERIES_MONTHS = 20                # month-end balance history per account / group


def months_back(year, month, n):
    """[(y, m), ...] oldest first — the n months ending at (year, month)."""
    pts = []
    y, m = year, month
    for _ in range(n):
        pts.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(pts))


def month_end_balance(fy_row, month):
    """Natural-sign balance at the end of `month` (1-12) for one fiscal-year AcctHist row
    ({"beg": Decimal, "p": [13 Decimals]}). Period index 0 = January; the 13th adjustment
    period (index 12) lands in December."""
    bal = fy_row["beg"] + sum(fy_row["p"][:month], D0)
    if month == 12:
        bal += fy_row["p"][12]
    return bal


def month_series(rows_by_fy, year, month, n=SERIES_MONTHS):
    """[(label 'YYYY-MM', balance-or-None)] for the n months ending at (year, month)."""
    out = []
    for y, m in months_back(year, month, n):
        fy = rows_by_fy.get(str(y))
        out.append(("%04d-%02d" % (y, m), month_end_balance(fy, m) if fy else None))
    return out


def sparkline(values, width=96, height=22, pad=2):
    """SVG polyline points for a small trend line; None gaps are skipped. Flat series draw a baseline."""
    pts = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return ""
    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    span = (hi - lo) or 1.0
    n = len(values) - 1 or 1
    return " ".join("%.1f,%.1f" % (pad + i * (width - 2 * pad) / n,
                                  pad + (height - 2 * pad) * (1 - (v - lo) / span))
                    for i, v in pts)


def bar_segments(groups, total_key="total"):
    """Composition-bar widths (% of the positive total) for groups; negative groups get 0 width
    and are reported so the template can footnote them."""
    positive = sum((g[total_key] for g in groups if g[total_key] > 0), D0)
    negatives = [g for g in groups if g[total_key] < 0]
    for g in groups:
        g["bar_pct"] = float(g[total_key] / positive * 100) if positive and g[total_key] > 0 else 0.0
    return negatives


def _f(v):
    return float(v) if v is not None else None


def ledger_breakdown(spec, snap, prev=None, today=None):
    """Everything the breakdown modal shows for one balance-sheet side, from local tables.
    `snap` = the live DailyFinanceSnapshot whose headline is being explained; `prev` = the live
    snapshot before it (day change = postings entered between the two pulls)."""
    today = today or snap.snapshot_date
    fy, month = today.year, today.month
    first_fy = str(fy - 3)
    where, inc, up, down = spec["where"], spec["inc"], spec["up"], spec["down"]
    accounts_map, names, groups_def = spec["accounts"], spec["names"], spec["groups"]
    group_index = {g[0]: i for i, g in enumerate(groups_def)}

    def classify(acct):
        return accounts_map.get(acct, ("other", ""))[0]

    def explain(acct):
        return accounts_map.get(acct, ("other", ""))[1]

    def name(acct, descr):
        return names.get(acct) or (descr or acct).lower().capitalize()

    # ---- AcctHist: every account of the side, by fiscal year (subs summed) + current-year subs ----
    pcols = ", ".join("SUM(p%02d) p%02d" % (i, i) for i in range(13))
    rows = fetch_dict("""SELECT acct, fiscal_year, MAX(descr) descr, SUM(beg_bal) beg, %s
                         FROM finance_glaccountbalance WHERE %s AND fiscal_year >= %%s
                         GROUP BY acct, fiscal_year""" % (pcols, where), [first_fy])
    by_acct = defaultdict(dict)
    descr = {}
    for r in rows:
        by_acct[r["acct"]][r["fiscal_year"]] = {"beg": r["beg"] or D0, "p": [r["p%02d" % i] or D0 for i in range(13)]}
        if r["fiscal_year"] == str(fy) or r["acct"] not in descr:
            descr[r["acct"]] = r["descr"] or ""
    subs = defaultdict(list)
    for r in fetch_dict("""SELECT acct, sub, beg_bal + p00+p01+p02+p03+p04+p05+p06+p07+p08+p09+p10+p11+p12 bal
                           FROM finance_glaccountbalance WHERE %s AND fiscal_year = %%s ORDER BY acct, sub""" % where, [str(fy)]):
        if r["bal"]:
            subs[r["acct"]].append({"sub": r["sub"], "bal": r["bal"]})

    # ---- ledger activity (recent postings; loader window = 95 days) ----
    since = today - timedelta(days=ACTIVITY_DAYS)
    activity = {r["acct"]: r for r in fetch_dict("""
        SELECT acct, COUNT(*) n, SUM(%s) up, SUM(%s) down, MAX(tran_date) last_date,
               STRING_AGG(DISTINCT module, ', ' ORDER BY module) modules
        FROM finance_glrecentposting WHERE %s AND sl_created_at >= %%s GROUP BY acct""" % (up, down, where), [since])}
    top_postings = defaultdict(list)
    for r in fetch_dict("""
        SELECT * FROM (
          SELECT acct, sub, tran_date, module, jrnl_type, batch_nbr, ref_nbr, tran_desc, %s amt, per_post,
                 ROW_NUMBER() OVER (PARTITION BY acct ORDER BY ABS(%s) DESC, tran_date DESC) rn
          FROM finance_glrecentposting WHERE %s AND sl_created_at >= %%s) x
        WHERE rn <= 8 ORDER BY acct, rn""" % (inc, inc, where), [since]):
        top_postings[r["acct"]].append(r)
    # day change per account = postings entered between the two pulls (the rows that moved the ledger)
    day_delta = {}
    if prev is not None and prev.as_of and snap.as_of:
        day_delta = {r["acct"]: r["d"] or D0 for r in fetch_dict("""
            SELECT acct, SUM(%s) d FROM finance_glrecentposting
            WHERE %s AND sl_created_at > %%s AND sl_created_at <= %%s GROUP BY acct""" % (inc, where), [prev.as_of, snap.as_of])}

    # ---- per-account rows ----
    total = D0
    accounts = []
    for acct, fys in by_acct.items():
        cur = fys.get(str(fy))
        balance = (cur["beg"] + sum(cur["p"], D0)) if cur else D0
        mtd = cur["p"][month - 1] if cur else D0
        ytd = (balance - cur["beg"]) if cur else D0
        ye = {str(y): (month_end_balance(fys[str(y)], 12) if str(y) in fys else None) for y in range(fy - 3, fy)}
        series = month_series(fys, fy, month)
        year_ago = series[-13][1] if len(series) >= 13 else None
        act = activity.get(acct)
        a = {"acct": acct, "descr": descr.get(acct, ""), "name": name(acct, descr.get(acct, "")), "group": classify(acct),
             "explain": explain(acct), "links": spec.get("links", {}).get(acct, []),
             "balance": balance, "mtd": mtd, "ytd": ytd, "ye": ye, "year_ago": year_ago,
             "vs_year_ago": (balance - year_ago) if year_ago is not None else None,
             "day": day_delta.get(acct), "series": series, "spark": sparkline([v for _, v in series]),
             "subs": subs.get(acct, []), "activity": act, "postings": top_postings.get(acct, []),
             "active": bool(balance) or bool(mtd) or bool(act)}
        accounts.append(a)
        total += balance
    for a in accounts:
        a["share"] = float(a["balance"] / total) if total else 0.0
    accounts.sort(key=lambda a: (group_index.get(a["group"], 99), -abs(a["balance"]), a["acct"]))

    # ---- groups ----
    groups = []
    for key, label, blurb, color in groups_def:
        members = [a for a in accounts if a["group"] == key]
        if not members:
            continue
        g = {"key": key, "label": label, "blurb": blurb, "color": color, "accounts": members,
             "total": sum((a["balance"] for a in members), D0), "mtd": sum((a["mtd"] for a in members), D0),
             "ytd": sum((a["ytd"] for a in members), D0),
             "day": (sum((a["day"] for a in members if a["day"] is not None), D0) if day_delta else None),
             "ye": {str(y): sum((a["ye"][str(y)] or D0 for a in members), D0) for y in range(fy - 3, fy)},
             "n_active": sum(1 for a in members if a["active"]), "n_cleared": sum(1 for a in members if not a["active"])}
        ya = [a["year_ago"] for a in members if a["year_ago"] is not None]
        g["vs_year_ago"] = (g["total"] - sum(ya, D0)) if ya else None
        labels = [lbl for lbl, _ in members[0]["series"]]
        sums = [sum((a["series"][i][1] or D0 for a in members), D0) for i in range(len(labels))]
        g["series"] = list(zip(labels, sums))
        g["spark"] = sparkline(sums)
        g["share"] = float(g["total"] / total) if total else 0.0
        groups.append(g)
    negatives = bar_segments(groups)

    # ---- headline ladder + movers ----
    headline = getattr(snap, spec["snapshot_field"], None)
    prev_headline = getattr(prev, spec["snapshot_field"], None) if prev is not None else None
    ladder = {
        "day": (headline - prev_headline) if (headline is not None and prev_headline is not None) else None,
        "mtd": sum((g["mtd"] for g in groups), D0), "ytd": sum((g["ytd"] for g in groups), D0),
        "year_ago": sum((g["vs_year_ago"] for g in groups if g["vs_year_ago"] is not None), D0),
    }
    movers = {
        "ytd": sorted([a for a in accounts if a["ytd"]], key=lambda a: -abs(a["ytd"]))[:5],
        "mtd": sorted([a for a in accounts if a["mtd"]], key=lambda a: -abs(a["mtd"]))[:5],
        "day": sorted([a for a in accounts if a["day"]], key=lambda a: -abs(a["day"]))[:5],
    }
    chart = {"labels": [lbl for lbl, _ in groups[0]["series"]] if groups else [],
             "groups": [{"key": g["key"], "label": g["label"], "color": g["color"], "data": [_f(v) for _, v in g["series"]]} for g in groups],
             "accounts": {a["acct"]: [_f(v) for _, v in a["series"]] for a in accounts}}
    return {"side": spec, "headline": headline, "total": total,
            "identity_gap": (headline - total) if headline is not None else None,
            "groups": groups, "accounts": accounts, "negatives": negatives, "by_acct": by_acct,
            "ladder": ladder, "movers": movers, "chart": chart,
            "years": [str(y) for y in range(fy - 3, fy)],
            "ye_totals": {str(y): sum((g["ye"][str(y)] for g in groups), D0) for y in range(fy - 3, fy)},
            "activity_since": since, "has_activity": bool(activity), "has_day": bool(day_delta), "fy": fy}


def fy_balance(by_acct, acct, fy):
    """Balance today of one account from the by_acct structure ledger_breakdown returns."""
    row = by_acct.get(acct, {}).get(str(fy))
    return (row["beg"] + sum(row["p"], D0)) if row else D0
