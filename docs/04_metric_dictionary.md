# 04 · Metric dictionary

All money is SL dollars; all percentages are stored as fractions (0.3043) and displayed as 30.4 %. "Current budget" means SL's only budget (no original exists).

| Metric | Definition | Source |
|---|---|---|
| Contract value (CV) | Σ `CONTRACT VALUE` budget over tasks — **after** the contract-value rules (`apps/analytics/contract_value.py`, docs/contract_value_reconciliation_plan.md): when SL's task entries are provably double-counted, mis-keyed or stale on a finished job, the app uses the corrected figure everywhere and keeps SL's raw sum in `contract_value_sl` with the rule in `contract_value_basis`; a ⓘ hover next to the value explains it, the Data Quality page lists every active correction, and a finance user can override or confirm on the project page | `PJPTDSUM.total_budget_amount` + local rules |
| Revenue budget | Σ `REVENUE` budget (≈ CV; flagged if ≠) | same |
| Budget labor (loaded) | Σ `LABOR`+`LABORUNION`(+`BURDEN` if budgeted) budget; budget hours = Σ `total_budget_units` on LABOR/LABORUNION | same |
| Budget material / subcontract / other direct | `MATERIALS` / `SUBCONTRACT` / `ODC`+`TRAVEL`(+`PURCHASEVARIANCE`, never budgeted) budgets | same |
| Budget direct cost | labor + material + subcontract + other direct budgets | derived |
| Sold GP $, Sold GP % | CV − budget direct cost; ÷ CV (NULL when CV = 0) | derived, current-budget basis |
| Budget labor rate | budget labor $ ÷ budget hours | derived |
| Change orders / budget history | Per project: tasks created > 7 days after the project (kind `change_order` when the id/description reads like a CO, else `scope_added`; amount = the task's current budgets, `exact` if never edited since), plus in-place budget edits (`exact` before → after from local history since 2026-08-17, otherwise `unknown_prior` with SL's edit date/user). "Original contract" = current CV − all known CV changes, blank when any CV edit has an unknown prior value. | `PJPENT` stamps, `PJPTDSUM` stamps, local `finance_projectaccountsummary` versions → `finance_projectchangeevent` |
| Budget touched after setup | CONTRACT VALUE row `lupd_datetime` > `crtd_datetime` + 1 day (shown as an edit date; derived hint only) | `PJPTDSUM` |
| Billed revenue | Σ `REVENUE` (+`BILLINGS`) actuals = AR invoices − credits | `PJPTDSUM.act_amount` = Σ `PJTran` |
| Earned revenue | CV × PM % complete | derived |
| Actual labor wage / burden / labor | `LABOR`+`LABORUNION` actuals / `BURDEN` actuals / sum | same |
| SL labor hours | Σ `PJTran.units` on LABOR/LABORUNION where `system_cd IN ('PA','PR','TM')` | `PJTran` |
| Actual material / subcontract / other direct / freight | `MATERIALS` / `SUBCONTRACT` / `ODC`+`TRAVEL` actuals; freight = `ODC` rows with GL 50750/50760 | same |
| Actual purchase variance | `PURCHASEVARIANCE` actuals: vendor invoice vs PO/receipt price on the job's purchases (usually negative). A material price correction, reported as its own component so breakdowns add up | same |
| Actual direct cost | labor + material + subcontract + other direct + purchase variance (= SL `PJPrjBgt.ACT_Labor + ACT_Exp`) | derived |
| Actual (final) GP $, GP % | billed revenue − direct cost; ÷ billed revenue | derived (equals SL `PJPrjBgt.ACT_Margin`) |
| Margin preservation (pts) | final GP % − sold GP %, revenue-weighted over jobs with CV > 0 | derived |
| Effective loaded labor rate | actual labor (wage + burden) ÷ SL labor hours | derived |
| Open commitments (material / subcontract) | Σ `open_amount` of `finance_projectcommitmentline`: open PO lines (ordered − received × unit cost) + project-inventory allocations **not yet shipped** to the job (FIFO-netted against `SOLine.QtyShip`). SL's raw `com_amount` is stored as `sl_reported_commitments`; the part that is allocations already shipped (cost already in actuals) is `phantom_commitments` and is never used (docs 02 §3b) | `PJCOMDET`+`InvProjAlloc`+`SOLine` |
| PTT hours | Σ live Job Report entries (form 1, status 1): onsite + OT + offsite; time-off never counts | `TimeEntry` |
| PTT − SL hours | PTT hours − SL labor hours (unposted-labor indicator) | derived |
| PM remaining hours | PTT `estimated_hours_to_completion` (`'1'` + `'2'`); history = `history[]` | PTT |
| PM % complete | PTT `estimated_percent_complete` ÷ 100; PTT formula = labor-weighted blend of hours-remaining and expense-remaining. Only 0–100 counts (`rules.pct_fraction`): an out-of-range value (remaining costs > PTT's cost basis, e.g. -538.82 %) is ignored everywhere, the last valid % stands, and `ptt_pct_out_of_range` is raised (docs/02 gotcha 16) | PTT |
| Labor % (calc) / **Hrs %** | PTT hours ÷ (PTT hours + PM remaining hours), using the **raw** estimate — deliberately *not* burned down like the EAC's (`services.py` `calc_pc`). Keeping it raw is what makes a stale PM estimate visible: the gap against the PTT %% is the staleness signal Owner reads. It does mean a job with an old estimate reads low (254612: 44 %% raw vs 79 %% burned down). Offered to Owner 2026-09-14 and left as is — change both this and the tooltip's per-type figures together, or neither | derived |
| System mix / Work-type mix | hour-weighted shares of PTT System / Work Type choices | `TimeEntry` |
| Solution class | dominant System class (≥ 50 %), `mixed_security` if security classes ≥ 50 %, else title keywords, else unknown | derived |
| Project mode | rule on title/status/PO: installation, tm_ticket, tm_service, service_agreement, joc, warranty, internal, canceled, template | `rules.project_mode` |
| Lifecycle | see Definitions page / spec §11 | derived |
| Close date | PTT `project_inactivation_date`, else SL `lupd_datetime` at the flip (approx), else last transaction | derived |
| Closed / stabilized | SL inactive and no material posting created in 45 days | derived |
| EAC labor hours | PTT hours + PM remaining hours, where the remaining hours are **burned down by every hour worked since the PM saved that estimate** (`eac_rules.burn_down_remaining`): an estimate is a statement made on a date, so hours worked after it are hours of it already spent. Without the burn-down the same hours were counted twice — once as worked, once as still to come (265296 Palmer House, 2026-09-14: 440 h estimated on Aug 30, 319 h worked before the PM revised it, EAC GP shown as $7.2k against a true ~$38k; 79 open jobs and ~$245k of phantom labor portfolio-wide). If the work done exceeds the estimate the estimate is *exhausted* and the job falls back to budget − actual **capped at the estimate itself**, so the correction can never raise remaining hours. Fallback budget − actual when the estimate is NULL **or PTT's never-entered default 0** (no timestamp, no revision history — 265312 taught us a 0 is not "nothing left"); an estimate > 60 days old on an in-progress job is still flagged | `eac.py`, `eac_rules.py` |
| EAC labor cost | posted labor + unposted hours × rate + remaining hours × rate; rate hierarchy: project's own posted rate (≥ 80 SL h **and ≤ 2.5× the division rolling rate / $250** — SL posts labor dollars without hours, e.g. 229425 at $416/h) → crew 13-week loaded rate → division rolling rate → budget rate → $90; method recorded | `eac.py` |
| EAC material / sub / other | Pooled expenses: actuals + aged valid PM expense estimate, at least actuals + real open commitments; pooled budget fallback without a usable estimate. Purchase variance belongs to material. See rules below. | `eac.py` |
| EAC revenue | CV; for T&M / service agreement max(billed, CV) | `eac.py` |
| EAC GP, EAC GP %, vs sold, shortfall | EAC revenue − EAC direct cost; ÷ EAC revenue; − sold GP %; shortfall = (sold GP or 0) − EAC GP for every job, so Σ shortfall = Σ sold − Σ forecast on the Forecast page | `eac.py` |
| Risk score / level | heuristic 0–100 (loss +45, margin <10 % +25, >5 pts below sold +25, >15 pts +10, hours EAC >110 % +15, >130 % +10, dormant +10, no PM estimate +5); low <25, moderate <50, high <75, critical ≥75 or projected loss > $50k | `eac.py` |
| Rating: raw effect | weighted mean residual of the entity's jobs from a ridge model that excludes the entity's identity | `ratings.py` |
| Rating: adjusted effect | raw × shrinkage, shrinkage = τ² / (τ² + σ²/n_eff) | `ratings.py` |
| Rating: reliability | shrinkage × diversity (customers/solutions vs thresholds) | `ratings.py` |
| Rating: status | insufficient (< min jobs) / provisional (reliability < 0.5 or few customers/solutions) / publishable | `ratings.py` |
| Remaining-hours forecast accuracy (PM) | for each revision on a closed job: (hours worked at revision + remaining at revision) − final hours; bias = mean, MAE = mean abs | `person_detail` |
| Time log (person page) | one row per PTT `TTFormResponse` for the person (`operations_timeentry`), newest first; On/OT/Off/Total are the Job Report hour fields, time-off forms show `hours_time_off`; the day line = live job-report hours that day across all projects (ignores the project filter); "Entered" = submitter when it was not the person themselves + submit time (US-Central), `edited` = PTT last_edited_time set, `removed` = PTT status 2 (hidden unless asked for, never counted); totals above the table count live entries only | `person_detail` (`_person_time_log`) |

### Field crew rating metrics (Ratings → Field Crew; `docs/model_cards/field_crew_v1.md`)
| Metric | Definition | Source |
|---|---|---|
| Net hours / 1,000 (field_hours_saved_per_1000) | RAPM: each closed installation/JOC job regresses log(actual PTT hours ÷ budget hours) on difficulty controls + every crew member's hours share + crew-lead indicators; ridge penalty CV-chosen; person effect × placebo-calibrated EB factor τ²/(τ²+SE²); displayed as (1−e^θ)×1000, positive = hours saved | `field_ratings.py` |
| Labor $ / $1,000 (field_cost_saved_per_1000) | Same design on log(actual labor $ ÷ labor budget $) | same |
| Margin effect (field_margin_preservation) | Same design on final GP % − sold GP % (points) — secondary, diluted by material/pricing | same |
| Crew-lead effect (crew_lead_hours_saved_per_1000) | The λ coefficient on the crew-lead indicator (submits PTT time for 2+ others), over and above their own hours share | same |
| Naive +/- | Exposure-weighted mean job over/under-run vs the population, no controls — shown to reveal how much context was doing | same |
| EB / Reliability | Placebo-calibrated shrinkage survival; reliability = EB × diversity(PMs, solutions); gates the status | same |
| Pair | Mutually inseparable people (≥70% shared hours both ways): columns merged, one joint effect shown on each row | same |
| Classification | Exact from PTT/SL labor class (CJOU, AFOR, A60…); ≈ values inferred from latest observed wage vs the union's modal journeyman rate that year; "leads crews" = crew-lead on ≥5 projects | `derive_classifications` |
| Base rate (Field page) | PTT `person.base_hourly_wage` — straight time, no OT, no burden | PTT |
| SL labor $ (Crew / Field / Person pages) | Per-person loaded cost: wages + employer payroll tax charged to the project for that employee, **plus** a pro-rata share of the project's union fringe vouchers (allocated by share of the project's LABORUNION wages) | `build_role_assignments` |
| Started (Field page) | SL `PJEMPLOY.date_hired` | SL |

