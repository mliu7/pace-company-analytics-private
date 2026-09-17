# 01 · Project overview

## Why this exists
Pace Systems (Application Owner, President) runs ~$30–40M/yr of project work with ~40 union electricians plus PMs, estimators and salespeople, and the divisions' margins are thin. Nobody at Pace has more than an anecdotal understanding of *which* projects, customers, PMs, solutions and field people are actually profitable once you strip out who worked with whom. Owner's brain dump asked for a "sabermetrics / net rating / ELO-like" system built from the fine-grained data Pace already has:

* **PTT** (Pace Time Tracker) — every electrician logs hours to a project every day with a note, a System (camera/access/AV/…) and a Work Type; PMs enter remaining hours and % complete.
* **Dynamics SL** — the accounting system: project master, budgets, billed revenue, every cost transaction, customers, and weekly per-employee labor postings.

The spec (v3) turned that into a staged plan. Release 1 = an honest, reconciled **truth layer** for division 070 (Premise Security Systems) with descriptive dashboards and first context-adjusted ratings; Release 2 = forecasting of active projects; later = field-crew ratings, other divisions, ML.

## Scope today (Release 1 + deterministic forecasting — built 2026-08-17)
* All divisions are ingested; **070** is the modelled division (switchable in the UI).
* Truth layer: SL revenue/cost by account category + PTT hours, reconciled to the cent against SL's own rollups every refresh.
* Lifecycle: awarded / in progress / field complete / dormant / closed-stabilizing / closed.
* Deterministic estimate-at-completion + risk score for every open project.
* Ratings v1 (ridge expected-outcome residual + empirical-Bayes shrinkage) for PMs, customers, sectors, solutions, project modes, commissioned salespeople, division-head eras.
* Field crew net +/- (RAPM, implemented 2026-08-20): hours saved per 1,000 budget hours per person, placebo-calibrated shrinkage, pair handling, crew-lead effect — Ratings → Field Crew; model card in `model_cards/field_crew_v1.md`. Classifications (journeyman/foreman/apprentice) derived for everyone, wage-inferred (≈) where PTT/SL lacks a class code.
* UI: Command Center, Projects, Project detail (every number traced to source rows), Active Book & Forecast, Project Managers, Field Crew, Customers & Sectors, Ratings, Data Quality & Refresh, Definitions.
* SharePoint integration (spec approved 2026-09-10, `Pace_Company_Analytics_SharePoint_Spec_v1.md`): the Project Portal bid log mirrored read-only through Graph — Bids (overview / board / calendar / list / analytics / bid page), Estimators (metrics + the concealed estimator rating v1.1, `model_cards/estimator_v1.md`), Planner mirror, production planning, resource scheduler, documents, estimating workbench (`docs/12_bids_and_estimators.md`, `docs/16_planner.md`, `docs/sharepoint_integration_build_log.md`).

Deliberately deferred: ML forecasting models (must beat the deterministic baseline first), a rated service-efficiency track (T&M has no beatable estimate). Estimator ratings now exist (source = the Project Portal's Bidder column) but stay concealed until validated.

## Key business facts learned from the data (details in 02_data_sources.md)
* 070 = Premise Security Systems; ~250–310 new projects/yr; $6–18M billed/yr at ~28–35 % GP on closed jobs; union burden ≈ 70 % of wages; loaded labor ≈ $84/h.
* Two-thirds of 070 projects are "non-commission" sales (`OT`); no estimator field exists anywhere.
* SL keeps only the current budget — no original budget, no change-order history. PTT keeps the PM remaining-hours revision history since Aug 2019.
* Biggest 070 customers since 2021: Chicago Public Schools, Sentinel (service agreements), SDI/O'Hare, City Colleges of Chicago, DePaul.

## People you will see in the data
PMs (SL `manager1`): Example employee EMP-DEMO, Example employee EMP-DEMO, Example employee EMP-DEMO (service tickets), Example employee EMP-DEMO, Example employee EMP-DEMO, Example employee EMP-DEMO, Michael ExampleSurname EMP-DEMO, Jeff ExampleSurname EMP-DEMO. Division heads (SL `manager2`): Example employee EMP-DEMO → Example employee EMP-DEMO (Oct 2024). the finance reviewer = finance contact for reconciliation sign-off.
