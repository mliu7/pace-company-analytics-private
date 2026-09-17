-- ptt.employees — every Pace person; employee_id is the SL key (PJEMPLOY.employee / PJTran.employee).
SELECT
    p.id                          AS ptt_person_pk,
    p.employee_id                 AS employee_key,
    p.first_name, p.last_name, p.short_name,
    p.employee_type,              -- 1 non-union, 2 union
    p.employee_role,              -- 1 head PM, 2 PM, 3 regular
    p.active_status,              -- 1 active, 2 inactive
    p.status                      AS ptt_record_status,   -- 1 live, 2 removed
    p.is_admin, p.is_visible,
    p.hourly_rate                 AS ptt_loaded_rate_estimate,   -- "Hourly Pace Burden"
    p.overtime_rate               AS ptt_loaded_ot_rate_estimate,
    p.base_hourly_wage,
    p.labor_class, p.union_code, p.work_location, p.work_comp_cd,
    p.sl_account, p.sl_subaccount, p.sl_username,
    p.sl_salesperson_ids,         -- comma list, e.g. "MB00,MB07"
    p.time_submission_expectation,
    p.time_reporter_id            AS ptt_time_reporter_pk,
    p.title_id                    AS ptt_title_pk,
    p.user_id                     AS ptt_auth_user_pk
FROM person_person p
WHERE p.client_id = %(client_id)s
ORDER BY p.id