**EAC non-labor (changed 2026-08-31):** material/sub/ODC EAC = spent (+ purchase variance) + real open
commitments per category, with the budget floor applied to the **pooled** unspent non-labor budget rather than per
category (`analytics/eac.nonlabor_eac`). Per-category flooring double-counted cross-bucket buying (subcontract
budget bought as material on 264932 → fake −$57k; the reverse on 265267 → fake −$90k) — 52 open jobs / $780k of
phantom EAC cost at the time of the fix. Open POs whose cost was already vouchered directly are netted out
(`voucher_matched`). As of 2026-09-21, a valid PTT cost estimate dated within 60 days replaces the budget floor when an SL expense baseline exists on the estimate date (or the following day for after-hours updates). Subsequent net expense increases consume the estimate; actuals plus real open commitments remain the minimum. Missing, stale or invalid estimates keep the pooled budget fallback. The PTT expense pool includes material, subcontract and other direct costs; any amount beyond commitments is allocated by unspent category budgets, with material as the residual category when no unspent budget exists.

On jobs with fewer than 80 PTT and 80 SL hours, a plausible PTT remaining-labor cost per remaining hour takes precedence over a sparse crew sample, only when the cost estimate and hours revision timestamps match within one second. Remaining hours still burn down. Method: `pm_cost_estimate`. This avoids extrapolating a single early worker's rate over an entire job.

