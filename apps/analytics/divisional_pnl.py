"""Divisional P&L — the fiscal-period income statement by GL subaccount prefix for any month,
quarter or fiscal year, with drill-downs to accounts, postings, projects and WIP.

Basis (docs/07_pnl_and_wip.md, docs/10_divisional_pnl.md):
  * Revenue = 40000/40001; other income = remaining 3I; costs = 5xxxx (except 50701) + 60000/60005;
    overhead = remaining 6xxxx/7xxxx.  Division = LEFT(sub, 3).  Fiscal year = calendar year.
  * WIP.  The accountants book the company WIP in the GL every month-end ("RECORD / REVERSE JOBS
    IN PROCESS" to 50701 OVER/(UNDERBILLING)).  For a closed month the ledger therefore already
    carries the earned view: WIP adj = -(50701 activity).  For a month that has not been closed
    yet (no 50701 activity) PCA supplies its own estimate from the WIP snapshots
    (CV x PTT % - billed, per job, docs/07 §3).  The two are never added together for the same
    month — that double counts.
  * Payroll lag.  Weekly hourly payroll posts promptly but the two semi-monthly salary batches
    are entered days-to-weeks after the pay date, so an open month's posted cost is light.  For
    open months an "unposted payroll" estimate (trailing closed-month average of the wage/FICA
    accounts minus what has posted) is netted out of every division.
  * Corporate (000) allocation.  Pool = sub-0000 overhead (+ its payroll-lag estimate when the
    month is open) x each division's DivisionOverheadShare (accountants' splits, editable at
    /finance/allocations/); the unallocated remainder and 000's own direct cost/income stay in an
    "Unallocated corporate" row so the company total still ties to the ledger.

Everything reads local tables only.
"""
from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from apps.analytics.finance_snapshot import COGS_SALARY_ACCTS, JOB_SALES_ACCTS
from apps.ingestion.bulk import fetch_dict

D0 = Decimal("0")
WIP_ACCT = "50701"
PAYROLL_LAG_ACCTS = ("60000", "60100", "64910", "64920", "50719", "50720", "50721")
LAG_BASELINE_MONTHS = 6          # closed months averaged for the unposted-payroll estimate
OPEN_LOOKBACK_MONTHS = 4         # a month older than this with no 50701 activity is treated as closed anyway
CORP = "000"
DIV_NAMES = {"000": "Corporate / Shared", "010": "Hardware Sales", "020": "IT Services", "025": "IT Managed Services (MSP)",
             "030": "Staffing", "040": "Audio Visual", "050": "Software / Programming", "060": "Electrical",
             "070": "Premise Security", "080": "Airport & Roadway"}

# ------------------------------------------------------------------------------- account groups
COST_GROUPS = ["Materials & goods", "Subcontractors", "Direct labor", "Labor burden & union", "Other direct costs"]
OVERHEAD_GROUPS = ["G&A salaries & benefits", "Facilities, insurance & depreciation", "Professional & office",
                   "Travel, auto & entertainment", "Taxes, interest & other"]
