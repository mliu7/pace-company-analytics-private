# 13b · Resource Scheduler (`/planning/schedule/`)

The weekly crew-allocation board for the field and engineering roster, rebuilt from the P-drive dashboard
(`resource_scheduler.html`, inventory in `docs/sharepoint_dashboards_inventory.md`) on PCA data. Spec: `Pace_Company_Analytics_SharePoint_Spec_v1.md`
§6.3 and Appendix A.5 (RS-01 … RS-15). Build log: `docs/build_logs/phase_E.md`. Code: `apps/scheduling/`.

## What it is
Every project being staffed carries **phases** (Mobilization, Demo, Rough, Pull, Trim, Test = union; Engineering, Fabrication,
Programming, Commissioning, Service = non-union; Test may be either) with **total man-hours** over a date window, or — for a short
project — one Union block and one Non-Union block with totals. The scheduler turns hours into a **crew plan** ("2 days of 3 resources,
1 day of 2"), lets the coordinator put named people on project-days within their weekly / daily caps, shows who is free, which jobs
still need people or dates, exports the week, and — new — compares the plan with the hours PTT actually recorded.

PCA is the system of record: the data lives in `scheduling_resource`, `scheduling_resourcepto`, `scheduling_planproject`,
`scheduling_planphase`, `scheduling_phaserange`, `scheduling_assignment` (docs/05). Every edit records who (`created_by` /
`updated_by`, `access.Account`), bumps a `version` (a stale form gets "Changed by X at hh:mm — reload") and writes an
`AuditEvent(kind="write_action")`. Nothing is written to PTT, SL, SharePoint or the share.

## The roster (RS-01)
Resources are `core_employee` rows (`Resource.employee`, one per person): `manage.py scheduling_seed_roster --apply` adds every active
employee who is field-hourly, a PM / head PM or an active non-union technician — trade **Union** when the employee has a union code,
union PTT type or an IBEW classification, else **Non-Union**; home division from the SL home subaccount. People can also be added by
hand (a subcontractor) with an optional employee link. Per person: trade (Union · Subcontracted Union · Non-Union — only exact
"Non-Union" is non-union for every rule), max hrs / week (default 40), home divisions (★ in the assign list; non-union people carry
none), Approved Overtime, PTO ranges, active flag. Removing a person deletes their assignments (confirmed first).

## The maths (RS-03 … RS-12) — `apps/scheduling/maths.py`, unit-tested
| Rule | Definition |
|---|---|
| Phase covers a day | explicit `dates` when present; else start ≤ d ≤ end and weekday, or Saturday / Sunday when the weekend rule says so. A completed-early record gates every later day of the phases in its scope. |
| Crew plan | `ceil(hours ÷ 8)` resource-days spread front-loaded over the covered days (first `rem` days get one more); the overage `slots × 8 − hours` is trimmed from the last days. Text: "n days of m resources, …". |
| Phase progress | slots sorted largest first; walking the days, a day consumes the first slot whose crew ≤ distinct people that day **and** slot × 8 ≤ hours that day. Green tag = phase fully allocated or the day consumed a slot; amber = some hours that day; red = none. |
| Phase staffed | hours > 0, a schedule exists, Σ assigned hours (any date) ≥ hours; completed early in scope → staffed. |
| Still needs (per day) | planned hours that day − assigned → "2 resources + 1 for 4h (20h)" / "1 resource for 4 hours". |
| Needs Staffing | a project is listed until both trades are resolved (fully staffed or no phases); **Needs Dates** = no on-site window / unscheduled / no phase has dates. |
| Got / need | need = Σ phase hours by trade (else the project totals); got = Σ assignment hours by the **phase's** trade when the assignment carries one, else the person's trade (the dashboard used the person only). |
| Weekly cap | max hrs/week − 8h per PTO weekday in that Mon–Sun week (never below 0). |
| **Capacity check (one rule)** | PTO blocks (always). Approved Overtime bypasses both caps. Else: hours that day + new > 8h → "daily"; hours this week + new > weekly cap → "weekly". Used by the person-day modal, the project modal, the phase panel, hour edits, Copy Allocation, drag-and-drop and the server. |
| Room today | min(weekly cap left, 8 − hours today); OT: 12 − hours today. Hours picker 1–8, or 1–12 with OT. |
| Week summary | per trade: cap = Σ weekly caps, booked = Σ min(cap, hours Mon–Sun), free, % utilised. |
| Day "Nh free" | Σ over people not on PTO of max(0, min(8 − hours that day, weekly cap left)). |
| Copy Allocation | today's people on a phase → selected days, replacing what was there; conflicts (PTO, already scheduled elsewhere that day, weekly cap unless OT) are listed first and skipped ("Copy the rest"). |
| Completed Early | date + scope (whole project · a phase · all Union / Non-Union work); releases only allocations **after** the date in scope; plan totals unchanged; reversible ("Not complete"). |
| Scheduling assistant | per phase / trade block: each person's free hours within the dates = per week min(8 − booked elsewhere that day, weekly cap − booked elsewhere), PTO 0; "Covered · Nh" / "Short Nh"; day chips (red 0 free, amber < hours ÷ days); project roll-up with an overlap note. |
| Adherence (RS-15) | per person × project for the week: planned = Σ assignment hours, worked = Σ PTT job-report hours (`operations_timeentry`, form type 1, live); Δ and a state (as planned / under / over / no PTT time / not planned). Only people with a PTT link on projects with an SL number. |

## The page
* **Schedule view** — week summary strip; filters Division (projects, chips and the people whose home division matches), PM, Rows
  (all / with hours); the board: Mon–Fri plus weekend columns only when an assignment or phase uses them, day headers with free hours,
  a lane per project on site that week (PM colour fill, division border, access time, phase tags green / amber / red; click → staff
  that day), one row per person (utilisation bar; click → editor) with chips per project-day (hours, phase tags, note dot, access;
  click → edit; **drag to another day**), PTO ✕ cells, daily-load footer. Week nav ‹ › + Today (live clock). **Needs Staffing** rail:
  division tabs, Needs Resources / Needs Dates, expand → per-trade totals, per-phase crew plan and "still needs" lines. **Planned vs
  worked** card below the board.
* **Projects view** — All-projects timeline (9 px/day, month and week ticks, today line, real horizontal scrollbar, division filter;
  click → editor) and project cards by division tab + PM (allocation bars, phase chips, people, completed-early badge, PTT worked ·
  PM remaining · SL labor budget · planned here, "plan > remaining" flag, link to the project page).
* **Modals** — person-day assignment (projects active that day by window + days toggles, phases for the person's trade, hours 0–24
  step ½, note); project staffing (phase summary with crew line, Allocated chips → Copy Allocation, Allocation met, Assign resources:
  search, role, division sub-tabs with ★ home and borrowing, "Nh left", OT, hours, phase, per-person phase panel; Assigned tab;
  Completed Early; Edit dates & hours); resource editor; project editor (SL project link, PM, division, colour, on-site window,
  access, days toggles, totals, short project blocks or the 11 phase rows with extra ranges / per-range hours / Test trade, inline
  "48h → 2 days of 3 resources", assistant on the right). Every toggle is remembered (`PCA.pref`, scope `schedule`); the week is in
  the URL and the sidebar brings you back to it.
* **Exports** — Week CSV (the dashboard's layout: PROJECTS row, per-resource cells, Hours by resource, Hours by project), Week XLSX
  (Week / Hours by resource / Hours by project / Assignments sheets), Resource schedules (pick people → standalone print page).

## Endpoints (all in `apps/access/registry.py`)
| Name | Cap | What |
|---|---|---|
| `planning_schedule` | planning.view | the page |
| `planning_schedule_json` | planning.view | `?week=YYYY-MM-DD` board payload; `kind=project&id=`, `project_search&q=`, `employee_search&q=`, `pms` |
| `planning_schedule_edit` | planning.write | POST JSON `{action, week, …}` → `{ok, board}`; 400 with `reason` (pto / daily / weekly / phase / dup), 409 on a stale `version` |
| `planning_schedule_export` | planning.view | `?kind=week_csv|xlsx|resources_html&week=&resources=` |

Actions: `resource_save`, `resource_delete`, `resource_ot`, `project_save`, `project_delete`, `assignment_save` (hours 0 deletes),
`assignment_delete`, `assignment_move`, `phase_panel`, `copy_preview`, `copy_apply`, `ce_preview`, `completed_early`,
`completed_early_clear`, `assist`.

## Receiver for the status board (RS-14)
`apps.scheduling.api.create_plan_project(payload, user)` — keys `source, status_row_id, project_id, name, division, pm_id, pm_raw,
hours_union, hours_per_day, days, start, end, engineer, notes, phase_status, equipment_status` (`kind` 'union' | other). A re-send with
the same `status_row_id` updates the row; without start and end the project is `unscheduled` (Needs Dates); with dates it gets a
synthetic short-project block per trade so it appears on the board immediately.

## Transition import (one time)
`manage.py scheduling_import [--file P] [--apply]` reads the dashboards' `resource_scheduler_data.json` (default: the repo copy under
`../internal_reports/Sharepoint Integration/resource-scheduler/`; at cut-over pass the live P-drive file), matches people to
`core_employee` (suffix codes AF/AJ/CJ/CF/CGF/AGF/A-APP/C-APP/APP stripped, "Last, First", first-name prefix, last name within one edit)
and projects to `core_project` by the leading six digits (000000 rule), prints the report, and with `--apply` seeds the roster and
writes resources (linking employees; a re-run folds an unlinked legacy row into the employee's row), projects, phases, ranges and
assignments (rows with no date are skipped). Re-runnable; never writes the file. Result 2026-09-10: 52/52 people, 38/38 projects,
78 assignments.

## Handy queries (local DB)
```sql
-- who is on which job this week
SELECT a.date, r.display_name, p.name, ph.name AS phase, a.hours FROM scheduling_assignment a
JOIN scheduling_resource r ON r.id=a.resource_id JOIN scheduling_planproject p ON p.id=a.plan_project_id
LEFT JOIN scheduling_planphase ph ON ph.id=a.phase_id WHERE a.date BETWEEN '2026-09-21' AND '2026-09-27' ORDER BY 1,2;
-- projects with no dates (Needs Dates)
SELECT name, source, unscheduled FROM scheduling_planproject WHERE start IS NULL OR end IS NULL OR unscheduled;
```