The project page's **Cost to complete revisions** shows cost observations separately from the PTT hours revision history because PTT's hours JSON contains no historic expense costs. Cost observations show saved labor/expense remaining, expense change, SL material and total expense spend at observation, projected expenses, and PTT's computed completion. Missing snapshots remain blank. The header prominently shows PTT completion, alongside the distinct hours-based completion.

## WIP and the period result (docs/07 §3 — the org's accounting rule, 2026-09-03)

| Metric | Definition | Source |
|---|---|---|
| WIP (job) | contract value × PTT % complete − billed to date; positive = underbilled. **Not profit** — earned-but-unbilled revenue booked. | `finance_wip.earned_wip` |
| ΔWIP (period) | WIP at the period end − WIP at the period start (snapshot to snapshot; = Δ earned − Δ billed). Only the change in the WIP booking. | snapshot `detail["wip_jobs"]`, `finance_wip.wip_movement` |
| GP (period) | revenue posted in the period − direct cost posted in the period (labor + burden + union; material + subcontract + other direct incl. purchase variance), by fiscal period. The Project Update workbook's REVENUE − COST (col AH). | `finance_wip.ledger_window`, `divisional_pnl.Model.projects` (PJTran) |
| Adjusted GP (period) | GP + ΔWIP = the real result for the period on the earned basis (= earned revenue − cost). Workbook column AJ. | WIP by Job "Period result" band; Divisional P&L per-project "Adj. GP" |

