# 12 — Bids and Estimators (`/bids/`, `/bids/estimators/`)

The Project Portal (SharePoint › Project List + Project Archive) is the company's bid log: every quote, who priced it
(*Bidder*), the rep, budget, value, probability of close, status and dates. PCA mirrors it read-only through Microsoft
Graph (`apps/ingestion/sources/graph_client.py`), links each row to its SL job, and builds the pipeline pages and the
estimator metrics on top. Spec: `Pace_Company_Analytics_SharePoint_Spec_v1.md` §4–§5; build history:
`docs/sharepoint_integration_build_log.md`; exploration notes: `docs/11_sharepoint_exploration.md`.

## Data flow
| step | where | what |
|---|---|---|
| pull | `apps/bids/loaders.load_project_list` / `load_project_archive` | all list items with the registered field set; content-hash upsert into `bids_bid` (source `list` / `archive`, identity = SharePoint item id); a partial pull never deletes |
| versions | `load_versions` | the list's version history per row → `bids_bidversion` (status / value / budget / bidder / probability / job # per version); capped per run, complete after the first backfill |
| link | `link_to_sl` | `rules.resolve_job_number`: the typed job number → `core_project` (000000-aware; `241517` ≠ `241517000000`); `won_by_sl` when the linked job has billings (SL is the truth for won, D4) |
| people | `resolve_aliases` → `bids_bidderalias` | every distinct Bidder / Sales Rep / PM spelling → employee via the alias ladder in `rules.alias_candidates` (exact, name words, fixed aliases — *ExampleSurname is always Mike*, surname, first name + initial, dominant surname, fuzzy); `OT` / `Other` = house account, never a person |
| clients | `_resolve_clients` → `bids_bidclientalias` | client name → customer by the linked SL jobs' customer, exact name, trigram similarity ≥ 0.6; candidates kept for review |
| apply | `apply_aliases` | writes estimator / salesperson / client / division on each bid; blank Bidder inferred (rep → PM of the won job → creator) and flagged `estimator_inferred`; division = SL job's → estimator's dominant PM division → home subaccount → estimator's dominant division across their own bids |
| project | `write_project_estimators` | `core_project.estimator` = the estimator of the awarded / won-by-SL bid (never an inferred one) |
| snapshot | `snapshot_pipeline` | `bids_bidsnapshot`: one row per day × division × estimator × stage (count, value, weighted value, aged) — the pipeline-over-time chart |
| quality | `check_quality` | DataQualityIssue codes `bid_bidder_unresolved`, `bid_rep_unresolved`, `bid_client_unresolved`, `bid_job_not_in_sl`, `bid_awarded_no_job`, `bid_won_by_sl_override`, `bid_value_vs_cv`, `bid_duplicate`, `bid_probability_fill`, `bid_bidder_blank` |

`refresh_all` runs the whole thing after CNET (best-effort; versions capped at 600 per run); `manage.py
refresh_sharepoint` runs it alone (`--only list,archive,link,aliases,apply,versions,snapshot,quality`, `--versions N`);
the **Refresh from Portal** button on the Bids pages re-pulls the list in-process (≈ 20 s).

## Stages (`rules.normalize_status`)
Portal statuses (including the legacy ones and the `;#…;#` multi-choice encoding) map to `quoting`, `submitted`,
`awarded`, `lost`, `did_not_bid`, `on_hold`, `no_decision`, `unknown`, with a flag (`in_progress`, `budgetary`,
`rebid`, `needs_assessment`, `completed`). **Open pipeline = quoting + submitted on Project List rows only.** Project
Archive rows that still read Quoting / Submitted are the portal's dead file (2,008 of them from 2022): they are filed as
`no_decision` with flag `archived_open` and labelled "(archived)". **Won** = portal Awarded *or* `won_by_sl`;
**decided** = won or lost; hit rate = won ÷ decided.

## Pages
* **Bids › Overview** — KPI tiles (open / submitted / quoting / weighted / expected bookings / this month / 12-month hit
  rate), funnel (last 24 months unless a year filter is on), open pipeline by bidder and by stated probability, win rate
  by quarter, *Needs attention* (the portal's own hygiene rules: missing bidder / due date / job # / value, BOM needed,
  past due, unscored), follow-ups, leaks (awarded ≥ 14 days with no SL job), stale open bids (untouched ≥ 30 days),
  quoting pivot by estimator / rep / division / client × month.
* **Pipeline Snapshot** (`/bids/snapshot/`, pinned in the sidebar; Owner 2026-09-10) — the Project Snapshot's shape for
  proposals. Day (Friday = Fri–Sun) or week windows stepping over windows that had a submission; division and estimator
  filters; a summary of the window's submissions by division or by estimator (count, value, budget, GP quoted, GP %,
  expected GP at the stated chance and at PCA's win rate, attention count); then three full-data tables — the window's
  submissions, **every open proposal** (submitted, awaiting a decision) and **quotes in progress** (quoting + on hold) —
  each with the needs-attention labels, work type, stated probability, PCA's estimated win rate, expected GP both ways,
  documents count, facet chips, multi-key sort and an expandable row (win-rate factors, the linked documents with
  open-at-source links, portal facts). Awarded / lost / due in the window fold under the summary.
  `apps/bids/views_snapshot.py`, `static/bids/snapshot.js`, win-rate model `apps/bids/winrate.py`.
