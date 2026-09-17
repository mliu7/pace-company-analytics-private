# Pace Company Analytics — SharePoint, P-drive & Planning Integration Spec v1.2 (2026-09-10)

Status: **v1.2, approved by Owner on 2026-09-10 — being built.** Implementation status per phase: `docs/sharepoint_integration_build_log.md`; parity checklist against Appendix A: `docs/build_logs/parity_status.json`. Written as: **v1.2, for Owner's approval — no code written.** v1.0 was written from the sources below, then reviewed and
revised twice (Section 19 lists every change of each pass). Supersedes draft v0.1 (2026-09-08).

Sources: Owner's answers of 2026-09-10 (Section 0.2); the read-only SharePoint exploration
(`docs/11_sharepoint_exploration.md`); an office-network discovery run on 2026-09-10 (Section 1.3); a line-by-line
inventory of the eight vibe-coded dashboards under `internal_reports/Sharepoint Integration/` (their code and JSON were
read to learn what they do — nothing from them is a data source; Appendix A maps every capability to its PCA home).

---

## 0. Purpose, scope, decisions

### 0.1 Purpose
Extend PCA with everything the company keeps outside PTT and SL: the Project Portal (bids, and the estimator of record),
the per-job documents on SharePoint and on the P: drive, Planner, and the planning tools the controller's team prototyped as
stand-alone dashboards (project status, active tasks, resource scheduling, punch lists, BOM/labor approvals, pricing
intelligence). Five outcomes:

1. **Estimators** become a first-class rated role: who bid what, at what margin, and how those jobs turned out.
2. **The bid pipeline** is visible to everyone, live from the Project Portal, at least as capable as the "Revenue
   Tracker" and "Project Portal" dashboards and easier to use.
3. **Production planning** — status board, active-task report, resource scheduler, punch lists, BOM/labor approvals —
   moves into PCA with live SL/PTT/SharePoint data and shared, audited state instead of JSON files on a drive.
4. **Documents** for every job are one click away and machine-readable (text, checks on proposals).
5. **Planner** is read into PCA once IT grants the permissions, and the AV production workflow it holds is surfaced.

### 0.2 Decisions taken (Owner, 2026-09-10)
| # | Decision |
|---|---|
| D1 | Scope includes the **network share** ("P:" on the Inuvika desktop), not only SharePoint. Exclude HR, Personnel Changes, personal sites and the CPS Genetec video library. The target is the **per-project folders** PMs fill. |
| D2 | **Bidder = estimator of record.** Blank Bidder → best guess (4.1), flagged as inferred. **ExampleSurname always = Example employee.** Division = the SL job's division when linked, else the bidder's division. Sales Rep **OT = "Other" = house account, no commission**. Who estimates (PMs, DMs, dedicated estimators) is discovered from the data, not assumed. |
| D3 | Proposals live on the **network share** (Section 7). |
| D4 | **Open pipeline = Quoting + Submitted.** Legacy statuses count in hit rates. **SL is the source of truth for "won"**: a bid whose job exists in SL with billings is won whatever the portal says. **Expected bookings weight by the stated probability**; historical hit rate is shown alongside. Which of "Probability of Close" / "% OF CLOSE" is used is unknown → PCA reads both, uses "Probability of Close" for weighting, and reports the fill rates (5.2). |
| D5 | **Everyone sees the whole pipeline** (no division scoping for bids). **Estimator ratings are concealed to Owner only**, like every other rating. |
| D6 | **Planner is in scope.** Tom uses it extensively; PCA reads it once permissions arrive and mirrors its boards (Section 9). |
| D7 | The eight dashboards under `internal_reports/Sharepoint Integration/` define the **parity floor**: everything they can do, PCA must do on live data, plus more, more usably, without an excessive number of nav entries (Section 10, Appendix A). Their spreadsheets/JSON were SharePoint exports and are **never** a data source. |

### 0.3 Non-goals
Writing to SharePoint, Planner or the P: drive (read-only by permission and by code guard, like PTT/SL). Replacing the
Project Portal as the place bids are entered (Section 18, item 1). Indexing OneDrives, mail, HR. Rendering Office files
in-browser (PCA deep-links to SharePoint / the share and previews PDF, images and extracted text). Re-creating the
dashboards' file-sync plumbing (P:-drive JSON, 3–5-second polling, `.bak` writes, last-writer-wins) — PCA replaces it
with a database, row-level edits and an audit trail.

---

## 1. Sources and what they hold

### 1.1 SharePoint (Graph app "Pace Secure AI SharePoint Reader", role `Sites.Read.All`) — verified 2026-09-08
* **Project Portal › Project List** (3,444 rows since 2022; **Project Archive** 2018–2021, same shape): the
  bid/project registry. Columns: Job Number (SL number when awarded; 1,453 filled; 6- or 12-digit; 1,369 match SL;
  415 active), Client (lookup, 1,431), Project Name, Status (Quoting / Submitted / Awarded / Lost / Did Not Bid + legacy
  Completed, In Progress, Budget, Re-bid, On Hold, No Decision), Bid Due / Date Submitted (2,364) / Date Awarded (1,344)
  / Start / End, **Budget** (2,553), **Project Value** (2,685), **Bidder** (the estimator), Sales Rep, Probability of
  Close (0/25/50/75/100), % OF CLOSE (10–90 %), PO Number, Sales Order #, COMMENTS, Walkthrough Date/Personnel, BOM
  Status, Ball In Court, Increment Number, Project ID, Created By; attachments on 116 rows (49 active).
  **Versioning is on**: 99 of 120 sampled rows carry status changes in their version history, median 7 versions,
  ~22 days between moves — stage timing is reconstructible back to 2022.
* **Division libraries with per-job files**: Premise Security › Active Jobs (`<SL#> - <name>` folders), AV Division
  (`/sites/TEST`, 550 per-job `.xlsx` trackers named by SL number), AV TEAM (261 scope/quote PDFs named by SL number),
  SS PM (AV quote-numbered `YY-NNNN` folders), OT AV quotes and proposals (348 files in named folders).
* **The lists the dashboards were meant to mirror are empty templates** (040/070/080 Master Schedule sites, Dashboard
  site, SS PM "BOM - Base Building"): task, status, punch and scheduling data never lived in SharePoint — it lives in the
  dashboards' P:-drive JSON files, the PMs' Master Schedule workbooks and Planner. Small live lists that matter: Tech
  Team › Active Job List (156), 070 Schedule, Personnel Schedule (53), AV Project Schedule Calendar.
* **Not readable yet**: list-item attachments (needs a certificate credential + SharePoint API permission), Planner
  (`Tasks.Read.All`, `Group.Read.All`, `User.ReadBasic.All`). Instructions went to IT on 2026-09-08.

### 1.2 Planner (the controller's boards)
Three plans are visible through their SharePoint attachment folders ("AV-Shipping Line-up", "AV Fabrication", one tied
to a Dynamics organisation). The AV division's Teams channels are named as a production sequence — *1. Installation
Schedule, 1.1 Install Line-UP, 2 Project Tracker, 3 Fabrication.Shipping Tracker, 6 Programming.Commissioning, 7 Signal
Flow Queue, AV A-Card, AV Signoff Sheet* — and the Project Status dashboard's 21 statuses use Planner's label colours.
Working assumption (verified the day permissions land): each channel has a plan, buckets are stages, tasks are jobs,
labels are the statuses. Section 9.

### 1.3 The network share ("P:") — discovered 2026-09-10, credentials pending
* The Mac is on the office network (VPN up; DNS 192.0.2.10/11; domain `pace-systems.com`). A reverse-DNS sweep of
  192.0.2.0/24 found 92 named hosts; 24 answer on SMB. **All of them reject anonymous and guest access**, the Mac holds
  no Kerberos ticket, and the Inuvika client stores only `gateway.pace-systems.com` — a share cannot be listed without
  domain credentials.
* File-server candidates by NetBIOS name: **PACE-FPS01** (192.0.2.58), **PACE-FPS1** (192.0.2.28), **PACE-FPS3**
  (192.0.2.21), **PACE-NAS01** (192.0.2.41) and the Inuvika file service **OVD_FS** (192.0.2.17). The dashboards' notes
  prove the P: root contains `PACE_Dashboard\…`.
* **What Owner does once (one minute)**: in the Inuvika desktop, right-click the P: drive → Properties → note the
  `\\server\share` target; *or* in Finder press ⌘K, enter `smb://pace-fps01.pace-systems.com`, sign in as
  `PACE\<user>`, read the share list (repeat for `pace-fps1`, `pace-fps3`, `pace-nas01`). Give PCA the UNC path and the
  project-folder root beneath it.
