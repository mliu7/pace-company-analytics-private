-- sl.employees — Project Controller employee master joined to the payroll master (for union/work-comp codes).
SELECT RTRIM(e.employee) AS employee_key, RTRIM(e.emp_name) AS name_last_first, e.emp_status AS sl_status,
       RTRIM(e.emp_type_cd) AS emp_type_cd, RTRIM(e.gl_subacct) AS home_subaccount,
       e.date_hired AS hire_date, e.date_terminated AS termination_date, RTRIM(e.manager1) AS manager_key,
       RTRIM(e.user_id) AS sl_user_id, e.crtd_datetime AS sl_created_at, e.lupd_datetime AS sl_updated_at,
       RTRIM(m.HomeUnion) AS home_union, RTRIM(m.WCCode) AS work_comp_code, RTRIM(m.DfltWrkloc) AS default_work_location,
       RTRIM(m.DfltExpSub) AS default_expense_subaccount, m.StdUnitRate AS payroll_std_unit_rate_stale,
       m.StrtDate AS payroll_start_date, m.EndDate AS payroll_end_date, RTRIM(m.Status) AS payroll_status
FROM PJEMPLOY e
LEFT JOIN Employee m ON m.EmpId = e.employee
ORDER BY e.employee
