"""Daily finance snapshot builder (docs/finance_daily_snapshot_plan.md).

Computes every headline number of the daily finance report from the local copies
(finance_glaccountbalance, finance_glrecentposting, finance_aropendocument,
finance_apopendocument) and upserts one DailyFinanceSnapshot row per day.
No source connections are opened here.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.core import related_parties as rp
from apps.finance.models import DailyFinanceSnapshot
from apps.ingestion.bulk import fetch_dict

from . import finance_wip

D0 = Decimal("0")

# GL account map (verified against the finance office's 7/6/26 email; see the plan file).
ACCT_CHECKING, ACCT_MMA, ACCT_PAYROLL, ACCT_PETTY = "10250", "10350", "10400", "10800"
ACCT_YTD_NI = "39999"
CREDIT_LINE_ACCTS = ("20005", "20007")
INVENTORY_PREFIX = "12"           # 12000..12010 inventory accounts (12600 excluded below)
INVENTORY_ACCTS = ("12000", "12001", "12003", "12010")
CURRENT_ASSET_MAX = "15000"       # 1A accounts below this are current (cash/AR/inventory/prepaids)
REVENUE_EXCLUDED_ACCTS = ("40100",)  # interest income — the daily report's "Revenue" excludes it
JOB_SALES_ACCTS = ("40000", "40001")     # the accountants' P&L "Sales" lines
COGS_SALARY_ACCTS = ("60000", "60005")   # division salaries + union benefits — in cost of sales per the P&L workbooks


def gl_pnl_bucket(acct, acct_type):
    """Official P&L bucket for a GL account, matching the accountants' division workbooks
    (070's 2024 revenue and COGS both reproduced to the penny with this classification)."""
    if acct_type == "3I":
        return "revenue" if acct in JOB_SALES_ACCTS else "other_income"
    if acct_type == "4E":
        return "cogs" if (acct.startswith("5") or acct in COGS_SALARY_ACCTS) else "overhead"
    return None


def period_of(d):
    return "%04d%02d" % (d.year, d.month)


def prior_month_period(d):
    first = d.replace(day=1)
    prev = first - timedelta(days=1)
    return period_of(prev), prev


def gl_year_balances(fiscal_year):
    """{acct: (acct_type, descr, natural_balance)} — BegBal + all 13 periods for the FY."""
    rows = fetch_dict("""SELECT acct, MAX(acct_type) t, MAX(descr) d,
                                SUM(beg_bal + p00+p01+p02+p03+p04+p05+p06+p07+p08+p09+p10+p11+p12) b
                         FROM finance_glaccountbalance WHERE fiscal_year=%s GROUP BY acct""", [fiscal_year])
    return {r["acct"]: (r["t"], r["d"], r["b"] or D0) for r in rows}


def previous_business_day(today):
    """Most recent day before today with any GL posting activity; falls back to previous weekday."""
    r = fetch_dict("""SELECT MAX(sl_created_at::date) d FROM finance_glrecentposting
                      WHERE sl_created_at::date < %s AND acct_type IN ('3I','4E')""", [today])
    if r and r[0]["d"]:
        return r[0]["d"]
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def pnl_for_period(period, as_of=None):
    """(revenue excl interest, cogs 5xxxx, overhead 6xxxx+7xxxx) posted to a fiscal period — as the
    ledger stood at the end of `as_of` when given (postings created on or before that day)."""
    r = fetch_dict("""SELECT SUM(CASE WHEN acct_type='3I' AND acct NOT IN %s THEN cr_amt - dr_amt ELSE 0 END) rev,
                             SUM(CASE WHEN acct_type='4E' AND (LEFT(acct,1)='5' OR acct IN %s) THEN dr_amt - cr_amt ELSE 0 END) cogs,
                             SUM(CASE WHEN acct_type='4E' AND LEFT(acct,1) IN ('6','7') AND acct NOT IN %s THEN dr_amt - cr_amt ELSE 0 END) ovh
                      FROM finance_glrecentposting WHERE per_post=%s AND (%s::date IS NULL OR sl_created_at::date <= %s)""",
                   [REVENUE_EXCLUDED_ACCTS, COGS_SALARY_ACCTS, COGS_SALARY_ACCTS, period, as_of, as_of])[0]
    return r["rev"] or D0, r["cogs"] or D0, r["ovh"] or D0


def pnl_days_for(period, month_start, as_of=None):
    """Revenue / cost posted into `period` by posting-creation day from `month_start` (through `as_of`)."""
    return [{"d": r["d"].isoformat(), "rev": float(r["rev"] or 0), "cost": float(r["cost"] or 0)}
            for r in fetch_dict("""SELECT sl_created_at::date d,
                                          SUM(CASE WHEN acct_type='3I' AND acct NOT IN %s THEN cr_amt - dr_amt ELSE 0 END) rev,
                                          SUM(CASE WHEN acct_type='4E' THEN dr_amt - cr_amt ELSE 0 END) cost
                                   FROM finance_glrecentposting WHERE per_post=%s AND sl_created_at::date >= %s
                                     AND (%s::date IS NULL OR sl_created_at::date <= %s)
                                   GROUP BY 1 ORDER BY 1""", [REVENUE_EXCLUDED_ACCTS, period, month_start, as_of, as_of]) if r["d"]]


def page_extras(day=None):
    """The daily page's list sections that come from today's open documents — AP aging by document
    type, cash due in the next 14 days, largest over-90 customers, largest open invoices, pending
    invoices — captured into each snapshot's detail["extras"] so any past day can be replayed. JSON-safe.

    The two "largest" lists exist to point at problems, so owner-family accounts are filtered out of them
    (apps.core.related_parties) and the omission is disclosed in `top_over90_note` / `largest_open_note`.
    Every aging total, balance and bucket elsewhere in the snapshot still includes them."""
    day = day or timezone.localdate()
    ap_matrix = {}
    for r in fetch_dict("SELECT doc_type, bucket, SUM(sign*doc_bal) amt FROM finance_apopendocument GROUP BY doc_type, bucket"):
        ap_matrix.setdefault(r["doc_type"], {})[r["bucket"]] = float(r["amt"] or 0)
    top_over90 = [{"customer_id_raw": r["customer_id_raw"], "name": r["name"], "amt": float(r["amt"] or 0), "n": r["n"],
                   "oldest": r["oldest"].isoformat() if r["oldest"] else None, "link_id": r["link_id"]}
                  for r in fetch_dict("""SELECT customer_id_raw, MAX(customer_name) name, SUM(sign*doc_bal) amt, COUNT(*) n, MIN(due_date) oldest,
                                                MAX(c.sl_customer_id) link_id
                                         FROM finance_aropendocument d LEFT JOIN core_customer c ON c.id = d.customer_id
                                         WHERE bucket='over90' AND released GROUP BY customer_id_raw ORDER BY amt DESC LIMIT 24""")]
    ap_due = [{"due_date": r["due_date"].isoformat(), "amt": float(r["amt"] or 0), "n": r["n"]}
              for r in fetch_dict("""SELECT due_date, SUM(sign*doc_bal) amt, COUNT(*) n FROM finance_apopendocument
                                     WHERE due_date BETWEEN %s AND %s GROUP BY due_date ORDER BY due_date""", [day, day + timedelta(days=14)])]
    largest_open = [{"ref_nbr": r["ref_nbr"], "customer_name": r["customer_name"], "customer_id_raw": r["customer_id_raw"],
                     "doc_date": r["doc_date"].isoformat() if r["doc_date"] else None, "due_date": r["due_date"].isoformat() if r["due_date"] else None,
                     "days_past_due": r["days_past_due"], "amt": float(r["amt"] or 0), "book": r["book"], "link_id": r["link_id"]}
                    for r in fetch_dict("""SELECT ref_nbr, customer_name, customer_id_raw, doc_date, due_date, days_past_due, sign*doc_bal amt, book,
                                                  (SELECT sl_customer_id FROM core_customer c WHERE c.id=d.customer_id) link_id
                                           FROM finance_aropendocument d WHERE released AND sign > 0 ORDER BY doc_bal DESC LIMIT 24""")]
    pending_count = fetch_dict("SELECT COUNT(*) n FROM finance_aropendocument WHERE NOT released")[0]["n"]
    # These two lists are "who should we chase" lists — owner-family accounts do not belong on them (policy and
    # reasoning in apps/core/related_parties.py). Totals and buckets elsewhere in the snapshot are untouched.
    top_over90, top_over90_out = rp.partition(top_over90, "customer", "customer_id_raw")
    largest_open, largest_open_out = rp.partition(largest_open, "customer", "customer_id_raw")
    return {"ap_matrix": ap_matrix, "top_over90": top_over90[:12], "ap_due": ap_due, "ap_due_from": day.isoformat(),
            "ap_due_to": (day + timedelta(days=14)).isoformat(), "largest_open": largest_open[:12], "pending_count": pending_count,
            "top_over90_note": rp.excluded_note(top_over90_out, noun="customer"),
            "largest_open_note": rp.excluded_note(largest_open_out, noun="invoice")}


def gl_rollback_as_of(day, latest_live_date):
    """Balance sheet, cash and P&L as the ledger stood at the end of `day`: today's AcctHist balances with
    every posting created after `day` reversed. finance_glrecentposting is a rolling window (loader
    GL_ACTIVITY_WINDOW_DAYS), so this is exact only for days inside it and in the current fiscal year;
    returns None otherwise. Equity accounts (3xxxx) are not in the window, so equity is today's figure and
    the identity gap shows any equity posting made since `day`. The P&L block needs both the month of `day`
    and the prior month to start inside the window; it is None when they do not."""
    from apps.ingestion.finance_loaders import GL_ACTIVITY_WINDOW_DAYS
    window_start = latest_live_date - timedelta(days=GL_ACTIVITY_WINDOW_DAYS - 2)   # the loader's since_dt at the last pull, with margin
    if day < window_start or day >= latest_live_date or day.year != latest_live_date.year:
        return None
    fy = str(day.year)
    bal = gl_year_balances(fy)
    ni_adj = re_adj = D0
    for r in fetch_dict("""SELECT acct, MAX(acct_type) t, (LEFT(per_post, 4) = %s) same_fy, SUM(dr_amt) dr, SUM(cr_amt) cr
                           FROM finance_glrecentposting WHERE sl_created_at::date > %s GROUP BY acct, 3""", [fy, day]):
        signed = (r["dr"] - r["cr"]) if r["t"] in ("1A", "4E") else (r["cr"] - r["dr"])   # natural sign
        if r["t"] in ("1A", "2L"):
            t, d, b = bal.get(r["acct"], (r["t"], "", D0))
            bal[r["acct"]] = (t, d, b - signed)
        elif r["same_fy"]:
            ni_adj += signed if r["t"] == "3I" else -signed        # revenue raised NI, expense lowered it
        else:
            re_adj += signed if r["t"] == "3I" else -signed        # prior-year P&L rolled into retained earnings
    assets = liabilities = equity = current_assets = ni = credit_line = inventory = D0
    cash = {}
    for acct, (t, _d, b) in bal.items():
        if t == "1A":
            assets += b
            if acct < CURRENT_ASSET_MAX:
                current_assets += b
            if acct in (ACCT_CHECKING, ACCT_MMA, ACCT_PAYROLL, ACCT_PETTY):
                cash[acct] = b
            if acct in INVENTORY_ACCTS:
                inventory += b
        elif t == "2L":
            if acct == ACCT_YTD_NI:
                ni = b
            elif acct.startswith("3"):
                equity += b
            else:
                liabilities += b
                if acct in CREDIT_LINE_ACCTS:
                    credit_line += b
    ni -= ni_adj
    equity -= re_adj
    out = {"assets": assets, "liabilities": liabilities, "equity": equity, "net_income_ytd": ni,
           "working_capital": current_assets - liabilities, "identity_gap": float(assets - liabilities - equity - ni),
           "cash_checking": cash.get(ACCT_CHECKING), "cash_payroll": cash.get(ACCT_PAYROLL), "cash_mma": cash.get(ACCT_MMA),
           "cash_petty": cash.get(ACCT_PETTY), "credit_line_gl": credit_line, "inventory_gl": inventory,
           "source": "rollback", "window_start": window_start, "pnl": None, "pnl_days": [], "prev_business_day": None}
    cur_period = period_of(day)
    prior_period, prior_last = prior_month_period(day)
    # The loader keeps every posting created inside the window plus every posting into periods from the
    # month before the latest pull on (finance_gl_activity.sql). A month older than that can be missing
    # postings entered early (recurring / reversing batches), so the prior-month column falls back to the
    # ledger's full-month total as it stands today; the month of `day` is always complete inside the window.
    since_period, _ = prior_month_period(latest_live_date)
    if day.replace(day=1) >= window_start:
        rev, cogs, ovh = pnl_for_period(cur_period, day)
        prior_as_of = prior_period >= since_period
        rev_p, cogs_p, ovh_p = pnl_for_period(prior_period, day) if prior_as_of else pnl_from_acct_hist(prior_period)
        prev_day = previous_business_day(day)
        rev_pd, cost_pd = pnl_created_on(prev_day, cur_period)
        rev_pdp, cost_pdp = pnl_created_on(prev_day, prior_period)
        out.update(pnl={"revenue_mtd": rev, "cogs_mtd": cogs, "overhead_mtd": ovh,
                        "revenue_prior_month": rev_p, "cogs_prior_month": cogs_p, "overhead_prior_month": ovh_p,
                        "revenue_prev_day_cur": rev_pd, "cost_prev_day_cur": cost_pd,
                        "revenue_prev_day_prior": rev_pdp, "cost_prev_day_prior": cost_pdp, "prior_as_of": prior_as_of},
                   prev_business_day=prev_day, pnl_days=pnl_days_for(cur_period, day.replace(day=1), day))
    return out


def pnl_from_acct_hist(period):
    """(revenue excl interest, cogs, overhead) for a fiscal period from AcctHist — the month's total as the
    ledger stands today (late postings included), same classification as the daily report."""
    fy, col = period[:4], "p%02d" % (int(period[4:]) - 1)
    rev = cogs = ovh = D0
    for r in fetch_dict("SELECT acct, MAX(acct_type) t, SUM(%s) v FROM finance_glaccountbalance WHERE fiscal_year=%%s AND acct_type IN ('3I','4E') GROUP BY acct" % col, [fy]):
        v = r["v"] or D0
        b = gl_pnl_bucket(r["acct"], r["t"])
        if b == "revenue":
            rev += v
        elif b == "cogs":
            cogs += v
        elif b == "overhead":
            ovh += v
    return rev, cogs, ovh


def pnl_created_on(day, period):
    """(revenue, cost) rows created on `day` posted to `period` — the report's 'previous day' lines."""
    r = fetch_dict("""SELECT SUM(CASE WHEN acct_type='3I' AND acct NOT IN %s THEN cr_amt - dr_amt ELSE 0 END) rev,
                             SUM(CASE WHEN acct_type='4E' THEN dr_amt - cr_amt ELSE 0 END) cost
                      FROM finance_glrecentposting WHERE sl_created_at::date=%s AND per_post=%s""",
                   [REVENUE_EXCLUDED_ACCTS, day, period])[0]
    return r["rev"] or D0, r["cost"] or D0


def build_daily_snapshot(run, as_of=None):
    now = timezone.now()
    today = (as_of or timezone.localdate())
    fy = str(today.year)
    cur_period = period_of(today)
    prior_period, _ = prior_month_period(today)

    # ---- balance sheet (natural-sign AcctHist: debit-normal positive, credit-normal positive) ----
    bal = gl_year_balances(fy)
    assets = liabilities = equity = current_assets = D0
    ni = cash = D0
    cash_accts = {}
    liab_accts = {}                   # per-account liability balances, kept in detail for day-over-day history
    credit_line = inventory = D0
    for acct, (t, _d, b) in bal.items():
        if t == "1A":
            assets += b
            if acct < CURRENT_ASSET_MAX:
                current_assets += b
            if acct in (ACCT_CHECKING, ACCT_MMA, ACCT_PAYROLL, ACCT_PETTY):
                cash_accts[acct] = b
                cash += b
            if acct in INVENTORY_ACCTS:
                inventory += b
        elif t == "2L":
            if acct == ACCT_YTD_NI:
                ni = b
            elif acct.startswith("3"):
                equity += b
            else:
                liabilities += b
                if b:
                    liab_accts[acct] = float(b)
                if acct in CREDIT_LINE_ACCTS:
                    credit_line += b

    # ---- P&L ----
    rev_mtd, cogs_mtd, ovh_mtd = pnl_for_period(cur_period)
    rev_pm, cogs_pm, ovh_pm = pnl_for_period(prior_period)
    prev_day = previous_business_day(today)
    rev_pd_cur, cost_pd_cur = pnl_created_on(prev_day, cur_period)
    rev_pd_prior, cost_pd_prior = pnl_created_on(prev_day, prior_period)
    month_start = today.replace(day=1)
    pnl_days = pnl_days_for(cur_period, month_start)

    # ---- AR (released docs; sign already encodes IN + / CM,PA −; credits sit in bucket 'current') ----
    ar = fetch_dict("""SELECT book, bucket, SUM(sign * doc_bal) amt, COUNT(*) n FROM finance_aropendocument
                       WHERE released GROUP BY book, bucket""")
    matrix = {}
    ar_total = D0
    buckets = {"current": D0, "d30": D0, "d60": D0, "d90": D0, "over90": D0}
    for r in ar:
        amt = r["amt"] or D0
        matrix.setdefault(r["book"], {})[r["bucket"]] = float(amt)
        ar_total += amt
        buckets[r["bucket"]] += amt
    ar_credits = sum((Decimal(str(v)) for b, cells in matrix.items() if b == "credit" for v in cells.values()), D0)
    ar_pending = fetch_dict("SELECT COALESCE(SUM(orig_amt),0) s FROM finance_aropendocument WHERE NOT released")[0]["s"]

    # ---- AP ----
    ap = fetch_dict("SELECT bucket, SUM(sign * doc_bal) amt FROM finance_apopendocument GROUP BY bucket")
    ap_buckets = {"current": D0, "d30": D0, "d60": D0, "d90": D0, "over90": D0}
    ap_total = D0
    for r in ap:
        ap_buckets[r["bucket"]] += r["amt"] or D0
        ap_total += r["amt"] or D0

    # ---- WIP / billing position (open jobs, company workbook formula) ----
    wip_job_rows = finance_wip.wip_jobs()
    wip = finance_wip.wip_totals(wip_job_rows)
    wip_jobs_detail = {j["canonical_project_number"]: [float(round(j["wip"], 2)), float(round(j["earned"], 2)), float(round(j["billed"] or 0, 2))]
                       for j in wip_job_rows if abs(j["wip"]) >= Decimal("0.5") or (j["billed"] or 0) > 0}

    snap, _created = DailyFinanceSnapshot.objects.update_or_create(
        snapshot_date=today,
        defaults=dict(
            as_of=now, reconstructed=False, ingestion_run=run,
            ar_total=ar_total, ar_current=buckets["current"], ar_d30=buckets["d30"], ar_d60=buckets["d60"],
            ar_d90=buckets["d90"], ar_over90=buckets["over90"], ar_credits=ar_credits, ar_pending=ar_pending,
            ap_total=ap_total, ap_current=ap_buckets["current"], ap_d30=ap_buckets["d30"], ap_d60=ap_buckets["d60"],
            ap_d90=ap_buckets["d90"], ap_over90=ap_buckets["over90"],
            assets=assets, liabilities=liabilities, equity=equity, net_income_ytd=ni,
            working_capital=current_assets - liabilities,
            cash_checking=cash_accts.get(ACCT_CHECKING), cash_payroll=cash_accts.get(ACCT_PAYROLL),
            cash_mma=cash_accts.get(ACCT_MMA), cash_petty=cash_accts.get(ACCT_PETTY),
            credit_line_gl=credit_line, inventory_gl=inventory,
            revenue_mtd=rev_mtd, cogs_mtd=cogs_mtd, overhead_mtd=ovh_mtd,
            revenue_prior_month=rev_pm, cogs_prior_month=cogs_pm, overhead_prior_month=ovh_pm,
            wip_overbilled=wip["over"], wip_underbilled=wip["under"], wip_net=wip["net"],
            wip_overbilled_cost=wip["over_cost"], wip_underbilled_cost=wip["under_cost"],
            prev_business_day=prev_day,
            revenue_prev_day_cur=rev_pd_cur, cost_prev_day_cur=cost_pd_cur,
            revenue_prev_day_prior=rev_pd_prior, cost_prev_day_prior=cost_pd_prior,
            detail={"matrix": matrix, "pnl_days": pnl_days,
                    "identity_gap": float(assets - liabilities - equity - ni),
                    "wip": {"jobs": wip["jobs"], "over_jobs": wip["over_jobs"], "under_jobs": wip["under_jobs"],
                            "no_pct_jobs": wip["no_pct_jobs"], "net_cost": float(wip["net_cost"])},
                    "wip_jobs": wip_jobs_detail, "liab_accts": liab_accts,
                    "extras": page_extras(today)},   # the page's document-list sections, so the day can be replayed
        ))
    return {"snapshot_date": str(today), "ar_total": str(ar_total), "ap_total": str(ap_total),
            "assets": str(assets), "identity_gap": str(assets - liabilities - equity - ni),
            "wip_net": str(wip["net"]), "wip_jobs": wip["jobs"]}