* **How PCA reads it** (13.3): a read-only SMB mount by a service account whose password lives in the macOS Keychain,
  never in the repo; every read goes through `apps/ingestion/sources/share_client.py`, which refuses writes.

### 1.4 The dashboards (the parity floor) — Appendix A is the full map
| Dashboard | What it is | Data today | PCA home |
|---|---|---|---|
| Revenue Tracker (`project_portal_dashboard.html`) | Pipeline KPIs, % Report, PM Quoting pivot, Awarded margin | Project List export via shared JSON | 5 (Bids) |
| Project Portal replica (`project_portal_list.html`) | Editable grid + Home tiles + Issue tracker + Archive + Client list + Calendar + notes | Project List export + shared JSON | 5 (Bids) |
| Project Status (`pace_planner.html`) | Editable task grid per division, two status pills, hours left, notes | P:-drive JSON | 6.1 |
| Active Task Dashboard | Priority by critical stop, KPIs, charts, PM print/Excel report | P:-drive JSON | 6.2 |
| Resource Scheduler | Weekly crew allocation, phases, caps, PTO, needs queues, Gantt | P:-drive JSON | 6.3 |
| Punch Lists 040/070/080 | Punch items per project: BIC, criticality, due, sign-off; overdue analysis | P:-drive JSON | 6.4 |
| BOM & Labor Approval | Request/approve workflow with attachments | P:-drive JSON | 6.5 |
| Pricing Intelligence | Estimating workbench: 663k-row dealer catalog, rooms, labor units, exports | browser localStorage | 8 |
| Dashboard Hub | Launcher | — | 10 (sidebar) |

---

## 2. Architecture

Follows the CNET pattern (`docs/09_010_sales.md`): a source client under `apps/ingestion/sources/`, a raw archive, loaders
in a domain app, a step in `refresh_all`, Data Quality checks, docs, tests. Two things are new and are stated plainly:
**(a) PCA becomes the system of record** for planning data that has no source system (statuses, schedules, punch items,
approvals, estimates, bid notes) — a write path to PCA's own tables with audit, never to a source; **(b) two more
read-only sources** (Graph, the SMB share).

```
apps/ingestion/sources/graph_client.py   Microsoft Graph: client credentials, paging, delta, 429/503 back-off,
                                         raw JSON archive (~/Library/Application Support/PaceCompanyAnalytics/graph/),
                                         GET-only guard (any other verb raises) — SharePoint lists/drives/versions, Planner
apps/ingestion/sources/share_client.py   SMB share: mount check, walk with excludes, stat/hash, read-only open;
                                         refuses write modes; never follows symlinks off the mount; size caps
apps/bids/          Bid, BidVersion, BidderAlias, BidClientAlias, BidSnapshot, BidNote, BidFollowup, BidRisk      (3, 4, 5)
apps/planning/      StatusRow, StatusHistory, Resource, ResourcePto, PlanProject, PlanPhase, PhaseRange, Assignment,
                    PunchProject, PunchItem, ApprovalRequest, ApprovalAttachment, ApprovalEvent                    (6)
apps/documents/     Repo, Folder, File, DocLink, DocText, DocFinding, ListAttachment                               (7)
apps/estimating/    CatalogSource, CatalogItem, LaborRate, Estimate, EstimateVersion, Room, Line                   (8)
apps/planner/       PlannerPlan, PlannerBucket, PlannerTask, PlannerTaskHistory (read-only mirror)                 (9)
apps/dashboard/     one sidebar group "Bids & Planning"; project / customer / bid page tabs; search groups         (10)
```

**Keys.** Every bid keeps `sp_item_id`; `job_number_raw` keeps what was typed; `project` is resolved with the 000000
rule (`260071` ≠ `260071000000`, CLAUDE.md). Planning rows key on `core_project` (the dashboards' 6-digit `job_key` is
kept only for display and matching of legacy imports; the tracker's own comment documents 37 six-digit keys shared by
change orders, which is exactly why the canonical number is the key).

**Refresh.** `refresh_all` gains a best-effort `sharepoint` step (an outage never sinks the refresh, like CNET): Project
List full pull (7 pages, seconds), versions only for rows whose `lastModifiedDateTime` moved, library indexes via Graph
delta, share walk incremental by mtime/size, Planner pulled per plan and diffed by task etag (Planner has no delta
query), text extraction and checks for new/changed files only. `refresh_sharepoint --full` re-pulls everything. A daily `BidSnapshot` starts the pipeline history the day this
ships (as `DailyFinanceSnapshot` did for finance).

**PCA-owned planning state.** Where the dashboards kept JSON on the P: drive, PCA keeps tables with `created_by`,
`updated_by`, timestamps, a `version` column and an `AuditEvent` (the Access app's audit log). Edits go through Django
views with CSRF and capability checks; they are row-level and immediate. A stale form gets "changed by X at hh:mm —
reload" instead of silently losing the other user's edit. No polling files, no last-writer-wins, no `.bak`.

**Read-only, always.** Source clients hold no write credentials; the Graph app has read roles only; the share is mounted
read-only. Secrets stay in `.env` / Keychain.

---

## 3. Data model (the new local tables)

**apps/bids**
* `Bid` — sp_item_id (unique), source (`list` / `archive`), portal_project_id, job_number_raw, project (FK, null),
  client_name, client (FK `core_customer`, null; via `BidClientAlias`), project_name, status (5.1 enum), status_raw,
  bid_due, submitted_on, awarded_on, start, end, budget, value, bidder_raw, estimator (FK `core_employee` via
  `BidderAlias`), estimator_inferred (bool), sales_rep_raw, salesperson (FK), house_account (bool = rep "OT"),
  probability, pct_close_raw, ball_in_court, bom_status, walkthrough_date, walkthrough_raw, po_number, sales_order,
  comments, attachments_flag, created_by_raw, portal_created, portal_modified, web_url, division (derived, 4.2),
  won_by_sl (bool, D4), last_seen_run.
* `BidVersion` — bid, version_no, modified_at, modified_by, status, value, budget, bidder, probability, job_number →
  stage entry/exit, re-pricing history, who changed what.
* `BidderAlias` — raw → employee, role (estimator / sales_rep / walkthrough / pm), confidence, source (`rule` /
  `manual`); unresolved values become DQ issues, edited on the Data Quality page.
* `BidClientAlias` — portal client name → `core_customer` (rule / manual).
* `BidSnapshot` — date × division × estimator × stage: count, value, weighted value (probability), aged buckets.
* `BidNote` (typed: general / client follow-up / internal / pricing question / risk), `BidFollowup` (owner, due,
  done), `BidRisk` (severity, owner, mitigations) — PCA-owned (5.5).

**apps/planning** (Section 6) — `StatusRow`, `StatusHistory`, `Resource`, `ResourcePto`, `PlanProject`, `PlanPhase`,
`PhaseRange`, `Assignment`, `PunchProject`, `PunchItem`, `ApprovalRequest`, `ApprovalAttachment`, `ApprovalEvent`.

**apps/documents** (Section 7) — `Repo` (SharePoint drive | share root), `Folder`, `File` (path, name, ext, size,
mtime, hash, web_url or unc), `DocLink` (file / folder ↔ project | bid | customer; rule; confidence; state),
`DocText`, `DocFinding`, `ListAttachment` (name, size, bid; readable once the certificate exists).

**apps/estimating** (Section 8) — `CatalogSource` (vendor price file: name, vendor, dated, rows), `CatalogItem`
(mfr, part, normalized part, description, cost, msrp, map, category, source, effective date, superseded_by),
`LaborRate` (id, group union / non-union, cost, sell, effective), `Estimate` (meta, bid FK, owner, status), `Room`,
`Line` (item, qty, cost, markup, sell, 11 labor-unit columns, area), `EstimateVersion`.

**apps/planner** (Section 9) — `PlannerPlan` (group, title, channel site), `PlannerBucket`, `PlannerTask` (title, bucket,
progress, priority, start, due, completed, labels[], checklist counts, assignees[], project FK by number match),
`PlannerTaskHistory` (etag deltas).

---

## 4. Pillar 1 — Estimators

### 4.1 Identity (D2)
* `Bidder` → `BidderAlias` → `core_employee`. Rules: fold case and whitespace (the list holds "ExampleSurname" and "ExampleSurname",
  "OT " with a trailing space); surname match; **ExampleSurname → Example employee**; ambiguous surnames resolved by division
  and role, else manually — ExampleSurname (Example employee, 070 PM), ExampleSurname (ExampleSurname, 080 head PM), ExampleSurname (Stephanie),
  ExampleSurname (Jim), ExampleSurname (Herb), ExampleSurname (ExampleSurname, three duplicate employee rows); nicknames (SULLY, DOBO) and
  portal-only names go to the manual alias table. Every alias shows its rule and confidence.
* **Blank Bidder (135 rows)** → inferred, in order: the Sales Rep when the rep is a known estimator (never OT); else
  the SL job's project manager when the bid is awarded and that PM appears as a bidder elsewhere; else the portal row's
  Created By; else "unassigned". Marked `estimator_inferred`; excluded from ratings; counted in volume with a marker.
* The awarded bid's estimator is written to `core_project.estimator` (the empty slot the ratings engine already
  expects). The bid's Sales Rep is kept on the bid and compared with SL's salesperson; nothing is written to SL fields.
