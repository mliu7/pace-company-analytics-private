# Financial Reports — Daily Finance Snapshot

The **Financial Reports** nav section holds company-level finance pages built directly from
Dynamics SL (read-only, same guarded clients as everything else). The first page,
**Daily Snapshot** (`/finance/daily/`), replaces the finance office's manual "End of Day
Snapshot" email. Build plan and full source verification: `docs/finance_daily_snapshot_plan.md`.

## How it works
- `manage.py refresh_finance` (~5–15 s) pulls four registered queries — `sl.finance_gl_balances`
  (AcctHist), `sl.finance_gl_activity` (posted 3I/4E GLTran, trailing 95 days),
  `sl.finance_ar_open` (ARDoc + Customer + SOHeader), `sl.finance_ap_open` (APDoc + Vendor) —
  into full-replace local tables, then writes one `DailyFinanceSnapshot` row for today.
  The same steps run inside `refresh_all`, and the page has a "Refresh now" button.
- History accumulates one snapshot row per day. `manage.py backfill_finance_history --days 180`
  reconstructed the past (balance = OrigDocAmt − adjustments created on or before each day);
  those rows are flagged `reconstructed` and carry AR/AP fields only.
- **Any past day** (added 2026-09-01): `/finance/daily/?date=YYYY-MM-DD`, with ‹ › arrows over the
  stored days, a date box (a day without a row falls back to the nearest earlier one, with a notice)
  and a *Today* button. What a past day can show depends on what exists for it:
  - **Stored live rows** (every refresh since 2026-08-26): every headline figure as stored. The
    document lists (AP by type, cash due in 14 days, largest over-90 customers, largest open
    invoices, pending count) are stored in `detail["extras"]` from 2026-09-01 on
    (`finance_snapshot.page_extras`); the four earlier live rows lack them.
  - **Reconstructed rows** (AR/AP aging back 180 days, WIP back 14 months + every Dec 31 since
    2018): AR/AP totals and buckets and the WIP strip / contributors / movers come from the row
    (`_wip_state(snap, live_rows=False)` reads `detail["wip_jobs"]`). Balance sheet, cash and
    the P&L are **rolled back from today's ledger** (`finance_snapshot.gl_rollback_as_of`): today's
    AcctHist balances with every posting created after that day reversed, month-to-date P&L =
    postings into the month created on or before the day. Verified against the stored live rows:
    exact to the penny at the pull time for Aug 26/27; a $4.5K residual on Aug 28/29 is a batch
    entered before the pull but posted after it (postings carry creation time, not posting time).
    Limits: only inside the GL posting window (`GL_ACTIVITY_WINDOW_DAYS` = 95 days) and the current
    fiscal year; equity accounts are not in the window, so equity is today's figure and the
    identity gap shows any equity posting since; the prior-month P&L column uses the ledger's
    full-month total when that month predates the loader's period floor (labeled in the hover).
  - Older days: WIP only (the rest says "not captured").
  - **Drill-downs on a past day** (2026-09-03; they used to be blocked with a tooltip, which read as
    "nothing happens"): the page binds `PCADrill` with `asOf` = the day and appends `?date=` to the
    breakdown URLs. `finance_drill?as_of=` cuts the GL drills (`gl`, `gl_acct`) at the stored live
    row's pull timestamp (`sl_created_at <= snap.as_of`; date-level when the day has no live row) so
    the month/day postings reproduce the figure on the page, and returns a `notice` string the modal
    shows in an amber callout. Open-document drills (`ar`, `ar_pending`, `ap`, `wip`) cannot be
    rolled back — the local tables hold only the latest pull — so they open today's list with a
    notice saying so; likewise the assets/liabilities breakdowns (`_ledger_breakdown_view` with
    `?date=` adds `viewed_date` / `viewed_headline` for the notice, the ledger itself is today's).
  - Not stored anywhere yet (a backfill would need SL document history): AR by book / by customer
    and AP by type for past days; balance sheets before the posting window (month-ends are
    derivable from AcctHist, intra-month days are not).
- Bank-portal figures (bank checking balance, float, borrowing base, credit line used) are
  **not in SL** (Cash Manager dead since 2023, AP ClearDate unreliable). The page shows GL book
  balances and offers an optional manual entry form (`BankFigureEntry`, local only, staleness-tagged).

## Formulas (all verified against the 7/6/26 email — see the plan file's table)
| Item | Definition |
|---|---|
| AR total / buckets | Open `ARDoc` (`Rlsed=1`, `DocBal≠0`): IN/DM/FC/NS/SB positive, CM/PA negative. Aged by **days past DueDate** (Current ≤0, 1–30, 31–60, 61–90, >90); **credits are not aged** — they net in Current. Reproduced the email's >90 bucket to the penny. |
| AR books | credit → pending (unreleased IN) → SO type (`SO1`/`SO2`; `RM1/RM2/RMS2`→rma) → project (`ProjectID` set) → other. The book × bucket matrix reconciles to the AR total by construction. |
| AP total / buckets | Open `APDoc` `VO`/`PP` positive, `AD` negative, same aging. **`VT` is excluded** (recurring-voucher template garbage). |
| Balance sheet | `AcctHist` (ACTUAL, natural-sign): assets = `1A`; liabilities = `2L` accounts `2xxxx`; equity = `2L` `3xxxx` except `39999`; net income YTD = account `39999` (system-maintained). Identity A = L + E + NI is asserted on every page load and in the verifier. |
| Revenue | 3I accounts except `40100` interest income (the email's definition — matched to the penny). |
| COGS / Overhead | 4E accounts `5xxxx` / `6xxxx`+`7xxxx`. The email's single "Cost" = all 4E; the page shows the split plus Gross margin and Operating income. |
| "Previous day" P&L | GL rows **created** on the previous business day (latest weekday with posting activity), split by the fiscal period they posted into — matched the email exactly (85,234.88 / 35,371.14 on 7/6). |
| Cash / credit line / inventory | GL book balances: 10250 checking, 10350 MMA (matched the email to the penny), 10400 payroll, 10800 petty; 20005/20007 credit line; 12000/12001/12003/12010 inventory (12600 project WIP excluded). |
| Ratios | debt-to-assets L/A · equity ratio (A−L)/A · leverage A/E · over-90 share · AP/AR. Thresholds in the hover text. |

### WIP · billing position (the strip under the top cards)
**Primary = the company WIP workbook formula** (the controller's monthly "Project Update" WIP tab):
per job, `WIP = contract value × PTT % complete − billed to date`, **positive = underbilled**;
the workbook's "Projected Total Cost" is derived as cost ÷ %, so PTT's % drives everything.
Verified against the August 2026 workbook: its "Prev Wip" (July) total of **$5,670,114.29
reproduced exactly**, and 521/601 rows match the pure formula to the penny — the rest are the controller's
monthly manual overrides (zeroing service rows, adjusting jobs mid-billing), so the live number
runs ~$2–3M above his adjusted total. Population: open jobs with CV > 0, all divisions, excluding
internal, service/T&M and staffing (reproduces his big zeroings, e.g. the SA billed $11.9M vs
$4.95M CV). Secondary (muted): the cost-vs-billings columns from PTT's WIP report — never the
headline, because that basis counts margin on billed work as overbilling and once produced a
fictional "overbilled" position here. History (`manage.py backfill_wip_history`): earned basis
reconstructed from cumulative billed by transaction date + each PTT %'s real validity window
(`PercentCompleteObservation.ptt_last_updated_at`; the earliest known % is held backward — the
one assumption); CV is current-only. Drill: per-job table, earned columns first; the strip's under/over numbers open side-filtered,
searchable job modals with project links. The Details panel shows day / week-to-date /
month-to-date WIP change (with under/over and earned-vs-billed attribution from per-job WIP
stored in each snapshot's `detail["wip_jobs"]`), a 3-series chart (net + underbilled + overbilled),
biggest contributors and biggest movers, each with a link into the **WIP by job** page
(`/finance/wip/`, also in the sidebar). That page runs a WIP report for a chosen **period**
(`?period=`): the live windows (`day`, `wtd`, the current month `YYYY-MM` = month to date, the
current year `YYYY` = year to date) or **any past month / year** since 2019 — WIP at the period
end and the change over the period (start = the day before the period, i.e. the prior month-end
or Dec 31). Historical per-job figures come from the stored finance snapshot for that date when it
carries earned/billed detail (daily rows for ~14 months) — so a closed month ties to the Daily
Snapshot history and the Divisional P&L — otherwise (older months; the sparse Dec-31 rows, which
store WIP only) they are reconstructed now by `finance_wip._as_of_detail` (the `wip_as_of` engine);
the header pill says which. Note the two can differ for the same date (June 30 2026: stored
$4.25M vs rebuilt $3.84M) because contract values are current-only and SL postings arrive late.
Columns sit under five **bands** (frozen Project and Customer while scrolling; Div / PM columns
hide when you filter to one; since 2026-09-04 the **Customer** column and each of the four bands can be
hidden — chips above the table or a click on the band heading — Customer off by default, the choice kept in
localStorage `pca.wip.cols`): **Job** · **Progress (PTT)** — % complete shown as *old → new* when
it moved over the period (old = the % in force at the period start from the real PTT validity
windows, `pct_series`), with the age in days of the entry that set it (green ≤14 days, amber
31–45, red >45 or never — 45 days is the `pm_percent_complete_stale` data-quality rule; hover for
the date and who set it; ≈ = estimated from cost before the first recorded %), hours-based % (hover or
click it for the union / non-union breakdown — hours used by the employee's PTT type, SL LABOR / LABORUNION
budget hours, the PM's remaining hours by type from the latest PTT revision, projected = used + remaining,
vs budget and % done per type — `finance_wip.hours_breakdown`, 2026-09-03),
remaining hours (no age of its own since 2026-09-04: the small figure on % complete is the days since the
*most recent* of the two PTT entries, lighter than the figure, in a fixed slot so the % signs line up; "-" =
never), remaining hours with the age of the revision, PTT hours worked in the period · **WIP = earned −
billed** — contract, earned, billed, WIP as *old → new* when it moved, and Δ WIP over the period
(no Δ billed / Δ earned columns: Δ billed always equals the period's Billed in the next band, because
every baseline's billed is re-anchored on the ledger as it stands — `finance_wip.rebase_jobs`, docs/07
§3 — and Δ earned = Δ WIP + Billed; the Δ WIP hover shows the earned / billed split) ·
**Period result = GP + Δ WIP** — billed and direct cost posted in the period (by
fiscal period for month / year reports, by transaction date for the day / week windows;
`finance_wip.ledger_window`; every line on the job counts whichever division's subaccount it posted to,
whereas the Divisional P&L's project list keeps only the division's own subaccounts — e.g. a 070 job's
material vouchered to 0100 is on the job here and in 010's column there), GP = billed − cost, and **Adj. GP = GP + Δ WIP**, the job's real
result for the period (docs/07 §3: Δ WIP alone is never profit; this is column AJ of the Project
Update workbook) — with an Adj. GP chip for the filtered set · **Profitability** — cost to date with cost ÷ EAC cost as a cost-based %
complete, GP% *sold → EAC* (sold = `sold_gp_percent`, current budget; EAC = the latest
`ProjectPrediction` as of the period end, bold, green/red vs sold), EAC GP $. Filters: period,
under/over/moved/left-the-population, division, PM, state, PTT-entry age (fresh / 30+ / 45+ /
90+ / never / no %), min |WIP| or |Δ|, search. Every column sorts; numeric headers sort descending
on the first click, and the signed columns (WIP, the Δs, GP, Adj. GP, EAC GP) sort **by size of the
move** on ▾ (biggest either way first, zeros last) and most-negative-first on ▴ — an ascending
sort on a Δ column used to bury the two rows carrying the whole month's movement under a hundred
zero rows. Totals for the filtered set sit in a **sticky footer** so the Δ WIP column visibly
sums to the Δ WIP chip. Zero movement renders as a faint 0, never "+$0"; values that round to
zero never print "-$0" (`pca.money`).
Jobs that left the population since the baseline (closed / reclassified, WIP → 0) appear only in
the "moved" and "left" views, tagged `left`. The old `?window=day|wtd|mtd|ytd` links still resolve.
Period parsing is pure (`finance_wip.resolve_period` / `period_options`, `tests/unit/test_wip_periods.py`).
Both pages share `views._wip_state` (live rows + baselines + `finance_wip.wip_movement`) so the
numbers agree. **The table itself is a shared component since 2026-09-08** — `apps/dashboard/job_table.py` (row derivation
`decorate`, column / band definitions `columns` with the identity columns chosen per page, `sort_rows`, `totals`,
`profitability`) rendered by `templates/dashboard/_job_table.html` + `static/dashboard/job_table.js` (frozen columns,
band / Customer chips remembered per page through `PCA.pref`, the Hrs % rich tip, click-to-pin definitions). The WIP by
Job page keeps its own population, baselines, period picker and filters and hands the rows to the component; the
**customer page's Projects card** (`/customers/<id>/`, `job_table.lifetime_table`) lists one customer's jobs — open and
closed, in the WIP population or not — with **Active / Completed** toggle chips (default: active only; remembered), a
footer total per visible set, and the same bands read *to date*: WIP as it stands today (closed jobs and service / T&M
work carry zero WIP by definition, docs/07 §3, and show "—" for earned), no Δ WIP column (since inception it would repeat
WIP), and "Result to date = GP + WIP" (Billed / Cost / GP posted over the job's life, Adj. GP = GP + WIP = earned − cost;
for a closed job simply its final GP) — hidden by default there, one chip away. Profitability shows a closed job's final
GP % and $ in place of the EAC. The Job band there is Div · PM · State · Mode · Created · Closed (no Customer column);
sorting is by querystring like the WIP page, the identity columns and default sort are parameters, so a PM's or a
division's page can reuse the component with its own first columns. The **Project Snapshot's Job activity card** (docs/project_snapshot_spec.md §3.2) has a
"WIP view" toggle: `job_table.window_table` runs the component over a day / week window — WIP at the window end (live, or
`wip_rows_as_of` for a past window), Δ WIP against the day-before baseline (`jobs_at`), the window's GP + Δ WIP by
transaction date — with the snapshot's own Updated-by verdicts and crew rows merged in (`extra=`), footers for All
activity / Jobs worked, and the identity column `updated` ("Updated by"). Alternate job rows are shaded (`.alt`, set by `job_table.js` over the visible rows only, so a toggle or filter never breaks the rhythm; crew rows stay white). Crew rows never move the columns: the note and hours cells hold a `width: 0; min-width: 100%` block, so their content fills the cell without entering the column-width calculation (Owner, 2026-09-08). Identity columns are narrow by design (PM and Updated by truncate
to the hover, State is a compact `state_short` tag) and *State* is a hideable group (`cb-state`; band cells shrink via
`data-span` in `job_table.js`). Updater names link to the person page, whose "PTT progress updates" card lists every %
save they made (`views._ptt_updates_by`). Cost / GP bands need `margins.view` (`columns(...,
margins=False)` drops them; sentinel `GP_RENDER` in `tests/access`); pure parts are covered by `tests/unit/test_job_table.py`. History reconstruction: pct(D) from real
PercentCompleteObservation validity windows; before the earliest window, cost(D) ÷ implied
projected total cost capped at the earliest known % (validated within 4% of the workbook's
per-job Prev Wip at 7/31). Chart convention: positive WIP = green (money waiting to be billed). History spans 14 months
(`backfill_wip_history --days 420` creates WIP-only snapshot rows where the AR/AP backfill
doesn't reach), so the year start + prior-year tail are always visible, and the Dec 31 baseline
powers two things: the **Year-to-date** change card and **"NI + WIP change (adj.)"** on the
balance-sheet card — GL net income (39999) is billed-basis, so the adjusted line adds PCA's ΔWIP
for the months the accountants have not closed yet (the chip reads **"ΔWIP since close <date>"** —
the last month-end with a 50701 entry — and, when more than one month is open, lists each month's
part, e.g. "Aug −$3.69M · Sep MTD +$3.49M"). Closed months already carry their WIP inside COGS
(docs/07 §3), so adding the full YTD swing would double count. The parts can see-saw around a
month-end: month-end invoices are dated into the old month (and re-anchored there by
`rebase_jobs`) while the PMs' month-end % updates are keyed in the first days of the new month and
dated from their PTT timestamp — Aug/Sep 2026 showed −$3.69M / +$3.49M for a net −$0.2M. Read the
total since the close, not one part. Owner declined a "close cutoff" hold-back rule for the % on
2026-09-08 (too messy for now). The P&L card applies the same adjustment per month: a **WIP change** row (from the daily WIP snapshots; prior month =
month-end minus prior-month-end baselines) and **Adj. operating income** = op income + ΔWIP,
with the % taken against earned revenue (billed + ΔWIP).

## Liabilities / assets breakdowns (click "Total liabilities" or "Total assets" on the snapshot)
`/finance/daily/liabilities/` and `/finance/daily/assets/` — modals (or `?page=1` for a bookmarkable
page) explaining the two balance-sheet headlines. Shared core `apps/analytics/finance_ledger.py`
(`ledger_breakdown(spec, snap, prev)`: per-account balances, time ladder, 20-month series, ledger
activity, groups); per-side specs with the account knowledge in `finance_liabilities.py` (+ AP
subledger tab) and `finance_assets.py` (+ AR subledger tab); view `_ledger_breakdown_view`; generic
template `finance_ledger_breakdown.html` with `_ledger_tab_ap.html` / `_ledger_tab_ar.html`; JS
`static/dashboard/finance_modals.js` (`PCALedger`, also hosts the shared JSON drill modal
`_fin_drill_modal.html`). Reads local tables only.

- **Assets population** = every `1A` account (debit-positive; an asset increases by DrAmt − CrAmt).
  Groups: Cash & bank (10250 book checking — negative when outstanding checks exceed the bank balance,
  10350 MMA, 10450 AMEX, 10800), Receivables (11000 AR, 11011, 11035), Inventory & jobs in process
  (12000 — debited by PO receipts, credited by OM shipments; **12600 = the accountants' jobs-in-process
  asset, a month-end "RECORD JOBS IN PROCESS" journal reversed the next month**, so it steps at close),
  Prepaids & other current, Property & equipment net (15xxx/16500 less 15710/15720 — a flat "ME
  DEPRECIATION" journal monthly), Deposits. The Receivables tab = the AR subledger behind 11000 (by
  customer with links, by age, by book, largest invoices, credits, pending, subledger − GL bridge; AR
  matched GL 11000 to the penny on 8/28). Asset day change reconciled to the card chip to the penny
  (565,736.32 both ways, 8/28).

- **Population = exactly the headline**: every `2L` account below 30000 (natural sign, credit =
  owed). Six display groups partition it — Trade payables (20000 AP, 20001 PO clearing, 20002/20003/
  20006), Bank debt (20005/20007 credit line, 20060 lease, 20650 interest), Customer prepayments &
  deferred (24000, 21000), Sales & other taxes (20500, 20800, 20700), Payroll/benefits/union (the
  rest of 20010–20550), Other (anything new) — so group totals reconcile to the card by construction;
  the fragment shows a red note if the ledger copy moved since the snapshot was built.
- **Ledger semantics** (read off the postings, 2026-08-28): **20001 PO clearing** = goods received on
  POs not yet vouchered (credited by PO receipts, debited by the AP voucher); **24000** = contracts
  billed ahead of the service period (UIH help desk, Abridge, staffing) released to revenue by a
  monthly journal; **21000** = prepaid service-hour blocks; **20005** moves only by "COMM LOAN
  BORROW / PAYMENT" journals; **20500** is credited per taxable invoice with the jurisdiction in the
  posting description and debited when a state is paid.
- **Time ladder** per account and group: Δ day = 2L postings entered between the previous live pull
  and this one (reconciles to the card's day chip to the penny — verified 8/28: 270,805.35 both ways);
  Δ MTD = AcctHist period-to-date (equals the period's postings exactly); Δ since Dec 31 = balance −
  beginning balance; vs year ago = balance − the month-end 12 months back; year-ends for the three
  prior fiscal years; 20-month month-end series (beg + periods through the month, the 13th adjustment
  period folded into December) as sparklines, per-account charts and a stacked group chart.
- **Accounts payable tab** = the AP subledger behind 20000: by vendor (top 20 + others, click for
  the vouchers), by age, by posting month, largest vouchers, due next 14 days, concentration, and the
  **subledger − GL 20000 bridge** (timing between the AP module and the ledger; not decomposed).
- **Ledger activity**: `sl.finance_gl_activity` now also pulls posted GLTran rows on balance-sheet
  accounts (`AcctType='1A'`, and `'2L' AND Acct < '30000'`, same window; ~57K rows total). Every P&L consumer filters on
  `acct_type IN ('3I','4E')` (`previous_business_day`, the `gl` drill default, the P&L sums), so the
  P&L numbers are unchanged — the verifier's layer 2 still passes. Drill kinds added: `gl_acct`
  (postings on one 1A/2L account, newest first, natural-sign change) and `ap&vendor=`. Each snapshot also stores
  `detail["liab_accts"]` (per-account balances) for future history.

## Division P&L (official basis) — the accountants' mapping
The Command Center's **"P&L by fiscal year"** table (per division filter) uses the exact mapping of
the accountants' income-statement workbooks, discovered and verified 2026-08-27 against
`Income Statement Tracker NT 2024-2025.xlsm` and `Jan - December 2025 PL Full.xlsx`:

- **Basis**: GL by fiscal posting period × **GL subaccount prefix** (070 = subs `0700`/`0701`),
  from AcctHist (full depth since 2014, `finance_glaccountbalance`).
- **Revenue** = accounts `40000` + `40001`. **COGS** = all `5xxxx` **plus `60000` SALARIES & WAGES
  and `60005` UNION BENEFITS** (the workbooks' cost-of-sales set). **Overhead** = remaining
  `6xxxx`/`7xxxx`. **Other income** = remaining `4xxxx` (interest, eBay/auto sales…) — shown
  separately because the two workbooks disagree (2025 folds it into Sales, 2024 doesn't).
- **Verified to the penny**: 070 FY2024 revenue 17,966,153.75 and COGS 12,332,722.79 equal the
  tracker's own "070" column exactly (verifier layer-3 anchors these; a failure = SL restated).
- **Known bridges to the workbook "Total" columns** (documented, each quantified):
  (1) the pivots carry a small hand-attributed "000" column (2024: +4,400.50 rev / +79,056.75
  COGS) whose rows are sub-0000 entries not attributable by any project/division rule — the
  projects on them belong to other divisions; (2) the 2025 workbook includes other income
  (+13,115.10) in Sales; (3) SL was **corrected after the 2025 workbook was frozen** — a
  −110,927.49 revenue adjustment plus a malformed-subaccount ('700') cleanup; PCA follows live SL,
  the system of record. Sum of bridges = the full observed gap to the penny.
- The old "Year by year" table was **closed-project cohorts by close year** (lifetime job
  economics) — a valid vintage view but not a P&L; it now sits collapsed under the P&L table with
  an explicit warning, and every other cohort view (quarterly chart, person/customer year tables)
  is labeled "closed-job cohorts". The daily-snapshot P&L's COGS/overhead split was aligned to the
  same account set (60000/60005 into COGS).

### Payments Received (`/finance/payments/`)
The in-app version of the daily "Payment Applications" email (SL stock report 08820): AR payments
(ARDoc type PA) + their invoice applications (ARAdjust, adjusting side), **every row since
`AR_HISTORY_FIRST_YEAR` = 2013** (~27k payments / ~82k applications, full replace, ~8 s) in
`finance_arpayment` / `finance_arpaymentapplication` (loader `load_ar_payments`, in both refresh
paths; the window was 430 days until 2026-09-08, when the customer page's payment history needed the
whole story — `finance_arinvoice` grew the same way and now carries `due_date` / `terms` / `cust_po`). **Day key = the payment's entry date, exactly like the report** — verified 8/26/26 to the
penny (11 payments, $172,757.70). Wire batches share one RefNbr across customers (all the Bechtel
entities on WT08262026), so payment identity = (ref, customer). The page adds what the report
omits: applications made today against older payments (a separate section — 8/26 had $9,270.54 of
these), late-vs-on-time flags per invoice, partial-payment tags, unapplied balances, customer
links, and project links with completion context (PM % done · billed % of CV) so a payment reads
as "…$128K on a job that's X% done". Prev/next-day navigation + date picker over the full window (day list clamped to <= today —
SL contains at least one fat-fingered future payment date, e.g. a 2035 entry, which once made the
next-day arrow jump a decade). UI: one unified grouped table — shaded payment rows (largest
first) with invoice rows beneath sharing the same columns so every dollar aligns; totals footer;
collapse/expand-all keeps one row per payment.

**Payment rows carry their invoices' values (Owner, 2026-09-11).** A collapsed payment is not a blank row: the columns are
filled in from its invoice lines — the invoice date and due date when every invoice shares one, else "(multiple)" (the
invoice-date hover gives the range); the paid timeframe when every invoice lands in the same one, else the simple average
days late marked "(avg.)" (on-time invoices count as 0, the hover gives how many were late and the spread); the project
when every invoice bills the same job, "(SO1 Order)" when every invoice is a job-less 010 hardware order, else
"(multiple)" (hover lists them); that project's % done and billed-of-contract when there is a single one; and the sums of
Invoice amt and Still open (an invoice paid by two applications counts once). The footer totals both sums as well.
Logic: `apps/analytics/payment_summary.py` (pure, `tests/unit/test_payment_summary.py`); the SO1 tie comes from
`finance_arinvoice.so_type`. Lateness everywhere on the page uses one label (`late_label`: days / weeks / months / years),
so a payment row's average is comparable with its invoice rows. **The table is fixed-layout** (`#paytable`, a colgroup of
explicit widths): expanding or collapsing a payment never moves a column — long text truncates with the full value on hover.

## Bank Reconciliation (`/finance/bank/`)
**Input**: the monthly BMO Bank N.A. statement PDF for the operating account (000-000-000-0 = GL
10250), dropped in `settings.BANK_STATEMENTS_DIR` (default `../internal_reports/bank/`, env
`PCA_BANK_STATEMENTS_DIR`). It is the only input in the app that is neither PTT nor SL. File names
don't matter; the account and period are read from the statement. A changed file replaces its
earlier import; an unchanged one is a no-op.

**Parse** (`apps/ingestion/bank_statements.py`, pdfplumber, positional): the WITHDRAWALS /
DEPOSITS header fixes the column split, transaction lines start `MON DD`, the check list follows
"THE FOLLOWING CHECKS…" as number/amount/date triplets, `Reve Check# n` credits are checks the bank
paid then reversed. A statement is accepted (`parse_ok`) only if deposit/withdrawal counts and
totals and previous + deposits − withdrawals = ending all agree with the bank's summary block.
Stored in `finance.BankStatement` / `BankStatementLine`.

**Reconcile** (`apps/analytics/bank_reconciliation.py`; SL read through three registered queries:
`sl.bank_gl_cash_activity`, `sl.bank_ap_checks`, `sl.bank_acct_balances`). Matching order, each
rule seeing only what earlier rules left: (1) checks by number — **the bank prints Pace's 6-digit
numbers without the leading digit** (62192 = SL 162192) — amount must agree; (2) bank reversals ↔
SL voids; (3) payroll (Paylocity `92358 PACE SYSTE` direct deposits / trust / tax collections) set
aside for a category compare by week; (4) exact 1:1 within ±10 then ±60 days; (5) one
ACH-origination settlement ("ACH OFFSET -SETT-IBANK") = subset of SL **01-series "EFT checks"** dated
0–7 days earlier; (6) one lump remote/package deposit = AR receipt batch(es), optionally + a small
journal item; (7) several same-vendor bank pulls = one SL payment (Ingram, union benefits);
(8) ±5 cents; (9) self-cancelling pairs drop out (void ↔ original check, journal reversal ↔
original, payroll accrual reversed across periods). Bridge: bank ending − outstanding paper checks
(SL checks ≤ period end, not voided, not on this or an earlier imported statement, SL clear date
after period end) − SL EFTs not yet on a statement + GL-only debits − GL-only credits − (payroll GL
− payroll bank) + bank-only debits − bank-only credits = book (AcctHist, BegBal + PtdBal00..m−1).
The **opening position** (bank previous balance − SL outstanding at prior month-end − prior book,
net of accruals reversed this month) shows how much of the residual is carried forward versus
created this month.

**Flags** (page + `result["flags"]`): residual (to the penny / carried-forward / unexplained),
checks not in SL or paid for a different amount (critical), unmatched bank items (high), entries
dated after period end posted to the period (payroll for the next pay date is routinely posted to
the prior period — book cash understated until it hits the bank), bank-reversed checks, SL clear
flags vs statement, SL EFTs not on a statement, duplicate SL check numbers, stale outstanding
checks (>60 days), payroll weeks differing ≥ $1,000.

**July 2026 (first month)**: 168/168 checks match, SL's own clear flags identical to the
statement, every bank line matched; residual −45,188.73 of which −43,934.77 was already implied
at 6/30 (a legacy difference that predates the imported history) and −1,253.96 arose in July
(payroll net-pay vs direct-deposit gaps). Commands: `manage.py reconcile_bank` (scan + reconcile
new; also runs inside `refresh_finance`), `--all` (re-run every month after a rule change),
`--reparse` (after a parser change). Unit tests: `tests/unit/test_bank_reconciliation.py`.

**August 2026 (run September 8, 2026)**: imported the user-provided
`../internal_reports/document_20260831.pdf` explicitly with `manage.py reconcile_bank <path>`;
the source remains at that path (outside the normal scan folder). Result: `/finance/bank/3/`.
The PDF summary and parsed records agree: opening $47,090.00; 130 deposits / $8,130,962.25;
243 withdrawals / $8,174,872.25, including 155 checks / $1,334,089.14; ending $3,180.00.
All 155 checks match SL by number and amount, but none carries an August SL cleared flag yet.
There are 39 outstanding paper checks / $149,233.73, eight unmatched bank items, and one
unmatched GL credit. These are review exceptions, not approved correcting entries.

The eight bank exceptions: loan-transfer credits August 19 $64,430.89 and August 31 $598,853.06;
Amex August 3 $7,496.44; loan sweep August 20 $55,826.15; BMO payment August 24 $122,498.20;
wire August 25 $37,637.00; loan debit August 31 $16,857.52; Capital One August 31 $354,503.05.
The unmatched GL credit is August 30, batch 0000009523, "COMM LOAN BORROW", $37,673.00.
Its $36 difference from the August 25 wire suggests a possible digit transposition, but amount,
date proximity and description alone are insufficient to match it or change SL.

**Carry-forward limitation to preserve in interpretation:** the stored August bridge shows
adjusted bank −$44,671.55 versus book −$249,520.35, residual +$204,848.80 and a zero change
from its *raw* opening comparison. That opening comparison is $47,090 − $293,153 − (−$450,911.80).
It does **not** carry all July reconciling adjustments, so +$204,848.80 is not directly comparable
to July's saved −$45,188.73 residual or evidence of newly missing cash. July's book balance is
unchanged; the difference between the two presentations is exactly $250,037.53:
$231,339.86 payroll posted ahead + $20,828.32 other payroll difference − $2,130.65 deposits in transit.
The guarded fresh SL read confirms batch 0000009500 has August 4-dated payroll credits of
$231,339.86 in fiscal period 202607. August's category comparison shows bank payroll
$1,369,758.60 versus August-period GL $1,162,237.83; the $207,520.77 difference must be
reviewed with that prior-period posting, not called wholly unbooked August payroll.
The July reconciliation was preserved. The August run remains provisional pending invoice,
payroll and prior-period roll-forward review; no SL/PTT data was changed.

## Verification
- `scripts/verify_finance_report.py` — run any time. Layer 1 (exact): snapshot == local doc
  tables, matrix reconciles, A−L−E−NI = 0. Layer 2 (drift-tolerant): local copies == fresh SL
  pulls via independent one-line queries. 17/17 passed at build time (2026-08-26).
- Against the 7/6/26 email (as-of reconstruction): AR total within 0.02%, **AR >90 exact to the
  penny**, equity exact, revenue/MTD/prev-day lines exact, AP within 1.4% (her run timing),
  inventory/assets within ~1.5% (late postings). Full table in the plan file.
- Unit tests: `tests/unit/test_finance_reports.py` (aging boundaries, book classification, periods).

## Notes / cautions
- SL is read at READ UNCOMMITTED and is live — intraday numbers move; the as-of stamp on the page
  says when the pull happened. Layer-2 verification flags drift rather than failing.
- The email's "Net working Capital" = Assets − Liabilities (really equity + NI). The page shows
  that line for continuity *and* true NWC (current assets − liabilities), each with hover text.
- Reconstructed history rows can't know unreleased/pending state or SO-book splits; they carry
  aging totals only.

## Roadmap (future pages in this section)
Weekly cash-flow forecast (AP due + expected collections) · company-wide customer payment
scorecard / DSO (the per-customer version is on every customer page since 2026-09-08, below) · monthly close P&L pack · borrowing-base tracker (uses the manual bank inputs) ·
WIP / over-under billing schedule from PCA project data.

## Divisional P&L (`/finance/pnl/`)

Month / quarter / year income statement by division with drill-downs to accounts, postings,
projects and WIP — the accountants' booked WIP (50701) for closed months, PCA's estimate and an
unposted-payroll estimate for open ones, corporate (000) allocated by the shares on
`/finance/allocations/`. Full description in `docs/10_divisional_pnl.md`.

## Vendors (`/finance/vendors/`, `views.vendors`, period filters 2026-09-03)

Everyone we buy from, one row per vendor, largest paid first. **Paid window** picker: YTD (default), 12 mo, every calendar
year since 2013, All, or a custom from/to range (`?p=ytd|12mo|all|YYYY|custom&from=&to=`; `q` = name / id search). Each
row shows paid in the window (checks / EFTs by check date, voids negative, count in brackets), the **same window a year
earlier** (YTD → last year to the same date; a calendar year → the year before; custom → the preceding span of the same
length) with the Δ in $ and % (amber = spending more, green = less), today's open AP and open POs, material received in
the window and the last activity. Totals in the footer and the heading.
Data: `finance_apcheck` is now a **full history since `AP_CHECK_FIRST_YEAR` = 2013** (~2,500 payments a year, full
replace on every finance refresh — `load_project_events`); PO receipts keep their ~3-year window, so the Received
column is flagged *partial* for earlier windows. One SL check is dated 2079 (data entry) and is kept out of the year list.

**Settled by card (2026-09-03).** A payment document's `APDoc.Acct` is the cash account it was drawn on
(`finance_apcheck.cash_acct`, the same field the bank reconciliation keys on). Company-wide only two accounts ever
appear: **10250** business checking and **10450 "AMEX HOLDING ACCOUNT"**, the clearing account for every payable
settled with a company credit card (`finance.CARD_CLEARING_ACCOUNTS`, `settled_by_card`). The mechanics, verified in
SL for ADI: the vendor's invoices are vouchered normally; when they go on the card, AP records a "check" to the
vendor drawn on 10450 (credit 10450 / debit 20000) and a voucher to the card issuer (CAP004 Capital One, AME004 /
AME007 / AME005 American Express) for the same amount with the vendor id as its invoice number; the issuer is later
paid by ACH from 10250. So the vendor's dollars would otherwise appear twice — under the vendor and again inside
the card issuer's payments. The Vendors index therefore splits **Paid** (cash from the bank) from **By card**, the
footer and heading show both, the Δ compares cash only, and rows sort by the sum; the vendor page shows *card* as
a payment method (pill, per-payment tag, a Card share column in Paid by year, the YTD KPI's "of it by card"); the
Project Snapshot's AP paid out and the 010 snapshot's distributor line tag card settlements and keep them out of
the cash total. SL does not say **which** card on the payment (all cards share 10450), so no card is named. In the
12 months to 2026-09-03: 332 payments / $5.8M through 10450 across 51 vendors (Ingram $2.5M, ADI $2.2M, Shure
$0.4M …); 47 of ADI's 47 payments were card settlements, none cash.

### Vendor page (`/finance/vendors/<id>/`, `views.vendor_detail`, rebuilt 2026-09-03)

Header: id, status, class, terms, location, contact, in SL since, first paid, all-time paid. KPIs: paid YTD vs the same span
last year; rank among vendors this year and share of AP paid; days to pay vs terms with the early (< 30 d) and after-due
shares and discounts taken; open AP (+ past due); open POs / received 12 mo. **How we pay them**: payment-type mix by
dollars over the whole history (ACH = SL check numbers starting `01`, EFT, printed check, hand check, void) and the cash /
holding account the payments were drawn on (the cash side of the payment in `APTran` — e.g. *Amex holding account* = paid on
the card), plus the median days between payments. **Paid by year** (every year since 2013: payments, paid, Δ, share of
company AP, largest, ACH share). **Jobs this vendor was charged to** (voucher + receipt lines naming a project). **Where the
money went**, by division × year and by GL account × year: the expense side of every voucher (`finance_apvoucherline`,
subaccount → division, AD credits netted); PO purchases only clear PO clearing 20001 on the voucher, so those dollars are
shown by what the PO **receipts** debited (`finance_poreceiptdist`: inventory 12000 / project cost by subaccount, the job's
division when the receipt names a job, *warehouse stock* when it names none — stock receipts carry no SO link, so 010 resale
cannot be attributed further). **Payments** (latest 60) with How + cash account, bank-cleared date (BMO checking, from
reconciliation), days held, and on expand the invoices covered with their expense split per division · account · job
(PO-clearing lines explained by their receipts; many-line vouchers collapsed per division / account).

Data: two incremental extracts since 2013, upsert on SL's natural key, watermark on `Crtd_DateTime` with a 7-day overlap
(`load_ap_voucher_lines`: `APTran` minus the 20000 AP-control offsets, ~500k rows, 17 s first load; `load_po_receipt_dist`:
`POTran` type R, ~180k rows, key RcptNbr + LineRef) — steps `finance_ap_lines` / `finance_po_receipt_dist` in
`refresh_finance` and `refresh_all`. `APTran` sign rule: amount is a debit when `DrCr = 'D'`, else a credit.

## Accounts Receivable page (`/finance/ar/`, `views_ar.py`, 2026-09-04)

Plan and the two design reviews: `docs/ar_page_plan.md`. Every open AR document (`finance_aropendocument`, the same
population and aging rules as the daily AR tile — the footer prints "= daily snapshot ✓" or the difference), grouped by
**customer** (default: the aging report), **age** (buckets oldest first), **due date** (overdue / this week / next week /
30 days / later — the cash-in forecast), **project**, **salesperson**, **division** (SO1 orders = 010) or **book**; Show
chips all / past due / over 90 / credits / pending; filters `q` (customer, invoice, PO, description, job, order,
salesperson), `div`, `sp`, `book`, `min`; CSV of what is on screen (`export=csv`). Group rows carry the aging matrix
(Current · 1–30 · 31–60 · 61–90 · over 90, credits netted), docs, oldest days past due and — in customer mode — the last
payment (linked to that day on the Payments page), $-weighted days-to-pay vs terms (amber > terms + 15, red > + 45),
billed / collected 12 mo, and a behaviour chip (`paid Nd ago` within 30 days; `no payment Nd` when past due and silent 90+
days); an `apply` chip flags credits / unapplied cash. Document rows show type / pending / partial tags, dates and terms,
the book and its tie (job link with "billed X% of contract, Y% done", SO1 → CNET order link, description, customer PO),
balance (of original when partial) and payment count. Clicking a document loads `/finance/ar/doc/` (JSON): **what was
billed** (`finance_aropeninvoiceline` — ARTran for open docs, tax lines folded), **payments on this document**
(applications with a running balance), **context** (job: PM, contract / billed / earned / PTT %, close date, other open
documents on the job; order: CNET number, ship-to; **who to call** from `finance_slcustomer` — SL Customer bill-to name,
attention, phone, e-mail, terms, statement cycle, credit limit when set). KPIs: Open AR, past due %, over 90 % with the
day-over-day chip, credits / pending, **DSO** = Open AR ÷ (billed last 90 days ÷ 90), collected vs billed 30 days,
concentration (top 3 / top 10). Sorting is client-side (signed columns by magnitude); expansion is always in place;
the table never scrolls inside the page. Visitors arriving from a past day on the snapshot see an "as of the latest
pull" banner. The daily page's over-90 and largest-invoice tables link here with context.
Data: `load_ar_detail` (step `finance_ar_detail` in `refresh_finance` / `refresh_all`) full-replaces
`finance_aropeninvoiceline` (~5.6k rows) and `finance_slcustomer` (~2.1k; SL holds a few duplicate master ids — first wins).

**Customer page — "Outstanding invoices" card** (`/customers/<id>/`, 2026-09-08, `views_ar.customer_open_ar`,
`_customer_ar.html`). The same documents, aging and derivations as the AR page for one customer, in a flat sortable table
(ref with type / pending / partial tags · invoiced · due · days late · what it is for — book, job link with billed % and PTT %
done, order, description, PO · terms and rep · original · paid so far with the application count · signed balance) with a
total row (originals, paid, open balance = the customer's line on the AR page). The heading carries open documents, open
total, past due (and %), credits and pending; a chip strip gives the aging matrix (Current · 1–30 · 31–60 · 61–90 · over
90), oldest days past due, terms, days-to-pay vs terms, last payment (linked to the Payments page), billed / collected 12 mo
and the behaviour chip; "AR page" links to `/finance/ar/?q=<id>`. Clicking a row opens the same detail panel as the AR page
(`static/dashboard/ar_panel.js` — `PCAArPanel.attach` / `.sortable`, shared by both pages, fed by `/finance/ar/doc/`).
Rendered only for `finance.view` holders (the view skips the query otherwise; sentinel `AR_RENDER` in `tests/access`);
per-row panels are drill-downs and are deliberately not remembered (docs/03 "Remembered toggles").

## Customer payment history (customer page, `analytics/customer_payments.py`, 2026-09-08)

*Owner: "on the customer page I want a table of all the payments they made, the invoices behind them, which jobs, some
stats on the job, and most importantly how many days it took them to pay — I should get a clear picture of whether this
entity is a good payer."* The **Payment history** card sits under the Outstanding invoices card on `/customers/<CustId>/`
(the table is paged JSON from `/customers/<CustId>/payments/`, `views_customer.customer_payments`, registered as
`customer_payments`). Both the card and JSON follow that endpoint's **effective Permissions rule**, using
`AccessContext.can_view()`: the code default is `customers.view` + `finance.view`, while Owner's September 17 saved
override requires only `customers.view`. Outstanding invoices still require Finance access. References in the
payment table link to Finance, project, employee and 010 pages only when the viewer can open the destination.

**Data.** Every AR payment application since 2013 (`finance_arpaymentapplication`, PA → invoice) joined to the payment
document (`finance_arpayment`: the check / wire date, total, unapplied balance) and the invoice master
(`finance_arinvoice`: amount, balance today, due date, terms, customer PO, order) and `core_project` (title, division,
PM, state, contract value, billed to date, PTT % complete). Zero-dollar invoices (SO2 shipment paperwork against a job —
213 of BUL001's 244) are counted but never listed.

**Definitions** (docs/04 "Customer payment behaviour"):
- *Paid on* = the payment document's date (ARDoc PA `DocDate`, the Payments Received page's day key), falling back to
  the application date when the payment is missing or dated in the future (SL has a 2035 entry).
- *Days to pay* = paid on − invoice date, per application; customer figures are **dollar-weighted** by the applied amount.
- *vs due* = paid on − due date. Buckets: within terms (≤ 0), 1–30, 31–60, 61–90, over 90 days late.
- *Retainage.* GCs hold 5 % / 10 % of every progress bill. An application that leaves exactly that share open (± 0.3 pt)
  is **paid to retention** — the customer paid what it owed, the rest is the contractual holdback (BUL001: 13 of its 17
  live applications). The later **retention release** is listed with its days but excluded from pays-in and the
  timing buckets: it measures the job's closeout, not the customer's promptness. Reversals (negative applications) are
  listed, re-open the invoice in the running balance, and never weight a metric.
- *Payer grade* (PCA's rule, not an SL field): dollar-weighted days to pay minus the terms on the customer's invoices
  (the terms code carrying the most dollars; unknown = 30): **prompt** ≤ 10 days over, **fair** ≤ 30, **slow** ≤ 60,
  **very late** beyond. Shown as a pill in the card heading with the "+N d vs terms" figure; the same grade per year.

**Card.** KPIs: *Pays in* (all-time weighted days / terms, last-12-month figure and the per-application median),
*Within terms* (share of dollars by the due date, with the late buckets), *Typically late by* (weighted days past due
over the late applications, the worst one named), *Invoices* (count, dollars, paid in full, open, credits, $0 count),
*Retainage* (held now, applications paid to retention, reversals) — or *Reversed* when there is no retention.
**By year** (remembered fold): invoiced by invoice year, collected by payment year, applications, pays-in, within-terms
share, 90+ share and the year's grade — the trend. **Every payment**: the project ledger's engine — search (refs,
project, title, description, PO, PM, exact amount), paid-on date range, chips for year / timing / how (wire / check /
card) / division / job, multi-key sortable headings, ten-row own-scroll area that pages 200 rows as you scroll. One row
per invoice application: paid on (bold on the first invoice of a multi-invoice payment; ×N shows how many), payment
reference (links to that day's Payments Received page; unapplied balance flagged), pay total, invoice (DM tag, k/N
when the invoice took several applications; description + PO on hover), invoice date, due date + terms, job (project
link, title, division, state, PM — or the 010 order link), job CV · % billed, invoice amount, applied (discount muted),
left open (paid off / amount / *10 % retention* / *retention release* / *reversal* tags), days to pay, vs due
(early / on the day / N d late; amber > 15, red > 60). The summary line under "Every payment" recomputes for the
filtered set (applications, payments, dollars, pays-in, within-terms share).

**Verified 2026-09-08 (BUL001, Bulley & Andrews):** 27 applications in SL = 27 rows; invoice 213294 dated 2023-01-31,
paid 2023-04-05 → 64 days, 34 days past its 2023-03-02 due date; invoice 217249 $36,493.82 paid $32,844.44 → $3,649.38
left = 10 % retention. Unit tests: `tests/unit/test_customer_payments.py`.

## Billings page (`/finance/billings/`, `views_billings.py`, `analytics/billings.py`, 2026-09-08)
*Plan and the SL findings behind it: `docs/billings_page_plan.md`.* What was billed, day by day: every AR invoice / credit
memo / debit memo issued in a window plus the general-journal revenue posted on jobs, with **who keyed it and when**
(`ARDoc.Crtd_User` / `Crtd_DateTime`, Central; screen `40690` = sales-order invoicing, `08010` = AR Invoice & Memo
entry), **the date on the invoice** (red `+Nd` tag when keyed N days after it — a quarter of all documents, typically
month-end progress billings entered in the first days of the next month), **the fiscal period** it posted into (amber
tag when not the invoice date's month), the job, and the job's live **WIP by Job columns** (division · PM · state ·
PTT % with age and who set it · hours % · contract · billed to date with % of contract · earned · WIP · cost to date ·
GP% sold → EAC · EAC GP, from `finance_wip.wip_job_meta` — today's figures whichever window is shown).

- **Window**: one day (`?day=`, ‹ › over days with a billing), week to date, last 7 / 30 days, any month or year the
  invoice lines cover (~14 months), the current month / year to date, or a custom `?from=&to=`. **Basis** (`basis=`):
  `entered` (default — the day keyed; groups by that day), `dated` (invoice date), `posted` (fiscal period; months /
  years only; groups stay by the day keyed so late entries into a closed month stand out). `?per=YYYYMM` narrows any
  window to documents posted into one period (the Daily Snapshot's "posted on <day> into <month>" split).
- **Numbers**: *Billed* = the document's lines on income accounts (40000 / 40001 / 40300), negative on a credit memo —
  exactly its GL revenue; *Total* = + sales tax (20500) + customer deposits / deferred billings (24000 / 21000, not
  revenue). Both come from `finance_arinvoiceline` (ARTran without the 11000 control line, signed; `kind` rev / tax /
  dep / oth) rolled up onto `finance_arinvoice` (`revenue`, `tax`, `deposits`, `other`, `division`, plus `crtd_user`,
  `crtd_prog`, `per_post`, `batch_nbr`, `so_type`, `task_id`, `slsper_id`, `lupd_*`). Documents older than the 430-day
  lines window show their total as revenue with a *total* tag. **Journals** (`finance_projectfinancialtransaction`,
  `category = revenue`, `system_cd = GL`) are listed as rows tagged *journal* because they are inside every job's
  billed-to-date; journal revenue not posted to any job appears only in the GL tie.
- **Grouping**: day (newest first) · project (group = the job's billing in the window, with its state / PM / billed %
  chips) · person · customer · division; every group folds its **010 hardware orders** (job-less SO1 invoices — 40 a
  day, ~80% of all rows) into one row whose invoices are fetched when the fold is opened (`?hwgroup=<group>|all`, the
  row partial rendered server-side), so the project billings stay readable and a year view stays ~12 MB instead of 27. Show: all / project billings / 010 hardware /
  no job / credits / back-dated (or posted into another period) / journals; filters: division, PM, who keyed it,
  search, min $, `project=` / `cust=` (from links); CSV. Column bands hide / show like the WIP page; expand / collapse
  all and the hardware fold are remembered (`PCA.pref`).
- **Detail panel** (click a row, JSON `finance/billings/doc/`): the lines with the GL account · subaccount each posted
  to (deferred / other tagged), payments applied and the balance (link to the Receivables page when open), keyed /
  edited by whom on which screen and batch, whether the invoice has reached the project ledger (*not on job yet* =
  the AR batch has not been released to the job, so the WIP page's billed-to-date excludes it), the job's other
  billings; journals show the entry's own facts.
- **GL tie** (card): GL 3I except 40100 for the same window and basis from `finance_glrecentposting`, by module — AR
  (= Σ invoice revenue on the page, ✓ to the penny when it matches) and GL journals (with the off-job share); for the
  current month by fiscal period it also says whether it equals the Daily Snapshot's revenue MTD. Available inside the
  95-day posting window only.
- **Links in**: Daily Snapshot revenue figures (→ that fiscal period, `basis=posted`), its "Posted <day>" line and the
  postings-by-day bars (→ that day, `per=` the month); WIP by Job's period *Billed* cell (→ the job, the period).
- Verification 2026-09-08: see the recap in `docs/billings_page_plan.md` §5 and `tests/unit/test_billings.py`.
- Day cutoffs are midnight Central everywhere (Owner, 2026-09-08): the Django database session runs in `America/Chicago`
  (`DATABASES["default"]["TIME_ZONE"]`), so every `sl_created_at::date` in the app — the Daily Snapshot's "posted on the
  previous business day" and postings-by-day chart, the past-day rollback, the `gl` drill's `day=`, the 010 snapshot — is
  the Central day. Before the fix Django pinned the session to UTC and a batch keyed after 7 PM Central counted as the
  next day (687 postings / $1.8M keyed the evening of 2026-08-27 showed under Aug 28; $1.7M of Aug 31 under Sep 1).
  Snapshot rows stored before 2026-09-08 09:30 carry the UTC-day previous-day figures; today's row was rebuilt.

## Sales Tax page (`/finance/sales-tax/`, `views_salestax.py`, `analytics/salestax.py`, superadmin only, 2026-09-08)
*Plan, SL findings and the numbers it surfaced: `docs/sales_tax_page_plan.md`.* **Concealed superadmin-tier page**
(capability `salestax.view`, 404 to everyone else, sidebar link only for holders). Everything behind GL 20500 ACCRUED
SALES TAX, state by state, from two new local tables (docs/05): `finance_salestaxposting` (every posted 20500 line since
2012 — 6,823 — joined at pull to the invoice, the shipper's ZIP-based tax ID / rate / taxable base and the customer's
state and exemption number, then classified by `analytics/salestax_parse.py` into collected · remitted · fee · reclass ·
adjustment · zero with a state and, for remittances, the period covered) and `finance_salestaxrate` (the SL tax master:
41,170 ZIP-level IDs + TAX2 / NONE). Loader `finance_loaders.load_sales_tax`, in both refresh paths.

- **Reconciles**: Σ classified postings per fiscal year = AcctHist net change for every year; the total is the ledger
  balance to the penny ($351,152.97 on 2026-09-08). The per-state balances (collected − remitted − fees + adjustments)
  sum to it except for lines whose state could not be read ("??").
- **Sections**: KPIs (set aside now, collected / remitted 12 months, not yet filed = collected after the last period each
  state's remittance covered, states collecting-but-never-remitted, adjustments) · balance over seven years · collected vs
  remitted by month · by state with flags (click a state: its month-by-month ledger, every remittance, recent invoices) ·
  filing calendar (last period paid, next period, collected since, due-date rule, discount) · remittances (date, state,
  period covered — "?" = inferred as the month before the payment, amount, entry, keyed by) and by-year totals ·
  adjustments and reclasses (the 2024-12-31 "Accrual Adj" moved $270,193 from 20500 to 40000 SALES) · sales into every
  state vs its economic-nexus test (12-month sales by ship-to state; project invoices by customer state) · untaxed
  invoices into states Pace remits to · customers charged the most tax · recent taxed invoices with tax ID / rate /
  taxable base · the rate master with checks (rate applied ≠ master's current rate; flat-Illinois TAX2 shipments to
  other states; 0 % IDs in taxing states) · the state rules reference (`analytics/salestax_rules.py`, all 50 + DC:
  base rate, local rates, nexus test, sourcing, installation labor, contractor treatment, due date, discount, link)
  with Pace's numbers alongside — labelled reference-not-advice.
- **Classification rules** (docs in the parser): AR-module lines are collections (state from the tax ID, else the
  description, else ship-to / customer state); GL debits naming a state and a tax are remittances ("TN SALES TAX MAR";
  the 2013–2021 "RECORD SALES TAX PAYMENT" lines are Illinois, the only state billed then); "REV …" credits reverse
  one; "PROJECT SUBACCT RECLASS" and RECLASS batches that net to zero inside 20500 are reclasses; FEE / PENALTY debits
  are fees; AP vouchers to state agencies are remittances (vendor id → state); everything else is an adjustment.
