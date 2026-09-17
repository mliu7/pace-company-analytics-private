# Contract Value Reconciliation — plan (2026-09-03)

**Status:** approved 2026-09-03 with all §8 defaults; phases A + B implemented the same day (rule engine `apps/analytics/contract_value.py`, nightly step `contract_values`, log `finance_contractvalueadjustment`, override on the project page, hover marker, Data Quality section, ratings exclusion, unit tests `tests/unit/test_contract_value.py`). Owner's implementation note: keep it almost invisible — users never care about this; the backend gets the value right and uses it everywhere, with a hover explaining any assumption so it can be fixed. **Design change from §2.1:** `core_project.contract_value` now *is* the effective value (so every existing consumer picks it up without code changes) and SL's raw sum lives in `contract_value_sl`. **2026-09-04:** stored WIP baselines (daily-snapshot `wip_jobs`, incl. the Divisional P&L's month-ends and the WIP page's Δ WIP) are now restated on the current basis at read time (`finance_wip.restate_stored`: earned × CV now ÷ CV the app used that day, from `ProjectFinancialSnapshot`), so a correction landing mid-period no longer shows as Δ WIP (250038: −$495k in Sep 2026 before, $0 after). Phase C proper (basis-dated history, G2, weekly digest) and D (cohort replay as a fixture test) remain open. **Owner:** Owner. **Never writes to SL.**

## 0. Why

Project **250038-000000 (Rush North & Harlem, div 040)** carries `CONTRACT VALUE` $1,060,019 in SL. The job is
100 % complete, idle for 461 days and fully billed at $563,097. SL's figure is a double count: the default task
`00` holds the whole contract ($564,685) *and* the `IT` task holds its own $495,334 slice. The app sums task
contract values (docs/04: CV = Σ `CONTRACT VALUE` budget over tasks), so it reported earned revenue of $1.06 M,
**$496,922 of phantom under-billed WIP**, a 53 % sold margin against a 12 % actual, and pushed all of that into
division 040's WIP and open-month ΔWIP on the Divisional P&L. Owner's direction: SL will not be cleaned up job by
job; the app must detect and rectify this class of error itself.

## 1. What the data says (audit of all 9,003 projects, run 2026-09-03; `scripts/contract_value_audit.py`)

**How Pace enters contract value.** Four conventions coexist:

| Structure | Projects | CV | Notes |
|---|---:|---:|---|
| One task carries the CV | 7,038 | $251.6 M | the norm |
| Default task + other tasks both carry CV | 783 | $171.3 M | **the ambiguous family** |
| Split across tasks, nothing on default | 227 | $38.5 M | sites / phases |
| No CV at all (billing exists on 365 finished jobs, $7.4 M) | 850 | — | T&M, service, tickets |

**Finished jobs bill exactly the CV 94.3 % of the time** (7,051 of 7,478 within ±3 %). The remaining 5.7 % is the
error population: 258 closed jobs over-stated by **$7.68 M** in total (080 $3.6 M, 020 $1.5 M, 070 $1.3 M, 040
$0.7 M) and 159 under-stated by $3.31 M.

**The "default + others" family, verified against final billing (finished jobs only):**

| Verified class | Jobs | Meaning |
|---|---:|---|
| C — billed = default + others | 517 | additive: default = base contract, other tasks = change orders / added scope (**legit, 66 %**) |
| B — billed = default only | 102 | components: the sub-task CVs are slices of the default's CV (**double count, $1.29 M over-stated**) |
| A — default == Σ others exactly | 15 | pure duplicate ($258 k) |
| D — billed = others only | 14 | default task's CV is stale |
| E — billed matches neither | 60 | mixed (partial billing, scope cuts, one $2.9 M job closed after cancellation) |
| F — not finished yet | 75 | cannot be verified by billing today (42 of them created in 2026) |

**Nothing available at setup time separates B from C.** Tested on the 648 verified jobs:

| Signal | catches B | false-flags C | verdict |
|---|---:|---:|---|
| Task id / description says "CO" | 44 | 148 | useless (369 legit-additive jobs have no CO-named task) |
| Default task holds CV but no cost budget | 11 | 20 | weak |
| REVENUE budget = default CV only | 2 | 0 | rare (REVENUE budget equals the *summed* CV in 89 of 102 B jobs — it is keyed from the same total) |
| Sub-task CVs all created at setup | 8 | 142 | useless |
| Implied sold margin > division p90 with sum, normal with default only | 8 | 23 | weak |
| Sub-tasks billed beyond their own CV while default unbilled (mid-life) | 6 | 9 | too imprecise to auto-correct |
| Same SL user (SHUBBARD edits most CV rows in both classes) | — | — | no signal |
| Strict combination of the above | 8 | 3 | precise but catches only $570 k of $1.29 M |

Conclusion: **billing is the only reliable discriminator, and it arrives over the life of the job.** The engine
must therefore (a) correct deterministically where evidence is conclusive, (b) neutralise the *financial* effect
of an unproven CV on finished jobs regardless, (c) flag the rest for a human, and (d) re-evaluate nightly so a
correction flips back the moment SL catches up.

**Other error types found in the same audit** (each gets a rule below): a missing digit (240045: CV $103,685,
REVENUE budget and billing both $1,037,685); round-number placeholders on the default task with the real CV on a
sub-task (231228: $100,000 + $99,739, billed $99,739); cancelled/moved jobs closed with the CV intact (208844:
CV $2.93 M, billed $63,655); CV never entered (365 finished jobs). Implied sold margins above 60 % (482 jobs)
are **not** CV errors — 437 of them billed exactly the CV; their cost budgets are thin (median 29 % of CV).
That is a separate "budget completeness" flag, not part of this plan.

**Exposure today (open jobs, the WIP population):**

| | Jobs | WIP now | WIP after rules |
|---|---:|---:|---:|
| All open jobs with CV | 598 | $4.64 M | ≈ $4.0 M |
| Finished (PM ≥ 99 %), quiet ≥ 60 days, CV ≠ billed by > 3 % | 10 | $622,710 | ≈ $0 |
| — of which 250038-000000 | 1 | $496,922 | $1,588 |
| — of which 229798 (030, excluded from WIP page anyway) | 1 | $123,809 | $0 |
| Finished but still active / backlog (leave alone, watch) | 18 | $171,168 | unchanged |
| Exact duplicate on an open job | 1 | $0 | $0 |

Division 040's WIP moves from $1.53 M to ≈ $1.03 M; the company total from $4.64 M to ≈ $4.0 M. Everything
else is small today, but the 42 unverifiable 2026 jobs in the ambiguous family ($18 M CV) are exactly where the
next 250038 will come from — hence the guard-rails, not just corrections.

## 2. Design principles

1. **SL stays untouched and unchanged in the app.** `core_project.contract_value` keeps SL's number. The app adds
   `contract_value_effective`, `contract_value_basis` (rule code or `sl` / `override`), and
   `contract_value_evidence` (JSON: the numbers the rule saw). Every consumer switches to *effective*.
2. **Auto-correct only on conclusive evidence.** Anything probabilistic is a flag with a dollar exposure, never a
   silent change.
3. **Guard-rails make the disaster impossible even when the CV is unproven** (§4).
4. **Reversible and re-evaluated nightly.** Rules run at the end of `refresh_all`; when SL is fixed or billing
   catches up, the basis returns to `sl` automatically. Every change of basis is logged
   (`finance_projectcommercialchange` already records CV movements — add basis flips there).
5. **A human can always win**: a per-project override with a reason (§5), local DB only.
6. **Everything is visible**: project-page banner with both numbers, Data Quality section, review queue ranked by
   dollars, and the audit script stays runnable.

## 3. The rule engine (`apps/analytics/contract_value.py`, pure function + tests, fed from local tables)

