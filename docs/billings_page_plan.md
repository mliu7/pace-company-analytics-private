# Billings page — plan (`/finance/billings/`)

*2026-09-08 · requested by Owner from the Daily Financial Snapshot: "a view where I can see precisely what billings occurred for
what projects/invoices and on what days and by whom, and the date attached to those billings … a breakdown BY DAY of what
projects were billed, how much … which project they were attached to, and critical information about the project, as
detailed on the WIP by Job sheet."*

## 1. What the page answers

| Question | Where on the page |
|---|---|
| What was billed today / this week / this month, day by day? | the table, grouped by **day** (default), newest day first |
| Who entered it, and when — and what date did they put on the invoice? | Entered (day + time, by whom) next to Invoice date; back-dated invoices are flagged with the gap in days, and an invoice posted into a different fiscal period than its date carries a period chip |
| Which project, and how is that job doing? | frozen Project column, then the WIP by Job columns for the job (PTT % complete with its age and who set it, hours %, contract, billed to date as a % of contract, earned, WIP, cost to date, GP% sold → EAC, EAC GP) |
| How much of the month's revenue on the Daily Snapshot is invoices, and does it tie? | the reconciliation line: GL revenue for the period = invoices on this page + journal revenue, to the penny inside the posting window |
| What exactly did an invoice bill, and has it been paid? | click the row: invoice lines (description, task, item, qty × price, GL account / subaccount), payments applied, balance, the job's other invoices in the window |

## 2. Data — what SL holds (verified 2026-09-08, read-only)

**Population**: `ARDoc` rows of type IN / CM / DM, released, doc date inside the trailing 430-day window (the same window as the
payments page). 13,884 documents Jul 2025 → Sep 2026.

- **Who**: `ARDoc.Crtd_User` is the SL login of the person who keyed the document. Twelve logins in 14 months; every one
  resolves to an employee by first initial + surname (MBURNETT → Example employee, SHUBBARD → Example employee, MCHAVEZ →
  Example employee …). `LUpd_User` differs from the creator on 144 documents (edits after entry) — shown on hover.
- **Which screen**: `Crtd_Prog` `40690` = sales-order invoicing (shipper release: 11,900 documents, the 010 hardware
  orders and SO2 project material orders — CPLACHY, JCLEMONS, LTHIEL, MCHAVEZ); `08010` = AR Invoice & Memo entry
  (1,700 documents, the project progress billings and credit / debit memos — MBURNETT, SHUBBARD, KKASPER, NTAYLOR, BKASPER,
  MMAGRINI).
- **Three dates**: `Crtd_DateTime` (when it was keyed — the day the billing *occurred*), `DocDate` (the date on the
  invoice), `PerPost` (the fiscal period it posted into). 10,536 documents were keyed on their invoice date, **3,344 were
  keyed later** (progress billings dated the last day of the month and entered in the first days of the next — e.g. eleven
  Aug-31 invoices keyed by MBURNETT on Sep 4), 4 earlier; 77 posted into a period other than their date's month.
- **Project**: `ARDoc.ProjectID` (header). 7,339 documents carry one; the rest are 010 hardware orders (SO1: 7,258 in the
  window, 930 with a project) and a handful of non-job invoices. Line-level `ARTran.ProjectID` never adds a project the
  header lacks (0 cases); 7 invoices split lines across two projects — the detail panel shows lines with their own project.
- **Lines** (`ARTran`, 59,584 rows for the window): one 11000 AR-control line per document (dropped), 15,611 sales-tax
  lines (20500), and revenue lines on 40000 SALES / 40001 SALES-MBE / 40300 FREIGHT (3I). A few lines hit 24000 / 21000
  CUSTOMER DEPOSITS (help-desk and staffing contracts billed ahead of service — $5.5M in the window, **not revenue**),
  61200 bank charges, 50730 rebates. **Credit memos store their lines positive**; revenue on a CM is the negative.
- **GL tie**: every 3I posting from the AR module (journal types AR and OM) carries the invoice's `RefNbr`; per invoice,
  Σ signed 3I lines = the GL revenue posted (11,469 / 11,469 invoices since July 2026 once CM lines are negated). The rest
  of GL revenue is `GL/GJ` journals ($5.0M in 15 months: deferred-revenue releases, reclasses, the −$110,927 adjustment).
- **Project ledger**: `PJTran` REVENUE rows for AR documents carry `voucher_num` = the invoice `RefNbr` (23,489 / 23,489
  join) and are created when the AR batch is released to the project ledger — days after the invoice on occasion (invoice
  218520 keyed Sep 4, on the project ledger Sep 8). 334 GL-journal revenue rows on projects in 14 months ($3.7M) are part
  of the jobs' "billed" and appear on the page as journals. 121 tax lines post to a project's REVENUE account with a
  non-zero amount ($55K in 14 months) — the only reason a job's ledger "billed" can exceed its invoices' revenue.

## 3. Local data

