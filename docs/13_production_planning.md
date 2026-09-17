# Production planning — Today, Status board, Active tasks, Punch lists, Approvals

**What it is.** The five P:-drive dashboards' *planning* data (Master Schedule status rows, active-task scoring, punch
lists, BOM / labor approval requests) rebuilt on PCA's own tables and enriched with what SL / PTT know. PCA is the
system of record for this data: every edit is stamped with who / when, kept in a history table, and protected by a
version check ("changed by X at hh:mm — reload" instead of silently losing the other person's edit). Spec:
`Pace_Company_Analytics_SharePoint_Spec_v1.md` §6.0–6.2, 6.4, 6.5, 6.7; parity matrix Appendix A.3, A.6–A.8; the
dashboards' behaviour is inventoried in `docs/sharepoint_dashboards_inventory.md`. Build log: `docs/build_logs/phase_D.md`.

Pages live under the sidebar entry **Production** (`/planning/`) with tabs *Today · Status board · Active tasks · Punch
lists · Approvals · Planner boards*; the Resource Scheduler (Phase E, `apps/scheduling`) is its own entry.

## 1. Data model (`apps/planning/models.py`)

| Table | What | Notes |
|---|---|---|
| `StatusVocabulary` | the 21-value status vocabulary (blank + 20), Planner label colours, misspelling aliases | seeded from `rules.STATUSES`; editable in the Django admin (PS-03 "vocabulary is data") |
| `PunchDepartment` | ball-in-court departments (the nine from the workbook + whatever PMs add) | admin-editable |
| `StatusRow` | one Master Schedule row: project FK (nullable) + `project_number_raw`, `name`, `division`, `task_num`, `phase_status` (S1), `equipment_status` (S2), `pm` / `pm2` FK + raw text, `engineer`, `foreman`, `start`, `end` (critical stop), `hours_left` (union), `percent`, `notes`, `site_contact`, `assigned_to`, `priority_raw`, `last_update_date`, `completed` / `completed_at`, `source` manual / planner / import, `source_key`, `planner_task_id`, `sent_to_scheduler_at`, `plan_project_id`, `created_by` / `updated_by`, `version` | key = `core_project` (000000 rule); rows that match no SL project keep the free name |
| `StatusHistory` | row, changed_at, changed_by, field, old, new | every inline edit, drawer save, completion toggle, import refresh, hand-off |
| `PunchProject` | division, `code` (NN-NNNN), title, PM FK + raw ("Unassigned" = triage), project FK, source | unique (division, code) |
| `PunchItem` | active (visual tick), date_entered, description, bic, critical 1–5, due_by, date_completed, assigned, engineer_signoff, verified, position, version, who / when | status is derived, never stored |
| `PunchHistory`, `PunchAttachment` | history per project / item; files in PCA's store (model only — no upload UI yet) | |
| `ApprovalRequest` | project FK or free name / number, kind BOM / Labor, requested_by (signed-in user; `_raw` only for imports), approver (employee) + raw, needed_by, department, notes, status pending / approved / reopened, submitted_at, decided_by / at, `estimate_id`, `bid` FK, version | |
| `ApprovalAttachment` | files under `APP_SUPPORT_DIR/planning_attachments/approvals/` (never inline base64) | served by `planning_approval_attachment` |
| `ApprovalEvent` | created / edited / approved / reopened / attachment_added / attachment_removed / imported | |

The Access app's `AuditEvent` (`write_action`) records every write request as well.

## 2. Rules (`apps/planning/rules.py` — pure, unit-tested)

* **Status vocabulary**: `canonical_status(raw)` upper-cases, folds whitespace, maps the misspellings
  (`PROCURMENT ONGOING`, `FIELD INSTALL IN PROCESS`, `TESING …`, `CLOSEOUT DOCS NEEDED`, `FABRICATION IN PROGRESS`,
  `ENGINEERING IN PROGESS`) and treats `-`, `n/a`, `none`, `no phase selected` and pure job numbers as blank. Unknown
  values are kept upper-cased and render with the blank palette. Both pills use the same list; no transition is enforced.
* **Priority** (`priority_bucket(end, today)`): red = critical stop before today · yellow = today … +14 days · green =
  later · none = no date. *Today counts as due* (the dashboard's UTC-vs-local bug is gone) and "today" is the live clock.
  `starting_soon` = start within 0…14 days. Active task = not completed and percent < 100.
* **PM values**: `split_pm("DOBO/ExampleSurname") → ("DOBO", "ExampleSurname")` on `/ \ + & , ; AND - – —`; junk (`?`, `n/a`,
  single letters) dropped; PM2 is a real column and is counted by the Active tasks PM filter.
* **Job numbers**: `job_key(name, proj_num)` = the dashboards' 6-digit key; `project_candidates("241517")` =
  `["241517", "241517000000"]` — never strip the 000000, the plain number wins when both exist. Punch codes: 6 digits →
  `NN-NNNN`; matching is on digits.
* **Excel header mapping**: whole-word alias matching, exact = alias length + 100, longest alias wins, columns claimed in
  the fixed priority order `status2, status, lastUpdate, pct, hours, project, task_num, name, pm2, pm, engineer, start,
  finish, division, notes` (a claimed column is never reused — this is what stops "pm" matching inside "Equipment");
  `name` is required. `find_header_row` scans the first 35 rows for the best-scoring row. Cell parsers: Excel serials,
  `M/D/YY`, `%` normalisation (≤ 1 without a % sign is a fraction), hours × 8 when the text says "day".
* **Punch items**: completed ⇐ Date Completed set · overdue ⇐ open and due strictly before today · else open. Critical
  = open at 4–5. Per-project stats: total / completed / open (overdue ⊂ open) / overdue / critical / crit5 / % complete.
* **Approvals**: pending or reopened —approve→ approved; approved —reopen→ reopened; anything else is refused (409).
  Overdue on needed-by = still awaiting and needed-by before today. Legacy `Needs Approval` reads as pending.
* **Scheduler payload** (`scheduler_payload(row)`): `source, status_row_id, project_id, name, division, pm_id, pm_raw,
  hours_union, hours_per_day 8, days [1,1,1,1,1,0,0], start, end, engineer, notes, phase_status, equipment_status`.

## 3. Pages

**Today** (`/planning/`) — one card per division (040 / 070 / 080): active tasks, past critical stop, due ≤ 14 days,
needs scheduling (NEED TO SCHEDULE in either pill), missing stop date, overdue punches, critical punches, pending
approvals (on that division's SL projects), bids due this week (from `apps.bids` when present). Every number links into
the tab with the matching filter. Approvers see the "N requests waiting for your approval" queue; the recent
status-board changes list shows who changed what.

**Status board** (`/planning/status/`) — the whole dataset is embedded in the page (no row caps) and rendered client-side
(`static/planning/status.js`): Active / Completed tabs with counts; division chips (All only in Active); search over the
visible columns and notes; the 13-column grid (#, ✓, task + number + customer, DIV, Phase status, Equipment status V2,
PM, PM2, Engineer, Start, End, Hours left, % done, Notes, Last updated) plus a column chooser for the **live SL / PTT
columns** (contract value, billed, PTT hours / budget, PM remaining hours, last PTT entry, estimator) — the task cell's
hover card shows the same figures. Inline edits post per field (`planning_status_edit`, JSON
`{op: update, id, version, field, value}`); a stale version returns 409 with the current row. Drawer (⋯ / Edit):
summary + history, or the full form incl. start date and the SL job number (linking a project turns the enrichment on).
New task (defaults: current division, NEED TO SCHEDULE, 8 h), duplicate (" copy"), delete (in-page confirm), complete
circle (manual, reversible). Column drag-resize, double-click auto-fit, zoom (45–140 %), Fit all columns, height slider /
Auto, Reset — all remembered with `PCA.pref`. Sort on every column. `?hl=<row id | project number>` switches to the
row's view / division, selects and flashes it (deep link from Active tasks, the project page and Today). `?div=` and
`?status=` pre-filter. **Send to Scheduler** posts the row id to `planning_status_send` → `api.send_to_scheduler` →
`apps.scheduling.api.create_plan_project(payload, request)` (Phase E); the row is stamped `sent_to_scheduler_at` /
`plan_project_id` and shows a "sent" chip. Rows whose SL project is closed carry a "closed in SL" chip.

**Active tasks** (`/planning/tasks/`) — a view over the status board (not completed, < 100 %). Division tabs with
badges; sidebar filters (search, PM incl. PM2 with union-hour meters, priority, status ∪ status V2 counting distinct
projects, reset); PM focus bar with this-schedule / all-schedules totals and the print / export buttons; six KPI tiles
(click-to-filter); four Chart.js charts each aggregated from every filter except its own facet (union hours by PM top 12
+ all others, by critical-stop month, tasks by priority, distinct projects by status doughnut) — click to filter; the
table (priority dot, provenance tag, PM1 / PM2 chips, start, critical-stop badge, union hrs red > 99, done bar, S1 / S2
pills, "board ↗" deep link, ✓ = mark complete on the board, audited). Row click = project detail across divisions
(average complete, hours, earliest start, latest stop, PMs incl. PM2, divisions, SL figures, records table). Priority
re-evaluates every minute against the live clock. **Print PM report** (`/planning/tasks/report/?pm=`) and **Export PM
report** (`/planning/tasks/export/?pm=`, 4-sheet openpyxl workbook: PM Report with letterhead, logo, workload / dates /
status sections, risk colouring, live `SUM` / `AVERAGE` / `COUNTIF`, autofilter, frozen header, landscape fit-to-width;
Summary; Attention Required; All Active Tasks).

**Punch lists** (`/planning/punch/?div=040`) — division switcher (separate projects, roster and colours per division);
stats strip (always the whole division; clicking a card opens All projects with that status filter); **PMs** cards (roster
from `core_employee`: PMs by PTT role / open SL projects plus anyone referenced; Unassigned is the triage card) → **PM
detail** with peekable project sections (chevron peeks the item table in place; header click opens the drawer; PM
select and delete inline; status-board chips: status / status V2 / stop / hours) → **project drawer** (editable title,
PM, SL project and status-board links, the spreadsheet-style item table, + Add punch); **All projects** grid with PM
chips (multi-select), status filter, sort (most overdue / most open / alphabetical / PM) and search; **Overdue items**
(explanatory note, 5 exec KPIs, bars by BIC and by PM — click to filter the table —, sortable overdue table with jump
links, project sections with overdue rows only). Item table: Active tick, date entered, auto-growing description, BIC
select, criticality popover (colour-coded 1–5 / none), due by (red when overdue), date completed, assigned, engineer
signoff, verified, delete. Edits post per field (`planning_punch_edit`, ops `item_update / item_add / item_delete /
project_add / project_update / project_delete / seed`) with a version check; counts refresh in place. **Sync from status
board** creates a punch project for every active status row of the division that has none (never deletes, never
touches items). Export Excel (sheet per project: title row, header row, items) and CSV (with PROJECT CODE / TITLE / PM).
`/planning/punch/<id>/` opens the page with that project's drawer; `?format=json` returns the dataset.

**Approvals** (`/planning/approvals/`) — KPI cards as filters (total, pending incl. reopened, approved, attached files,
**overdue on needed-by**), search, status / type selects, type chips / status chips / approver names as filters,
table sortable on every column (newest first by default), attachments as download links, create / edit drawer (project
name*, SL number — resolved to the project —, type*, requester = signed-in user, approver from the roster, needed by,
department, notes, attachments add / remove), **Approve / Reopen only with `planning.approve`** (the URL registry
enforces it; the decision records the signed-in user and time), delete with in-page confirm, CSV export of the filtered
view (`/planning/approvals/export/?q=&status=&type=&filter=`). Other apps create requests through
`apps.planning.api.create_approval_request(project_id, kind, requested_by_user, needed_by, department, notes,
estimate_id=None, …)` (the estimating workbench, Phase G).

**Project page card** (`planning/_project_production.html`, included on every project page): the job's status rows,
open punch items and approval requests with links; renders nothing when the project has no planning data.

## 4. Transition import (spec §6.1 last bullet, §6.7)

    manage.py planning_import --seeds [dir]      # SOURCE_TASKS in pace_planner.html + DIVISION_RAW_DATA in 040_punch_list_dashboard.html
    manage.py planning_import --dir <P:…/data/resource-scheduler>   # project_status_data.json, 0x0_punch_list_data.json, bom_labor_approval_data.json
    manage.py planning_import --excel "070 Master Schedule.xlsx"   # any Master Schedule export (.xlsx/.xls/.csv, every sheet)
    … --apply            # write (default is a dry run: counts, unmatched project numbers, unknown PMs)
    … --seed-punch all   # then create punch projects from the active status rows
    … --seed-all-active  # seed import: keep every seed row Active like the dashboard did

The same importer is available in the app at `/planning/status/import/` (planning.write): upload → mapping step (sheet,
header row, resolved columns) + dry-run report → Apply. Merge rules: identity = division + project digits, else division
+ normalised name (and the legacy row id); **completed and manual rows are never updated, removed or reactivated**; an
import never completes a row except when the source itself carries the user's own completion (`completionTouched` in
the live JSON; for the embedded seed, the Master Schedule's `completed` flag at 100 %); a file without notes / last-update
keeps the old ones. PM names resolve through the bids alias rules (`apps/bids/rules.alias_candidates`, confidence ≥ 0.8:
exact, surname, nickname, joint entries, typos within 2 edits); unresolved names stay as raw text and are listed.
Project numbers resolve against `core_project` with the 000000 rule; unmatched numbers are listed and the rows kept with
their free name. `loaders.py` holds every importer; `Resolver` / `Report` are reusable.

## 5. Live enrichment (`apps/planning/enrich.py`)

One read-only query over `core_project` (rebuilt nightly from SL / PTT): contract value, billed, SL labor-hour budget,
PTT hours to date (+ %), the PM's remaining-hours estimate and its date, PTT % complete, last PTT entry, hours in the
last 30 days, lifecycle, SL PM, estimator, customer. `closed_in_sl()` flags rows whose SL project is closed.

## 6. Access

Capabilities (Access Spec conventions, registry in `apps/access/registry.py`): `planning.view` for every page and JSON,
`planning.write` for the edit / send / import endpoints, `planning.approve` for `planning_approval_decide`. Pages render
read-only controls without `planning.write` (`can_write` in the templates); the approve button only renders with
`planning.approve` and the server refuses the decision without it (403 from the middleware).

## 7. Tests

* `tests/unit/test_planning_rules.py` — vocabulary, aliases, colours, priority buckets, starting-soon, KPI summary, PM
  splitting, job keys / 000000 rule, punch codes, division detection, merge keys, header alias mapping and claim order,
  header-row scan, cell parsers, punch status / stats / days overdue, approval decisions / overdue, scheduler payload.
* `tests/planning/test_views.py` (`manage.py test tests.planning`) — page rendering per role, 403 for roles without
  planning.view / write, inline edit → history + version + stamp, stale version → 409, manual completion, create /
  duplicate / delete, SL linking + enrichment, Send to Scheduler (with and without the Phase E receiver), project card,
  punch seed / add / edit / delete / export, approvals create with attachment + download, non-approver 403 / approver
  identity recorded / double-approve 409 / reopen, CSV export filters, `api.create_approval_request`, importer merge
  rules, punch seed import, workbook mapping.

## 8. Known gaps (see the build log)

Punch photo / attachment upload UI (model only); Planner label sync waits for Phase I (`apps.planner.sync
.planner_statuses_for` is looked up with a guard); approvals ↔ estimate / bid links are stored but not yet shown
(Phase G / C); the import's mapping step shows the resolved columns but no numeric confidence; Data Quality page items
(unmatched rows, closed-in-SL rows) are surfaced in-page and in the import report, not yet on the Data Quality page.
