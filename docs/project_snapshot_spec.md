# Daily / Weekly Project Snapshot — full spec & implementation plan

**Status: IMPLEMENTED 2026-08-28** — `/projects/snapshot/` (nav "Project Snapshot"), registered in
the access registry as `project_snapshot` (projects.view + margins.view; AP-paid section
additionally gated on `acc.finance`; NO field ratings anywhere). Implementation notes vs spec:
PO/receipt line detail came straight from SL's live purchasing tables (PurchOrd/PurOrdDet/POTran
type R — investigation I1 resolved, no fallback needed); internal buckets (269999* warehouse/admin)
show hours but are excluded from earned/margin economics; company-wide is the true default
(explicit ?div= filters); window math unit-tested (test_snapshot_windows). Verified: page hours ==
raw PTT (368.5h/24 jobs on 8/26), Δearned ties to snapshot WIP detail within timing noise, billing
== ARDoc issue-dated invoices (larger than PJTran revenue by exactly the order-based invoices that
never touch projects — by design), cash == Payments page data. 132 tests green incl. access suite.

## 1. Purpose & audience

One page, two time grains, answering: *"What happened on our jobs this day / this week — who worked
where, did the work pay for itself, are those jobs healthy, and what money/material moved?"*

- **Audience**: Owner + execs now; division heads very soon; PMs after that. Profitability is shown
  openly. **Field-crew ratings NEVER appear on this page** (Owner-only; the page will be shared).
  No `EntityRating` queries anywhere in this view.
- **Filters**: division (or all — company-wide is the default) **and Project Manager** (so PMs can
  see just their book). Both remembered per the app's localStorage filter memory.
- **Views**: `?view=day` (default) and `?view=week`, toggle buttons on the page.

## 2. Time semantics (exact rules)

- **Day view**: business days only, **Friday's page covers Fri + Sat + Sun** (weekend PTT entries,
  postings, receipts roll into Friday's bucket). Requesting a Sat/Sun date normalizes to that
  week's Friday. Default = latest business day with any activity. Navigation: ← previous / next →
  business day **with activity** (same pattern as Payments Received), plus a date picker and a
  small 3-week activity strip (mini bars of field hours per day; click to jump). Nav layout:
  the ← / → arrows flank the window label itself ("← Week of Aug 24 – Aug 30, 2026 →").
- **Week view**: **calendar weeks Mon–Sun**. Week key = the Monday (`?week=YYYY-MM-DD`, normalized
  to Monday). Label "Week of Mon Aug 24 – Sun Aug 30". Navigation: ← / → week, "this week"
  shortcut, plus a 12-week activity strip. Day and week views cross-link (a day page links to its
  containing week and vice versa).
- All windows are date ranges `[start, end]` internally; every section takes the same window, so
  day and week are one code path with different bounds.

## 3. Page sections (shared by both views)

### 3.1 Header KPI strip (one row, dense)
| KPI | Definition |
|---|---|
| Field hours | Σ PTT hours (onsite+OT+offsite) in window, filtered set |
| People / jobs | distinct workers, distinct jobs touched |
| Earned value moved | Σ per-job Δearned = CV × Δ%complete over the window (workbook WIP basis; equals ΔWIP + Δbilled) |
| Est. labor cost | Σ hours × person loaded rate (13-week loaded rate machinery from `eac._rate_tables`, division fallback) — *estimated*, real-time, no payroll-posting lag |
| Work margin (a) | Earned value moved − est. labor cost, colored; tooltip explains noise (PMs update % sporadically — day view especially) |
| Billing out | Σ invoices issued in window (AR) with big-ticket count |
| Cash in | Σ payments applied to jobs in window (links to Payments Received) |
| AP paid out | Σ AP checks cut in window with big-ticket count |
| Material ordered / received | Σ new PO value / Σ receipts in window |