- `finance_arinvoice` (extended): + `crtd_user`, `crtd_prog`, `per_post`, `batch_nbr`, `task_id`, `due_date`, `terms`,
  `slsper_id`, `cust_po`, `lupd_user`, `lupd_at`, `so_type` (SOHeader), and the per-document line roll-up **signed**
  (negative on credit memos): `revenue` (3I lines), `tax` (205xx), `deposits` (21000 / 24000), `other`, plus `division`
  (the job's division; SO1 → 010; else the subaccount prefix of the largest revenue line). `amount` / `balance` keep
  SL's sign convention (positive on a CM) so the AR page and Project Snapshot are unchanged.
- `finance_arinvoiceline` (new): every line of those documents except the AR-control line, amount signed, with a `kind`
  (`rev` / `tax` / `dep` / `oth`), GL account + subaccount, project / task, item, qty × price, shipper and order.
  Full replace with the finance refresh (~46k rows, ~3 s).
- Journal revenue on jobs: `finance_projectfinancialtransaction` (`category = 'revenue'`, `system_cd = 'GL'`) — already
  loaded.
- GL revenue for the tie: `finance_glrecentposting` (3I except 40100, 95-day window, by module).

## 4. Page structure

```
Billings                                     [‹ day ›] [period ▾ day · week · month · year · range]  basis: entered | invoice date | fiscal period
[KPIs]      Billed (revenue, net of credits) · Invoice totals (incl. tax / deposits) · Project billings (jobs · customers)
            · 010 hardware · Back-dated (n · $) · GL tie
[Controls]  Group by: Day | Project | Person | Customer | Division   Show: All | Project billings | 010 hardware | Credits | Back-dated | Journals
            Division · PM · Entered by · Search · min $      Expand / Collapse all · CSV
[Table]     group row (label · n · Σ · chips) → invoice rows (frozen Invoice + Project columns, then bands:
            Entry · Job · Progress (PTT) · WIP · Profitability) → click a row for the detail panel
[Footer]    totals for the filtered set
```

- **Day basis** (`basis=`): `entered` (default — the day the billing occurred; Central time, like every day cutoff in the app since the session time zone fix of 2026-09-08), `dated` (the invoice date),
  `posted` (fiscal period, month / year periods only — the basis that ties to the Daily Snapshot's revenue). Groups
  are always by the entry day under `entered` / `posted` and by invoice date under `dated`.
- **010 hardware orders** (SO1 without a project) dominate the count — 40 a day. Every group folds them into one
  "010 hardware orders · n · $" row whose invoices are fetched when the fold is opened (`?hwgroup=`), so the project
  billings stay readable, nothing is hidden, and a year view does not ship 10,000 folded rows.
- **Job columns are today's figures** (the same rows as the live WIP by Job page — `finance_wip.wip_job_meta`), whichever
  period is shown; the header says so.
- Column bands hide / show like the WIP page (chips, remembered); expand / collapse all and the hardware fold are
  remembered (`PCA.pref`).
- Links in: the Daily Snapshot's revenue figures (→ the fiscal period, `basis=posted`), its "Posted <day>" line and the
  postings-by-day bars (→ that day, `basis=entered`, `per=` the period); the WIP by Job "Billed" column (→ the job and
  period). Links out: project, customer, PM / person pages, the 010 order page, the Receivables page for an open balance.

## 5. Verification

- Unfiltered `basis=posted` for a month inside the posting window: invoices + journals = GL 3I (ex 40100) to the penny;
  for the current month that is the Daily Snapshot's revenue MTD (at its pull time).
- Per job, `basis=posted` for a closed month: Σ invoices + journals = the WIP by Job page's "Billed" for that month,
  up to the tax-to-REVENUE lines noted above.
- Unit tests: line classification and signing, division rule, period resolution, login → name matching, back-dated flag.

## 6. Verification (2026-09-08, after the first refresh with the new loader)

| Check | Result |
|---|---|
| Roll-up coverage | 13,788 documents in the 430-day window carry lines / revenue / tax / deposits; 45,403 lines (Σ rev $78.19M · tax $0.47M · deposits $3.28M · other $0.08M); every one of the 101,036 invoices since 2013 carries its keyer, screen and period |
| Who keyed what (14 months) | CPLACHY 9,747 (40690) · JCLEMONS 1,720 (40690) · MBURNETT 858 (08010) · LTHIEL 556 · SHUBBARD 396 · BKASPER 233 (credits, −$219K) · KKASPER 114 · MCHAVEZ 65 · NTAYLOR 43 · MMAGRINI 21 |
| August 2026 by fiscal period | Σ invoice revenue $11,014,248.89 = GL AR-module revenue $11,014,248.89 (exact); journals $56,383.56 of which on jobs $55,914.37 (15 entries); invoices + journals = $11,070,632.45 = the Daily Snapshot's August revenue (exact) |
| Sep 4 keyed (Central) | local = SL per person: CPLACHY 16 / $36,023.83 · MBURNETT 15 / $37,957.17 · JCLEMONS 3 / $65,970.00 · KKASPER 1 / $1,749.88 |
| Unit tests | `tests/unit/test_billings.py` 11 tests; whole unit suite 218 OK; access sentinel suite 48 OK |
| Pages | every variant (day / month / year / range / posted / dated / each grouping / CSV / doc JSON) renders in < 0.3 s |