* **Who estimates** is reported, not assumed: the Estimators roster lists every alias with bids per year, SL role (PM /
  head PM / regular), division, active flag — the answer to "PMs, DMs or dedicated estimators". In 2025–26 the top
  bidders were ExampleSurname 288, ExampleSurname 244, ExampleSurname 193, ExampleSurname 152, ExampleSurname 105, ExampleSurname 87 (inactive), ExampleSurname 78.

### 4.2 Division (D2)
Order: the linked SL job's division → the division where the estimator has managed the most SL jobs → the estimator's
home subaccount → "unassigned" (counted, shown). Verified: ExampleSurname (home 080, 565 jobs in 070 → 070), ExampleSurname (home
0000, 968 jobs in 040 → 040).

### 4.3 Metrics (per estimator × division × period; every figure drills to the bids)
* **Output**: bids created / submitted per month, $ submitted, median bid size, share of the division's bids, hygiene
  (blank budget / value / dates / probability).
* **Hit rate**: won ÷ (won + lost), count- and $-weighted; Did Not Bid, No Decision, On Hold reported as their own
  rates; **won = portal Awarded / Completed / In Progress OR SL job with billings (D4)**; by customer, sector, size
  band, house vs rep-sold, year.
* **Cycle**: bid due → submitted (lateness), submitted → decision, awarded → SL job created (leak: awarded with no Job
  Number after 14 days), portal Start vs SL first work date.
* **Bid margin**: (Value − Budget) ÷ Value at submission; distribution; drift; re-pricing between versions (how
  often, how much, direction).
* **Estimate accuracy** (the headline): on the SL job, **final GP % − bid margin** and **SL direct cost ÷ portal
  Budget**; same closed-job population and stabilisation rules as the PM rating (`ratings._population`). First cut on
  799 closed jobs: mean bid margin 20.8 % vs mean final GP 32.1 % — the gap itself (change orders, conservative budgets,
  SL cost timing) is explained on the page before anyone is rated on it.
