# P&L basis, WIP, and overhead allocation — the definitive reference

Read this before touching ANY revenue/GP/yearly number in PCA. Everything here was learned the
hard way in Aug 2026 by reconciling PCA against the company's official P&L workbooks
(`../internal_reports/Income Statement Tracker NT 2024-2025.xlsm`, `Jan - December 2025 PL Full.xlsx`)
and the WIP workbook (`Project Update 082026 August.xlsm`), each to the penny or with the
residual explained to the penny.

## 1. The two bases — the single most important distinction in this app

**Fiscal-period P&L (the official basis).** Revenue and cost in the period they were POSTED to
the GL. This is what the accountants, the bank, and Owner's quoted numbers use. In PCA:
`queries.division_pnl_by_year` (Command Center "P&L by fiscal year") and the Daily Finance
Snapshot's monthly P&L.

**Closed-project cohorts.** Lifetime job economics grouped by the year the job CLOSED
(`queries.by_period`, `historical_summary`, `breakdown`). Right for judging project vintages, PMs,
customers — **never for "how did the business do in year X"**. A job closing in Jan 2025 carries
all its 2024 revenue into the 2025 cohort. Presenting cohorts as the year once made 070's "2025
revenue" read $20.4M when the real P&L was $13.6M (48% off), and "2024" $13.7M vs the real $18.0M.
Every cohort view is now labeled "closed-job cohorts"; keep it that way.

## 2. The official P&L mapping (verified to the penny)

- Source: `AcctHist` (ACTUAL ledger, natural-sign, per account × **GL subaccount** × fiscal year,
  13 period buckets) → local `finance_glaccountbalance`, full depth since 2014.
- **Division = GL subaccount prefix** (070 = subs `0700`,`0701`; corporate/admin = `0000`).
- **Revenue** = accounts `40000` + `40001` only. **Other income** (40100 interest, 40250/40300/
  40400 misc/eBay) is shown separately — the 2025 workbook folds it into Sales, the 2024 tracker
  does not; PCA splits it out so both reconcile.
- **COGS** = all `5xxxx` **plus `60000` SALARIES & WAGES and `60005` UNION BENEFITS PACE
  ELECTRIC** (the workbooks' cost-of-sales; classifier: `finance_snapshot.gl_pnl_bucket`).
- **Overhead** = remaining `6xxxx`/`7xxxx`.
- **Anchors (verifier layer 3, hard-coded):** 070 FY2024 revenue **17,966,153.75** and COGS
  **12,332,722.79** equal the tracker's "070" column exactly. If these ever fail, SL restated a
  closed year — investigate, don't "fix" the anchor silently.

**Known, quantified bridges to the workbook "Total" columns** (do NOT chase these as bugs):
1. Each division tab's pivot has a small hand-attributed "000" column (2024: +4,400.50 rev /
   +79,056.75 COGS for 070). Those rows were traced to sub-0000 GL entries whose *projects belong
   to other divisions* — the attribution is manual with no derivable rule. PCA shows the pure
   ledger.
2. The 2025 workbook was FROZEN before SL was corrected: a −110,927.49 revenue adjustment and a
   malformed-subaccount (`'700'`) cleanup posted afterward. PCA follows live SL (system of
   record); AcctHist-vs-workbook-dump total differs by exactly that amount.
3. Other income (+13,115.10 in 2025's Sales line).
These three sum to the full observed 2025 gap to the penny.

## 3. WIP (over/under billing)

- **The company WIP = the Project Update workbook formula**: per open job,
  `WIP = contract value × PTT % complete − billed to date`, positive = **underbilled** (money
  earned, not yet invoiced). The workbook's "Projected Total Cost" is *derived* as cost ÷ %.
  Verified: the workbook's "Prev Wip" total reproduced **$5,670,114.29 exactly**; 521/601 rows
  match the raw formula; ~80/month are the controller's manual overrides (zeroing service rows, adjusting
  jobs mid-billing) — PCA's population exclusions (service/T&M modes, staffing 030, internal)
  approximate the big ones, so PCA runs ~$2–3M above his adjusted totals.