* **Board** — quoting / submitted / on-hold columns from the live list; sort and grouping remembered; no drag (the
  portal stays the system of record — "Open in Portal" on the bid page).
* **Calendar** — bid due, submitted, awarded, walkthrough and project start dates; legend chips hide kinds.
* **List** — every row under the filters (no cap), facet chips, multi-key sort, column sets, CSV / Excel export.
* **Analytics** — pipeline history (snapshots), win rate by quarter, probability calibration, decision cycle, bid value →
  SL contract value, % of close report, hit rate by client / sector / size / rep, estimator × rep matrix.
* **Bid** (`/bids/<id>/`) — the portal facts, SL outcome (contract value, Δ vs bid, billed, final GP, accuracy, cost ÷
  budget), status trail with field-level version diffs, time in stage, PCA-only notes / follow-ups / risks, documents.
* **Estimators** (`/bids/estimators/`) — the roster: role, division, bids, open, submitted $, median bid, hit rate
  (count and $), DNB, bid margin, accuracy, cost ÷ budget, cycle, late, leaks, hygiene, house share; charts drill to the
  estimator page (trend, calibration, by client / sector / size, open bids, closed jobs).
* Hooks: project page bid card, Command Center "Bid pipeline" tile, Project Snapshot "Bids" card, Data Quality alias
  editor (unresolved bidders / reps / clients → pick the person or customer; manual picks are never overwritten).

Filters (`div`, `est`, `rep`, `client`, `year`, `house`, `src`, `size`, `stage`, `q`, `bom`, `ball`, `prob`) stick across
the tabs; every chart element drills to the rows behind it (PCADrill); table sorts, board sort, calendar month and hidden
kinds, list facets and column set are remembered per page (`PCA.pref`).

## Metrics (see also `04_metric_dictionary.md`)
| metric | definition |
|---|---|
| value / budget | the portal's Project Value / Budget as typed (no SL) |
| bid margin | (value − budget) ÷ value |
| weighted pipeline | Σ value × probability ÷ 100 over scored open bids; unscored bids add nothing |
| expected bookings | open list bids whose expected decision date (submitted, else due, + the division's median submitted→awarded days) falls inside 90 days, valued at the stated probability; "by hit rate" values the same bids at each estimator's historical hit rate; "past the median cycle" counts open bids that have waited longer than that median |
| hit rate | won ÷ (won + lost); the $-weighted variant uses Σ value |
| accuracy | SL final GP % − bid margin, in points, on closed & stabilised jobs only; positive = the job beat the estimate |
| cost ÷ budget | SL actual direct cost ÷ bid budget on closed & stabilised jobs |
| cycle | awarded − submitted, days (submitted ≤ awarded) |
| late | submitted after bid due |
| hygiene | share of submitted / decided bids with budget, value, due date and probability all filled |
| calibration | per stated probability bucket: actual win rate − stated, on decided bids |
| GP quoted | value − budget (portal figures); GP % = GP quoted ÷ value |
| expected GP (stated / PCA) | GP quoted × stated probability; GP quoted × PCA's estimated win rate |
| PCA estimated win rate | `winrate.Model`: log-odds blend of the smoothed hit rates of the estimator, client, division, work type, size band, sector and rep (12 decided bids = half weight; prior strength 12), damped by λ fitted on a time split (0.8 on 2026-09-10; Brier 0.108 vs 0.143 base). Rep counts only when it is not the estimator. Every prediction carries its factor breakdown |
| work type | keyword classifier on the project name (`rules.work_type`): access control, cameras / CCTV, alarm, AV / conferencing, network / cabling, IT / managed, hardware / box sale, service / repair, RFP; else the division's trade |

## Ratings (concealed)
`apps/analytics/ratings.py` rates the estimator of record with the same ridge + empirical-Bayes engine as PMs, plus
the `bid_accuracy` metric (final GP − the bid margin frozen at bid time). Visible only with `ratings.view` (Owner) — on
the Ratings page and as a block on the estimator page. Model card: `docs/model_cards/estimator_v1.md`.

## Gotchas
* The portal keeps *one* row per quote; re-bids are new rows with the same job number — the bid page lists "other rows
  on this job".
* `Probability of Close` blank ≠ 0 % (`rules.probability_from` returns None); `% OF CLOSE` is a free-text duplicate kept
  raw.
* Joint bidders ("MIKE E CHRIS S") credit the first name; the raw stays in `bidder_raw`.
* Version history is one Graph call per row: the first backfill took ~25 minutes; later runs only touch rows whose
  `portal_modified` moved.
* Never write to SharePoint. Notes, follow-ups and risks are PCA tables (`bids_bidnote`, `bids_bidfollowup`,
  `bids_bidrisk`).
