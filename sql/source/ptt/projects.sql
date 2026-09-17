-- ptt.projects — all PTT projects for the Pace client, with the SL keys PTT mirrors.
-- Full read every run (8,999 rows). JSON columns are returned as text and parsed locally.
SELECT
    p.id                                              AS ptt_project_pk,
    p.project_id                                      AS project_number_raw,
    p.project_status                                  AS ptt_status,            -- 1 active, 0 inactive
    p.status                                          AS ptt_record_status,     -- 1 live, 2 removed
    p.removed_time                                    AS ptt_removed_at,
    p.project_inactivation_date,
    p.description                                     AS title,
    p.project_lead_id                                 AS ptt_project_lead_pk,
    pl.employee_id                                    AS project_lead_employee_key,
    p.customer_id                                     AS ptt_customer_pk,
    c.customer_id                                     AS sl_customer_id,
    p.salesperson_id                                  AS ptt_salesperson_pk,
    sp.employee_id                                    AS salesperson_employee_key,
    p.expense_account                                 AS numbering_style_code,  -- SL PJPROJ.user1 (SO2/SO3)
    p.expense_subaccount                              AS sl_subaccount,         -- SL PJPROJ.gl_subacct
    p.start_date                                      AS planned_start,
    p.end_date                                        AS planned_end,
    p.project_created_time,
    p.estimated_percent_complete,                                              -- 0..100
    p.estimated_percent_complete_last_updated_time,
    p.estimated_percent_complete_last_updated_by_id   AS ptt_pc_updated_by_pk,
    upd.employee_id                                   AS pc_updated_by_employee_key,
    p.hours_budgets                                   AS hours_budgets_json,     -- {"1": non-union h, "2": union h}
    p.hours_actuals                                   AS hours_actuals_json,     -- SL act_units copy
    p.estimated_hours_to_completion                   AS remaining_hours_json,   -- {"1":..,"2":..,"history":[...]}
    p.costs_budgets                                   AS costs_budgets_json,     -- {SL acct: cents}
    p.costs_actuals                                   AS costs_actuals_json,
    p.remaining_labor_costs                           AS remaining_labor_costs_cents,
    p.remaining_expense_costs                         AS remaining_expense_costs_cents
FROM project_project p
LEFT JOIN person_person pl  ON pl.id  = p.project_lead_id
LEFT JOIN person_person sp  ON sp.id  = p.salesperson_id
LEFT JOIN person_person upd ON upd.id = p.estimated_percent_complete_last_updated_by_id
LEFT JOIN project_customer c ON c.id  = p.customer_id
WHERE p.client_id = %(client_id)s
ORDER BY p.id