### 3.2 Job activity (the centerpiece — REWORKED 2026-08-28: one merged table)
"Jobs worked" and "Progress updates & job spend" are now ONE table (`#jobact`): every open,
non-internal job with a % update, field hours, or posted non-labor cost in the window. Header
filter toggles **Worked** (hours in window) vs **All activity** (default; remembered in
localStorage `pca-snap-jobfilter`); two totals rows (worked / all). Columns: Job (crew drill
kept — click a row for each person's hours + PTT notes) · PM · Updated by (or "no % update") ·
% complete (old → new, the clear format) · Δ earned · Hours · Est. labor · Other costs ·
**Net +/− (default sort: SIGNED descending — winners top, losers bottom, never absolute)** ·
**GP% sold → EAC** (one column since 2026-09-01: sold GP% muted, EAC GP% bold, green/red by the
direction vs sold; the points difference and the 7/30-day EAC movement live in the hover — the old
"EAC GP%", "vs sold" and "Trend" columns were wordy) · Risk. All headers click-sortable (group rows
carry data-v sort values; crew rows travel with their job); headers are sticky (wrapper scrolls,
max-height 80vh). **Footer (2026-09-01)**: Jobs worked total · All activity total · Overhead
buckets (hours, est. cost, −cost as net) · **Work margin** — the last row equals the KPI exactly;
the overhead and margin rows hide with the Worked filter.

**WIP view (2026-09-08).** The card has two layouts, **Activity** (above) and **WIP view**, toggled by the buttons in the
heading and remembered (`PCA.pref('jobview')`). The WIP view is the shared job table (`dashboard/job_table.window_table`,
docs/06 §WIP by job) over the same rows and the same window: Job band = Project · Div · **PM** · **Updated by** · State;
Progress (PTT) with % complete *old → new* over the window and the age of the last PTT touch; WIP = earned − billed at the
window end (live figures when the window reaches today, else the population as of the window end via `wip_rows_as_of`)
with **Δ WIP since the day before the window** (`jobs_at` baseline — stored snapshot or reconstructed); Period result =
Billed · Cost (posted by transaction date inside the window) · GP · **Adj. GP = GP + Δ WIP**; Profitability. Jobs outside
the WIP population at the window end (service / T&M, closed, no contract value) carry zero WIP and show plain GP. The
crew drill (click a job → each person's hours, overtime bolt, days, work types and latest PTT note) works in both
layouts, as do Worked / All activity (footer totals per set), Collapse / Expand, and the column-band chips. Default order
= the Activity table's: the job's result (Adj. GP), signed, winners top; every header sorts (querystring `sort`, as on the
WIP page). Note the WIP view's *Cost* is posted labor + material (GL, lags payroll) where the Activity table's *Est. labor*
prices PTT hours — the two nets differ by design; hover the headers.

**Updated by (2026-09-08, second pass).** The WIP view's *Updated by* and *State* columns are narrow (name truncated to
~80 px like PM, full text on hover; State as a compact tag — `state_short` — with the full label on hover); *State* is a
column group of its own, so the chips above the table (and a click on the band) can hide it (shown by default,
remembered). Updater names link to the person's page, which gained a **PTT progress updates** card (`views._ptt_updates_by`):
every % complete save that person made on any job — when, the job, the % in force the day before → the % saved, Δ points,
Δ earned, the remaining hours they revised that day — 12 / 24 months / all, newest first. The filter bar has an
**Updated by** multi-select (`details.msel`, checkboxes with counts, plus *no % update*; `?upd=KEY&upd=KEY`, `none`), kept
through the day / week navigation. It narrows the Job activity table — both layouts and their Worked / All footers — while
the KPIs, flags, money sections and the Overhead / Work-margin footer rows stay window-wide (those two rows hide while the
filter is on, so the table never shows a "margin" for a subset).

#### (superseded original spec below)
One row per **billable** job with hours in the window (9999 internal buckets are split out into
their own "Overhead buckets" section — grouped by bucket job (division-tagged), expandable to per-person hours/notes, with est. loaded
cost and latest note; the Field-hours KPI still counts them so it ties to raw PTT); columns:
- Job (link) · Div · PM (link) · Customer (link)
- **Hours** (window) · crew size
- **Δ%** complete in window (`—` badge "no % update" when zero — makes red economics explainable)
- **Δ earned $** (CV × Δ%) · **Est. labor $** (window) · **Window margin (a)** (Δearned − est. labor, colored)
- **Job health (b)**: % complete · EAC GP% now · vs sold (pts) · **trend arrow** (EAC GP% vs 7 days ago and 30 days ago from `analytics_projectprediction` history; ▲▼ with pts in tooltip; history exists from ~Aug 17 2026 onward — older windows degrade to "—")
- Risk tag (existing risk_level)
- Flags: OT-heavy, lone worker, hours-over-budget pace
- **Expand (per row, same-table detail rows like Payments Received)**: the crew — person (link),
  hours, OT flag, PTT work-type/system, and the day's activity notes (PTT notes are gold — show
  truncated with full text in hover); plus job billing/cash/material events in the window.

### 3.3 People & exceptions
- Exceptions strip (chips with counts, each expandable to the list):
  **Big OT** (person-day OT > 2h or total > 10h) · **Spread thin** (person on 3+ jobs in a day) ·
  **No hours logged** (PTT-active field employees with zero hours in window — the daily
  where-is-everybody signal) · **Lone worker** (job worked by exactly one person).
- The full roster lives inside §3.2's expansions (grouped by job); an alternate "by person"
  ordering toggle re-groups the same data (person → jobs, hours) for "who is where" scanning.