## Cost to complete revisions (project page; hours history introduced 2026-09-03)

One row per PTT remaining-hours revision, newest first, plus a **Now** row when hours were logged after the last revision.
- **Spent to date** = PTT Job Report hours (`operations_timeentry`, live, form_type 1) with `work_date` ≤ the revision's Central date, split by the employee's PTT type (`ptt_employee_type` non_union / union; untyped hours count in the total only).
- **PM remaining** = `remaining_hours_non_union` / `_union` / `_total` as saved in PTT; the small figure is the change vs the previous revision (amber when remaining went up).
- **Projected at completion** = spent + remaining, per type; green under / red over the **current SL labor budget hours** (`finance_projectaccountsummary.budget_units`, LABOR = non-union, LABORUNION = union — SL keeps no original budget). Small figure = change in the projection vs the previous revision (red = grew).
- **vs budget** = projected total − budget total, in hours and as % of budget. **Implied %** = spent ÷ projected.
- **Since prev** = days since the previous revision and PTT hours logged in between. Summary tiles repeat budget / spent / remaining / projected / vs budget for today; "N h since last revision" flags work logged after the estimate was last touched; the estimate's age is amber over 30 days and red over 45 on open jobs.
- The note under the table gives SL's posted labor hours (payroll basis) for comparison with PTT.