_COST_GROUP_BY_ACCT = {
    "50700": COST_GROUPS[0], "50705": COST_GROUPS[0], "50730": COST_GROUPS[0], "50750": COST_GROUPS[0],
    "50760": COST_GROUPS[0], "50500": COST_GROUPS[0],
    "50716": COST_GROUPS[1], "50717": COST_GROUPS[1],
    "50714": COST_GROUPS[2], "50726": COST_GROUPS[2], "60000": COST_GROUPS[2], "60005": COST_GROUPS[2],
    "50706": COST_GROUPS[3], "50710": COST_GROUPS[3], "50711": COST_GROUPS[3], "50712": COST_GROUPS[3],
    "50718": COST_GROUPS[3], "50719": COST_GROUPS[3], "50720": COST_GROUPS[3], "50721": COST_GROUPS[3],
}
_OVH_GROUP_BY_ACCT = {
    "60100": OVERHEAD_GROUPS[0], "62300": OVERHEAD_GROUPS[0], "63500": OVERHEAD_GROUPS[0], "64450": OVERHEAD_GROUPS[0],
    "64910": OVERHEAD_GROUPS[0], "64912": OVERHEAD_GROUPS[0], "64914": OVERHEAD_GROUPS[0], "64920": OVERHEAD_GROUPS[0],
    "64930": OVERHEAD_GROUPS[0], "65411": OVERHEAD_GROUPS[0],
    "62000": OVERHEAD_GROUPS[1], "62200": OVERHEAD_GROUPS[1], "62400": OVERHEAD_GROUPS[1], "62401": OVERHEAD_GROUPS[1],
    "62410": OVERHEAD_GROUPS[1], "62850": OVERHEAD_GROUPS[1], "63600": OVERHEAD_GROUPS[1], "63650": OVERHEAD_GROUPS[1],
    "63700": OVERHEAD_GROUPS[1], "64800": OVERHEAD_GROUPS[1],
    "60010": OVERHEAD_GROUPS[2], "61200": OVERHEAD_GROUPS[2], "61300": OVERHEAD_GROUPS[2], "61800": OVERHEAD_GROUPS[2],
    "61900": OVERHEAD_GROUPS[2], "61951": OVERHEAD_GROUPS[2], "61975": OVERHEAD_GROUPS[2], "62690": OVERHEAD_GROUPS[2],
    "62700": OVERHEAD_GROUPS[2], "62710": OVERHEAD_GROUPS[2], "63000": OVERHEAD_GROUPS[2], "63001": OVERHEAD_GROUPS[2],
    "63002": OVERHEAD_GROUPS[2], "63200": OVERHEAD_GROUPS[2], "63410": OVERHEAD_GROUPS[2], "64400": OVERHEAD_GROUPS[2],
    "65200": OVERHEAD_GROUPS[2],
    "61000": OVERHEAD_GROUPS[3], "62800": OVERHEAD_GROUPS[3], "62801": OVERHEAD_GROUPS[3], "65400": OVERHEAD_GROUPS[3],
    "65410": OVERHEAD_GROUPS[3], "65420": OVERHEAD_GROUPS[3], "65430": OVERHEAD_GROUPS[3], "65440": OVERHEAD_GROUPS[3],
}
PJTRAN_GROUP = {"labor_wage": COST_GROUPS[2], "labor_burden": COST_GROUPS[3], "material": COST_GROUPS[0],
                "subcontract": COST_GROUPS[1], "other_direct": COST_GROUPS[4], "revenue": "Revenue"}


def pnl_line(acct, acct_type):
    """Which income-statement line a GL account belongs to (None for balance-sheet accounts)."""
    if acct_type == "3I":
        return "revenue" if acct in JOB_SALES_ACCTS else "other_income"
    if acct_type == "4E":
        if acct == WIP_ACCT:
            return "wip"
        if acct.startswith("5") or acct in COGS_SALARY_ACCTS:
            return "costs"
        if acct[:1] in ("6", "7"):
            return "overhead"
    return None


def cost_group(acct):
    return _COST_GROUP_BY_ACCT.get(acct, COST_GROUPS[4])


def overhead_group(acct):
    return _OVH_GROUP_BY_ACCT.get(acct, OVERHEAD_GROUPS[4])


def pjtran_group(category, gl_account):
    """Group for a project transaction: by its GL account when SL recorded one, else by category
    (payroll allocations reach PJTran without a GL account — they are the 50714/burden postings)."""
    if gl_account:
        line = pnl_line(gl_account, "3I" if gl_account.startswith("4") else "4E")
        if line == "revenue":
            return "Revenue"
        if line == "costs":
            return cost_group(gl_account)
        if line == "other_income":
            return "Other income"
    return PJTRAN_GROUP.get(category, COST_GROUPS[4])


# ------------------------------------------------------------------------------- periods
class Period:
    __slots__ = ("kind", "year", "idx", "key", "label", "months", "start", "end")

    def __init__(self, kind, year, idx=0):
        if kind == "month":
            if not 1 <= idx <= 12:
                raise ValueError("month 1-12")
            self.months = ["%04d%02d" % (year, idx)]
            self.key, self.label = "%04d-%02d" % (year, idx), "%s %d" % (calendar.month_abbr[idx], year)
        elif kind == "quarter":
            if not 1 <= idx <= 4:
                raise ValueError("quarter 1-4")
            self.months = ["%04d%02d" % (year, m) for m in range(3 * idx - 2, 3 * idx + 1)]
            self.key, self.label = "%04d-Q%d" % (year, idx), "Q%d %d" % (idx, year)
        elif kind == "year":
            idx = 0
            self.months = ["%04d%02d" % (year, m) for m in range(1, 13)]
            self.key = self.label = "%04d" % year
        else:
            raise ValueError("kind must be month, quarter or year")
        self.kind, self.year, self.idx = kind, year, idx
        m0, m1 = int(self.months[0][4:]), int(self.months[-1][4:])
        self.start = date(year, m0, 1)
        self.end = date(year, m1, calendar.monthrange(year, m1)[1])

    @property
    def fiscal_year(self):
        return "%04d" % self.year

    def prev(self):
        if self.kind == "year":
            return Period("year", self.year - 1)
        n = 12 if self.kind == "month" else 4
        return Period(self.kind, self.year - 1, n) if self.idx == 1 else Period(self.kind, self.year, self.idx - 1)

    def next(self):
        if self.kind == "year":
            return Period("year", self.year + 1)
        n = 12 if self.kind == "month" else 4
        return Period(self.kind, self.year + 1, 1) if self.idx == n else Period(self.kind, self.year, self.idx + 1)

    def contains_month(self, ym):
        return ym in self.months

    def __repr__(self):
        return "Period(%s)" % self.key


