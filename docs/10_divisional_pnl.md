# Divisional P&L (`/finance/pnl/`)

The fiscal-period income statement by division for any **month, quarter or fiscal year**, with a
drill-down from every number to the accounts, GL postings, projects and WIP moves behind it.
Built 2026-08-31 after the "050 profitability is WAY off" finding (see §4). Read
`docs/07_pnl_and_wip.md` first — this page is the official basis (§1 there), never closed-job cohorts.

## 1. Pages

| URL | What |
|---|---|
| `/finance/pnl/?grain=month\|quarter\|year&year=YYYY&p=<period>&div=ALL\|<code>&metric=<line>` | The table. `p` selects the period (`2026-08`, `2026-Q3`, `2026`). With `div=ALL`: the by-division table for the selected period + a divisions × periods trend matrix for one metric. With a division: that division's full P&L lines × periods + the by-division table with its row highlighted. Month and quarter grains end with a **YTD** column ("2025 total" for a past year): the fiscal year's own `Model.table(Period("year", y))`, so every division's row sums exactly and the percentage lines are recomputed from the year totals rather than summed. |
| `/finance/pnl/<div>/<period>/` | The statement behind one cell (`div` = code, `000` = unallocated corporate, `ALL` = company). Anchors: `#revenue #costs #wip #gross #overhead #corp #op #projects #wipjobs #pool #lag`. |
| `/finance/pnl/lines/?kind=postings&div&p&acct` | JSON: GL posting lines for an account (trailing `GLRecentPosting` window only). |
| `/finance/pnl/lines/?kind=pjtran&div&p&cpn[&group]` | JSON: a project's ledger lines (PJTran) in the period, optionally one cost group. |

All three are registered in `access.registry.URL_ACCESS` under `finance.view`. Nav: Financial Reports → Divisional P&L.

## 2. Columns and formulas (`apps/analytics/divisional_pnl.py`)

Division = `LEFT(sub, 3)` of the GL subaccount (070 = 0700/0701, 025 = 0250 MSP, 000 = corporate). Fiscal year = calendar year; AcctHist period `p00` = January … `p11` = December (`p12`, the 13th period, has never been used and is folded into December).