- **Service agreements are never WIP (Owner, 2026-09-14).** Jobs titled "SA …" bill on a schedule, so
  contract × PTT % − billed says nothing about earned work. They are excluded by mode
  (`service_agreement`, which `core/rules.project_mode` assigns to every SA-prefixed title) **and** by
  the title itself (`finance_wip.SA_TITLE_RE` / `SA_TITLE_SQL`, so a classifier change cannot let them
  back in), in every population path: `wip_jobs`, `_as_of_detail`, `in_wip_population`, the history
  backfill, and stored snapshots are filtered at read time (`snapshot_jobs_at`). They are **broken
  out, not hidden**: the Daily Snapshot's WIP card and the WIP by Job page each carry an "excl. n
  service agreements · $billed" pill that lists them (`?side=sa`, drill `kind=wip&side=sa`), the
  Project Snapshot prices no earned value on their % moves (row tag "SA · no EV", KPI "excl. n
  SA/T&M"), and a service-agreement project page says "billed on schedule, not in WIP" instead of
  over/underbilled. `finance_wip.service_agreement_rows()` / `service_agreement_summary()` are the
  one source for those breakouts; their `wip` is what the formula *would* say and is never summed.
- **The PTT WIP report's cost-vs-billings "accounting" columns are NOT the company WIP** — they
  count margin on billed work as overbilling. Kept only as a labeled secondary
  (`wip_under_over`). Leading with them once fabricated an overbilled position that never existed.
- **History** (`backfill_wip_history`): billed/cost cumulative by transaction_date; **pct(D) uses
  the recorded % inside its real PTT validity window (`ptt_last_updated_at`), and BEFORE the
  earliest window it is cost(D) ÷ implied projected total (cost ÷ pct), capped at the earliest
  known %** — never hold a later % backward (that fabricated a $12.6M peak once; 359/477 jobs had
  fabricated percentages at the fake peak). Validated at 7/31 within 4% of the workbook per-job.
  CVs are current-only (SL keeps no CV history) — documented drift, grows with lookback.
- **Year-end baselines**: `backfill_wip_history --year-ends-since 2018` writes reconstructed
  Dec-31 snapshot rows (with per-job detail) powering the P&L table's ΔWIP column and the
  balance-sheet "NI + WIP change" line. 2025-12-31 from this sparse path equals the daily
  backfill's value exactly (cross-check of both code paths).
- **The GL DOES book WIP — every month (corrected 2026-08-31).** The earlier note here ("GL books no
  WIP during the year, 22000 = $0") looked at the wrong account. At every month-end the accountants
  post "RECORD JOBS IN PROCESS / REVERSE JOBS IN PROCESS" (GJ batches, since 2019) to **50701
  OVER/(UNDERBILLING)** — a 5xxxx account, i.e. *inside the workbooks' COGS*: reverse last month's
  WIP (debit), record this month's (credit). The 7/31/2026 records sum to $5,672,268 = the Project
  Update workbook total, so the booked figure is the controller's adjusted WIP. Net effect on a closed month's
  P&L = −ΔWIP in cost = +ΔWIP in profit: **a closed month already carries the earned view.**
  Consequences: (a) never add PCA's ΔWIP to a closed month/year — that double counts (070 FY2026
  YTD was overstated ≈$1.0M before the fix); (b) PCA's snapshot ΔWIP is the right correction *only*
  for months with no 50701 activity yet (the open month, until close — `divisional_pnl.open_month_set`);
  (c) "COGS" in the workbooks includes 50701; the Divisional P&L page splits it out as its own
  "WIP adj" line so costs and WIP are visible separately. The Command Center yearly table, the
  Daily Snapshot's "Adj. operating income" / "NI + WIP change" and the Divisional P&L all follow
  (a)–(b) now. PCA's per-job list and the booked total differ by the workbook's manual overrides
  (service rows zeroed, jobs adjusted mid-billing) — both are shown, the booked one is official.
- **ΔWIP is not profit (Owner, 2026-09-03 — the org's accounting rule; apply it everywhere).** WIP is the
  earned-but-unbilled revenue we book (positive = underbilled); ΔWIP for a period is only the change in
  that booking. A job's (or a division's) real result for a period is **Adjusted GP = GP + ΔWIP**, where
  GP = revenue billed in the period − direct cost posted in the period (the Project Update workbook's
  REVENUE − COST, column AH) and ΔWIP = WIP at the period end − WIP at the start (workbook column AJ
  "Adj GP" = AH + X − Y). Equivalently Adjusted GP = earned revenue − cost. Every page that shows a period
  result shows the three parts and labels ΔWIP as the WIP adjustment, never as profit: WIP by Job
  ("Period result = GP + Δ WIP" band, `finance_wip.ledger_window`), Divisional P&L (WIP adj line; the
  per-project "Adj. GP" columns), Daily Snapshot ("Adj. operating income"), Command Center ("Loaded").
- **Billed-to-date in a period-end WIP is the ledger as it stands, never as it was observed.** A
  daily snapshot stores each job's [wip, earned, billed] as of the pull; an invoice entered later but
  dated into the month (job 260098: a $5,250 Aug-31 invoice keyed on Sep 2) is then missing from the
  month's WIP and shows up as next month's billing — so ΔWIP and the ledger's billed-in-period disagree
  and Adjusted GP double counts. Every per-job baseline is therefore re-anchored before use
  (`finance_wip.rebase_jobs`): billed = Σ revenue lines with `fiscal_period ≤` the month (transaction
  date ≤ day for a non-month-end day), earned kept as it stood (contract × PTT % then), WIP = earned −
  billed. Used by the WIP by Job page (`jobs_at`, `wip_rows_as_of`), the Daily Snapshot windows
  (`views._wip_state`) and the Divisional P&L open-month WIP (`divisional_pnl.WIPSnapshots`), so the
  period's Δ billed equals the revenue posted in the period everywhere. `billed_revenue` on the project
  equals Σ PJTran revenue lines for every project (checked 2026-09-03), so the live "now" side is the
  same basis. Reconstructed year-end rows (WIP only) can't be re-anchored and are left as they are.

**Stored baselines and contract-value corrections (2026-09-04).** Every stored per-job baseline (the daily
snapshot's `wip_jobs`, which the WIP page's Δ WIP, the Daily Snapshot's WIP movement and the Divisional P&L's
month-ends all read) is restated on the *current* contract-value basis at read time — `finance_wip.restate_stored`:
earned × (CV now ÷ CV the app used that day, from `ProjectFinancialSnapshot`, else SL's raw figure), only for jobs
where the app's own basis is involved (basis now or then ≠ `sl`). Otherwise a correction landing mid-period shows the
whole correction as Δ WIP (250038: −$495k in Sep 2026 until this rule). A plain SL contract-value edit stays real
movement. See docs/contract_value_reconciliation_plan.md §6 ("until Phase C, the current effective CV applies to history").

## 4. Corporate (000) overhead allocation

- Pool = sub-`0000` overhead (6xxxx/7xxxx excl 60000/60005). FY2024 $3.35M, FY2025 $3.49M. For an
  open month the Divisional P&L adds 0000's own unposted-payroll estimate to the pool (docs/10 §2).
- The accountants DO allocate it: each division tab's "000" pivot column on overhead accounts.
  Extracted splits (seeded in `finance.DivisionOverheadShare`, migration 0007):
  **FY2024**: 010 9.20% / 020 15.42% / 040 22.23% / 070 22.83% / 080 23.13% (92.8% of pool).
  **FY2025**: 020 12.72% / 040+070+080 ≈24.77% each (87.0%; 010 dropped). Remainder stays
  corporate — intentional, matches the workbooks.
- Editable at `/finance/allocations/`; years without explicit rows inherit the latest earlier
  year. The P&L table's "000 OH" and "Op (loaded)" columns use these.
- **Why it matters**: 080 FY2025 shows op income $925,812 on its own overhead — but $14,054 after
  its $863K corporate share. Division managers who ignore the allocation think they're profitable
  at volumes that don't carry the company.

## 5. Where things live

`queries.division_pnl_by_year` (the yearly P&L table math; WIP piece from `divisional_pnl`) ·
`apps/analytics/divisional_pnl.py` + `/finance/pnl/` (month/quarter/year statement with drill-downs,
booked-vs-estimated WIP, payroll-lag estimate, allocation — docs/10) · `finance_snapshot.gl_pnl_bucket` (account
classification) · `finance_wip.py` (WIP formulas, `wip_as_of`) · `backfill_wip_history` (history +
year-ends) · `finance.DivisionOverheadShare` + `/finance/allocations/` (allocation) ·
`scripts/verify_finance_report.py` (22+ checks incl. the FY2024 anchors) ·
`tests/unit/test_finance_reports.py`. Related: `docs/06_finance_reports.md` (daily snapshot),
`docs/finance_wip_plan.md` (WIP correction history).
