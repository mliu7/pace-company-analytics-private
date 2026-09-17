# Daily Finance Snapshot · WIP (over/under billing) — plan

> **Status: IMPLEMENTED 2026-08-26, CORRECTED same day.** The first version led with the cost-vs-billings
> "accounting" columns from PTT's WIP report — wrong: that basis counts normal margin on billed work as
> overbilling, understated WIP (~$2.6M vs the real ~$6.4M) and fabricated an "overbilled" March that never
> happened. Owner caught it against the **Project Update workbook** (`../internal_reports/Project Update
> 082026 August.xlsm`, WIP tab), whose per-job "(Over)/Under" was reverse-engineered as
> **CV × PTT % complete − billed** ("Projected Total Cost" there is *derived* as cost ÷ %). Reproduced its
> "Prev Wip" total **$5,670,114.29 to the penny**, and 521/601 rows match the pure formula exactly (the
> other 80 are the controller's monthly manual overrides — zeroing service rows like the SA at −$6.8M, adjusting
> jobs mid-billing — which our population exclusions approximate; our live number therefore runs
> ~$2–3M above his adjusted total). The earned basis is now the primary metric (net POSITIVE =
> underbilled); the cost basis remains as a labeled secondary. History reconstructed on the earned basis
> using each PTT %'s real validity window (`ptt_last_updated_at`) + cumulative billed by transaction
> date; the corrected 180-day series is underbilled throughout (Feb +$10.3M → June peak +$12.6M →
> today +$6.4M), matching the company's actual experience.
>
> **Second correction + buildout (same day).** The first history reconstruction held each job's
> earliest-known PTT % backward in time, fabricating a $12.6M June peak (359 of 477 jobs at June 1
> had their % set only later). Fixed: pct(D) now uses the recorded % inside its real validity
> window (`ptt_last_updated_at`), and BEFORE the earliest window it is estimated as
> cost(D) ÷ implied projected total cost (the workbook's own identity, projected = cost ÷ %),
> capped at the earliest known % — percentage-of-completion by cost. Validated: reconstruction at
> 7/31 lands within 4% of the workbook's per-job "Prev Wip" ($5.39M vs $5.61M on matched jobs);
> corrected series Feb $3.1M → Jun $5.0M → Aug $6.4M, underbilled throughout, peak $7.5M.
> The strip also gained: expandable detail panel (day/WTD/MTD change chips with under/over
> attribution, a 3-series chart, biggest contributors, biggest movers with earned-vs-billed
> drivers from per-job WIP stored in each snapshot's detail JSON), clickable under/over numbers
> with searchable job modals, and green-positive chart colors (positive WIP = money waiting).

**Goal.** Add the company's WIP position — over/underbilled on open jobs — to the Daily Finance
Snapshot, with history, so the AR/AP/cash picture reads correctly (AP and cash rising while AR is
flat usually means billing ahead of work: an overbilled position building). **Deliberately small:
one thin strip on the page + a drill-down + a trend line. Not the centerpiece.**

## Method (ported from PTT's WIP report — `../pacescheduler/apps/time_tracking/reports/wip.py`)
PTT's "accounting" WIP is **cost vs billings with caps**, per job:
- `under = 0 if cost ≤ billed; else cost − billed if cost ≤ CV; else max(CV − billed, 0)`
  (costs in excess of billings, never claimed beyond the contract price)
- `over  = 0 if billed ≤ cost; else billed − cost if budget_cost > billed; else max(budget_cost − cost, 0)`
  (billings in excess of costs, capped at remaining budgeted cost; projected cost as fallback when no budget)

Inputs all live in `core_project` already (billed_revenue, actual_direct_cost, budget_direct_cost,
contract_value, per PJPTDSUM for **all divisions**). Secondary, shown in the drill and tooltip:
the **earned basis** = CV × PM % complete vs billed (PTT's project-page "Over/Under Billed" widget).

**Population** (mirrors PTT's report): open lifecycle states (awarded/in-progress/field-complete/
dormant), `contract_value > 0`, not internal buckets, **excluding service/T&M modes**
(tm_ticket / tm_service / service_agreement — e.g. 136400 "SA - PACE SCHEDULING" is billed $11.9M
vs $4.95M CV and is SaaS billing, not construction WIP) and **staffing division 030**, exactly the
jobs PTT leaves off its WIP tables. 582 open CV jobs today → ~520 after exclusions.

## History
- Live: three new columns on `DailyFinanceSnapshot` (wip_overbilled, wip_underbilled, wip_net =
  over − under, positive = billed ahead) + earned-basis pair; accumulates daily like AR/AP.
- Backfill (180 d): **local data only** — billed(D)/cost(D) from `finance_projectfinancialtransaction`
  cumulative by `transaction_date`; job open at D via `close_date`; caps use *current* CV/budget
  (documented limitation). No SL reads needed. Fills wip fields on existing snapshot rows where null.
- GL cross-check: SL books no WIP during the year (deferred revenue 22000 = $0 in FY2026; customer
  deposits 21000/24000 ≈ $955K are a separate liability) — stated in the tooltip, not a reconciliation.

## UI (small)
A single thin full-width strip under the top cards: **"WIP · billing position"** —
Overbilled $X (n jobs) · Underbilled $Y (m jobs) · **Net $Z** with a plain-English chip
("billed ahead of work" / "working ahead of billing"), a small net-WIP sparkline (180 d), and a
drill link → modal with per-job rows (project link, customer, mode, CV, PM %, cost, billed,
over/under cost-basis, over/under earned-basis), sorted by |position|. History table gets one
Net WIP column. Nothing else on the page changes.

## Verify before done
1. Unit tests on the ported PTT formulas (caps, both branches) with hand-computed cases.
2. Spot-check 3 jobs against their project pages (billed/cost/CV) and PTT's formula by hand.
3. Snapshot totals == recomputation from core_project (verify script check).
4. Backfill continuity: reconstructed WIP for yesterday ≈ live value today's components.
5. Browser: strip renders, drill modal sums match the strip, no console errors.