Inputs per project: task-level `CONTRACT VALUE` / `REVENUE` budgets and billed actuals
(`finance_projectaccountsummary`, current rows), project state (`lifecycle_state`, `pm_percent_complete`,
`last_work_date`, `last_transaction_date`, `hours_last_30_days`, open commitments, open sales-order lines),
`revenue_budget`, `budget_direct_cost`, and any override. Rules are evaluated top-down; the first hit sets the basis.

**Tier 0 — override** (`override`): a `finance.ContractValueOverride` row wins.

**Tier 1 — deterministic corrections (auto-apply)**

| Code | Condition | Effective CV | Today |
|---|---|---|---|
| `duplicate_tasks` | default-task CV = Σ other-task CVs (±$1) | default CV | 15 closed, 1 open |
| `components_billed` | default CV > 0, other CVs > 0, default billed < 5 % of its CV, Σ other billed within 3 % of default CV, PM % ≥ 90 % | default CV | 250038 (+2 tiny) |
| `billing_final` | job finished (PM % ≥ 99 %, or SL status C / closed lifecycle) **and quiet** (no PTT hours and no SL postings for 60 days, no open commitments, no open sales-order lines) and \|CV − billed\| > 3 % | billed | 10 open jobs; 417 closed jobs |
| `revenue_budget_digit` | CV < cost budget (negative sold margin) while REVENUE budget > cost budget and REVENUE budget = CV × 10ⁿ or matches billing within 3 % | REVENUE budget | 30 jobs (1 open) |
| `billed_no_cv` | finished, CV = 0, billed > 0 | billed | 365 closed jobs, 3 open |

`billing_final` applies to over-billed jobs too (billed > CV = an unrecorded change order); 254380 (070) is one.

**Tier 2 — flags (review queue + banner + Data Quality issue; no change)**

| Code | Condition | Why it is only a flag |
|---|---|---|
| `components_suspected` | default + others, sub-tasks billed beyond their own CV while default unbilled | 6 true / 9 false in history |
| `margin_outlier` | implied sold GP above division p90 with the summed CV but inside the band with default only | 8 / 23 |
| `revenue_budget_mismatch` | REVENUE budget ≠ CV (60 open jobs, $25 M) | right more often than CV (127 vs 91) but not reliably |
| `placeholder_cv` | default CV is a round number (multiple of $5,000) and Σ others within 5 % of it | 231228 pattern |
| `finished_unbilled` | finished but not quiet, CV ≠ billed (18 jobs, $171 k) | a final invoice may be coming |
| `closed_gap` | closed job billed/CV outside 0.97–1.03 (417 jobs) | informational; drives §6 ratings handling |
| `no_cv_active` | open job billing with CV = 0 (3 jobs) | T&M-style work outside WIP |

Each flag carries `exposure_dollars` = the WIP or sold-GP swing if the suspicion is right, so the queue sorts by
money. A flag clears itself when a Tier-1 rule fires or the evidence disappears.

## 4. Guard-rails in the math (independent of whether the CV is right)

- **G1 — finished-and-quiet jobs carry no WIP.** In `finance_wip` and the Divisional P&L ΔWIP, a job meeting the
  `billing_final` state has earned revenue := billed. This is the same test as the rule, applied at read time, so
  even a job that slipped past the nightly run cannot inject phantom WIP.
