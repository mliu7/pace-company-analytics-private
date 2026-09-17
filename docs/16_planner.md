# Planner boards (`apps/planner`)

Microsoft Planner is where the controller's teams run their production work (SharePoint spec §1.2, §9). PCA mirrors **every plan
of every Microsoft 365 group** the reader app can see — read-only, through Graph — and shows them under
**Production › Planner boards** (`/planning/planner/`). Other apps read the mirror through `apps/planner/sync.py`.

Built in Phase I (2026-09-10); log in `docs/build_logs/phase_I.md`.

## What Planner holds (verified 2026-09-10)

93 unified groups, **56 plans, 222 buckets, 1,499 tasks**. The plans that matter for production:

| Plan | Group | Kind | What it is |
|---|---|---|---|
| Project Tracking (401 tasks, 16 buckets, 14 labels) | CPS Genetec Upgrades | workflow | one task per CPS job (`26-5166 …`), buckets = QC / execution stages, labels = technicians + states |
| Service (364) | CPS Genetec Upgrades | workflow | CPS service tickets |
| 1. Fabrication (163) | AV Division | workflow · AV stage *Fabrication / Shipping* | AV jobs by fabrication stage |
| AV Programming List (32) | AV Division | workflow · AV stage *Programming / Commissioning* | AV jobs being programmed |
| AV A-Card Schedule (3) / A-Card Schedule (0) ×2 | Pace Systems, Pace AV | workflow · AV stage *A-card* | A-card scheduling |
| 265092 - Skokie Paging (15) | 070 Master Schedule | job | a per-job plan |
| `YY-NNNN …` plans (9–18 each) | SS PM, OT AV quotes… | job | one plan per AV job, the standard 9-task PM template |
| PUNCH LIST JOBS (24) | Pending Punch List items | punch | a bucket per job, a task per punch item |
| everything else (Bart Tasks, SOC 2 Compliance, Onboarding…) | | admin | team to-do lists |

The AV Teams channels named as a production sequence (*1. Installation Schedule, 1.1 Install Line-UP, 2 Project
Tracker, 3 Fabrication.Shipping Tracker, 6 Programming.Commissioning, 7 Signal Flow Queue, AV A-Card, AV Signoff
Sheet* — spec §1.2) do **not** each have a plan: only *Fabrication*, *Programming* and *A-card* exist as plans today.
The combined "AV production" board therefore has three columns; it grows by itself when the other channels get plans
(the stage is derived from the plan title, never from a hard-coded id).

## Data flow

```
Graph /groups (Unified) ──► PlannerGroup (93)         one call
  └ /groups/{id}/planner/plans ──► PlannerPlan (56)    one call per group
      ├ /planner/plans/{id}/details   labels (categoryDescriptions)      ┐
      ├ /planner/plans/{id}/buckets   PlannerBucket                       ├ 3 calls per plan, every refresh
      └ /planner/plans/{id}/tasks     PlannerTask  — diffed by @odata.etag ┘
          └ /planner/tasks/{id}/details  description / checklist / references — only when the task's etag moved
                                          and the task has any (capped: 600 per run, rest flagged details_pending)
Graph /users ──► PlannerUser (413) → core_employee by name (apps.bids.rules.alias_candidates, ≥ 0.85)
```

* **No delta query exists for Planner**, so each refresh re-reads every plan's task list (one paged call) and compares
  etags; an unchanged etag costs nothing more. ~100 s for the whole tenant, ~210 s on the first pull.
* **History**: every change to a tracked field — `percent`, `bucket`, `due`, `start`, `title`, `priority`, `labels`,
  `assignees`, `completed` — writes a `PlannerTaskHistory` row (`created` for new tasks with Planner's creation time,
  `deleted` when Planner stops returning a task; the task row is kept, flagged inactive). Planner keeps no change log,
  so `changed_at` is the pull that noticed the change, except creation and completion which carry Planner's own stamps.
