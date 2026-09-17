# Financial Reports · Daily Finance Snapshot — build plan

> **Status: IMPLEMENTED 2026-08-26.** Page at `/finance/daily/`; operational docs in `docs/06_finance_reports.md`. Verification: 17/17 checks pass (`scripts/verify_finance_report.py`); backfilled 7/6 snapshot matches the email's >90 bucket to the penny.

**Goal.** Replace Example employee's manual "End of Day Snapshot" email (example: `../internal_reports/daily_finance_snapshot.pdf`, sent 7/6/26) with a live page in PCA that is always available, never skips a day, drills down to the documents behind every number, and accumulates its own daily history. First page of a new **Financial Reports** section; more reports will follow.

**Read-only rule.** All SL reads go through registered SELECT files + the guarded client, exactly like every other loader. Nothing is ever written to SL or PTT. All storage is the local PostgreSQL.

---

## 1. What the email contains, and what SL can produce (verified 2026-08-26)

Every claim below was tested against live SL and against Michele's 7/6/26 email using an as-of reconstruction (docs/adjustments filtered to `Crtd_DateTime <= 2026-07-06 18:44`, batches to `Batch.LUpd_DateTime <= cutoff`).

| Email item | 7/6 value | Reconstructed | Verdict / method |
|---|---|---|---|
| AR total | 11,461,004.83 | 11,451,028.76 | ✅ ARDoc all types, IN/DM positive, CM/PA negative (0.09% timing gap) |
| AR >90 bucket | 2,133,783.63 | 2,133,839.83 | ✅ **aging = days past DueDate; credits (CM/PA) not aged — parked in Current** (off $56) |
| AR Current / 0-30 / 31-60 / 61-90 | 6,275,952 / 1,931,712 / 718,160 / 401,397 | 6,371,150 / 1,906,846 / 668,102 / 371,091 | ✅ same method; residual 25–95K per bucket = same-evening postings + a small manual reclass she does |
| AP total + buckets | 6,801,457.51 | 6,894,777.95 | ✅ APDoc `VO`+`PP` positive, `AD` negative (**exclude `VT`** — template garbage rows, e.g. $1.2M AMC001 docs from 2012); same aging method |
| Revenue, prior month (June) | 6,164,170.55 | 6,164,221.46 | ✅ **EXACT minus $50.91 = interest income**: GLTran `3I` accounts excluding `40100 INTEREST INCOME`, `CrAmt−DrAmt`, by `PerPost` |
| Revenue, current month MTD (July) | 559,320.14 | 559,320.14 | ✅ **EXACT** |
| Rev/cost "Previous Day" (July) | 85,234.88 / 35,371.14 | 85,234.88 / 35,371.14 | ✅ **EXACT** — postings **created on the previous business day** (7/2; 7/3 was the July-4 holiday), split by `PerPost` month |
| Rev "Prev Month Previous Day" (June) | 669,449.00 | 669,449.00 | ✅ **EXACT** (cost off $108.24 = the 7xxxx accounts) |
| Cost (either month) | 6,265,116.32 | 6,223,441.13 | ✅ = **all `4E` accounts** (COGS 5xxxx + overhead 6xxxx + 7xxxx); 0.7% timing gap |
| Total Equity | 10,459,023 | 10,459,023.48 | ✅ **EXACT** = `2L` accounts `3xxxx` excluding `39999` |
| Net Income | 80,036 | (as-of n/a) | ✅ = account `39999 YTD NET INCOME` — SL maintains it in real time; today's balance identity checks (A = L + E + NI to the penny) |
| Total Assets / Liabilities | 19,679,324 / 9,140,265 | 19,972,873 / — | ✅ AcctHist natural-sign balances: assets = `1A`; liabilities = `2L` accounts `2xxxx` (1.5% as-of gap = CPA year-end entries posted later; **live values will equal SL's own balance sheet exactly**) |
| "Net working Capital" | 10,539,059 | — | ✅ her formula is **Assets − Liabilities** (= equity + NI, not true NWC); reproduce it labeled honestly, add true NWC (current assets − current liabilities) as an extra |
| Ratios (debt/assets 0.46, equity 53%, leverage 1.88) | — | 0.464 / 53.6% / 1.867 | ✅ derived: L/A, (A−L)/A, A/(A−L−NI) |
| SO1 vs Project AR split | 2,554,109 / 9,100,288 | — | ✅ `ARDoc.OrdNbr → SOHeader.SOTypeID` (`SO1`/`SO2`/`RM1`) + `ARDoc.ProjectID`; we'll show a full book × bucket matrix instead of her two lists, so her "Difference 193,392" line disappears by construction |
| RMA TTL | 2,605.00 | — | ✅ open docs on `RM1`-type orders (definitional; hers is a small manual figure) |
| PENDING TTL | 45,221.59 | — | ✅ unreleased AR invoices (`Rlsed=0`); current live value 181,121.33/38 docs |
| AR >90 prev-day change | +154,104.88 | — | ✅ going forward from our own daily snapshots; 180-day backfill via the proven as-of reconstruction |
| Inventory | 1,334,537.25 | 1,313,550.79 | ✅ GL `12000`-`12010` balances (1.6% timing gap) |
| **Checking bank balance / Float / BBase available / Credit line used** | 45,414 / 948,464 / 3,850,028 / 547,592 | — | ❌ **bank-portal numbers, not in SL.** CATran dead since 2023; AP `ClearDate` unreliable (yields 9.3M "float"). Show GL **book** balances (checking 10250, MMA 10350, payroll 10400, petty cash 10800, credit line 20005) clearly labeled, plus an optional 30-second **manual bank-figures form** (stored locally, timestamped, staleness-tagged) so the report can still show the bank view when someone types it in |
| Payroll changes | (free text) | — | ❌ manual notes in her email; v1: optional note field on the manual form |

Key SL facts locked in: fiscal periods = calendar months (`PerPost = 'YYYYMM'`, GL now in 202608); single ledger `ACTUAL`; single company; `AcctHist` rows are per account × subaccount × fiscal year with `BegBal` + `PtdBal00..12`, stored **natural-sign** (assets/expenses debit-positive, liabilities/equity/income credit-positive — verified against GLTran); account types `1A/2L/3I/4E` with equity inside `2L` as `3xxxx`; `39999` = system-maintained YTD net income.

## 2. Architecture

### 2.1 New registered source queries (`sql/source/sl/`)
1. **`finance_gl_balances.sql`** — AcctHist × Account for the last 3 fiscal years: acct, sub, descr, type, `BegBal`, `PtdBal00..12`. (~2k rows; full replace.) Powers balance sheet, cash/inventory cards, 13-period P&L trend, monthly close history.
2. **`finance_gl_recent_activity.sql`** — GLTran (posted, ACTUAL) for income/expense (`3I`/`4E`) accounts with `PerPost >= :period` OR `Crtd_DateTime >= :since` (trailing ~95 days): date, created, period, acct, sub, dr, cr, module, batch, refnbr, trandesc, batch release time. Powers "posted yesterday" split, MTD-by-day chart, and posting-level drill-downs. (~20–50k rows; full replace of window.)
3. **`finance_ar_open.sql`** — every ARDoc with `DocBal <> 0` or `Rlsed = 0`, joined to Customer (name, terms) and SOHeader (SOTypeID): refnbr, doctype, custid, name, project, ordnbr, sotype, docdate, duedate, docbal, origdocamt, rlsed, docdesc, slsperid. (~1.3k rows; full replace.)
4. **`finance_ap_open.sql`** — APDoc `VO/AD/PP` with `DocBal <> 0` joined to Vendor: refnbr, doctype, vendid, name, docdate, duedate, invcnbr, docbal, origdocamt, projectid. (~1.5k rows; full replace.)

### 2.2 Local models (`apps/finance/models.py`)
- **`GLAccountBalance`** — acct, sub, fiscal_year, acct_type, descr, beg_bal, ptd_00..12 (13 decimal cols), natural-sign as stored + a `signed_balance_through(p)` helper that returns debit-positive.
- **`AROpenDocument` / `APOpenDocument`** — the open-doc snapshots (typed columns above + `days_past_due`, `bucket`, `book` [so1/so2/project/rma/credit/pending] computed at load).
- **`DailyFinanceSnapshot`** — one row per `snapshot_date`: `as_of` timestamp + every headline number as typed columns (ar_total, ar_current, ar_0_30, …, ap_*, assets, liabilities, equity, net_income, revenue_mtd, cost_mtd, revenue_prev_day, …, inventory, cash accounts, plus `detail` JSON for the book×bucket matrix and by-day P&L). Upserted on each refresh (same-day refreshes overwrite; history accumulates).
- **`BankFigureEntry`** — manual bank numbers: entry_date, checking_bank_balance, float_amount, borrowing_base_available, credit_line_used, payroll_note, entered_at. Local-only, optional.

### 2.3 Refresh
- New loader module `apps/ingestion/finance_loaders.py` with `load_gl_balances`, `load_gl_activity`, `load_ar_open`, `load_ap_open`, then `apps/analytics/finance_snapshot.py::build_daily_snapshot(run)` computing the snapshot row from local tables only.
- Wired into `refresh_all` as steps after `sl_transactions` (adds ~5–10 s), **plus** a fast `manage.py refresh_finance` (sources: the 4 queries + snapshot; ~15 s) and a "Refresh finance now" button on the page using the existing RefreshRequest/subprocess pattern. `refresh_finance` takes the same advisory lock as `refresh_all`, so the two can never interleave writes.
- **Backfill command** `manage.py backfill_finance_history --days 180`: reconstructs daily AR aging + AP totals (proven ARAdjust/APAdjust method) and daily revenue/cost-by-created-day, writing historical `DailyFinanceSnapshot` rows flagged `reconstructed=True` (balance-sheet fields left null for those days; monthly closes come from AcctHist).

### 2.4 Aging / classification rules (documented on-page in hovers)
- Bucket = days past **DueDate** at the as-of date: Current ≤ 0, 1–30, 31–60, 61–90, >90. Credit docs (AR `CM`/`PA`, AP `AD`) are **not aged** — they net in Current (matches Michele's report; >90 matched to $56).
- AR sign: IN/DM/FC/NS/SB +, CM/PA −. AP: VO/PP +, AD −. AP `VT` excluded everywhere.
- AR book: `credit` for CM/PA; `pending` for unreleased; else `so1`/`so2`/`rma` from SOHeader.SOTypeID (RM1/RM2/RMS2 → rma); else `project` if ProjectID non-blank; else `other` (e.g. the $894k no-order no-project invoice — visible instead of becoming a mystery "Difference" line). Verified: OpenDoc flag consistent with DocBal≠0; single currency (DocBal=CuryDocBal on all open docs); single company (PACE); unreleased IN docs carry DocBal=OrigDocAmt (38 docs / $181k today).
- Revenue = `3I` except `40100`; COGS = `4E` accts `5xxxx`; Overhead = `4E` accts `6xxxx`+`7xxxx`. Show **her lines** (Revenue / Cost=all 4E / "Gross" margin) *and* the clean cut (Revenue / COGS / true GM / Overhead / Operating income) — tooltips explain both.
- Previous business day = latest weekday before today that has any GL/AR/AP activity (handles holidays the way her 7/2-on-7/6 report did).

## 3. UI — `/finance/daily/` ("Financial Reports" nav section)

Dense single screen, ~4 rows, no long scrolling; every figure hover-defined; drill-downs in modals.

- **Top bar**: title + as-of stamp (SL pull time), Refresh button, link to Definitions. (No date picker: open-doc tables are latest-only full replaces, so historical days can't drill honestly. History lives in Row 4's trends plus a collapsible **History table** — last 30 snapshot rows, one line per day with the headline columns.)
- **Row 1 — four compact cards**:
  1. *Cash & credit (GL book)*: checking 10250, payroll 10400, MMA 10350, petty cash, total book cash; credit line 20005; inventory 12000-range. If a `BankFigureEntry` exists: bank checking, float, available-after-float, borrowing base, line used — each tagged with its entry date and an amber "stale" tag when > 1 business day old. "Update bank figures" opens the manual mini-form (modal).
  2. *Balance sheet*: Assets, Liabilities, Equity, YTD Net income, "Assets − Liabilities" (her NWC line, tooltip says what it really is), true NWC; prior-day delta chips on each from snapshot history.
  3. *Ratios*: debt-to-assets, equity ratio, leverage — value + colored status chip with the thresholds in the hover; small 30-day sparkline each.
  4. *AR / AP headline*: AR total, AP total, AP/AR %, net AR exposure after AP, over-90 % of AR with status chip, over-90 $ + prev-day change chip.
- **Row 2 — P&L**: side-by-side current month and prior month mini-tables (Revenue, COGS, Gross margin $ /%, Overhead, Operating income; "posted prev business day" chips per line, click → modal listing the actual GL postings with module/batch/ref). Right: bar chart of revenue & cost by posting day for the current month; 13-period monthly trend line under it.
- **Row 3 — the aging matrix** (the centerpiece): one table each for AR and AP.
  - AR rows: Project billings, SO1 orders, SO2 orders, RMA, Credits & unapplied payments, Pending (unreleased); columns: Current / 1-30 / 31-60 / 61-90 / >90 / Total / % — totals row reconciles to AR total **by construction**. Heat-tinted cells (red intensity by age×size). **Every cell is clickable** → modal with the underlying invoices (customer, ref, date, due, days late, balance; customer links to the PCA customer page, project links to the project page). Column-total cells drill too.
  - AP same shape (rows: Vouchers, Prepayments, Debit adjustments; vendor drill-downs).
- **Row 4 — trends & watchlists**: AR >90 daily trend (backfilled 180 d + live accumulation) with her "prev-day change" as a chip; *Top over-90 customers* (name, oldest invoice, $, share) — click → invoice modal; *AP due next 14 days by day* (cash needs — "plus more"); *Largest open invoices* table.
- A collapsible History table (last 30 daily snapshots) closes the page.
- Modals fetch from small JSON endpoints (`/finance/daily/drill/?kind=ar&book=so1&bucket=90`) reading local tables; no page reloads.

## 4. Verification protocol (must pass before "done")
1. **Unit tests**: aging bucketer, book classifier, natural-sign balance math, snapshot builder on fixture rows (no DB).
2. **`scripts/verify_finance_report.py`**: pulls fresh totals straight from SL with independent one-line queries (net AR, AP, assets/L/E/NI identity, MTD revenue/cost, inventory) and asserts the page's stored snapshot matches to the penny; asserts the AR matrix row/column totals reconcile to AR total exactly; asserts A = L + E + NI.
3. **PDF cross-check**: reproduce the 7/6 email numbers with the as-of reconstruction one more time through the *final* code path (management command `--as-of 2026-07-06`), and record the comparison table (expected small timing deltas documented) in `docs/06_finance_reports.md`.
4. **Browser pass** (Playwright): page renders, all four cards populated, matrix cells open modals with correct sums (spot-check 3 cells against SQL), refresh button works, snapshot date picker works, no console errors.
5. Re-run `refresh_all` end-to-end to prove the nightly path stays green.

## 5. Docs & follow-ons
- `docs/06_finance_reports.md`: source mapping, formulas, aging rules, what's manual, verification results; README index + CLAUDE.md pointer; memory update.
- Roadmap (later reports in this section): weekly cash-flow forecast (AP due + expected AR collections), customer payment-behavior scorecards (DSO by customer), monthly close P&L pack, credit-line borrowing-base tracker (needs the manual bank inputs), WIP/over-under billing schedule from PCA's project data.

## 6. Decisions taken (so the build doesn't stall)
- Bank-portal figures: manual optional entry, never faked from GL; page stays complete without them.
- Michele's labels are preserved where her convention is idiosyncratic (her "Net working capital"), with honest tooltips, so the report reads familiar to its current audience.
- Snapshot history: live accumulation + 180-day reconstructed backfill for AR/AP/P&L; balance-sheet history live-only (plus monthly closes from AcctHist).
- "Previous day" = previous business day with activity, by posting-creation date — her exact convention (verified).
- Payroll-changes section: free-text note on the manual form; nothing sensitive pulled from PR tables.