## Materials on the job (project page, 2026-09-03)
Source: `apps/analytics/project_materials.py`, fed by the five SL document streams in docs/02 §3b and the ChannelOnline document linked through `SOHeader.User2`. One row per SL item across every document.
- **Quoted cost (ChannelOnline)** — Σ `total_item_cost` of the CNET document(s) behind the job's sales orders; the sales-order version of a number is preferred over its quote. **Bought vs quote** (chip; was "SL vs quote" until 2026-09-08) — Σ over items with a matching CNET line (part number = SL InvtID, case-insensitive) of (bought unit cost − quoted unit cost) × quantity the job ordered, where bought = the PO price (SL's stock cost on the sales order when the job raised no PO). Negative = bought below the quote.
- **Ordered on POs** — Σ extended cost of non-cancelled PO lines tied to the job (SL ProjectID on the line, or deduced from sales-order demand, marked ≈). **Vendors billed** — Σ AP vouchers referencing those POs (VO + AC − AD, voids excluded); a voucher covers the whole PO, which can include other jobs' lines.
- **Received** — Σ PO receipt lines for the job (POTran R). For drop-ship POs the receipt is the delivery to the site.
- **Shipped to job** — Σ shipper-line cost for the job; the shipper's invoice is what posts MATERIALS (OM/IN), so this tracks the ledger's MATERIALS actual up to GL corrections and credit memos.
- **Item status** (PM's view): *No PO* = on a sales order for the job, no purchase order found · *On order* = PO placed, nothing received · *Partly received* / *At warehouse* (= fully received) · *Drop-shipped* · *Partly shipped* / *Shipped to job* (the job's full sales-order quantity left the warehouse) · *PO cancelled* · *Returned* (net quantity ≤ 0 after RM returns). Items on a PO for the job but never put on a sales order keep the PO-side status and show "no SO".
- **Freight** — vendor freight on the job's PO vouchers (`APDoc.FreightAmt`) vs the ODC/MATERIALS postings whose description says freight/shipping (what actually landed on the job); PO-header freight estimates and shipper freight (`TotFrtCost`/`TotFrtInvc`) are shown in the detail rows. **Purchase variance** is the project's PURCHASEVARIANCE account (PO price − vendor invoice; negative = paid less).
- **Items table price chain (2026-09-08)** — per item, four prices read left to right, each as the unit price with *× qty = total* beneath: **Quoted** = unit cost on the ChannelOnline line for the same part (the cost the estimate was built on; the quote's own quantity); **Bought** = the actual purchase price, i.e. the unit cost on the job's non-cancelled PO lines over the PO quantity — *stock* when the job raised no PO and the item came out of inventory at SL's cost on the sales order; **Vendor billed** = what the vendor's invoice actually charged, `PurOrdDet.CostVouched ÷ QtyVouched` (SL matches AP vouchers to PO lines), with *vs PO* = the item's purchase variance on the vouched quantity; **Sale** = SL `SlsPrice` on the sales order. **Drift vs quote** = bought − quoted per unit, as % of the quoted unit cost, and × the quantity the job ordered (the PO quantity for items with no sales order); the chip **bought vs quote** is its sum over the matched items. Notes flag an earlier cancelled PO, a job quantity that differs from the quote, and a PO quantity that differs from the job's. The footer sums the quote's extended cost (matched items), bought, vendor billed, sale and drift. The PO detail rows show *Billed* (`CostVouched`) next to the PO line's extended cost.
- **Ordered by / entered by / shipped by / received by** — SL logins (`Crtd_User`) on the PO, sales order, shipper and receipt; `SYSADMIN` on a sales order means the ChannelOnline import, so the CNET creator's name is shown instead when known.
- Window: SL documents dated within `MATERIALS_DAYS_BACK` (3 years); older jobs show only the ledger totals.

## Vendor payments (docs/06 Vendors, 2026-09-03)

| Metric | Definition | Source |
|---|---|---|
| Paid (vendor, window) | AP payments to the vendor by check date (CK / HC / EP; voids negative) drawn on a bank account — cash out. | `finance_apcheck` where `cash_acct` not in `CARD_CLEARING_ACCOUNTS` |
| Settled by card (vendor, window) | AP payments drawn on the credit-card holding account 10450: the vendor's invoices cleared by a company credit card, the issuer vouchered instead — no cash to the vendor. Never added to Paid. Which card is not recorded on the payment. | `finance_apcheck.cash_acct` = `APDoc.Acct`, `finance.settled_by_card` |

## Customer payment behaviour (customer page → Payment history, 2026-09-08)

Source: `finance_arpaymentapplication` (every PA → invoice application since 2013) + `finance_arpayment` +
`finance_arinvoice`; logic in `apps/analytics/customer_payments.py` (pure functions, unit-tested). Full description in
docs/06 "Customer payment history".

| Metric | Definition |
|---|---|
| Paid on | ARDoc PA `DocDate` (check / wire date); ARAdjust `DateAppl` when the payment is missing or dated > 7 days in the future |
| Days to pay | paid on − invoice date, per application |
| vs due | paid on − invoice due date; ≤ 0 = within terms; late buckets 1–30 / 31–60 / 61–90 / over 90 |
| Pays in | Σ(applied × days to pay) ÷ Σ applied over *counting* applications (applied > 0, not a reversal, not a retention release); all-time and last 12 months (by paid-on) |
| Within terms | Σ applied in the ≤ 0 bucket ÷ Σ applied with a known due date (same population) |
| Typically late by | Σ(applied × days past due) ÷ Σ applied over applications with vs due > 0 |
| Paid to retention | an application whose remaining open balance is 5 % or 10 % (± 0.3 pt) of the invoice amount |
| Retention release | a later application on that invoice equal to 5 % / 10 % of the invoice that closes it (± $0.50) |
| Reversal | applied < 0 |
| Left open | invoice amount − cumulative (applied + discount) through this application, in payment-date order |
| Terms (customer) | the SL terms code carrying the most invoice dollars (fallback: the applications' terms); due days from `TERMS_DAYS` (Net N → N, DU / CD / PP → 1, card codes → 10, PW → none) |
| Payer grade | pays-in − terms days (unknown terms = 30): prompt ≤ 10, fair ≤ 30, slow ≤ 60, very late > 60 |
| Invoices / open / retention held | IN + DM with amount ≠ 0; open = balance > 0 today; retention held = Σ open balances that are exactly 5 % / 10 % of their invoice |

## WIP population — service agreements (2026-09-14)
Service agreements (mode `service_agreement`, every job titled "SA …") are outside WIP by rule on every page: the
Daily Snapshot card, the WIP by Job page, the Divisional P&L's ΔWIP, the Project Snapshot's earned value and its
WIP view, the history backfill. Each page shows them as a labelled breakout (count, contract, billed) rather than
silently dropping them. docs/07 §3 has the reasoning and the code points.

## Bids and estimators (Bids pages, Estimators; docs/12, 2026-09-10)
Source: the SharePoint Project Portal (Project List + Archive) mirrored read-only into `bids_bid`; SL supplies the outcome.
| metric | definition |
|---|---|
| bid value / budget | the portal's Project Value / Budget as typed at bid time (SL keeps no original estimate) |
| bid margin | (value − budget) ÷ value |
| open pipeline | Σ value of Project List rows in stage quoting or submitted; archive rows are never open |
| weighted pipeline | Σ value × stated Probability of Close ÷ 100 over scored open bids (unscored add nothing; blank ≠ 0 %) |
| expected bookings (90 d) | open list bids whose expected decision date — submitted (else due) + the division's median submitted→awarded days — is within 90 days, at stated probability; "by hit rate" values them at each estimator's historical hit rate |
| won / decided | won = portal Awarded **or** the linked SL job has billings (SL is the truth); decided = won or lost; DNB / on hold / no decision are not decided |
| hit rate | won ÷ decided by count; the $ variant weights by value |
| estimate accuracy | SL final GP % − bid margin, margin points, closed & stabilised jobs only; positive = beat the estimate |
| cost ÷ budget | SL actual direct cost ÷ bid budget, closed & stabilised jobs |
| decision cycle | awarded − submitted in days (median per division drives expected bookings) |
| calibration gap | actual win rate − stated probability per bucket, decided bids |
| hygiene | share of submitted / decided bids with budget, value, due date and probability all present |
| leak | awarded in the portal ≥ 14 days ago with no SL job number |
| GP quoted / GP % | portal value − portal budget; ÷ value (Pipeline Snapshot) |
| expected GP (stated / PCA) | GP quoted × stated probability; GP quoted × PCA's estimated win rate |
| PCA estimated win rate | descriptive log-odds blend of factor hit rates (estimator, client, division, work type, size, sector, rep) on decided bids since 2019, evidence-weighted and damped; shown with its breakdown; not a rating (`apps/bids/winrate.py`) |
| estimator rating (concealed) | ridge + empirical-Bayes effect of the estimator of record on final GP %, margin preservation and bid accuracy (final GP − bid margin); `docs/model_cards/estimator_v1.md` |

## Project Snapshot — Job activity, WIP view (2026-09-11)
The Job activity table's WIP columns (WIP at the window end, Δ WIP since the day before the window, GP + Δ WIP) read
**both ends of the window by PTT % validity** (`finance_wip.jobs_at(day, prefer="validity")`), the same series behind
the page's "Work margin" KPI (Σ Δ% × CV). They never use the stored daily WIP snapshot for a window end: a snapshot is
knowledge as of that day's refresh, so a % keyed in PTT at 22:09 is missing from the 16:30 snapshot and would land as
Δ WIP on the *next* day while the KPI books it on the day it was keyed (job 264888, +$874k, Sep 8 vs Sep 9). The Daily
Financial Snapshot's day-over-day WIP change and the WIP-by-Job page's closed-month periods still read the stored
snapshots on purpose — those tie to the P&L as it was booked.