### 3.4 Money on jobs (three compact tables, each with totals + big-ticket highlighting)
- **Billing out**: invoices issued in window — invoice ref, job (link), customer, amount, big flag.
  Source: new `finance_arinvoice` window table (ARDoc IN/CM by DocDate incl. already-paid ones —
  the existing open-docs table can't see same-week paid invoices).
- **Cash in**: payment applications in window with project links (reuse `ARPaymentApplication`);
  per-job rollup, link to the Payments Received page for the day.
- **AP paid out**: checks/EFTs cut in window (payments drawn on the card holding account are tagged *card* and kept
  out of the cash total — docs/06 Vendors) — vendor (links to its vendor page), invoice-date
  range, **Held** ($-weighted days from vendor invoice date to check date, ! when a voucher was paid
  UNDER 30 days from its invoice — paying before it's necessary, per Owner), amount, big flag; each check expands to the vouchers it paid
  (`finance_apcheckapplication` = APAdjust + voucher APDoc: invoice nbr/date, due date, days
  held). Vendor pages: `/finance/vendors/` index + `/finance/vendors/<id>/` detail (master info
  from `finance_slvendor`, paid-12mo, avg days to pay, paid-late share, monthly bars, open AP,
  open PO lines, receipts, top items) — finance.view capability. Material line items render
  item + full description across the first columns (job cell only when a PO/receipt mixes jobs).

### 3.5 Equipment / material
- **Ordered**: new PO lines with po_date in window — per-job rollup rows expanding to line items
  (vendor, item id + description, qty, unit cost, extended $, job link). Big-PO highlight.
- **Received**: receipts in window — same shape (receipt date, vendor, item, qty, $, job).
- Source note (I1 resolved): SL's live purchasing tables (`PurchOrd`/`PurOrdDet`/`POTran` type R)
  carry full line detail, receipts fresh daily. **Deduced job/customer ties (2026-08-28)**: most
  warehouse ("stock") PO/receipt lines ARE job- or customer-bound — Pace cuts POs against
  sales-order demand without SL's SO→PO link fields (PurOrdDet.SOOrdNbr always blank), e.g.
  rcpt 089210 / PO 070104 = 1,055 HP EliteDesks for SO ORD0052853 → job 264888. The loader
  (`_deduce_material_ties`, new pull `sl.so_demand_lines`) matches blank-project lines to SOLine
  demand per item within [−270d, +14d]: unique real project → job tie ('so'); exact-qty tiebreak
  ('so_qty'); else unique customer incl. no-project resale orders (`SOLine.ProjectID` 'ZZ'/blank
  = ChannelOnline product sale, quote in SOHeader.User2) → customer tie ('so_cust'). Validated
  against lines that DO carry a project: 94.8% of decided matches agree, 98.8% by dollars.
  Receipts since Jul 1: $5.8M SL-tied, $1.7M deduced-job, $3.2M deduced-customer, $1.9M true
  stock. UI shows deduced ties dashed with an ≈ prefix and tooltip — never as SL fact; div/PM
  filters honor them.

### 3.6 Progress updates & job spend (window accountability)
Every open, non-internal job with a % update, field hours, **or posted non-labor cost** in the
window, in one table: who updated the % (a "no % update" tag when spend landed without one, an
"(unchanged)" marker when a PM re-saved the same %), old → new %, Δearned $, hours, est. labor $,
**other costs** (SL postings dated in the window: material incl. purchase variance, subcontract,
other direct — labor postings excluded since est. labor covers labor), and **net = Δearned −
est. labor − other costs**, with a totals row. Sorted by |net|. Zero-hour note-only PTT entries
and unchanged re-saves with no spend are filtered out. Δ% from `PercentCompleteObservation`
validity windows. This is the WIP-movement accountability view: a big negative net beside
"no % update" usually means a stale estimate, not a loss (tooltip says so).

### 3.7 Big-ticket thresholds
Constants with tooltips (v1, config later if wanted): invoice ≥ $25k · payment in ≥ $25k ·
AP check ≥ $25k · PO ≥ $10k · receipt ≥ $10k. Highlight = amber row tint + tag.

## 4. Definitions (exact, for tooltips and docs/07 consistency)
- **Δ% complete**: pct(end) − pct(start) from PercentCompleteObservation piecewise validity
  windows (same machinery as WIP history; never hold-backward inside the window).
- **Δ earned** = CV × Δ%. Window WIP change = Δearned − Δbilled; both shown where relevant so the
  page ties to the Daily Financial Snapshot's WIP strip.
- **Est. labor cost** = Σ person-hours × person loaded rate (13-week wage+tax(+fringe for union)
  from `eac._rate_tables`; division rolling rate fallback; $90 default) — labeled *estimated*.
- **Work margin (a)** (REDEFINED 2026-08-28, per Owner): the Job-activity net across EVERY job
  with window activity — Σ Δ% × CV − est. labor − other posted costs — **minus overhead-bucket
  labor cost**. Shown as the last row of the Job-activity footer (All activity − Overhead buckets =
  Work margin) so the KPI and the table always agree. With no
  % update, a job's spend earns nothing on record (Δ earned shows an em-dash). Honest caveats in tooltip: excludes
  material/sub consumed; % updates are lumpy (weekly grain is the fair read; daily is noisy).
- **Trend (b)** = EAC GP% (latest prediction in/before window end) minus EAC GP% at −7d and −30d.

## 5. Data & backend work

| # | Task | Detail |
|---|---|---|
| D1 | Shared window helpers | `snapshot_window(view, key)` → (start, end, label, prev, next); Friday-bundling + Monday-week normalization; unit-tested |
| D2 | Rate helper extraction | refactor `eac._rate_tables` → shared `apps/analytics/labor_rates.py` used by EAC + this page (no behavior change; EAC tests keep passing) |
| D3 | `finance_arinvoice` | new model + registered query `sl/finance_ar_invoices.sql` (ARDoc IN/CM/DM, DocDate ≥ 430d, incl. closed; ref, customer, project, amount, date); full-replace loader in refresh_finance + refresh_all |
| D4 | AP checks | new model `finance_apcheck` + query (APDoc CK/HC/EP + VC voids, DocDate ≥ 430d; vendor, amount, date) + application drill pull (APAdjust by AdjgRefNbr for the drill modal) |
| D5 | PO/receipt source (I1) | investigate PJCOMDET retention & PurchOrd/POReceipt tables; then either extend commitments loader (keep received lines in-window) or add `finance_poline` window table; fallback wiring from transactions |
| D6 | Pct-window reuse | extract the pct-validity-piecewise helper (currently duplicated in backfill + `wip_as_of`) into `finance_wip.pct_series(project_ids)`; use for Δ% here |
| D7 | View + template | `/projects/snapshot/` (`dashboard:project_snapshot`), nav entry "Project Snapshot" in the top group; day/week toggle; div + PM filters (PM dropdown = PMs with open jobs or window activity) |
| D8 | Indexes | confirm `operations_timeentry(work_date)` and prediction (project, as_of_date) indexes support the window queries; add if missing |

**No source-DB writes anywhere; all new SL pulls are registered read-only SELECTs.**

## 6. UI principles (per Owner's emphasis)
- Everything is **tables with aligned money/number columns**, totals rows, largest-first —
  the Payments-Received grouped-table pattern (shaded parent rows + aligned child rows), never
  `<details>` lists.
- Scannability first: color only where meaning exists (margin, late, big-ticket); flags as chips;
  drill-downs inline (expandable child rows) or click-tooltips, no page-hopping needed — but every
  job/PM/customer/person IS a link for when you want the deep page.
- Empty-day handling: sections collapse to one-line "nothing in this window" rows.
- Every header/term click-tooltipped with its exact definition (existing convention).

## 7. Verification plan (before "done")
1. Unit tests: window math (Fri+weekend bundling, Sat/Sun normalization, Mon–Sun weeks,
   year-boundary weeks), Δ% piecewise reads, threshold flagging.
2. Cross-checks vs existing verified pages: window Σ Δearned − Δbilled == ΔWIP between the two
   snapshot dates (Daily Financial Snapshot numbers); billing-out totals == GL revenue postings
   for the window (per `finance_glrecentposting`); cash-in == Payments Received day totals;
   hours == PTT project-page hours for spot jobs.
3. Browser pass at day + week grains, all filters, expansions, with console-error check.
4. Docs: `docs/06_finance_reports.md` cross-ref + this spec updated to "implemented"; memory notes.

## 8. Phasing
- **Phase 1** (bulk of value): D1–D3, D6–D8 → header KPIs, jobs-worked table with economics +
  health + trends + crew expansion, exceptions, billing out, cash in, progress updates.
- **Phase 2**: D4 (AP paid out) + D5 (PO/receipt line detail) + by-person roster toggle.
- Both phases in this plan; implement sequentially in one go unless something in I1 blocks D5.

## 9. Open items (not blockers)
- PM identity for PM-filtered sharing is just a URL filter for now (no auth); fine while the app
  is Owner-local. When division heads get access, revisit whether filters need enforcement.
- Big-ticket thresholds hardcoded v1; move to a settings model if Owner wants to tune them.
- Weekly email/export of this page: explicitly out of scope for v1 (Owner hasn't asked).

**2026-09-11 — WIP view basis.** The Job activity WIP columns read both window ends by PTT % validity (never the stored snapshot), so Δ WIP and Σ Δ% × CV move on the same day. See `04_metric_dictionary.md` › Project Snapshot — Job activity.