* **Raw archive**: every payload is gzipped under `~/Library/Application Support/PaceCompanyAnalytics/graph_raw/<date>/`
  (`planner_groups`, `planner_users`, `planner_plan_<id>_{details,buckets,tasks,task_details}`).
* **Refresh**: `refresh_all` runs `apps.planner.loaders.refresh_all_step` (best effort, after the SharePoint step);
  `manage.py refresh_planner` runs it alone (`--details N`, `--plan <id>`, `--local` to recompute links / kinds / DQ
  from local rows). Runs are `IngestionRun(source_system="planner")`.
* **Read-only**: only `graph_client.get`/`paged` (GET) are used; the permission audit (`Tasks.Read.All`,
  `Group.Read.All`, `User.ReadBasic.All`, `Sites.Read.All` — no write role) runs first and is recorded on the run.

## Rules (`apps/planner/rules.py`, unit-tested in `tests/unit/test_planner_rules.py`)

* **Board kind** (editable — `board_kind_manual` stops the rule from overwriting): `punch` when "punch" is in the plan or
  group name; `job` when the plan title or group name carries a job / quote number (one plan per job); `workflow` when
  ≥ 30 % of task titles or bucket names carry job numbers, or the title is a production / schedule / tracker word;
  `admin` otherwise.
* **AV stage**: the plan title matched against the eight stage patterns (install schedule, line-up, project tracker,
  fabrication / shipping, programming / commissioning, signal flow, a-card, sign-off); the title or group must say AV
  unless the word is unmistakably production. The combined board needs ≥ 2 distinct stages; an empty duplicate of a
  stage that already has a plan is left out.
* **Project link** (`match_project`): numbers in the title, then the bucket name — 12-digit runs, 6-digit runs, and
  `YY-NNNN` (how PMs write job 265166 as `26-5166`; also the AV quote form) — each through
  `apps.bids.rules.job_number_candidates` (000000-aware: `24-1608` found only `241608000000`); then the `YY-NNNN` against
  `core_project.quote_reference`. The rule that matched is kept (`title:quote`, `bucket:quote`, `title:digits6`,
  `quote_reference`) and shown on the card. Result today: 278 of 1,499 tasks linked (254 title:quote, 18 bucket:quote,
  6 title:digits6); every quote-style number resolved to an SL job. When both `NNNNNN` and `NNNNNN000000` exist the
  6-digit job wins (nothing in a title can tell them apart).
* **Labels**: `category1..25` → Planner's fixed colours (Pink, Red, Yellow, Green, Blue, Purple, Bronze, Lime, Aqua,
  Gray, Silver, Brown, Cranberry, Orange, Peach, Marigold, LightGreen, DarkGreen, Teal, LightBlue, DarkBlue, Lavender,
  Plum, LightGray, DarkGray); the plan's `categoryDescriptions` give the names. A label applied but never named shows
  as its colour name.
* **Dates**: `startDateTime` / `dueDateTime` are UTC instants for a picked date → the **US-Central calendar date**.
  Overdue = due before *today* on the live clock and not complete (the page re-evaluates every minute).
* **Priority** 0-10 → Urgent (≤ 1), Important (≤ 4), Medium (≤ 7), Low. **State**: 0 % not started, 1–99 in
  progress, 100 complete.
* **Links**: Planner's own "copy link" forms — task `https://tasks.office.com/<tenant domain>/Home/Task/<taskId>?Type=TaskLink&Channel=Link`,
  plan `…/Home/PlanViews/<planId>?Type=PlanLink&Channel=Link`; the tenant domain comes from the group mailbox
  (`pace-systems.com`). The Teams deep link was not used (it needs channel / tab ids Graph's planner endpoints do not
  return). The group's SharePoint site is recorded as `/sites/<mailNickname>` by convention (not verified per group).

## The page (`/planning/planner/`)

1. **Board picker** — an *AV production sequence* row (the combined board + each stage plan), then plans grouped by
   kind (workflow → per-job → punch → admin) and group, each with open / total, overdue, and the newest change. Picker
   state, the chosen board, the view and every filter are remembered (`PCA.pref`, keys `planner.*`).