def parse_period(key):
    """'2026-08' -> month, '2026-Q3' -> quarter, '2026' -> year."""
    key = (key or "").strip().upper()
    if len(key) == 4 and key.isdigit():
        return Period("year", int(key))
    if len(key) == 7 and key[4] == "-" and key[5] == "Q":
        return Period("quarter", int(key[:4]), int(key[6]))
    if len(key) == 7 and key[4] == "-" and key[5:].isdigit():
        return Period("month", int(key[:4]), int(key[5:]))
    raise ValueError("bad period %r" % key)


def periods_in_year(kind, year):
    if kind == "month":
        return [Period("month", year, m) for m in range(1, 13)]
    if kind == "quarter":
        return [Period("quarter", year, q) for q in range(1, 5)]
    return [Period("year", year)]


def period_for_month(kind, ym):
    y, m = int(ym[:4]), int(ym[4:])
    if kind == "month":
        return Period("month", y, m)
    if kind == "quarter":
        return Period("quarter", y, (m - 1) // 3 + 1)
    return Period("year", y)


def month_end(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return date(y, m, calendar.monthrange(y, m)[1])


def prior_month(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return "%04d%02d" % ((y - 1, 12) if m == 1 else (y, m - 1))


def months_back(ym, n):
    out, cur = [], ym
    for _ in range(n):
        cur = prior_month(cur)
        out.append(cur)
    return out


# ------------------------------------------------------------------------------- pure math
def lag_estimate(baseline_values, posted):
    """Unposted-payroll estimate for an open month: mean of the closed-month baselines minus what
    has posted, floored at zero (a month can't have negative missing payroll)."""
    vals = [Decimal(str(v)) for v in baseline_values if v is not None]
    if not vals:
        return D0
    avg = sum(vals, D0) / Decimal(len(vals))
    return max(avg - Decimal(str(posted or 0)), D0)


def open_month_set(months_with_wip_activity, today, lookback=OPEN_LOOKBACK_MONTHS):
    """Months (YYYYMM) counted as 'not closed': the current month and the previous `lookback`
    months that show no 50701 (WIP) activity. Older months without activity are historical and
    treated as closed."""
    cur = "%04d%02d" % (today.year, today.month)
    cands = [cur] + months_back(cur, lookback)
    return {m for m in cands if m not in months_with_wip_activity}


def allocate(pool, shares):
    """{div: pool x share}; the remainder (1 - sum(shares)) stays corporate."""
    pool = Decimal(str(pool or 0))
    alloc = {d: (pool * Decimal(str(s))) for d, s in shares.items() if s}
    used = sum((Decimal(str(s)) for s in shares.values()), D0)
    return alloc, pool * (Decimal(1) - used)


def row_math(r):
    """Derive the computed lines of a P&L row in place. Inputs: revenue, other_income, costs,
    wip_booked, wip_pca, overhead, payroll_lag, corp_alloc (Decimals, may be None)."""
    g = lambda k: r.get(k) or D0  # noqa: E731
    r["wip"] = g("wip_booked") + g("wip_pca")
    r["gross"] = g("revenue") - g("costs") + r["wip"]
    earned = g("revenue") + r["wip"]
    r["gross_pct"] = (r["gross"] / earned) if earned > 0 else None
    r["op_income"] = r["gross"] + g("other_income") - g("overhead") - g("payroll_lag") - g("corp_alloc")
    r["op_pct"] = (r["op_income"] / earned) if earned > 0 else None
    r["earned"] = earned
    return r


# ------------------------------------------------------------------------------- GL access
class GL:
    """Account x division x month cells for a set of fiscal years (AcctHist copy, natural sign,
    13th period folded into December — it has never been used)."""

    def __init__(self, fiscal_years):
        self.fiscal_years = sorted({str(y) for y in fiscal_years})
        self.cells = defaultdict(dict)          # (div, ym) -> {acct: amount}
        self.meta = {}                          # acct -> (acct_type, descr)
        self.divisions = set()
        self.wip_months = set()                 # months with any 50701 activity
        if not self.fiscal_years:
            return
        rows = fetch_dict("""
            SELECT LEFT(sub, 3) div, fiscal_year fy, acct, acct_type, MAX(descr) descr,
                   SUM(p00) m1, SUM(p01) m2, SUM(p02) m3, SUM(p03) m4, SUM(p04) m5, SUM(p05) m6, SUM(p06) m7,
                   SUM(p07) m8, SUM(p08) m9, SUM(p09) m10, SUM(p10) m11, SUM(p11)+SUM(p12) m12
            FROM finance_glaccountbalance
            WHERE fiscal_year = ANY(%s) AND acct_type IN ('3I','4E') AND sub <> ''
            GROUP BY 1, 2, 3, 4""", [self.fiscal_years])
        for r in rows:
            div = r["div"]
            self.meta[r["acct"]] = (r["acct_type"], r["descr"] or "")
            for m in range(1, 13):
                v = r["m%d" % m]
                if not v:
                    continue
                ym = "%s%02d" % (r["fy"], m)
                self.cells[(div, ym)][r["acct"]] = self.cells[(div, ym)].get(r["acct"], D0) + v
                self.divisions.add(div)
                if r["acct"] == WIP_ACCT:
                    self.wip_months.add(ym)

    # -- aggregates --------------------------------------------------------------------------
    def accounts(self, div, months):
        """{acct: amount} summed over months; div None = whole company."""
        out = defaultdict(lambda: D0)
        keys = [(d, m) for d in (self.divisions if div is None else [div]) for m in months]
        for k in keys:
            for acct, v in self.cells.get(k, {}).items():
                out[acct] += v
        return dict(out)

    def lines(self, div, months):
        out = {"revenue": D0, "other_income": D0, "costs": D0, "wip_booked": D0, "overhead": D0}
        for acct, v in self.accounts(div, months).items():
            line = pnl_line(acct, self.meta[acct][0])
            if line == "wip":
                out["wip_booked"] -= v          # credit to 50701 = WIP recorded = earned, not billed
            elif line:
                out[line] += v
        return out

    def payroll_posted(self, div, ym):
        return sum((self.cells.get((div, ym), {}).get(a, D0) for a in PAYROLL_LAG_ACCTS), D0)

    def overhead_pool(self, ym):
        """Sub-0000 overhead for one month (the allocation pool, docs/07 §4)."""
        return self.lines(CORP, [ym])["overhead"]


# ------------------------------------------------------------------------------- WIP snapshots
class WIPSnapshots:
    """Division-level WIP from the daily/month-end snapshot rows (per-job detail joined to the
    project's division)."""

    def __init__(self):
        from apps.finance.models import DailyFinanceSnapshot
        self.divmap = {r["k"]: r["div"] for r in fetch_dict(
            "SELECT p.canonical_project_number k, d.code div FROM core_project p JOIN core_division d ON d.id = p.division_id")}
        self._snap = DailyFinanceSnapshot
        latest = DailyFinanceSnapshot.objects.filter(wip_net__isnull=False).order_by("-snapshot_date").first()
        self.latest_date = latest.snapshot_date if latest else None
        self._cache = {}
        self._jobs_cache = {}
        self._billed = {}

    def snapshot_on(self, day):
        if day not in self._cache:
            self._cache[day] = self._snap.objects.filter(snapshot_date=day, wip_net__isnull=False).first()
        return self._cache[day]

    def _jobs(self, day, ym):
        """Per-job [wip, earned, billed] of the snapshot on `day` (or the latest one if `day` is beyond
        it), with billed re-anchored on the ledger as it stands through fiscal period `ym` — so an
        invoice entered later but dated into the month sits in that month's WIP, not the next one's
        (finance_wip.rebase_jobs, docs/07 §3). (jobs or None, snapshot date or None)."""
        from apps.analytics.finance_wip import billed_through, rebase_jobs, restate_stored
        if self.latest_date and day >= self.latest_date:
            day = self.latest_date
        key = (day, ym)
        if key not in self._jobs_cache:
            snap = self.snapshot_on(day)
            if not snap:
                self._jobs_cache[key] = (None, None)
            else:
                if ym not in self._billed:
                    self._billed[ym] = billed_through(ym=ym)
                jobs = restate_stored(rebase_jobs((snap.detail or {}).get("wip_jobs") or {}, day, billed=self._billed[ym]), day)
                self._jobs_cache[key] = (jobs, snap.snapshot_date)
        return self._jobs_cache[key]

    def at(self, day, ym=None):
        """{div: wip, '__all__': net} at a month-end (or the latest snapshot if `day` is beyond it),
        billed through fiscal period `ym` (default: the month of `day`). None when no snapshot exists."""
        jobs, sdate = self._jobs(day, ym or "%04d%02d" % (day.year, day.month))
        if jobs is None:
            return None
        agg = defaultdict(lambda: D0)
        for cpn, v in jobs.items():
            agg[self.divmap.get(cpn, "?")] += Decimal(str(v[0]))
        agg["__all__"] = sum(agg.values(), D0)
        agg["__date__"] = sdate
        return agg

    def delta(self, div, ym):
        """PCA ΔWIP of a month for a division (None = whole company); None if a baseline is missing."""
        end, start = self.at(month_end(ym), ym), self.at(month_end(prior_month(ym)), prior_month(ym))
        if end is None or start is None:
            return None
        k = "__all__" if div is None else div
        return end.get(k, D0) - start.get(k, D0)

    def jobs_at(self, day, ym=None):
        return self._jobs(day, ym or "%04d%02d" % (day.year, day.month))


# ------------------------------------------------------------------------------- shares
def share_table():
    from apps.finance.models import DivisionOverheadShare
    rows = list(DivisionOverheadShare.objects.all())
    years = sorted({r.fiscal_year for r in rows})

    def for_year(fy):
        eligible = [y for y in years if y <= str(fy)] or years[:1]
        if not eligible:
            return {}, None
        use = eligible[-1]
        return {r.division_code: r.share for r in rows if r.fiscal_year == use}, use
    return for_year


# ------------------------------------------------------------------------------- the table
class Model:
    """Everything the page needs, loaded once per request."""

    def __init__(self, fiscal_years, today=None):
        self.today = today or date.today()
        # always carry the current and prior fiscal year: the payroll-lag baseline and the
        # open-month detection look back across a year boundary in January/February
        self.gl = GL(set(str(y) for y in fiscal_years) | {str(self.today.year), str(self.today.year - 1)})
        self.wip = WIPSnapshots()
        self.shares_for = share_table()
        self.open_months = open_month_set(self.gl.wip_months, self.today)
        self.cur_month = "%04d%02d" % (self.today.year, self.today.month)
        self._lag_cache = {}

    def division_codes(self):
        return sorted(d for d in self.gl.divisions if d != CORP and d)

    def name(self, div):
        return DIV_NAMES.get(div, "Division %s" % div)

    def is_open(self, ym):
        return ym in self.open_months and ym <= self.cur_month

    def lag(self, div, ym):
        """Unposted-payroll estimate for an open month (0 for closed months)."""
        if not self.is_open(ym):
            return D0
        key = (div, ym)
        if key not in self._lag_cache:
            base = [m for m in months_back(ym, 14) if not self.is_open(m)][:LAG_BASELINE_MONTHS]
            self._lag_cache[key] = lag_estimate([self.gl.payroll_posted(div, m) for m in base], self.gl.payroll_posted(div, ym))
        return self._lag_cache[key]

    def pool(self, ym):
        return self.gl.overhead_pool(ym) + self.lag(CORP, ym)

    def row(self, div, period):
        """One division (or None = company) over a period; the company row carries no allocation."""
        months = [m for m in period.months if m <= self.cur_month]
        r = self.gl.lines(div, months)
        r.update(div=div, name=self.name(div) if div else "Company", period=period.key, months_open=[m for m in months if self.is_open(m)])
        r["open"] = bool(r["months_open"])
        r["wip_pca"] = D0
        r["wip_pca_missing"] = []
        for m in r["months_open"]:
            dv = self.wip.delta(div, m)
            if dv is None:
                r["wip_pca_missing"].append(m)
            else:
                r["wip_pca"] += dv
        r["payroll_lag"] = sum((self.lag(div, m) for m in r["months_open"]), D0) if div else \
            sum((sum((self.lag(d, m) for d in self.gl.divisions), D0) for m in r["months_open"]), D0)
        if div and div != CORP:
            alloc = D0
            share = None
            for m in months:
                shares, _ = self.shares_for(m[:4])
                share = shares.get(div, D0)
                alloc += self.pool(m) * Decimal(str(share))
            r["corp_alloc"], r["share"] = alloc, share
        else:
            r["corp_alloc"], r["share"] = D0, None
        return row_math(r)

    def table(self, period):
        """Division rows + an 'Unallocated corporate' row + the company total for one period.
        The total is the pure ledger (plus the open-month estimates): every dollar of sub-0000
        cost is either allocated to a division row or sits in the unallocated row."""
        months = [m for m in period.months if m <= self.cur_month]
        all_rows = [self.row(d, period) for d in self.division_codes()]
        rows = [r for r in all_rows if sum(abs(r[k]) for k in ("revenue", "costs", "overhead", "wip", "corp_alloc")) >= 500]  # display only
        corp = self.row(CORP, period)          # 000's own ledger lines (no allocation applied)
        pool_posted = sum((self.gl.overhead_pool(m) for m in months), D0)
        corp_lag = corp["payroll_lag"]
        pool_total = pool_posted + corp_lag
        allocated = sum((r["corp_alloc"] for r in all_rows), D0)
        remainder = pool_total - allocated
        rem_lag = (remainder * corp_lag / pool_total) if pool_total else D0
        un = {"div": CORP, "name": "Unallocated corporate", "period": period.key, "open": corp["open"], "months_open": corp["months_open"],
              "revenue": corp["revenue"], "other_income": corp["other_income"], "costs": corp["costs"],
              "wip_booked": corp["wip_booked"], "wip_pca": D0, "wip_pca_missing": [],
              "overhead": remainder - rem_lag, "payroll_lag": rem_lag,
              "corp_alloc": D0, "share": None, "pool": pool_total, "pool_posted": pool_posted, "allocated": allocated}
        row_math(un)
        total = {"div": "ALL", "name": "Company", "period": period.key, "open": any(r["open"] for r in all_rows + [un]),
                 "months_open": corp["months_open"], "wip_pca_missing": sorted({m for r in all_rows for m in r["wip_pca_missing"]}),
                 "share": None, "corp_alloc": allocated}
        for k in ("revenue", "other_income", "costs", "wip_booked", "wip_pca", "overhead", "payroll_lag"):
            total[k] = sum((r[k] for r in all_rows), D0) + un[k]      # every sub, dormant ones included
        row_math(total)
        ledger = self.row(None, period)         # cross-check: company straight from the ledger
        return {"period": period, "rows": rows, "unallocated": un, "total": total, "ledger": ledger,
                "pool": pool_total, "pool_posted": pool_posted, "allocated": allocated,
                "share_year": self.shares_for(period.fiscal_year)[1], "open": total["open"], "months_open": total["months_open"]}

    def matrix(self, kind, year):
        """Per-period tables across a year (or across all years for kind='year')."""
        if kind == "year":
            periods = [Period("year", int(fy)) for fy in self.gl.fiscal_years]
        else:
            periods = [p for p in periods_in_year(kind, year) if p.months[0] <= self.cur_month]
        return [self.table(p) for p in periods]

    # -- drill --------------------------------------------------------------------------------
    def statement(self, div, period):
        """Account-level income statement for a division (None = company) and period, grouped the
        way the accountants' P&L workbook lays it out."""
        months = [m for m in period.months if m <= self.cur_month]
        accts = self.gl.accounts(div, months)
        sections = {"revenue": [], "other_income": [], "costs": defaultdict(list), "overhead": defaultdict(list), "wip": []}
        for acct, amt in sorted(accts.items()):
            t, descr = self.gl.meta[acct]
            line = pnl_line(acct, t)
            if not line or not amt:
                continue
            item = {"acct": acct, "descr": descr, "amount": amt if line != "wip" else -amt, "months": {m: self.gl.cells.get((div, m), {}).get(acct, D0) if div else sum((self.gl.cells.get((d, m), {}).get(acct, D0) for d in self.gl.divisions), D0) for m in months}}
            if line == "costs":
                sections["costs"][cost_group(acct)].append(item)
            elif line == "overhead":
                sections["overhead"][overhead_group(acct)].append(item)
            else:
                sections[line].append(item)
        costs = [{"group": g, "items": sorted(sections["costs"][g], key=lambda i: -abs(i["amount"])),
                  "amount": sum((i["amount"] for i in sections["costs"][g]), D0)} for g in COST_GROUPS if sections["costs"].get(g)]
        overhead = [{"group": g, "items": sorted(sections["overhead"][g], key=lambda i: -abs(i["amount"])),
                     "amount": sum((i["amount"] for i in sections["overhead"][g]), D0)} for g in OVERHEAD_GROUPS if sections["overhead"].get(g)]
        wip_by_month = [{"month": m, "booked": -self.gl.cells.get((div, m), {}).get(WIP_ACCT, D0) if div else -sum((self.gl.cells.get((d, m), {}).get(WIP_ACCT, D0) for d in self.gl.divisions), D0),
                         "open": self.is_open(m), "pca": self.wip.delta(div, m)} for m in months]
        return {"revenue": sections["revenue"], "other_income": sections["other_income"], "costs": costs, "overhead": overhead,
                "wip_by_month": wip_by_month, "months": months}

    def projects(self, div, period):
        """Project attribution of the period's revenue and direct cost (PJTran), plus each project's
        PCA WIP change over the period. Division match: the transaction's GL subaccount when SL
        recorded one, else the project's own division (payroll allocations carry no subaccount)."""
        months = [m for m in period.months if m <= self.cur_month]
        where = "COALESCE(NULLIF(LEFT(t.gl_subaccount, 3), ''), d.code) = %s" if div else "TRUE"
        params = [months] + ([div] if div else [])
        rows = fetch_dict("""
            SELECT p.canonical_project_number cpn, p.title, c.canonical_name customer, d.code pdiv,
                   e.canonical_name pm, p.lifecycle_state state, t.category, NULLIF(t.gl_account, '') gl_account,
                   SUM(t.amount) amt, SUM(t.units) units
            FROM finance_projectfinancialtransaction t
            JOIN core_project p ON p.id = t.project_id
            LEFT JOIN core_division d ON d.id = p.division_id
            LEFT JOIN core_customer c ON c.id = p.customer_id
            LEFT JOIN core_employee e ON e.id = p.project_manager_id
            WHERE t.fiscal_period = ANY(%%s) AND t.category <> 'excluded_memo' AND %s
            GROUP BY 1, 2, 3, 4, 5, 6, 7, 8""" % where, params)
        by = {}
        group_tot = defaultdict(lambda: D0)
        for r in rows:
            if not r["amt"] and not r["units"]:
                continue
            g = pjtran_group(r["category"], r["gl_account"])
            j = by.setdefault(r["cpn"], {"cpn": r["cpn"], "title": r["title"], "customer": r["customer"], "division": r["pdiv"],
                                         "pm": r["pm"], "state": r["state"], "revenue": D0, "costs": D0, "groups": defaultdict(lambda: D0), "hours": D0})
            if g == "Revenue":
                j["revenue"] += r["amt"] or D0
            elif g == "Other income":
                j["revenue"] += r["amt"] or D0
            else:
                j["costs"] += r["amt"] or D0
                j["groups"][g] += r["amt"] or D0
                if r["category"] == "labor_wage":
                    j["hours"] += r["units"] or D0
            group_tot[g] += r["amt"] or D0
        # PCA WIP change per job across the period (start = month-end before the period, end = period end / latest)
        start_jobs, start_date = self.wip.jobs_at(month_end(prior_month(months[0])), prior_month(months[0])) if months else (None, None)
        end_jobs, end_date = self.wip.jobs_at(month_end(months[-1]), months[-1]) if months else (None, None)
        # the daily snapshot stores [wip, earned, billed] per job; sparse reconstructed year-end rows
        # store WIP only, so the earned-vs-billed driver split is shown only when both ends carry it
        drivers = start_jobs is not None and end_jobs is not None and _has_drivers(start_jobs) and _has_drivers(end_jobs)
        if start_jobs is not None and end_jobs is not None:
            keys = set(start_jobs) | set(end_jobs)
            for cpn in keys:
                if div and self.wip.divmap.get(cpn) != div:
                    continue
                s, e = start_jobs.get(cpn) or [0], end_jobs.get(cpn) or [0]
                d0, d1 = _snap_val(s, 0), _snap_val(e, 0)
                if d0 == d1:
                    continue
                j = by.get(cpn)
                if not j:
                    meta = fetch_dict("""SELECT p.title, c.canonical_name customer, d.code pdiv, e.canonical_name pm, p.lifecycle_state state
                                         FROM core_project p LEFT JOIN core_division d ON d.id = p.division_id
                                         LEFT JOIN core_customer c ON c.id = p.customer_id LEFT JOIN core_employee e ON e.id = p.project_manager_id
                                         WHERE p.canonical_project_number = %s""", [cpn])
                    m = meta[0] if meta else {}
                    j = by.setdefault(cpn, {"cpn": cpn, "title": m.get("title", ""), "customer": m.get("customer"), "division": m.get("pdiv"),
                                            "pm": m.get("pm"), "state": m.get("state"), "revenue": D0, "costs": D0, "groups": defaultdict(lambda: D0), "hours": D0})
                j["wip_start"], j["wip_end"], j["dwip"] = d0, d1, d1 - d0
                if drivers:  # ΔWIP = Δearned − Δbilled by construction
                    j["earned_start"], j["earned_end"] = _snap_val(s, 1), _snap_val(e, 1)
                    j["billed_start"], j["billed_end"] = _snap_val(s, 2), _snap_val(e, 2)
                    j["dearned"], j["dbilled"] = j["earned_end"] - j["earned_start"], j["billed_end"] - j["billed_start"]
        for j in by.values():
            j.setdefault("dwip", None)
            j["gross"] = j["revenue"] - j["costs"]
            j["contribution"] = j["gross"] + (j["dwip"] or D0)
            j["groups"] = dict(j["groups"])
        out = sorted(by.values(), key=lambda j: -abs(j["contribution"]))
        return {"projects": out, "group_totals": dict(group_tot), "wip_start_date": start_date, "wip_end_date": end_date,
                "wip_drivers": drivers}


def _snap_val(entry, i):
    """Element i of a stored per-job snapshot entry ([wip, earned, billed]) as a Decimal; 0 when absent."""
    if entry is None or len(entry) <= i or entry[i] is None:
        return D0
    return Decimal(str(entry[i]))


def _has_drivers(jobs):
    """True when a snapshot's per-job detail carries earned/billed (the daily snapshot does; the sparse
    reconstructed year-end rows store WIP only, as [wip, 0, 0])."""
    return any(len(v) > 2 and (v[1] or v[2]) for v in jobs.values())


# ------------------------------------------------------------------------------- line-level drills
def postings(div, months, acct):
    """GL posting lines behind an account for a division/period — only available inside the
    trailing GLRecentPosting window; returns (rows, available)."""
    avail = fetch_dict("SELECT MIN(per_post) lo FROM finance_glrecentposting")[0]["lo"]
    if not avail or min(months) < avail:
        return [], False
    where = "LEFT(sub, 3) = %s" if div else "TRUE"
    params = [acct, months] + ([div] if div else [])
    rows = fetch_dict("""
        SELECT tran_date, per_post, sub, module, jrnl_type, batch_nbr, ref_nbr, tran_desc,
               CASE WHEN acct_type = '3I' THEN cr_amt - dr_amt ELSE dr_amt - cr_amt END amt
        FROM finance_glrecentposting WHERE acct = %%s AND per_post = ANY(%%s) AND %s
        ORDER BY tran_date, batch_nbr""" % where, params)
    return rows, True


def project_transactions(cpn, months, group=None, div=None):
    """PJTran lines for one project in the period (optionally one cost group)."""
    rows = fetch_dict("""
        SELECT t.transaction_date, t.fiscal_period, t.system_cd, t.category, NULLIF(t.gl_account,'') gl_account, t.gl_subaccount,
               t.vendor_num, v.name vendor, e.canonical_name employee, t.comment, t.voucher_num, t.amount, t.units
        FROM finance_projectfinancialtransaction t
        JOIN core_project p ON p.id = t.project_id
        LEFT JOIN finance_slvendor v ON v.vendor_id = t.vendor_num
        LEFT JOIN core_employee e ON e.id = t.employee_id
        WHERE p.canonical_project_number = %s AND t.fiscal_period = ANY(%s) AND t.category <> 'excluded_memo'
        ORDER BY t.transaction_date, t.id""", [cpn, months])
    out = []
    for r in rows:
        g = pjtran_group(r["category"], r["gl_account"])
        if group and g != group:
            continue
        if div and (r["gl_subaccount"] or "")[:3] not in ("", div):
            pass  # keep: attribution follows the project's division when the sub differs
        r["group"] = g
        out.append(r)
    return out
