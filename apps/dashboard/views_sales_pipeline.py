"""010 Quote Pipeline (/sales/010/pipeline/) — one filtered window over ChannelOnline quotes.

Everything on the page derives from a single filtered set `w` (created-date window × rep × author × outcome ×
customer × sector × size × text × manufacturer): funnel KPIs with prior-window deltas, quotes-by-period-by-outcome
series, rep × age-bucket, customer, sector, manufacturer, author and size-band breakdowns, and a client-rendered
quote table whose rows expand in place to line-item detail (JSON from `quote_json`). Read-side SQL over the
local apps.sales tables only — nothing here touches PTT / SL / CNET.

Definitions (docs/09_010_sales.md):
- outcome: won = linked to a CNET sales order (note_ref / heuristic_total) or status Ordered; lost = status
  Lost / Expired / Dead / Closed / Cancelled; hold = status Hold; everything else = open.
- CNET never expires quotes (2013 quotes still say "Open"), so "open pipeline" only means something inside a
  created-date window — default the last PIPE_DAYS days; older unconverted quotes are reported as abandoned.
- jumbo: quotes ≥ JUMBO_AMT (budgetary placeholders, zero-line lump sums — one $230M placeholder exists) are
  excluded from every total by default and listed in their own card, so they cannot swamp the real activity.
- stale: open and no CNET activity (created/modified) for STALE_DAYS.
- win rate = won ÷ quotes created in the window (by count and by $). Reps essentially never mark quotes lost,
  so a "decided" rate would be meaningless; recent windows read low because open quotes may still convert.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from apps.ingestion.bulk import fetch_dict
from apps.sales.models import CnetDocument

from .views import _ctx, _f
from .views_sales import PIPE_DAYS

D0 = Decimal(0)
JUMBO_AMT = Decimal(1_000_000)
STALE_DAYS = 30
# CNET quote costs are hand-entered; a cost above this multiple of the price is a unit-vs-extended entry error
# (one open quote carries a $165M cost on a $550k price). Such quotes are kept in every $ total but out of GM.
COST_SANITY = Decimal("1.5")
ROW_CAP = 1500
RANGES = {"30": 30, "90": 90, "180": 180, "365": 365}
OUTCOMES = ("open", "won", "lost", "hold")
SIZE_BANDS = ["< $1k", "$1k–10k", "$10k–50k", "$50k–250k", "$250k–1M", "≥ $1M"]
LOST_STATUSES = ("Lost", "Expired", "Dead", "Closed", "Cancelled")
INTERNAL_DOMAIN = "@pace-systems.com"


def _parse_date(s):
    try:
        return date.fromisoformat((s or "").strip())
    except ValueError:
        return None


def _window(g, today):
    """(range key, from, to) — explicit from/to wins; else a preset (default PIPE_DAYS)."""
    rng = (g.get("range") or "").strip()
    f, t = _parse_date(g.get("from")), _parse_date(g.get("to"))
    if f or t:
        rng = "custom"
        t = t or today
        f = f or (t - timedelta(days=PIPE_DAYS))
    elif rng == "ytd":
        f, t = today.replace(month=1, day=1), today
    elif rng == "all":
        f, t = date(2013, 1, 1), today
    else:
        rng = rng if rng in RANGES else str(PIPE_DAYS)
        f, t = today - timedelta(days=RANGES[rng]), today
    if f > t:
        f, t = t, f
    return rng, f, t


def _filters(g):
    """Non-date filter clauses over the raw document row `q` (+ customer `c`). Returns (clauses, params, echo)."""
    where, params, echo = [], [], {}
    for key, clause in (("rep", "COALESCE(NULLIF(q.salesperson_name,''),'House') = %s"),
                        ("creator", "q.created_by_name = %s"),
                        ("cust", "q.customer_sl_id = %s"),
                        ("sector", "COALESCE(NULLIF(c.market_sector,''),'(none)') = %s")):
        v = (g.get(key) or "").strip()
        if v:
            where.append(clause); params.append(v); echo[key] = v
    q = (g.get("q") or "").strip()
    if q:
        like = "%" + q + "%"
        where.append("(q.document_number ILIKE %s OR q.customer_company ILIKE %s OR c.canonical_name ILIKE %s OR q.description ILIKE %s"
                     " OR q.customer_po ILIKE %s OR q.note_internal ILIKE %s OR q.created_by_name ILIKE %s)")
        params += [like] * 7; echo["q"] = q
    for key, op in (("minamt", ">="), ("maxamt", "<")):
        v = (g.get(key) or "").replace(",", "").replace("$", "").strip()
        try:
            n = Decimal(v) if v else None
        except ArithmeticError:
            n = None
        if n is not None:
            where.append("COALESCE(q.total,0) %s %%s" % op); params.append(n); echo[key] = v
    mfr = (g.get("mfr") or "").strip()
    if mfr:
        where.append("EXISTS (SELECT 1 FROM sales_cnetdocumentline l WHERE l.document_id = q.id AND l.manufacturer ILIKE %s)")
        params.append(mfr); echo["mfr"] = mfr
    if g.get("self") == "1":
        where.append("q.created_by_email <> '' AND LOWER(q.created_by_email) NOT LIKE %s")
        params.append("%" + INTERNAL_DOMAIN); echo["self"] = "1"
    if g.get("nolines") == "1":
        where.append("q.line_count = 0 AND COALESCE(q.total,0) > 0"); echo["nolines"] = "1"
    return where, params, echo


def _base(f, t, extra_where, extra_params, now):
    """The `w` CTE: every quote in the window with derived outcome / age / jumbo / activity columns."""
    sql = """
    WITH w AS (
      SELECT q.id, q.document_number, q.customer_sl_id, q.customer_id,
             COALESCE(NULLIF(c.canonical_name,''), NULLIF(q.customer_company,''), q.customer_sl_id, '(no customer)') customer,
             c.sl_customer_id ccode, COALESCE(NULLIF(c.market_sector,''),'(none)') sector,
             COALESCE(NULLIF(q.salesperson_name,''),'House') rep, COALESCE(NULLIF(q.created_by_name,''),'?') creator,
             (q.created_by_email <> '' AND LOWER(q.created_by_email) NOT LIKE %s) self_service,
             q.created_at, GREATEST(q.created_at, COALESCE(q.modified_at, q.created_at)) last_activity,
             q.status, q.stage, q.probability, q.line_count, COALESCE(q.total,0) total, q.total_item_cost,
             q.description, q.note_internal, q.customer_po, q.ship_city, q.ship_state,
             q.converted_document_id, q.conversion_method, q.converted_at,
             CASE WHEN q.converted_document_id IS NOT NULL OR q.status = 'Ordered' THEN 'won'
                  WHEN q.status IN %s THEN 'lost'
                  WHEN q.status = 'Hold' THEN 'hold' ELSE 'open' END outcome,
             (COALESCE(q.total,0) >= %s) jumbo,
             (q.total_item_cost IS NOT NULL AND q.total_item_cost > 0 AND COALESCE(q.total,0) > 0
              AND q.total_item_cost <= COALESCE(q.total,0) * %s) cost_ok,
             EXTRACT(days FROM %s - q.created_at)::int age,
             EXTRACT(days FROM %s - GREATEST(q.created_at, COALESCE(q.modified_at, q.created_at)))::int idle,
             EXTRACT(days FROM q.converted_at - q.created_at)::int days_to_order
      FROM sales_cnetdocument q LEFT JOIN core_customer c ON c.id = q.customer_id
      WHERE q.doc_type = 'quote' AND NOT q.deleted AND q.created_at >= %s::date AND q.created_at < %s::date + 1
    """ + "".join(" AND " + c for c in extra_where) + ")\n"
    params = ["%" + INTERNAL_DOMAIN, LOST_STATUSES, JUMBO_AMT, COST_SANITY, now, now, f, t] + list(extra_params)
    return sql, params


def _rate(a, b):
    return (Decimal(a) / Decimal(b)) if b else None


def pipeline(request):
    g = request.GET
    today = timezone.localdate()
    now = timezone.now()
    rng, f, t = _window(g, today)
    xw, xp, echo = _filters(g)
    outcome = g.get("outcome") or "open"
    if outcome not in OUTCOMES + ("all",):
        outcome = "open"
    include_jumbo = g.get("jumbo") == "1"
    X = "TRUE" if include_jumbo else "NOT w.jumbo"        # jumbo gate applied to every total
    base, bp = _base(f, t, xw, xp, now)
    stale_cut = now - timedelta(days=STALE_DAYS)

    # ---- funnel KPIs for the window (all outcomes), plus the same for the prior window of equal length
    kpi_sql = """
      SELECT COUNT(*) n, SUM(w.total) amt, COUNT(DISTINCT w.customer_sl_id) custs,
             COUNT(*) FILTER (WHERE w.jumbo) jumbo_n, SUM(w.total) FILTER (WHERE w.jumbo) jumbo_amt,
             COUNT(*) FILTER (WHERE w.outcome='open') open_n, SUM(w.total) FILTER (WHERE w.outcome='open') open_amt,
             percentile_cont(0.5) WITHIN GROUP (ORDER BY w.total) FILTER (WHERE w.outcome='open') open_med,
             SUM(w.total_item_cost) FILTER (WHERE w.outcome='open' AND w.cost_ok) open_cost,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.cost_ok) open_priced,
             COUNT(*) FILTER (WHERE w.outcome='open' AND w.cost_ok) open_costed_n,
             COUNT(*) FILTER (WHERE w.total_item_cost > w.total * %s AND w.total > 0) costbad_n,
             COUNT(*) FILTER (WHERE w.outcome='open' AND w.last_activity < %s) stale_n,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.last_activity < %s) stale_amt,
             COUNT(*) FILTER (WHERE w.outcome='won') won_n, SUM(w.total) FILTER (WHERE w.outcome='won') won_amt,
             percentile_cont(0.5) WITHIN GROUP (ORDER BY w.days_to_order) FILTER (WHERE w.outcome='won' AND w.days_to_order IS NOT NULL) won_med_days,
             COUNT(*) FILTER (WHERE w.outcome='lost') lost_n, SUM(w.total) FILTER (WHERE w.outcome='lost') lost_amt,
             COUNT(*) FILTER (WHERE w.outcome='hold') hold_n, SUM(w.total) FILTER (WHERE w.outcome='hold') hold_amt,
             COUNT(*) FILTER (WHERE w.line_count = 0 AND w.total > 0) nolines_n, SUM(w.total) FILTER (WHERE w.line_count = 0 AND w.total > 0) nolines_amt,
             COUNT(*) FILTER (WHERE w.self_service) self_n, SUM(w.total) FILTER (WHERE w.self_service) self_amt
      FROM w WHERE """ + X
    jumbo_count = fetch_dict(base + "SELECT COUNT(*) n, SUM(w.total) amt FROM w WHERE w.jumbo", bp)[0]
    k = fetch_dict(base + kpi_sql, bp + [COST_SANITY, stale_cut, stale_cut])[0]
    for key in ("amt", "open_amt", "won_amt", "lost_amt", "hold_amt", "stale_amt", "nolines_amt", "self_amt", "jumbo_amt"):
        k[key] = k[key] or D0
    k["jumbo_n"], k["jumbo_amt"] = jumbo_count["n"], jumbo_count["amt"] or D0    # the excluded set, regardless of the gate
    k["win_n"] = _rate(k["won_n"], k["n"])
    k["win_amt"] = _rate(k["won_amt"], k["amt"])
    k["open_gm"] = ((k["open_priced"] - k["open_cost"]) / k["open_priced"]) if k["open_priced"] else None
    k["avg"] = (k["amt"] / k["n"]) if k["n"] else None
    span = (t - f).days + 1
    pf, pt = f - timedelta(days=span), f - timedelta(days=1)
    pbase, pbp = _base(pf, pt, xw, xp, now)
    prior = fetch_dict(pbase + """SELECT COUNT(*) n, SUM(w.total) amt, COUNT(*) FILTER (WHERE w.outcome='won') won_n,
                                   SUM(w.total) FILTER (WHERE w.outcome='won') won_amt FROM w WHERE """ + X, pbp)[0]
    prior["amt"], prior["won_amt"] = prior["amt"] or D0, prior["won_amt"] or D0
    prior["win_n"] = _rate(prior["won_n"], prior["n"])
    prior["from"], prior["to"] = pf, pt
    k["d_n"] = k["n"] - prior["n"]
    k["d_amt"] = k["amt"] - prior["amt"]
    k["d_won_amt"] = k["won_amt"] - prior["won_amt"]
    k["d_win_n"] = (k["win_n"] - prior["win_n"]) if (k["win_n"] is not None and prior["win_n"] is not None) else None

    # ---- quotes created per period × outcome (weekly ≤ 200-day windows, monthly beyond)
    unit = "week" if span <= 200 else "month"
    ser = fetch_dict(base + """SELECT date_trunc(%s, w.created_at)::date p, w.outcome, COUNT(*) n, SUM(w.total) amt
                               FROM w WHERE """ + X + " GROUP BY 1, 2 ORDER BY 1", bp + [unit])
    periods = {}
    for r in ser:
        p = periods.setdefault(r["p"], {"p": r["p"], "n": 0, "amt": D0})
        p[r["outcome"] + "_n"] = r["n"]
        p[r["outcome"] + "_amt"] = r["amt"] or D0
        p["n"] += r["n"]
        p["amt"] += r["amt"] or D0
    series = []
    for p in sorted(periods):
        r = periods[p]
        for o in OUTCOMES:
            r.setdefault(o + "_n", 0); r.setdefault(o + "_amt", D0)
        r["win_n"] = _rate(r["won_n"], r["n"])
        r["win_amt"] = _rate(r["won_amt"], r["amt"])
        series.append(r)

    # ---- by rep: volume, open by age bucket, stale, won, win rate, quoted GM% on open
    reps = fetch_dict(base + """
      SELECT w.rep, COUNT(*) n, SUM(w.total) amt, COUNT(DISTINCT w.customer_sl_id) custs,
             COUNT(*) FILTER (WHERE w.outcome='open') open_n, SUM(w.total) FILTER (WHERE w.outcome='open') open_amt,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.age <= 7) a0,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.age BETWEEN 8 AND 30) a1,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.age BETWEEN 31 AND 60) a2,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.age BETWEEN 61 AND 90) a3,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.age > 90) a4,
             COUNT(*) FILTER (WHERE w.outcome='open' AND w.age <= 7) c0,
             COUNT(*) FILTER (WHERE w.outcome='open' AND w.age BETWEEN 8 AND 30) c1,
             COUNT(*) FILTER (WHERE w.outcome='open' AND w.age BETWEEN 31 AND 60) c2,
             COUNT(*) FILTER (WHERE w.outcome='open' AND w.age BETWEEN 61 AND 90) c3,
             COUNT(*) FILTER (WHERE w.outcome='open' AND w.age > 90) c4,
             COUNT(*) FILTER (WHERE w.outcome='open' AND w.last_activity < %s) stale_n,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.last_activity < %s) stale_amt,
             COUNT(*) FILTER (WHERE w.outcome='won') won_n, SUM(w.total) FILTER (WHERE w.outcome='won') won_amt,
             COUNT(*) FILTER (WHERE w.outcome='lost') lost_n,
             percentile_cont(0.5) WITHIN GROUP (ORDER BY w.days_to_order) FILTER (WHERE w.outcome='won' AND w.days_to_order IS NOT NULL) won_med_days,
             SUM(w.total_item_cost) FILTER (WHERE w.outcome='open' AND w.cost_ok) open_cost,
             SUM(w.total) FILTER (WHERE w.outcome='open' AND w.cost_ok) open_priced,
             SUM(w.total_item_cost) FILTER (WHERE w.cost_ok) all_cost,
             SUM(w.total) FILTER (WHERE w.cost_ok) all_priced
      FROM w WHERE """ + X + " GROUP BY 1 ORDER BY 3 DESC", bp + [stale_cut, stale_cut])
    for r in reps:
        for key in ("amt", "open_amt", "won_amt", "stale_amt", "a0", "a1", "a2", "a3", "a4"):
            r[key] = r[key] or D0
        r["win_n"] = _rate(r["won_n"], r["n"])
        r["win_amt"] = _rate(r["won_amt"], r["amt"])
        r["open_gm"] = ((r["open_priced"] - r["open_cost"]) / r["open_priced"]) if r["open_priced"] else None
        r["all_gm"] = ((r["all_priced"] - r["all_cost"]) / r["all_priced"]) if r["all_priced"] else None

    # ---- by customer (top by open $, then volume) and by sector
    custs = fetch_dict(base + """
      SELECT w.customer_sl_id cid, MAX(w.customer) customer, MAX(w.ccode) ccode, MAX(w.sector) sector, COUNT(*) n, SUM(w.total) amt,
             COUNT(*) FILTER (WHERE w.outcome='open') open_n, SUM(w.total) FILTER (WHERE w.outcome='open') open_amt,
             MAX(w.age) FILTER (WHERE w.outcome='open') oldest,
             COUNT(*) FILTER (WHERE w.outcome='won') won_n, SUM(w.total) FILTER (WHERE w.outcome='won') won_amt,
             MAX(w.created_at) last_quote, string_agg(DISTINCT w.rep, ', ') reps
      FROM w WHERE """ + X + " GROUP BY 1 ORDER BY 8 DESC NULLS LAST, 6 DESC LIMIT 15", bp)
    for r in custs:
        r["win_n"] = _rate(r["won_n"], r["n"])
    sectors = fetch_dict(base + """
      SELECT w.sector, COUNT(*) n, SUM(w.total) amt, COUNT(DISTINCT w.customer_sl_id) custs,
             COUNT(*) FILTER (WHERE w.outcome='open') open_n, SUM(w.total) FILTER (WHERE w.outcome='open') open_amt,
             COUNT(*) FILTER (WHERE w.outcome='won') won_n, SUM(w.total) FILTER (WHERE w.outcome='won') won_amt
      FROM w WHERE """ + X + " GROUP BY 1 ORDER BY 3 DESC", bp)
    for r in sectors:
        r["win_n"] = _rate(r["won_n"], r["n"])

    # ---- what is being quoted: manufacturers on the window's quote lines (+ won / open share, quoted GM%)
    mfrs = fetch_dict(base + """
      SELECT l.manufacturer, COUNT(DISTINCT l.document_id) quotes, SUM(l.ext_price) amt,
             SUM(l.ext_price - l.ext_cost) FILTER (WHERE l.ext_cost > 0 AND l.ext_price > 0 AND l.ext_cost <= l.ext_price * %s) gp,
             SUM(l.ext_price) FILTER (WHERE l.ext_cost > 0 AND l.ext_price > 0 AND l.ext_cost <= l.ext_price * %s) priced,
             SUM(l.ext_price) FILTER (WHERE w.outcome='open') open_amt, SUM(l.ext_price) FILTER (WHERE w.outcome='won') won_amt
      FROM sales_cnetdocumentline l JOIN w ON w.id = l.document_id
      WHERE """ + X + " AND l.manufacturer <> '' GROUP BY 1 ORDER BY 3 DESC NULLS LAST LIMIT 14", bp + [COST_SANITY, COST_SANITY])
    for r in mfrs:
        r["gm"] = (r["gp"] / r["priced"]) if r["priced"] else None
        r["won_share"] = _rate(r["won_amt"] or 0, r["amt"] or 0) if r["amt"] else None

    # ---- size bands (never jumbo-gated: the ≥ $1M band is exactly the excluded set)
    bands = fetch_dict(base + """
      SELECT CASE WHEN w.total < 1000 THEN 0 WHEN w.total < 10000 THEN 1 WHEN w.total < 50000 THEN 2
                  WHEN w.total < 250000 THEN 3 WHEN w.total < 1000000 THEN 4 ELSE 5 END band,
             COUNT(*) n, SUM(w.total) amt,
             COUNT(*) FILTER (WHERE w.outcome='open') open_n, SUM(w.total) FILTER (WHERE w.outcome='open') open_amt,
             COUNT(*) FILTER (WHERE w.outcome='won') won_n, SUM(w.total) FILTER (WHERE w.outcome='won') won_amt,
             percentile_cont(0.5) WITHIN GROUP (ORDER BY w.days_to_order) FILTER (WHERE w.outcome='won' AND w.days_to_order IS NOT NULL) med_days
      FROM w GROUP BY 1 ORDER BY 1""", bp)
    band_bounds = [(None, 1000), (1000, 10000), (10000, 50000), (50000, 250000), (250000, 1000000), (1000000, None)]
    for r in bands:
        r["label"] = SIZE_BANDS[r["band"]]
        r["lo"], r["hi"] = band_bounds[r["band"]]
        r["win_n"] = _rate(r["won_n"], r["n"])
        r["win_amt"] = _rate(r["won_amt"] or 0, r["amt"] or 0) if r["amt"] else None
        r["jumbo"] = r["band"] == 5

    # ---- who writes the quotes (author ≠ rep is common: coordinators quote for a rep; customers self-serve)
    authors = fetch_dict(base + """
      SELECT w.creator, bool_or(w.self_service) self_service, COUNT(*) n, SUM(w.total) amt,
             string_agg(DISTINCT w.rep, ', ') reps, COUNT(*) FILTER (WHERE w.outcome='won') won_n,
             COUNT(*) FILTER (WHERE w.outcome='open') open_n, SUM(w.total) FILTER (WHERE w.outcome='open') open_amt
      FROM w WHERE """ + X + " GROUP BY 1 ORDER BY 3 DESC LIMIT 12", bp)
    for r in authors:
        r["win_n"] = _rate(r["won_n"], r["n"])

    # ---- the quote table (outcome-filtered; jumbo-gated; capped, client-side sort/search on top)
    row_sql = """
      SELECT w.document_number num, w.customer, w.ccode, w.sector, w.rep, w.creator, w.self_service, w.created_at, w.last_activity,
             w.age, w.idle, w.status, w.stage, w.probability, w.line_count, w.total, w.total_item_cost, w.outcome, w.conversion_method,
             w.days_to_order, w.description, LEFT(w.note_internal, 240) note, w.customer_po, w.ship_city, w.ship_state, w.jumbo,
             w.cost_ok, (w.total_item_cost > w.total * %s AND w.total > 0) costbad, so.document_number so_num
      FROM w LEFT JOIN sales_cnetdocument so ON so.id = w.converted_document_id
      WHERE """ + X + ("" if outcome == "all" else " AND w.outcome = %s")
    order = "w.total * GREATEST(w.age, 1) DESC" if outcome == "open" else "w.created_at DESC"
    rows = fetch_dict(base + row_sql + " ORDER BY " + order + " LIMIT %s", bp + [COST_SANITY] + ([] if outcome == "all" else [outcome]) + [ROW_CAP])
    in_view = fetch_dict(base + "SELECT COUNT(*) n, SUM(w.total) amt FROM w WHERE " + X + ("" if outcome == "all" else " AND w.outcome = %s"),
                         bp + ([] if outcome == "all" else [outcome]))[0]
    jumbo_rows = fetch_dict(base + row_sql.replace("WHERE " + X, "WHERE w.jumbo") + " ORDER BY w.total DESC LIMIT 60",
                            bp + [COST_SANITY] + ([] if outcome == "all" else [outcome]))

    def ser_row(r):
        return {"num": r["num"], "customer": r["customer"], "ccode": r["ccode"] or "", "sector": r["sector"], "rep": r["rep"],
                "creator": r["creator"], "self": bool(r["self_service"]),
                "created": r["created_at"].date().isoformat() if r["created_at"] else "",
                "activity": r["last_activity"].date().isoformat() if r["last_activity"] else "",
                "age": r["age"], "idle": r["idle"], "status": r["status"], "stage": r["stage"] or "",
                "prob": _f(r["probability"]), "lines": r["line_count"], "total": _f(r["total"]), "cost": _f(r["total_item_cost"]),
                "gm": _f((r["total"] - r["total_item_cost"]) / r["total"]) if r["cost_ok"] else None, "costbad": bool(r["costbad"]),
                "outcome": r["outcome"], "method": r["conversion_method"] or "", "days": r["days_to_order"],
                "so": r["so_num"] or "", "descr": r["description"] or "", "note": r["note"] or "", "po": r["customer_po"] or "",
                "ship": " ".join(x for x in (r["ship_city"], r["ship_state"]) if x), "jumbo": bool(r["jumbo"]),
                "score": _f((r["total"] or D0) * max(r["age"] or 0, 1))}
    table = {"rows": [ser_row(r) for r in rows], "jumbo": [ser_row(r) for r in jumbo_rows], "cap": ROW_CAP,
             "capped": len(rows) >= ROW_CAP, "n_in_view": in_view["n"], "amt_in_view": _f(in_view["amt"] or D0),
             "outcome": outcome, "stale_days": STALE_DAYS}

    # ---- context: abandoned-before-window note, filter option lists, status vocabulary in the window
    abandoned = fetch_dict("""SELECT COUNT(*) n, SUM(total) amt FROM sales_cnetdocument
                              WHERE doc_type='quote' AND NOT deleted AND converted_document_id IS NULL AND status NOT IN %s
                                AND status <> 'Ordered' AND created_at < %s::date""", [LOST_STATUSES, f])[0]
    opts = {
        "reps": fetch_dict("""SELECT COALESCE(NULLIF(salesperson_name,''),'House') r, COUNT(*) n FROM sales_cnetdocument
                              WHERE doc_type='quote' AND NOT deleted AND created_at >= %s GROUP BY 1 ORDER BY 2 DESC""",
                           [now - timedelta(days=730)]),
        "creators": fetch_dict("""SELECT created_by_name r, COUNT(*) n FROM sales_cnetdocument
                                  WHERE doc_type='quote' AND NOT deleted AND created_at >= %s AND created_by_name <> ''
                                  GROUP BY 1 HAVING COUNT(*) >= 3 ORDER BY 2 DESC LIMIT 40""", [now - timedelta(days=730)]),
        "sectors": fetch_dict("""SELECT COALESCE(NULLIF(c.market_sector,''),'(none)') r, COUNT(*) n
                                 FROM sales_cnetdocument q LEFT JOIN core_customer c ON c.id = q.customer_id
                                 WHERE q.doc_type='quote' AND NOT q.deleted AND q.created_at >= %s GROUP BY 1 ORDER BY 2 DESC""",
                              [now - timedelta(days=730)]),
    }
    statuses = fetch_dict(base + "SELECT w.status, w.outcome, COUNT(*) n FROM w GROUP BY 1, 2 ORDER BY 3 DESC", bp)
    # the customer filter echo needs a display name
    cust_name = None
    if echo.get("cust"):
        hit = fetch_dict("SELECT canonical_name FROM core_customer WHERE sl_customer_id = %s", [echo["cust"]])
        cust_name = hit[0]["canonical_name"] if hit else echo["cust"]
    charts = {
        "unit": unit,
        "series": [{"p": r["p"].isoformat(), "won": _f(r["won_amt"]), "open": _f(r["open_amt"]), "lost": _f(r["lost_amt"] + r["hold_amt"]),
                    "won_n": r["won_n"], "open_n": r["open_n"], "lost_n": r["lost_n"] + r["hold_n"], "n": r["n"], "win_n": _f(r["win_n"])}
                   for r in series],
        "reps": [{"rep": r["rep"], "a": [_f(r["a0"]), _f(r["a1"]), _f(r["a2"]), _f(r["a3"]), _f(r["a4"])],
                  "c": [r["c0"], r["c1"], r["c2"], r["c3"], r["c4"]]}
                 for r in sorted(reps, key=lambda r: -r["open_amt"]) if r["open_amt"] > 0][:10],
    }
    win = {"range": rng, "from": f, "to": t, "span": span, "unit": unit, "outcome": outcome, "include_jumbo": include_jumbo,
           "any_filter": bool(echo) or outcome != "open" or rng != str(PIPE_DAYS) or include_jumbo}
    return render(request, "dashboard/sales/pipeline.html", _ctx(
        request, "sales-pipeline", k=k, prior=prior, win=win, echo=echo, cust_name=cust_name, series=series, reps=reps,
        custs=custs, sectors=sectors, mfrs=mfrs, bands=bands, authors=authors, table=table, charts=charts,
        abandoned=abandoned, opts=opts, statuses=statuses, pipe_days=PIPE_DAYS, jumbo_amt=JUMBO_AMT,
        stale_days=STALE_DAYS, g=g))


def quote_json(request, number):
    """JSON for the in-row drilldown: one CNET document (quote preferred over an order with the same number),
    its lines with quoted cost / price / margin, a manufacturer rollup, and the linked sales order's
    fulfillment summary when the quote converted."""
    d = CnetDocument.objects.filter(document_number=number).order_by("doc_type").select_related("customer", "converted_document").first()
    if d is None:
        raise Http404
    lines, by_mfr = [], {}
    for l in d.lines.order_by("line_order", "line_number"):
        gp = (l.ext_price - l.ext_cost) if (l.ext_price is not None and l.ext_cost is not None) else None
        lines.append({"n": l.line_number, "part": l.part_number, "mfr": l.manufacturer, "descr": l.description,
                      "cat": l.category or l.product_type, "supplier": l.supplier_name, "sku": l.supplier_sku,
                      "qty": _f(l.qty), "unit_cost": _f(l.unit_cost), "unit_price": _f(l.unit_price),
                      "ext_cost": _f(l.ext_cost), "ext_price": _f(l.ext_price), "gp": _f(gp),
                      "gm": _f(gp / l.ext_price) if (gp is not None and l.ext_price) else None,
                      "dropship": l.dropship, "status": l.status, "note": l.note})
        m = by_mfr.setdefault(l.manufacturer or "(none)", {"mfr": l.manufacturer or "(none)", "lines": 0, "amt": D0, "cost": D0, "priced": D0})
        m["lines"] += 1
        m["amt"] += l.ext_price or D0
        if l.ext_price is not None and l.ext_cost is not None:
            m["priced"] += l.ext_price
            m["cost"] += l.ext_cost
    mfrs = sorted(by_mfr.values(), key=lambda m: -m["amt"])
    for m in mfrs:
        m["gm"] = _f((m["priced"] - m["cost"]) / m["priced"]) if m["priced"] else None
        m["amt"], m["cost"], m["priced"] = _f(m["amt"]), _f(m["cost"]), _f(m["priced"])
    total = d.total or D0
    gp = (total - d.total_item_cost) if (d.total_item_cost is not None) else None
    so = d.converted_document
    out = {
        "num": d.document_number, "type": d.doc_type, "status": d.status, "stage": d.stage or "", "prob": _f(d.probability),
        "revision": d.revision, "deleted": d.deleted, "descr": d.description,
        "customer": (d.customer.canonical_name if d.customer else "") or d.customer_company,
        "ccode": d.customer.sl_customer_id if d.customer else "", "sector": (d.customer.market_sector if d.customer else "") or "",
        "rep": d.salesperson_name or "House", "am": d.account_manager_name, "creator": d.created_by_name, "creator_email": d.created_by_email,
        "created": d.created_at.isoformat() if d.created_at else None, "modified": d.modified_at.isoformat() if d.modified_at else None,
        "po": d.customer_po, "terms": d.terms, "price_profile": d.price_profile, "tax_status": d.tax_status,
        "ship_to": " · ".join(x for x in (d.ship_company, d.ship_attn, d.ship_addr1, " ".join(y for y in (d.ship_city, d.ship_state, d.ship_zip) if y)) if x),
        "note_internal": d.note_internal, "note_external": d.note_external, "note_shipping": d.note_shipping,
        "totals": {"subtotal": _f(d.subtotal), "tax": _f(d.tax), "sh": _f(d.shipping_handling), "misc": _f(d.misc_amount),
                   "total": _f(total), "cost": _f(d.total_item_cost), "gp": _f(gp),
                   "gm": _f(gp / total) if (gp is not None and total) else None},
        "line_count": d.line_count, "lines": lines, "mfrs": mfrs,
        "so": None,
        "detail_url": "/sales/010/orders/%s/" % d.document_number,
    }
    if so is not None:
        rr, rc = so.realized_revenue, so.realized_cost
        out["so"] = {"num": so.document_number, "status": so.status, "ordered": so.ordered_at.date().isoformat() if so.ordered_at else None,
                     "sl": so.sl_ord_nbr, "sl_type": so.sl_so_type, "sl_status": so.sl_status, "cancelled": so.sl_cancelled,
                     "total": _f(so.total), "cost": _f(so.total_item_cost), "realized_rev": _f(rr), "realized_cost": _f(rc),
                     "realized_gp": _f(rr - rc) if (rr is not None and rc is not None) else None,
                     "invoices": so.invoice_count, "shipped": so.fully_shipped,
                     "first_ship": so.first_ship_date.isoformat() if so.first_ship_date else None,
                     "last_ship": so.last_ship_date.isoformat() if so.last_ship_date else None,
                     "method": d.conversion_method,
                     "days": (d.converted_at - d.created_at).days if (d.converted_at and d.created_at) else None,
                     "url": "/sales/010/orders/%s/" % so.document_number}
    return JsonResponse(out)


def customer_sales010(cust, today, now):
    """The customer page's "010 hardware quotes & orders" card (docs/09): every ChannelOnline quote and sales order for
    one SL customer, all time, with the pipeline's outcome / age / stale / jumbo rules, plus summary counts. The view
    calls this only for sales010.view holders (never compute-then-hide). Returns None when the customer has neither."""
    base, bp = _base(date(2013, 1, 1), today, ["q.customer_sl_id = %s"], [cust], now)
    quotes = fetch_dict(base + """
      SELECT w.document_number num, w.rep, w.creator, w.self_service, w.created_at, w.last_activity, w.age, w.idle, w.status, w.stage,
             w.probability, w.line_count, w.total, w.total_item_cost, w.outcome, w.conversion_method, w.days_to_order, w.converted_at, w.description,
             LEFT(w.note_internal, 240) note, w.customer_po, w.ship_city, w.ship_state, w.jumbo, w.cost_ok,
             (w.total_item_cost > w.total * %s AND w.total > 0) costbad,
             so.document_number so_num, so.sl_ord_nbr so_sl, so.sl_status so_status, so.sl_cancelled so_cancelled
      FROM w LEFT JOIN sales_cnetdocument so ON so.id = w.converted_document_id
      ORDER BY w.total DESC, w.created_at DESC""", bp + [COST_SANITY])
    orders = fetch_dict("""
      SELECT d.document_number num, d.sl_ord_nbr, d.sl_so_type, d.sl_status, d.sl_cancelled, d.status, COALESCE(d.ordered_at, d.created_at) ordered_at,
             COALESCE(d.total,0) total, d.total_item_cost, d.realized_revenue, d.realized_cost, d.fully_shipped, d.last_ship_date, d.invoice_count,
             d.line_count, COALESCE(NULLIF(d.salesperson_name,''),'House') rep, d.customer_po, d.description,
             (SELECT string_agg(q.document_number, ', ' ORDER BY q.document_number) FROM sales_cnetdocument q WHERE q.converted_document_id = d.id) from_quotes
      FROM sales_cnetdocument d WHERE d.doc_type = 'sales_order' AND NOT d.deleted AND d.customer_sl_id = %s
      ORDER BY COALESCE(d.ordered_at, d.created_at) DESC, d.document_number DESC""", [cust])
    if not quotes and not orders:
        return None
    k = {"n": len(quotes), "amt": D0, "open_n": 0, "open_amt": D0, "stale_n": 0, "aged_n": 0, "aged_amt": D0, "won_n": 0, "won_amt": D0,
         "won12_n": 0, "won12_amt": D0, "lost_n": 0, "hold_n": 0, "jumbo_n": 0, "jumbo_amt": D0}
    since12 = now - timedelta(days=365)
    for r in quotes:
        r["ship"] = " ".join(x for x in (r["ship_city"], r["ship_state"]) if x)
        r["gm"] = ((r["total"] - r["total_item_cost"]) / r["total"]) if r["cost_ok"] else None
        r["stale"] = r["outcome"] == "open" and (r["idle"] or 0) > STALE_DAYS
        r["bucket"] = "aged" if (r["outcome"] == "open" and (r["age"] or 0) > PIPE_DAYS) else r["outcome"]
        k["amt"] += r["total"]
        if r["bucket"] == "open":
            k["open_n"] += 1; k["open_amt"] += r["total"]; k["stale_n"] += 1 if r["stale"] else 0
        elif r["bucket"] == "aged":
            k["aged_n"] += 1; k["aged_amt"] += r["total"]
        elif r["bucket"] == "won":
            k["won_n"] += 1; k["won_amt"] += r["total"]
            if r["converted_at"] and r["converted_at"] >= since12:
                k["won12_n"] += 1; k["won12_amt"] += r["total"]
        elif r["bucket"] == "lost":
            k["lost_n"] += 1
        else:
            k["hold_n"] += 1
        if r["jumbo"]:
            k["jumbo_n"] += 1; k["jumbo_amt"] += r["total"]
    k["win_n"] = _rate(k["won_n"], k["n"]); k["win_amt"] = _rate(k["won_amt"], k["amt"])
    o = {"n": len(orders), "amt": D0, "invoiced": D0, "backlog_n": 0, "backlog_amt": D0, "last": orders[0]["ordered_at"] if orders else None}
    for r in orders:
        r["backlog"] = r["sl_status"] == "O" and not r["sl_cancelled"]
        r["backlog_amt"] = (r["total"] - (r["realized_revenue"] or D0)) if r["backlog"] else D0
        r["real_gm"] = ((r["realized_revenue"] - (r["realized_cost"] or D0)) / r["realized_revenue"]) if r["realized_revenue"] else None
        if not r["sl_cancelled"]:
            o["amt"] += r["total"]; o["invoiced"] += r["realized_revenue"] or D0
        if r["backlog"]:
            o["backlog_n"] += 1; o["backlog_amt"] += r["backlog_amt"]
    return {"quotes": quotes, "orders": orders, "k": k, "o": o, "pipe_days": PIPE_DAYS, "stale_days": STALE_DAYS}