- **G2 — earned revenue is capped at billed + open backlog** (open sales-order lines + open commitments at the
  job's sold margin) once a job is past 90 % complete. Prevents a wrong CV from earning revenue the job cannot
  still bill. (Defer to Phase C; needs validation on the 18 finished-but-active jobs.)
- **G3 — Project Snapshot Δ % × CV** uses the effective CV; when the basis is a flag-only state the row shows a
  "CV unverified" marker so the margin line is read with care.
- **G4 — Forecast / EAC** (`eac.py`: `eac_rev = max(billed, cv)`) uses the effective CV.

## 5. Override and review (Phase B)

- `finance.ContractValueOverride`: project, effective value, reason (required), who, when, `confirm_sl`
  (= "SL is right, silence flags"). Requires `finance.write`; superadmin-visible audit trail; never expires;
  banner shows "Contract value overridden by Owner on … — SL says $X".
- Review queue page `/finance/contract-review/` (registered in `URL_ACCESS`, `finance.view`): every Tier-1
  correction and Tier-2 flag, sortable by exposure, with the task table inline (like the project page's
  "By SL task"), one-click override / confirm. Counts and dollars also appear on the Data Quality page.
- Optional export "SL clean-up list" (CSV) for the accountants — corrections keep working whether or not they act.

## 6. Where the effective CV flows (every consumer switches)

| Consumer | File | Change |
|---|---|---|
| Nightly financial snapshot: sold GP $, %, earned revenue | `apps/analytics/services.py` | compute from effective CV; store basis on the snapshot row |
| WIP by Job, company WIP, WIP series | `apps/analytics/finance_wip.py` | effective CV + G1; historical series uses the basis in force on each date (Phase C; until then current effective applies to history and the page says so) |
| Divisional P&L ΔWIP | `apps/analytics/divisional_pnl.py` | via finance_wip |
| Forecast / EAC | `apps/analytics/eac.py` | effective CV (G4) |
| Project Snapshot Δ % × CV, Command Center, Project Map towers, customer / PM / sector pages | `apps/dashboard/views.py`, `project_map.py`, `queries.py` | effective CV; hover shows SL's number and the basis |
| Ratings & field ratings (weights √(CV/median), eligibility CV > 500, margin preservation) | `ratings.py`, `field_ratings.py` | effective CV; jobs whose basis is `billing_final` with a > 20 % move are excluded from *margin preservation* (sold vs final would be circular) but keep their weight |
| Change-order history | `change_orders.py` | keeps SL raw values (it documents what SL did) — untouched |
| Project page scoreboard "Sold" | `project_detail.html` | effective, with banner: rule, evidence, both numbers |

## 7. Phases and effort

| Phase | Scope | Effort |
|---|---|---|
| **A** | model fields + migration; rule engine with unit tests on the audited cases (250038, 240045, 231228, 208844, a clean additive job, a legit CO job); nightly step in `refresh_all`; effective CV wired into snapshot, WIP, P&L, forecast, snapshot page; project banner; Data Quality section; G1 | 1–2 days |
| **B** | override model + review queue page + audit; export list | 1 day |
| **C** | basis-dated WIP history; G2; ratings handling; weekly digest of new flags and any single correction > $100 k | 1 day |
| **D** | back-test: replay rules against the 2019–2025 closed cohorts and confirm no legit additive job (class C) is ever auto-corrected — this is the acceptance test, added to `tests/unit` as fixtures | ½ day |

Acceptance for A: 250038 shows sold $564,685 / WIP $1,588 with a banner; division 040 WIP drops by ≈ $0.5 M;
no class-C job in the audit changes value; `refresh_all` checksum unaffected (the rules never touch loaders).

## 8. Questions for Owner (defaults in brackets)

1. Tolerance for "billed = CV": 3 % [yes].
2. "Quiet" window for `billing_final`: 60 days without hours, postings, commitments or open sales-order lines [yes].
3. Apply `billing_final` to over-billed quiet jobs as well (CV rises to billed) [yes].
4. Apply `billing_final` to **closed** cohorts (moves sold GP on 417 closed jobs, 5.7 %; 150 of the over-stated
   ones move by > 10 pts) so ratings and cohort "sold" totals use reality [yes — with the margin-preservation
   exclusion in §6].
5. Who may override: `finance.write` holders, or Owner only [finance.write, audited].
6. Export the clean-up list to the accountants at all [export only; no expectation that SL is fixed].
7. Alert threshold for a single nightly correction: $100 k [yes], delivered as a Data Quality "error" plus the
   existing failure notification path.