2. **Board** — buckets as columns (own horizontal scroll, always-visible scrollbar; columns scroll vertically), tasks
   as cards: labels in the plan's colours, state pill, due date (red when overdue, amber within three days), checklist
   n/m, description / attachment marks, assignee initials (blue = you), and for linked jobs the SL number (→ project
   page), division, **CV · billed · PTT hours / budget hours · PTT %** (money only with `projects.view`; never GP).
   Clicking a card opens a detail panel (description, checklist, attachments, changes seen) with the Planner link.
   The combined AV board's columns are the stage plans; cards are sub-grouped by their real bucket.
3. **List** — the same cards as a table, every row (no cap), frozen first column, multi-key sort (shift-click), same
   filters.
4. **Changes** — history rows for the board since 1 / 7 / 14 / 30 / 90 / 365 days, grouped by day.
5. **Filters**: division of the linked job, assignee, label, state (default *open*), overdue only, my tasks (the
   signed-in account's Planner user by e-mail / employee), linked to a job, free-text search.

Empty mirror → "Planner not connected — see Data Quality" (spec §9) and nothing else breaks.

JSON: `/planning/planner/data/` (picker), `?plan=<id>&since=<days>` (one board), `?plan=av` (combined). Both URLs
require `planning.view` (registry `planning_planner`, `planning_planner_json`); the money fields need `projects.view`.

## Sync contract (`apps/planner/sync.py`) — for the status board and project pages

```python
from apps.planner.sync import planner_statuses_for, open_tasks_for_project
planner_statuses_for(["265092", "241608000000"])   # {cpn: [task_status, ...]} — absent when a job has no task
open_tasks_for_project("265092")                   # active, not complete
```

`task_status` keys (stable): `plan`, `plan_id`, `board_kind`, `group`, `bucket`, `task_id`, `task_title`, `task_url`,
`labels`, `label_keys`, `label_colors` (`[{label,name,bg,fg}]`), `percent`, `state`, `priority`, `priority_label`,
`start`, `due`, `completed_at`, `assignees`, `checklist` (`{done,total}`), `quote_ref`, `match_rule`, `last_change`.
Ordered workflow → job → punch → admin, then plan title, bucket, Planner order. Phase D maps `labels` onto the
status-board vocabulary and `bucket` onto its stage.

## Data Quality (source `planner`)

* `planner_task_no_project` (info) — per workflow / punch plan: open tasks with no SL number (count + sample). Today
  8 plans, mostly the CPS Service tickets and the untitled Fabrication cards.
* `planner_user_unmatched` (warning) — Planner users with assignments but no `core_employee` match (5 today: people
  outside the PTT / SL rosters).
* The permission audit is on every planner run (`permissions_audit.graph`).

## Local tables

`planner_plannergroup`, `planner_plannerplan`, `planner_plannerbucket`, `planner_plannertask`,
`planner_plannertaskhistory`, `planner_planneruser` (`docs/05_local_database.md` conventions). Handy:

```sql
SELECT g.name, p.title, p.board_kind, p.av_stage, p.open_count, p.task_count FROM planner_plannerplan p JOIN planner_plannergroup g ON g.id = p.group_id WHERE p.active ORDER BY 3, 1, 2;
SELECT project_rule, COUNT(*) FROM planner_plannertask WHERE active GROUP BY 1;
SELECT h.changed_at, t.title, h.field, h.old, h.new FROM planner_plannertaskhistory h JOIN planner_plannertask t ON t.id = h.task_id ORDER BY h.changed_at DESC LIMIT 50;
```

## Gaps and assumptions

* The board kind is editable in the model (`board_kind`, `board_kind_manual`) but there is no UI for it yet (Django
  admin / shell).
* `SS PM › 25-xxxx Bulley & Andrews …` (a placeholder number) is classed *admin*; per-job plans need a real number.
* Task attachments live in the group site's "Microsoft Planner" folder — indexed by Phase F (documents), not here.
* Assignment order / `assigneePriority` and the plan's `sharedWith` are not stored.