| Column | Definition |
|---|---|
| Revenue | 40000 + 40001 on the division's subaccounts (`pnl_line`). Other income (40100 etc.) is a separate line inside the drill and inside operating income. |
| Costs | all 5xxxx **except 50701**, plus 60000/60005 (division salaries, union benefits — the accountants' cost of sales). Note the accountants' "COGS" *includes* 50701; here it is split out so WIP is visible. |
| WIP adj | **Closed month:** −(50701 activity) — the accountants' month-end "RECORD / REVERSE JOBS IN PROCESS" entry, exactly as booked. **Open month:** PCA's own ΔWIP from the WIP snapshots (Σ per job CV × PTT % − billed, month-end vs month-end or latest). Never both for the same month. |
| Gross | revenue − costs + WIP adj. GP % uses earned revenue = revenue + WIP adj. |
| Overhead | the division's own 6xxxx/7xxxx other than 60000/60005. |
| Unposted payroll (est.) | open months only: trailing 6 closed-month average of the wage/FICA accounts (60000, 60100, 64910, 64920, 50719–50721) minus what has posted, floored at zero per month. |
| Corp. alloc | pool × share. Pool (per month) = sub-0000 overhead (+ 0000's own unposted-payroll estimate when open); share = `DivisionOverheadShare` for the fiscal year (inherits the latest earlier year), editable at `/finance/allocations/`. |
| Op. income | gross + other income − overhead − unposted payroll − corp. alloc. |
| Unallocated corporate row | 0000's own revenue / direct cost / other income plus the pool remainder `pool × (1 − Σ shares)`. |
| Company row | Σ divisions + unallocated. **It equals the pure ledger** (`Model.row(None, …)`, shown in the footnote): the allocation only moves cost between rows. |

A period is **open** when the month has no 50701 activity and is within the last 4 months
(`open_month_set`); older months without an entry are history. Open rows are marked ≈ and carry the
two estimates; both vanish once the accountants close the month.

## 3. The drill-down (`finance_pnl_detail`)

1. **Income statement** in the accountants' workbook layout — Revenue accounts → Other income →
   Costs by group (Materials & goods · Subcontractors · Direct labor · Labor burden & union · Other
   direct) → WIP adjustment by month (booked vs PCA, with PCA's number alongside for closed months)
   → Gross → Overhead by group (G&A salaries & benefits · Facilities, insurance & depreciation ·
   Professional & office · Travel, auto & entertainment · Taxes, interest & other) → Unposted payroll
   → Corporate allocation → Operating income.
   * Every account row: **postings** (modal) when the period is inside the trailing GL posting window (~3 months); older periods show balances only.
   * Revenue and every cost group: **N projects** toggles the project attribution — PJTran lines carrying the same GL accounts (payroll allocations arrive without a GL account and map by category to Direct labor / Labor burden), the "On projects" subtotal and the **"Not on a project (GL only)"** residual (journal entries, inventory relief, re-classes). Division match = the transaction's GL subaccount when SL recorded one, else the project's own division.
2. **Projects in this period** — every project with ledger activity or a WIP change: revenue, costs (hover = split by group + labor hours), GP, ΔWIP (PCA snapshots bracketing the period), **Adj. GP = GP + ΔWIP** (docs/07 §3: ΔWIP alone is not profit); filter box; **lines** opens the project's PJTran rows; project number links to the project page.
3. **WIP change by job** — every job whose PCA WIP moved over the period: WIP at the start/end snapshot and Δ; the **driver** split (Δearned vs Δbilled, ΔWIP = Δearned − Δbilled — shown only when both bracketing snapshots store earned/billed, i.e. not across the sparse reconstructed year-ends); the job's ledger **revenue, costs (hover = split by group + labor hours) and gross** for the period from the same PJTran attribution as §3.2; **Adj. GP** = GP + ΔWIP (the job's real, earned-basis result for the period — the workbook's column AJ; ΔWIP alone is not profit, docs/07 §3); a totals row for the listed jobs. For closed periods the booked total and PCA's total are shown side by side (they differ: the workbook zeroes service rows and hand-adjusts jobs mid-billing). The card header links to **WIP by Job** for the same period and division (`/finance/wip/?period=YYYY-MM|YYYY&division=<code>&side=moved`; a quarter opens its last month because that page runs by month or year) and each row links to its own job there (`&q=<project>`).
4. **Corporate pool** (division view) — 0000 overhead by account × the division's share.
5. **Unposted payroll estimate** (open periods) — per month × account: baseline average, posted, gap.

## 4. Why it exists — what was wrong before

* **Payroll lag.** Only the weekly hourly payroll batches post promptly; the two semi-monthly
  salary batches (15th, month-end; ≈$690k/month company-wide) are entered days-to-weeks later.
  050 (all salaried, 14 people, ≈$124k/month wages + ≈$43k G&A/burden) showed **$0 wages** for
  August against $226k revenue → a fake +$196k gross. Real August ≈ +$15k.
* **WIP is booked monthly.** docs/07 used to say the GL keeps no WIP during the year. It does:
  since 2019 the accountants post "RECORD JOBS IN PROCESS / REVERSE JOBS IN PROCESS" to 50701 at
  every month-end (the 7/31/2026 entries sum to $5,672,268 = the Project Update workbook total).
  A closed month's COGS therefore already carries the earned view; adding PCA's ΔWIP on top double
  counted (070 FY2026 YTD by ≈$1.0M). The yearly P&L table and the Daily Snapshot's adjusted
  lines were corrected the same day (docs/07 §3).
* **Corporate cost by row.** The 000 row was shown on its own; Owner wants it attributed, so the
  page allocates it and keeps the remainder visible.

## 5. Data and limits

* GL: `finance_glaccountbalance` (AcctHist, full depth since 2014) → any month since 2014.
  Posting lines: `finance_glrecentposting` (trailing ~95 days).
* Projects: `finance_projectfinancialtransaction` (PJTran, all history) by `fiscal_period`.
  Attribution never ties to the penny — see the residual line; July 2026 070 revenue is 92 % on
  projects, labor cost lands via payroll allocations.
* WIP snapshots: month-ends monthly from 2025-07-31, Dec 31 every year since 2018, daily live
  from 2026-08-26. Each month-end's per-job billed is re-anchored on the ledger through that fiscal
  period before use (`finance_wip.rebase_jobs`, docs/07 §3), so a back-dated invoice sits in its own
  month's WIP. A month without both bracketing snapshots shows "?" for the PCA estimate.
  `backfill_wip_history` can add more month-ends (CVs are current-only; drift grows with lookback).
* Division names for 025/030/050 are hard-coded in `DIV_NAMES` (SL's SubAcct table isn't copied locally).

## 6. Verification

* `Model.table(period)["total"]["op_income"] == Model.row(None, period)["op_income"]` for every
  period (the footnote prints the ledger figures) — allocation is zero-sum.
* Closed month: Σ rows' `wip_booked` == −Σ 50701 activity for the month (`scripts/query.py local`).
* `tests/unit/test_divisional_pnl.py` — periods, classification, estimates, allocation, row math.
* Cross-check against `/finance/daily/` monthly P&L (same GL, COGS there includes 50701) and the
  Command Center "P&L by fiscal year" (same basis; its ΔWIP column = open-month estimate only).
