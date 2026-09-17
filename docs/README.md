# Documentation index

| Doc | Read it when you need to… |
|---|---|
| [01_project_overview.md](01_project_overview.md) | understand what Pace Company Analytics is for, Owner's vision, the release plan and what is built today |
| [02_data_sources.md](02_data_sources.md) | connect to PTT or Dynamics SL, know which tables/columns/codes carry what, query them safely, avoid the known traps |
| [03_architecture.md](03_architecture.md) | change the app: Django apps, models, ingestion pipeline, analytics, UI, where each thing lives |
| [04_metric_dictionary.md](04_metric_dictionary.md) | know exactly how any number on a page is computed |
| [05_local_database.md](05_local_database.md) | query the app's own PostgreSQL (tables, keys, handy SQL) |
| [06_finance_reports.md](06_finance_reports.md) | Financial Reports section: daily finance snapshot — GL/AR/AP mapping, formulas, verification |
| [07_access.md](07_access.md) | multi-user access: roles, Superadmin tier, console, SSO runbook, the 46-test guarantee |
| [08_channelonline_exploration.md](08_channelonline_exploration.md) | CNET/ChannelOnline API: what it serves, schema, SL linkage (SOHeader.User2), 010 sizing, ingestion sketch |
| [09_010_sales.md](09_010_sales.md) | 010 Hardware Sales section: CNET+SL data flow, metric definitions (bookings/realized/drift/backlog/pipeline), pages, access, gotchas |
- `10_divisional_pnl.md` — Divisional P&L page (`/finance/pnl/`): month/quarter/year statement by division, booked-vs-estimated WIP, payroll-lag estimate, corporate allocation, drill-downs to accounts/postings/projects.
| [project_snapshot_spec.md](project_snapshot_spec.md) | Daily/Weekly Project Snapshot (IMPLEMENTED): what happened on jobs — day/week economics, health trends, roster & flags, money & material movement |
| [010_daily_snapshot_plan.md](010_daily_snapshot_plan.md) | 010 Daily/Weekly Snapshot (IMPLEMENTED 2026-09-01, `/sales/010/snapshot/`): the hardware-sales body of the snapshot page — quoted / sold / shipped & invoiced / purchased / cash / returns / GL day, backlog & pipeline as-of, exceptions; data inventory + the SOShipLine/SOShipLot extracts it needs |
| [07_pnl_and_wip.md](07_pnl_and_wip.md) | **READ FIRST for any revenue/GP number**: fiscal-period P&L vs closed-job cohorts, the workbook mapping (verified to the penny), WIP methodology + correction history, 000 overhead allocation |
- `08_project_map.md` — the Project Map page: where locations come from (ship-to → customer → city → manual), the payload, towers/crew/timeline, caveats. Plan: `project_map_plan.md`.
- `sharepoint_dashboards_inventory.md` — the line-by-line inventory of the eight dashboards the spec must match (formulas, views, rules, defects); read-only design notes, never a data source.
- `13_production_planning.md` — Production planning on PCA tables (`/planning/`): today, status board, active tasks, punch lists, approvals; `planning_import` transition importer (dry-run first); Send to Scheduler; Planner label sync.
- `13b_resource_scheduler.md` — Resource Scheduler (`/planning/schedule/`): week board, crew-plan maths (reproduced from the dashboard, PTO-adjusted capacity), staffing / project / resource modals, adherence vs PTT, `scheduling_import`.
- `14_documents.md` — Documents (`/documents/`): SharePoint libraries via Graph delta + the P: drive walk (budgeted, resumable), job / bid linking rules (AV `YY-NNNN` = SL job number), text extraction, proposal checks (clauses = placeholders until Owner supplies them), previews, findings; read-only everywhere.
- `15_estimating.md` — Estimating workbench (`/estimating/`): vendor price catalog (folder loader + upload, hygiene), search ladder, room-based builder with the 11 labor columns and the 1.265 markup coupling, effective-dated rate card, versions, imports / exports, bid link and the approval bridge into Production.
- `16_planner.md` — Microsoft Planner mirror (`apps/planner`, `/planning/planner/`): groups / plans / buckets / tasks / history, board kinds, the AV production sequence, the `planner_statuses_for` sync contract, DQ codes.
- `11_sharepoint_exploration.md` — SharePoint via Graph (reader app in `.env`): the Project Portal's Project List registry (budgets, values, bid stages) and where per-job documents live; attachments need a certificate credential.
- `contract_value_reconciliation_plan.md` — **proposed**: detect and correct SL contract-value entry errors (double-counted task CVs, missing digits, stale CVs on finished jobs) inside the app — rule engine, guard-rails, override queue; audit behind it: `scripts/contract_value_audit.py`.
| [field_crew_rating_plan.md](field_crew_rating_plan.md) | build the field-crew net +/- rating (plan, not yet implemented) |
| [12_related_parties.md](12_related_parties.md) | the owner-family (related-party) policy: what is kept out of exception / collection / highlight views, what always stays in the books, and why identity is by SL id and never by name |
| [runbooks/operations.md](runbooks/operations.md) | run, refresh, scheduled refresh (launchd 07:00/12:00/16:30), back up, restore |
| [runbooks/mail.md](runbooks/mail.md) | read & send mail for `recipient@example.invalid` via OAuth + Microsoft Graph (`scripts/mail.py`); basic auth is disabled so IMAP/SMTP can't be used |
| [../Pace_Company_Analytics_Django_070_Build_Spec_v3.md](../Pace_Company_Analytics_Django_070_Build_Spec_v3.md) | the full spec; §1/§6/§16 = verified source mapping; top has an "Implementation status" section |
| [../Pace_Company_Analytics_Access_Spec_v1.md](../Pace_Company_Analytics_Access_Spec_v1.md) | the multi-user rollout: SSO, roles/permissions, Superadmin tier, admin console, user switcher, audit, test plan |

Ground rule for everything here: PTT and SL are read-only, always. The app's only writable database is `pace_company_analytics` on 127.0.0.1:5433.
- `ar_page_plan.md` — the Accounts Receivable page: plan, the power and usability reviews, and what was built (2026-09-04).
- `sales_tax_page_plan.md` — the superadmin-only Sales Tax page (`/finance/sales-tax/`): the 20500 liability by state, remittances and the periods they covered, filing calendar, nexus screen, rate checks, state rules reference; the SL facts behind it (2026-09-08).
- `billings_page_plan.md` — the Billings page (`/finance/billings/`): what was billed day by day, who keyed it, the date on the invoice vs the day keyed vs the fiscal period, the job's WIP columns; the SL facts behind it (screens, logins, back-dating, the GL tie) (2026-09-08).

- `shared_deployment.md` — company main deployment and state recovery.
- `ad_hoc_reports.md` — individual and group report access.