* **Probability calibration**: stated probability vs realised outcome per estimator and per bucket (the number the
  tracker's "Avg Close %" tile hides).

### 4.4 Rating (extends `apps/analytics/ratings.py`, entity `estimator`; concealed to Owner, D5)
Same ridge + empirical-Bayes method as the PM rating (`METHOD_VERSION` bump), same controls (size, sold margin, labor
/ material share, mode, solution, customer, sector, year) plus the **PM as a control** — the estimator and the PM are
different people on most jobs, so each is identified only with the other held. Metrics `estimate_accuracy`,
`hit_rate_adj` (logistic, context-adjusted win probability vs expected), `budget_accuracy`, `probability_calibration`.
Thresholds (12 closed jobs, 3 customers, 2 solutions) for the accuracy metrics; ≥ 30 decided bids for hit rate.
Selection bias is named and handled: outcomes exist only for won jobs — accuracy models use awarded-and-closed bids,
the hit-rate model all decided bids; both report counts and intervals. Model card `docs/model_cards/estimator_v1.md`;
blind review gate before a leaderboard leaves Owner's screen.

### 4.5 UI
* **Estimators page** (`/estimators/`): roster table — bids, $ submitted, hit rate (count / $), median bid margin,
  estimate accuracy, cycle times, open queue, hygiene; division / period filters; every number opens the bid list.
* **Estimator detail** (`/estimators/<key>/`): trends (hit rate, bid margin vs realised GP by quarter, calibration
  curve), bid book (open / decided), customers and sectors, jobs estimated with SL outcomes, findings on their proposals.
* **Project page**: a **Bid** card — estimator, bid dates, value vs contract value, budget vs actual cost, status trail
  (from versions), link to the portal row; the Documents tab (7).
* **Ratings page**: estimator block once publishable (concealed tier).

---

## 5. Pillar 2 — The bid pipeline (Project List, live)

### 5.1 Stages and truth
`quoting → submitted → (awarded | lost | did_not_bid)`; legacy: Completed / In Progress → awarded; Budget → quoting
(flag `budgetary`); Re-bid → submitted (flag `rebid`); On Hold → on_hold; No Decision → no_decision; Needs Assessment →
quoting (flag); blank → unknown. **Won-by-SL override (D4)**: a bid linked to an SL job with billed revenue is *won*
whatever the portal says (shown with a "portal says X" marker and a DQ issue). Open pipeline = quoting + submitted;
on_hold is shown separately. Stage entry dates come from `BidVersion` when present, else the date columns, else
`modified`.

### 5.2 Probability
Both fields are read. `Probability of Close` (0 / 25 / 50 / 75 / 100; 25 % is the default on 2,496 rows) is the
weighting field for expected bookings (D4); `% OF CLOSE` (10–90 %) is shown when filled; both fill rates are reported on
the Data Quality page so Owner can retire one. Calibration (4.3) says whether the stated probabilities mean anything. A
blank probability is **never** scored as 0 (the tracker did that); it is "unscored" and listed.

**Expected bookings** = Σ over open bids of value × probability, placed in the 30 / 60 / 90-day window by the bid's
*expected decision date*: Date Submitted (else Bid Due) + the median submitted-to-decision cycle for that division
(from `BidVersion` history; the estimator's own median when it rests on ≥ 10 decisions). The historical-hit-rate
variant replaces the stated probability with the estimator's trailing-24-month $-weighted hit rate. Both are shown.

### 5.3 Pages — one sidebar entry **Bids** with in-page tabs
1. **Overview** (the Revenue Tracker, corrected and extended). KPI row: open pipeline $ and count (with the Submitted /
   Quoting split as sub-labels so the controller's "Pipeline = Submitted only" reconciles), probability-weighted forecast (and
   "% of pipeline"), expected bookings 30 / 60 / 90 days (weighted by probability, with the historical-hit-rate variant
   beside it), submitted this month, awarded this month ($ and average bid margin), distinct bidders, average bid size,
   average close % (+ N scored, unscored count), hit rate trailing 12 months (count / $), median cycle. Charts, each a
   filter when clicked and each aggregated "excluding its own facet" (SharePoint-style): bidder pipeline (top 8 + "all"),
   close-probability buckets, ball-in-court, stage funnel by division, win-rate trend by quarter, **monthly quoting
   pivot** (rows = estimator *or* rep *or* PM, columns = months, values = $ submitted / count, expandable Status → Year →
   Month → Client → Project, grand total — the "PM Quoting" tab; when PM is blank the pivot says "no PM" instead of
   silently substituting the bidder). Queues: **needs attention** (past due, missing due date, missing bidder,
   submitted without value, awarded without job #, BOM needed), leaks (awarded without SL job), stale quoting (30+ days
   without movement), follow-ups due this week.
2. **Board** — open bids as cards by stage (Quoting / Submitted / On hold) with due date, estimator, rep, value,
   probability, ball in court, walkthrough, BOM status, days in stage, red edge when overdue; filters division /
   estimator / rep / client / size / house-vs-rep; the Portal replica's "status board" but live.
3. **Calendar** — bid due dates, walkthroughs, submissions, awards and awarded start dates (work-week / week / month),
   type filter, persisted view, event → bid detail; "+N more" expands in place.
4. **List** — every bid with every column: column chooser and order, sticky header **with frozen identity columns**
   (number, name), multi-value column filters with counts computed from the other filters, facet chips, group-by with
   collapsible groups, totals row (Σ value, Σ budget over the filtered set), free-text search, saved views (mine /
   shared), CSV / XLSX export **of the filtered view**, print. No silent row caps: full data via JSON with a ten-row
   own-scroll area, like the WIP page. Archive is the same list with the archived-status facet.
5. **Analytics** — hit rate by client / sector / size band / rep × estimator; cycle-time distributions; value vs final
   contract-value drift; probability calibration; pipeline history from `BidSnapshot`; "% report" (share of bids per
   probability bucket, per status, per bidder); client rollups (projects, quoting, submitted, awarded, lost, value per
   client — the replica's Client List, on `core_customer` where the alias resolves).
6. **Bid detail** (`/bids/<id>/`) — every field, version timeline (who / when / what), linked SL job with its outcome,
   documents (7), notes / follow-ups / risks (5.5), estimate (8), link to the portal row and "open in portal to edit".

Elsewhere: the **Command Center** gets an expected-bookings tile and the division funnel; the **Project Snapshot** gets
"bids due / submitted / awarded this week" for its window; **global search** gets a Bids group (number, name, client,
estimator) beside the Documents group.

### 5.4 Personas (D5: everyone sees everything)
Exec: Overview. DM: Overview filtered to a division (a remembered filter, not a restriction), Calendar. PM: Calendar
(awarded starts), List filtered to their jobs. Estimator: Board + Calendar filtered to self, own hit rate vs division on
the Estimators page. Admin: DQ. First screen per persona is remembered with `PCA.pref`.

### 5.5 What the Portal replica added that SharePoint lacks (PCA-owned, audited)
Typed conversations / comments per bid, follow-ups (owner, due date, done), risks / blockers, a notebook (general
notes per division) — `BidNote`, `BidFollowup`, `BidRisk` with author and time, on Bid detail and rolled up on Overview.
The replica's grid editing, undo / redo, recycle bin, "reset original", column-colour registry and Excel import of the
list are **not** rebuilt: PCA does not edit the portal; "+ New bid" and "edit" deep-link to the portal's own forms, and
PCA re-reads within the refresh cadence (a manual "refresh bids now" button runs the list pull in seconds). Status,
BOM, walkthrough and ball-in-court colours follow PCA's tag palette.

### 5.6 Parity — Appendix A.1 (Revenue Tracker), A.2 (Portal replica), A.4 (Hub)

---

## 6. Pillar 3 — Production planning (the P:-drive dashboards, rebuilt on PCA data)

The five tools below share one fact: their data has **no source system**. PCA keeps it (Section 2) and enriches it with
what SL / PTT / SharePoint / Planner know. Everything is division-scoped by data (040 / 070 / 080 today, any division
tomorrow), PM-aware, keyed on the SL project. The pages live under one sidebar entry **Production** (tabs: Today ·
Status board · Active tasks · Punch lists · Approvals · Planner boards) plus **Resource Scheduler** as its own entry.

### 6.0 Today (replaces the hub)
One screen per division with the counts that matter now — active tasks, past critical stop, due ≤ 14 days, needs
staffing, overdue punches, critical punches, pending approvals, bids due this week — each a link into the tab.

### 6.1 Status board (from `pace_planner.html`)
* **Model** `StatusRow`: project (FK; a free name only until an SL number exists), division, task name, **phase status**
  and **equipment status** (the 21-value vocabulary with Planner colours and the misspelling aliases), pm, engineer,
  start, end (critical stop), hours_left, notes, completed (manual only, reversible), last_updated (+ by), source
  (`manual` / `planner` / `import`). `StatusHistory` on every change. Percent complete is a real column (the dashboard
  carried it but never showed it).
* **Live enrichment** the dashboard never had: SL contract value and billed, PTT hours used vs budget, PTT remaining
  hours, last PTT entry, the bid's estimator — read-only columns and a hover card.
* **Page**: Active / Completed tabs with counts; division tabs; global search; the 13-column editable grid (inline
  edits stamped with who / when; drawer edit form incl. start date; new / duplicate / delete with in-page confirm),
  column resize + auto-fit persisted, zoom / fit, height; sort on every column; **Send to Scheduler** creates a
  `PlanProject` (6.3) carrying name, division, PM, hours as union, 8 h/day, Mon–Fri, statuses, project, start / end,
  engineer and notes — and the scheduler *receives* it (the original sent a payload nobody received).
* **Planner sync** (9): when Planner is readable a row's statuses follow the Planner labels of the matching task; each
  cell shows "from Planner"; a manual edit wins until the Planner value changes again (both are kept, with history).
* The Master Schedule Excel import stays as an **admin transition tool** (same alias rules, multi-sheet, division from
  cell / sheet / file, 6-digit number, merge that preserves completed / manual rows) — until the P:-drive JSON is retired.

### 6.2 Active tasks & PM report (from `active_task_dashboard.html`)
* A **view** over `StatusRow` (no second data set): active = not completed and percent < 100.
* Priority by critical stop: past due / ≤ 14 days / on track / no date (today counts as due, local time, live clock);
  starting soon (start within 14 days); union hours remaining; KPI tiles (active, union hrs, starting soon, past critical
  stop, missing stop date, urgent union hrs = past due + due soon); charts with click-to-filter, each aggregated
  excluding its own facet (union hours by PM top 12 + all, union hours by critical-stop month, tasks by priority, tasks
  by status ∪ status V2 counting distinct projects); PM filter global across divisions **including PM2**; PM focus bar;
  **PM report**: print view and 4-sheet styled Excel (letterhead, workload, dates / status, attention required, all
  active) with live totals and the logo; project detail merging the same job across divisions; deep link to the status
  board row with a highlight (PCA's `?hl=`).
* Fixed on purpose: "today" moves; PM2 counts; edits persist; no seed snapshot; no dormant importer.

### 6.3 Resource Scheduler (from `resource_scheduler.html`)
* **Model**: `Resource` (FK `core_employee` — the roster comes from PTT / SL people, not a typed list; trade Union /
  Subcontracted Union / Non-Union; max hrs / week default 40; divisions; approved-overtime flag), `ResourcePto`
  (ranges), `PlanProject` (project FK, name, PM, division, earliest / latest on-site, access window, days-needed
  toggles, total union / non-union hours, colour, short-project flag, completed-early {date, scope}, source),
  `PlanPhase` (11 named phases with fixed trade and Test either; date window, weekend rule none / sat / sun / both,
  man-hours, explicit dates) + `PhaseRange`, `Assignment` (resource × plan project × date × hours × phase × note).
* **Maths kept exactly** (Appendix A.5): crew plan `ceil(hours ÷ 8)` resource-days spread front-loaded over covered
  days ("2 days of 3 resources, 1 day of 2"); per-day shortfall text; phase progress by distinct-people × hours slots;
  weekly cap PTO-adjusted; daily cap 8 h; OT bypass; Copy Allocation with conflict preview (PTO / elsewhere / weekly
  cap); Completed Early releases only later allocations for the chosen scope. **One** capacity rule everywhere (the
  dashboard had three).
* **Page**: week board (Mon–Fri + weekend columns when used; per-day free hours; projects lane with phase tags green /
  amber / red; one row per person with utilisation bar; chips per project-day; PTO ✕; daily load footer), Needs
  Staffing rail (division tabs, Needs Resources / Needs Dates), Projects view (Gantt with today line and month / week
  ticks; cards with allocation bars and phase chips), person-day and project-staffing modals, scheduling assistant in
  the editor, exports (week CSV, per-resource weekly print view, XLSX), drag-and-drop of a chip between days (new).
* **Live enrichment**: PTT hours already worked per person per job; PTT remaining-hours vs planned hours; SL labor
  budget vs planned; each person's actual PTT entries against the plan — **schedule adherence** (planned vs worked per
  week), the first such number the company has had.

### 6.4 Punch lists (from `040_punch_list_dashboard.html`)
* **Model**: `PunchProject` (project FK; code; title; PM; status chips come from `StatusRow`), `PunchItem` (active
  flag, date entered, description, BIC department — the nine plus an editable list —, criticality 1–5, due by, date
  completed, assigned, engineer sign-off, verified, photos / attachments uploaded into PCA's own document store),
  history.
* Derived status exactly as the dashboard (completed = date completed set; overdue = open and due before today;
  critical = open at 4–5; "Active" is visual only, explained in-page) — with "today" live.
* **Page**: division switcher; PM overview cards → PM detail with peekable project sections; All Projects grid with PM
  chips / status filter / sort / search; project drawer with the spreadsheet-style item table (sortable, column hide);
  Overdue analysis (5 exec KPIs, bars by BIC and by PM, sortable overdue table with jump links); Excel export (sheet
  per project) and CSV; projects seeded from the status board, never deleting local items; PM roster from
  `core_employee`, not a hard-coded list.

### 6.5 BOM & labor approvals (from `bom_labor_approval.html`)
* **Model**: `ApprovalRequest` (project FK or free text until linked, type BOM / Labor, requested by = the signed-in
  user, approver (employee), needed by, department, notes, status Pending / Approved / Reopened, decided by / at),
  `ApprovalAttachment` (files in PCA's own document store, not inline base64), `ApprovalEvent` (audit).
* **Page**: KPI cards as filters (total, pending, approved, with files, **overdue on needed-by** — new), search, status /
  type selects, table sortable by column (newest first by default), create / edit dialog with attachment add / remove
  and required fields, **Approve requires `planning.approve`** (the signed-in approver's identity is recorded — no
  typed names), reopen, delete with confirm, CSV export of the filtered view; approvals link to the estimate (8) and the
  bid. Notifications: an in-app queue for approvers ("3 requests waiting") on Today.

### 6.6 Parity — Appendix A.3, A.5–A.8

### 6.7 Cut-over from the dashboards (one time)
The status rows, punch items, schedule roster / projects / assignments and approval requests exist **only** in the
dashboards' P:-drive JSON files (the repo copies under `internal_reports/` are stale exports). On cut-over PCA runs a
one-time, reviewed import of the *live* files from the share (`P:\PACE_Dashboard\…\data\resource-scheduler\*.json`,
the one exception to the `PACE_Dashboard` exclude), with a dry-run report Owner approves first (rows, unmatched project
numbers, unknown people), then the dashboards are retired. Rows that match no SL project are kept with a free name and
flagged. This is the only time PCA reads those files; it never writes them.

---

## 7. Pillar 4 — Documents (SharePoint libraries + the P: drive project folders)

### 7.1 Index and linking
* **SharePoint**: the allowlisted libraries (1.1; configurable), Graph delta.
* **P: drive**: the project-folder tree (root from Owner, 1.3) walked read-only with an exclude list (HR, personnel,
  `PACE_Dashboard`, video); folder names with an SL number or a quote number are linked; the PM drop folders are the
  target (D1). Folder conventions are learned from the tree and reported per division (how many folders follow each
  naming style, how many follow none).
* Links by: folder-name number, file-name number, quote reference (SS PM `YY-NNNN` ↔ `core_project.quote_reference`
  once the rule is confirmed), bid client + name (fuzzy, low confidence, reviewable), list attachments (after the
  certificate). Every link records rule and confidence; low-confidence links are marked and can be confirmed / rejected
  (PCA-owned state).

### 7.2 Access to the files
* **Project page › Documents tab** (also Bid detail and Customer page): every linked file — name, type, size, modified
  by / when, location (SharePoint or P: path), confidence — grouped by location and folder; one click opens the file in
  SharePoint (`webUrl`, the user's own login) or opens the share path (`smb://` link; the UNC is shown for the Windows
  desktop); **in-app preview** for PDF and images streamed through PCA (`/documents/<id>/content`, cached, behind
  capability checks); "recently changed on this job".
* **Global search** gets a Documents group (names now, full text after 7.3).
* **Documents page** (`/documents/`, under *Projects*): browse by location / project / type / age; unlinked-files queue;
  folder-naming hygiene per division.

### 7.3 Text and proposal checks
Extraction (pdf via pdfplumber with OCR fallback for scanned pages, docx, xlsx cell text, txt, msg bodies) into
`DocText`; searchable. **Proposal checks** (rules first): template / version fingerprint, missing sections (scope,
exclusions, clarifications, terms, schedule, price, signature block, revision / date), consistency with the bid (name,
number, client, price vs Project Value, dates), exclusions / clarifications / terms extracted and compared with the
standard clause set the estimators supply, payment-terms and warranty deviations → `DocFinding` on the bid, the
estimator and the Documents page with the evidence (page, quoted text). **Model-assisted review** (Claude API) stays
**off until Owner approves it explicitly**; rules-only is the default.

---

## 8. Pillar 5 — Estimating workbench (from `pricing_intelligence.html`)

The dashboard is a single-user browser tool over a 663k-row catalog built from 436 vendor price-list spreadsheets. In
PCA it becomes a shared, versioned tool tied to bids and approvals:

* **Catalog**: `CatalogSource` per vendor price file (from a designated folder on the share; vendor price lists are
  third-party data — the one legitimate spreadsheet input, loaded by a loader, never hand-copied), `CatalogItem` with
  the dashboard's hygiene rules as a loader with a report (manufacturer alias cleanup; duplicate-part collapse keeping the
  better row; version-suffix archiving; pre-2023 removal); search with the same scoring ladder (exact / prefix /
  contains / brand / description / word overlap), brand and has-cost filters, compare up to 6 with best-cost /
  best-margin; per-manufacturer data-files view.
* **Estimates**: `Estimate` (title, client, notes, **bid FK**, owner, status draft / submitted / approved), `Room`,
  `Line` (item, qty, cost, markup ↔ sell coupling with the 1.265 default, 11 labor-hour columns — 6 union / 5
  non-union, hours not multiplied by qty — at the rate card, per-line / per-room / grand totals; column chooser instead
  of a 71-column wall), `LaborRate` card (today's defaults, effective-dated), versions on save; import from Excel (room
  column or sheet-per-room, header aliasing, price lookup by part); exports (multi-room xlsx with Summary sheet, the
  PACE BOM template once its rules are confirmed, detail CSV, clipboard); quick-add panel with running totals.
* **What PCA adds**: the estimate's totals flow to the bid (Budget / Value auto-filled for comparison with what was
  typed into the portal), the estimator's own history (their margin vs realised GP per line category), the **Estimator
  Notes** policy enforced as warnings (peer review > $25k or > 80 h; minimum markup 1.265; pull labor ≥ .010/ft;
  consumables markup 1.5; 8 h field labor per $1k consumables), and the BOM / labor approval request (6.5) raised from
  the estimate. The trade labelling bug (field labor shown as non-union) and the dropped imported labor units are fixed
  by construction.
* Phase: last (Section 15) — it is a product of its own; nothing else waits for it.

---

## 9. Planner

When `Tasks.Read.All` + `Group.Read.All` + `User.ReadBasic.All` are granted: mirror every plan of every group the app can
see (plans, buckets, tasks, labels, checklist counts, assignments, progress, dates) into `apps/planner`, delta by etag,
history on change (a full pull per plan each refresh, diffed by etag — Planner offers no delta query). Uses:
* **Status board sync** (6.1): task ↔ `StatusRow` by project number in the task title or a label; Planner labels →
  the 21 statuses; bucket = stage.
* **Production › Planner boards**: the AV sequence (Install schedule → Line-up → Project tracker → Fabrication /
  Shipping → Programming / Commissioning → Signal-flow queue → A-card → Sign-off) as columns, jobs as cards, live from
  Planner, with SL / PTT figures on each card; other divisions' plans appear as their own boards automatically.
* **Attachments**: Planner drops task files in the group site's "Microsoft Planner" folder — already indexed by 7.1.
* Until permissions exist the tab says "Planner not connected — see Data Quality" and nothing else breaks.

---

## 10. Navigation, personas, nav budget (D7)

**One new sidebar group — "Bids & Planning" — with four links** (a fifth, Estimating, only when Phase G ships):
* **Bids** (`/bids/`; tabs Overview · Board · Calendar · List · Analytics)
* **Production** (`/planning/`; tabs Today · Status board · Active tasks · Punch lists · Approvals · Planner boards)
* **Resource Scheduler** (`/planning/schedule/`; its own full-width page)
* **Estimators** (`/estimators/`)
**Documents** (`/documents/`) goes under *Projects*. No other group changes; pinned links stay. Project, customer and
bid pages carry the cross-links (Bid card, Documents tab, Production status), so most users reach everything from
pages they already use. The dashboards' hub is replaced by the sidebar and by Production › Today.

Per-persona first screen (remembered via `PCA.pref`): Exec → Bids Overview; DM → Bids Overview (division filter) +
Production Today; PM → Production Status board (own rows) + Calendar; Estimator → Bids Board (own) + Calendar; Admin →
Data Quality.

---

## 11. Access control (Access Spec v1 conventions)

Capabilities: `bids.view` (every role — D5), `bids.refresh` (re-pull the list now — PM, DM, estimator, exec), `bids.notes` (write notes / follow-ups / risks), `planning.view`,
`planning.write` (edit status rows, punch items, schedule, requests — PM, DM, estimator, finance-ops), `planning.approve`
(approve BOM / labor requests — DM, exec), `estimators.view` (Estimators pages, metrics), `documents.view`,
`documents.findings`, `estimating.view` / `estimating.write`; **`ratings.view` (superadmin) covers estimator ratings**.
New role **`estimator`**: projects.view, customers.view, bids.view + bids.notes, planning.view + planning.write,
estimators.view, documents.view, estimating.view + estimating.write. Existing roles gain bids.view, planning.view,
documents.view; PM / DM gain planning.write; DM / exec gain planning.approve. Every URL in `URL_ACCESS`; sentinel tests:
no rating value on any estimator page for non-superadmins; no document from an excluded location ever renders; no
approve button without `planning.approve`.

---

## 12. Data quality (Data Quality page, new "SharePoint & planning" section)
Unresolved bidder / rep / client values; blank-bidder (inferred) rows; awarded rows without a Job Number after 14 days;
Job Numbers not in SL; SL jobs with no bid row; portal Value ≠ SL contract value by > 5 %; portal says lost / quoting
but SL has billings (won-by-SL); duplicate bids (client + name + due date); probability fill rates (both fields);
missing PM on awarded / submitted; submitted without value; BOM needed; project folders that match no job; jobs with no
folder; files that failed extraction; status rows whose project closed in SL; scheduled hours with no PTT entries after
the date (plan not followed); Planner tasks with no project number; certificate / Planner permission status; share
mount health. Every item links to where it is fixed (portal row, alias table, folder).

---

## 13. Refresh, operations, the share

### 13.1 Schedule
`refresh_all` (07:00 / 12:00 / 16:30): Project List + versions + libraries delta + share walk (incremental) + Planner
delta + BidSnapshot; text extraction runs after the pull, bounded per run (new / changed files first, then a backlog
queue). A "refresh bids now" button (`bids.refresh`) re-pulls the list in seconds. Graph budget: ~10 calls for the
list, ~1 per library delta, ~1 per plan; the **first** version-history pull is one call per row (~3,500, spread over a
few runs), after that only rows whose `lastModifiedDateTime` moved; 429 → exponential back-off.

### 13.2 Archive and audit
Raw Graph payloads archived per run (like CNET); share-walk manifest (path, size, mtime, hash) archived; PCA-owned
planning edits in `AuditEvent`.

### 13.3 The share mount
A dedicated read-only service account (IT creates it; Owner's own account is the fallback), password in the macOS
Keychain (`security add-internet-password`), mounted by `scripts/mount_share.sh` at `/Volumes/pace-p` with
`mount_smbfs -o rdonly`; the refresh checks the mount, remounts if needed, and records health on the Data Quality page.
`share_client.py` walks only the configured roots, skips the exclude list, never follows symlinks off the mount, opens
files read-only, caps file size for extraction (50 MB). Nothing is ever written to the share.

---

## 14. Docs and tests
`docs/12_bids_and_estimators.md`, `docs/13_production_planning.md`, `docs/14_documents.md`,
`docs/15_estimating.md`, `docs/16_planner.md`, `docs/model_cards/estimator_v1.md`; metric definitions added to
`04_metric_dictionary.md`; `docs/11` stays the exploration record. Unit tests for every pure rule (status normalisation,
bidder aliasing, job-number resolution, stage timing, expected bookings, crew-plan maths, punch status, priority
buckets, proposal checks, catalog hygiene, search scoring); loader tests on archived payloads; access tests for every
URL; a **parity test** that reads Appendix A's ids and fails when a capability's page, tab or view is missing.

---

## 15. Phases (each ships usable on its own; sizes are working estimates)

| Phase | Delivers | Size |
|---|---|---|
| A | Graph client + archive; Bid / BidVersion / BidderAlias / BidClientAlias loaders; SL linking with won-by-SL; DQ; `refresh_all` step; docs | ~1 week |
| B | Estimator identity → `core_project.estimator`; Estimators page + detail; project page Bid card | ~1 week |
| C | Bids: Overview (Revenue Tracker parity + queues), Board, Calendar, List, Analytics, Bid detail, notes / follow-ups / risks, BidSnapshot, Command Center tile; access roles | ~2 weeks |
| D | Production: Today, Status board, Active tasks + PM report, Punch lists, Approvals on PCA tables; transition import; SL / PTT enrichment | ~2 weeks |
| E | Resource Scheduler (board, needs rail, Gantt, modals, exports, drag-and-drop, PTT adherence) | ~2 weeks |
| F | Documents: share mount + client, library and share index, linking, Documents tab / page, previews, search group; text extraction; proposal rule checks; findings | ~2 weeks |
| G | Estimating workbench: catalog loader + hygiene, search / compare, estimates / rooms / lines / rates, imports / exports, policy warnings, bid + approval links | ~2 weeks |
| H | Estimator rating model, model card, blind review, Ratings block | ~1 week + review |
| I | Planner mirror + status sync + production boards; list attachments — when IT delivers | ~1 week |

A → B → C is the spine; D and E can run in parallel with F; G last; H after enough linked closed jobs exist; I when IT
delivers.

---

## 16. Dependencies
IT: certificate credential + SharePoint API permission (attachments); Graph Planner permissions; a read-only service
account for the share. Owner: the P: UNC path and project-folder root (1.3); the standard clause set for proposal
checks; approval of the Claude-API review (default off); confirmation of the quote-reference rule for SS PM folders;
the folder of vendor price lists for the catalog loader.

---

## 17. Risks and how the spec handles them
* **Estimator identity errors** poison ratings → alias table with confidence, inferred flag, nothing rated on inferred
  rows, roster page for review before Phase H.
* **Portal data is hand-typed** (mixed case, blank fields, 25 % default probability) → hygiene metrics per estimator,
  DQ queue, "unscored" never treated as 0.
* **Selection bias** in estimate accuracy → stated on the page; separate populations per metric; model card.
* **The share** could be slow or huge → incremental walk by mtime, size caps, excludes, extraction backlog queue.
* **Two systems of record during transition** (dashboards' JSON vs PCA) → the transition import exists, the JSON is
  read once per file at cut-over and then retired; PCA never writes to the P: drive.
* **Planner may not match the assumption** (buckets = stages) → the mirror is generic (plans / buckets / tasks /
  labels); board mapping is configuration, verified the day access lands.
* **Nav bloat** → one group, four links; everything else reached from existing pages.

---

## 18. Open items (decided during the build unless Owner objects)
1. Whether PCA should eventually **own bid entry** (replace the portal form) — not in this spec; the Portal stays.
2. Which probability field to retire (5.2) — reported; Owner decides.
3. The estimating template (PACE BOM xlsx) rules — the dashboard's template engine is dead code; confirm the target.
4. Vendor price-list folder on the share (catalog loader).
5. Standard clause set for proposal checks.
6. Whether the punch-list "Active" checkbox should keep meaning nothing (kept as-is, explained) or become "in progress".

---

## 19. Review log
* **v0.1 → v1.0**: incorporated D1–D7; added the share source (1.3, 13.3), production planning (6), the estimating
  workbench (8), Planner (9), the nav budget (10), the parity appendix; won-by-SL rule; blank-bidder inference;
  probability handling; PCA-owned planning state; PTT schedule adherence.
* **v1.1 → v1.2 (second review pass, after the document was believed complete)**: (1) added 6.7, the one-time
  cut-over import of the dashboards' live JSON — those files are the only copy of status rows, punch items,
  schedules and approvals, so retiring the dashboards without it would lose data. (2) 5.2 now defines *when* an
  expected booking lands (expected decision date from the observed cycle) and the historical-hit-rate variant.
  (3) Planner has no delta API — Sections 2 and 9 now say "pull per plan, diff by etag". (4) 13.1 states the first
  version-history pull costs one call per row. (5) 5.3 gained the Command Center / Project Snapshot / global-search
  integration that Phase C already promised. (6) `bids.refresh` named; `bids.view` is every role, matching D5.
  (7) 6.4 says where punch photos are stored. (8) Review log made honest: the v1.0 → v1.1 list below records changes
  made while composing v1.1 from v1.0.
* **v1.0 → v1.1 (first review pass)**: (1) Overview KPI list rewritten so the tracker's numbers reconcile (Submitted-only
  sub-labels) and blank probability is "unscored", never 0. (2) Pivot no longer substitutes the bidder for a blank PM.
  (3) List tab: frozen identity columns, filter-aware exports, no row caps (Owner's wide-table rules). (4) Today tab
  replaces the hub; Production tab list fixed. (5) Status board: Send-to-Scheduler payload now carries dates, engineer
  and notes and is received. (6) Active tasks: "excluding own facet" charts, PM2 in filters. (7) Scheduler: one
  capacity rule; drag-and-drop added; exports named. (8) Approvals: approver identity from sign-in, overdue on
  needed-by, attachments out of JSON. (9) Punch lists: roster from `core_employee`, table sortable. (10) Estimating:
  column chooser, the two labelling / key bugs listed as fixed by construction. (11) Estimator identity list corrected
  to what the data showed (ExampleSurname inactive; ExampleSurname ambiguity; duplicate ExampleSurname rows). (12) Added Section 17 (risks)
  and item 6 in Section 18. (13) Appendix A ids made stable (`RT-`, `PL-`, `BA-`, `HB-`, `RS-`, `PS-`, `AT-`, `PU-`,
  `PI-`) for the parity test.

---

## Appendix A — Parity matrix (every capability of the eight dashboards → where PCA does it)

Legend: **Keep** = same behaviour on live data; **Better** = kept and improved (how, in the note); **Drop** = not
rebuilt (why, in the note). Every row has a stable id for the parity test (14).

### A.1 Revenue Tracker (`project_portal_dashboard.html`) → Bids › Overview / Analytics / List / Bid detail
| Id | Capability | PCA | Note |
|---|---|---|---|
| RT-01 | Load the list from the canonical store, change detection, status pill | Better | Live SharePoint pull on the refresh + "refresh bids now"; freshness in the sidebar footer |
| RT-02 | Ad-hoc `.xlsx` import with alias mapping | Drop | PCA reads the portal itself; spreadsheets are never a source (D7) |
| RT-03 | Planner / status enrichment by 6-digit job with ambiguity refusal | Better | Enrichment by canonical project FK; ambiguity impossible; status board + Planner labels shown on the bid |
| RT-04 | Derived margin (1 − Budget ÷ Value) | Keep | Bid margin, plus realised GP beside it |
| RT-05 | % Report universe = Submitted + Quoting; facets Bidder / Status / Close % / Ball; search | Better | Open pipeline = same set; facets are multi-value with counts; search is PCA search |
| RT-06 | Header stats Projects / Bidders / Submitted / Pipeline (Σ submitted) / Wtd Forecast | Better | Same numbers as sub-labels; headline shows open (Q + S) with the split |
| RT-07 | KPI tiles: All Projects, Avg Project Value, Submitted, Weighted Forecast (+ % of pipeline), Avg Close % (+ N scored) | Better | Adds unscored count; blank ≠ 0; adds expected bookings 30 / 60 / 90 and hit rate |
| RT-08 | Bidder pipeline bars (top 8, other-filter-aware, click = filter) | Keep | + "all bidders" expand |
| RT-09 | Close-probability bars (thresholds 70 / 40, click = filter) | Keep | Both probability fields; calibration link |
| RT-10 | Ball-in-court bars (top 8, "N active", click = filter) | Keep | Footnote corrected |
| RT-11 | Projects table with status fill, tag filters, sort pills + header sort, row → panel; 300-row cap | Better | No cap; correct sort direction; frozen identity column |
| RT-12 | PM Quoting: PM list (count + value, search), status / priority chips, project search, chip bar, clear | Better | Pivot axis switchable estimator / rep / PM; blank PM shown as "no PM" |
| RT-13 | Priority rule Past due / ≤ 14 days / On track / No date from Bid Due | Keep | Live clock, local time |
| RT-14 | Preview cards (Current PM, Submitted, Quoting, Priority) as filters | Keep | |
| RT-15 | Expandable pivot Status → Year → Month → Client → Project → Close % with Σ value, grand total, row → list, resizable | Keep | Height persisted via `PCA.pref` |
| RT-16 | Detail table (ID, Project, Status, Priority, Close %, Value, Ball); 80-row cap | Better | No cap |
| RT-17 | Awarded set = status Awarded or Date Awarded; KPIs Awarded Projects / Value / Avg Margin / BOM Complete | Better | Won-by-SL added; margin colours actually rendered; SL final GP beside bid margin |
| RT-18 | Awarded table: search, BOM select, sort Awarded / Value / Margin; 400-row cap | Better | No cap; column chooser |
| RT-19 | Detail slide-over: info, financials with margin bar, timeline; Esc / overlay close | Better | Bid detail page + version timeline + documents + notes |
| RT-20 | Deep link to the list with match-and-highlight | Keep | `/bids/<id>/` and `?hl=` on the List tab |

### A.2 Project Portal replica (`project_portal_list.html`) → Bids › List / Overview / Calendar / Analytics / Bid detail
| Id | Capability | PCA | Note |
|---|---|---|---|
| PL-01 | Editable list of the 20 columns, configurable visible columns and order, sticky header, live count | Better (read-only) | Column chooser + order + saved views; frozen identity columns; edits go to the portal via deep link |
| PL-02 | Header menu: sort A–Z / Z–A, filter by, group by, column settings, totals, colours | Better | All except colour editing (PCA tag palette) |
| PL-03 | Multi-value column filter panel with counts from the other filters, value search, select-all, clear; chips | Keep | |
| PL-04 | Global search across all columns | Keep | Plus global search group "Bids" |
| PL-05 | Grid edit with windowing; cell / range selection | Drop | PCA does not edit the portal (5.5) |
| PL-06 | Detail / new-item form with typed controls; required Client + Project Name | Drop | Deep link to the portal's form; PCA shows the row |
| PL-07 | + New with auto Project ID, Created = today; Ctrl+N / Ctrl+S / Esc | Drop | "+ New bid" opens the portal's form |
| PL-08 | Undo / redo 25 deep | Drop | no edits in PCA |
| PL-09 | Delete → recycle bin, restore, delete forever | Drop | no deletes in PCA; the portal's own bin applies |
| PL-10 | Import Excel / CSV replacing the list; % OF CLOSE normalisation | Drop | never a source; PCA normalises what SharePoint holds at read time |
| PL-11 | Export Excel / CSV (whole list, ignoring filters) | Better | exports honour the filtered view |
| PL-12 | Reset to original seed | Drop | dangerous by design |
| PL-13 | Choice-option registry with colours | Drop | PCA tag palette; option sets come from SharePoint's column definitions |
| PL-14 | Locked Bidder list; lookup dropdowns | Better | Alias table with rules and confidence |
| PL-15 | Home: Total projects, Clients, Data issues, Due next 14, Submitted value, Awarded value; status board; needs attention; recent; bidder workload; data health | Keep | Bids Overview tiles + queues; data health → DQ section |
| PL-16 | Issue tracker rules (missing bidder / PM / due date, awarded no job #, submitted no value, BOM needed, past due); top-reason cards; queue | Keep | Overview "needs attention" + DQ; "missing bidder" not counted on archived rows |
| PL-17 | Archive (Completed / Lost / Did Not Bid): counts, value, list, cleanup counters | Keep | List tab with the archived facet; cleanup counters in DQ |
| PL-18 | Client list with rollups (projects, quoting, submitted, awarded, lost, value) and drill-through; import / export | Better | On `core_customer` via aliases; Analytics tab + customer page; no import |
| PL-19 | Calendar month grid (Bid Due / Submitted / Awarded), prev / next / today, type filter, persisted, event → project | Better | + walkthroughs, awarded starts; week views; "+N more" expands |
| PL-20 | Notebook, follow-ups, risks, conversations | Better | `BidNote` / `BidFollowup` / `BidRisk`, audited, rolled up |
| PL-21 | Edit / admin page: navigation, column tools, list health, bidder list | Better | DQ section + alias table |
| PL-22 | Classic links page | Drop | |
| PL-23 | Autosave with change detection, verification, back-off, pull-before-write, 5-second multi-user refresh, hub broadcast | Drop | replaced by a database and the refresh cadence |
| PL-24 | Deep-link handoff with match-and-highlight | Keep | `?hl=` |

### A.3 BOM & Labor Approval (`bom_labor_approval.html`) → Production › Approvals
| Id | Capability | PCA | Note |
|---|---|---|---|
| BA-01 | Request record: project name / number, BOM \| Labor, requester, approver, needed-by, department, notes, attachments | Better | project FK; requester = signed-in user; attachments in PCA's store |
| BA-02 | States Pending ↔ Approved with approver + timestamps; reopen; delete | Better | approver identity from sign-in; `planning.approve` required; audit events |
| BA-03 | Summary cards as filters; search; status / type selects; chips and approver names as filters | Keep | + overdue-on-needed-by card |
| BA-04 | Table newest-first with attachment downloads and inline actions | Better | sortable columns |
| BA-05 | Create / edit dialog with attachment add / remove; required fields | Keep | |
| BA-06 | CSV export of the filtered view | Keep | |
| BA-07 | Shared storage with 5-second refresh; local backup; Refresh | Drop | database |
| BA-08 | Real approver identity / roles; overdue on Needed By (gaps the inventory flagged) | New | delivered |

### A.4 Dashboard Hub (`dashboard.html`) → sidebar + Production › Today
| Id | Capability | PCA | Note |
|---|---|---|---|
| HB-01 | Launcher with a card per tool | Better | sidebar group; Today shows live counts per tool |
| HB-02 | Consistent Home from every page | Keep | the sidebar |

### A.5 Resource Scheduler → Production › Resource Scheduler
| Id | Capability | PCA | Note |
|---|---|---|---|
| RS-01 | Roster: name + trade, role Union / Subcontracted Union / Non-Union, max hrs / week, divisions, PTO ranges, OT flag; add / edit / remove | Better | resources are `core_employee` rows; cascade rules kept |
| RS-02 | Projects: name, PM, division, on-site window, access times, days-needed toggles, union / non-union totals, colour, short project or up to 11 phases with windows, weekend rule, man-hours, extra ranges; Test per trade; clamping; cascade delete | Keep | + project FK, source, start / end from the status board |
| RS-03 | Phase trade rule and fixed phase colours | Keep | |
| RS-04 | Crew-plan maths (`ceil(h/8)` front-loaded), per-day shortfall text, phase / day completion by slots | Keep | unit-tested |
| RS-05 | Week board: Mon–Fri + conditional weekend columns, day free hours, projects lane with phase tags, person rows with utilisation, chips, PTO ✕, daily load; Division + PM filters; week nav + Today | Keep | + drag-and-drop of chips |
| RS-06 | Week summary: union (and non-union) hours not utilised, booked / cap, % utilised (PTO-adjusted) | Keep | |
| RS-07 | Needs Staffing rail: division tabs, Needs Resources / Needs Dates, per-project got / need, per-phase plan, per-day "still needs" | Keep | |
| RS-08 | Projects view: Gantt (today line, ticks, division filter) + cards (allocation bars, phase chips, people count, completed-early badge) | Keep | Gantt uses a real scrollbar |
| RS-09 | Person-day assignment modal (project active that day, phase for the trade, hours 0–24 step .5, note, cap check) | Better | the single capacity rule applies here too |
| RS-10 | Project staffing modal per day / whole project: phase summary, crew options, allocation-met chips, Assign resources (search, role filter, division sub-tabs with home ★ and borrowing, "Nh left", hours picker, phase pick, per-person phase panel, cap warnings, OT bypass), Assigned tab, Copy Allocation with conflict preview, Edit dates & hours | Keep | |
| RS-11 | Completed Early: date + scope, releases later allocations only, gates coverage / need / staffing / colouring, badge, reversible | Keep | |
| RS-12 | Scheduling assistant in the editor (free hours per person, Covered / Short, day chips, roll-up, overlap note) | Keep | |
| RS-13 | Exports: full JSON, week CSV, per-resource weekly HTML / print; JSON import (replace all); Reset; Save / Refresh to P-drive | Better | CSV, XLSX and print kept; JSON import / reset / P-drive dropped (database) |
| RS-14 | Receiver for the planner handoff | New | delivered (6.1 → 6.3) |
| RS-15 | Planned vs worked (PTT) per person per week; SL labor budget vs plan | New | schedule adherence |

### A.6 Project Status (`pace_planner.html`) → Production › Status board
| Id | Capability | PCA | Note |
|---|---|---|---|
| PS-01 | Active / Completed tabs with counts; division tabs; global search | Keep | search on visible + notes, not hidden ids |
| PS-02 | 13-column editable grid; column resize / auto-fit persisted; zoom / fit; height; reset | Keep | + sort on every column, percent-complete column |
| PS-03 | 21-value status vocabulary with Planner colours and misspelling aliases; both pills; no enforced transitions | Keep | vocabulary is data, editable by admin |
| PS-04 | Inline edits stamp Last Updated; drawer edit form; new / duplicate / delete | Keep | drawer includes start date; stamps who |
| PS-05 | Manual-only completion, reversible, immune to imports; Completed per division | Keep | |
| PS-06 | Excel / CSV Master Schedule import with alias mapping and merge rules | Keep (admin, transition) | with a visible mapping step and confidence |
| PS-07 | Send to Scheduler payload + "sent" flag + acknowledgement | Better | payload complete; receiver exists |
| PS-08 | Deep link from Active tasks with fuzzy match, pulse, fallback search | Keep | `?hl=` |
| PS-09 | Triple persistence with verify / backoff / autosave-off guard; Save Now; hub broadcast; `job_key` | Drop | database; canonical project key |
| PS-10 | SL / PTT enrichment columns; history per row; Planner label sync | New | |

### A.7 Active Task Dashboard → Production › Active tasks
| Id | Capability | PCA | Note |
|---|---|---|---|
| AT-01 | Division tabs with active-task badges; JSON poll; Refresh | Keep | live view over the status board |
| AT-02 | Priority buckets and starting-soon rule | Keep | live clock |
| AT-03 | KPI tiles (active, union hrs, starting soon, past critical stop, missing stop date, urgent union hrs) | Keep | |
| AT-04 | Sidebar filters: search, PM (global, hours meter), priority, status ∪ status V2 (distinct projects), reset | Better | PM2 included |
| AT-05 | Charts with click-to-filter: union hours by PM (top 12), by critical-stop month, tasks by priority, by status (doughnut) | Better | each excludes its own facet |
| AT-06 | Active tasks table: dot, name + provenance, PM1 / PM2 chips, start, critical stop badge, union hrs (red > 99), done bar, S1 / S2 chips, remove; sortable | Better | "remove" is a real, audited change |
| AT-07 | PM focus bar; Print PM report; Export PM report (4-sheet styled xlsx with logo, formulas, autofilter, freeze) | Keep | |
| AT-08 | Project detail overlay merging a job across divisions; Open in status board | Keep | |
| AT-09 | Drag-and-drop Master Schedule import; dormant AI mapping; dormant Add task | Drop | the status board owns edits and the transition import |

### A.8 Punch Lists → Production › Punch lists
| Id | Capability | PCA | Note |
|---|---|---|---|
| PU-01 | Division switcher with isolated data, PM rosters and colours | Better | roster from `core_employee` |
| PU-02 | Projects seeded / refreshed from the status board, matched on project, never deleting items | Keep | |
| PU-03 | Project: code, editable title, PM (Unassigned triage), status chips; add / delete; drawer | Keep | |
| PU-04 | Punch item fields (Active, entered, description, BIC, critical 1–5, due, completed, assigned, sign-off, verified); add / delete; inline edit; critical popover | Keep | + photos / attachments |
| PU-05 | Derived status rules; counts per project / PM / division; % complete | Keep | live clock |
| PU-06 | Views: PM overview → PM detail with peekable sections; All Projects grid with chips / filter / sort / search; drawer; Overdue analysis (5 KPIs, bars by BIC and PM, sortable table) | Keep | |
| PU-07 | Export Excel (sheet per project) with CSV fallback; JSON backup / restore; reset to seed; theme | Better | Excel / CSV kept; backup / restore / reset dropped (database) |
| PU-08 | Shared JSON autosave + 5-second pull; in-page dialogs and toasts | Drop / Keep | database; PCA dialogs |

### A.9 Pricing Intelligence → Estimating (Phase G)
| Id | Capability | PCA | Note |
|---|---|---|---|
| PI-01 | Catalog search with the scoring ladder, brand / has-cost filters, sorts, 400 cap | Better | no cap (paged) |
| PI-02 | Selected-product card: cost, MSRP, MAP, source date, margin, confidence | Keep | |
| PI-03 | Compare up to 6 with best-cost / best-margin | Keep | |
| PI-04 | Estimate builder: rooms, lines, qty, cost, markup ↔ sell (1.265), 11 labor-hour columns at the rate card, per-line / room / grand totals; quick add | Better | shared, versioned, column chooser; trade labels correct |
| PI-05 | Labor rate card with defaults and reset; Estimator Notes policy text | Better | effective-dated; policy enforced as warnings |
| PI-06 | Saved estimates: save / update / open / duplicate / delete / new; restore banner; meta auto-save | Better | server-side, per user, linked to bids |
| PI-07 | Estimate import from Excel; vendor catalog import (multi-file, aliasing, dedupe, modes, pre-2023 skip, history, template, export, clear) | Better | catalog import is a loader from the share folder; estimate import kept |
| PI-08 | Catalog hygiene (alias cleanup, duplicate collapse, version archiving, pre-2023 removal) with counts | Keep | loader report |
| PI-09 | Exports: multi-room xlsx, PACE BOM template (dead code today), detail CSV, clipboard; Data Files view | Keep | template after 18.3 |
| PI-10 | Browser-only persistence; no sharing; no approval bridge | Better | database; approval request from the estimate |
