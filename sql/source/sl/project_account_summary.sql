-- sl.project_account_summary — PJPTDSUM, the current budget/actual/commitment state per project x task x account.
-- Full read every run (193,927 rows, < 2 s). No reliable per-row watermark for budgets, so it is diffed locally by hash.
SELECT RTRIM(s.project) AS project_number_raw, RTRIM(s.pjt_entity) AS task_id, RTRIM(s.acct) AS sl_acct,
       s.act_amount, s.act_units, s.com_amount, s.com_units, s.eac_amount, s.eac_units, s.fac_amount, s.fac_units,
       s.total_budget_amount AS budget_amount, s.total_budget_units AS budget_units,
       s.crtd_datetime AS sl_created_at, RTRIM(s.crtd_user) AS sl_created_by,
       s.lupd_datetime AS sl_updated_at, RTRIM(s.lupd_user) AS sl_updated_by, RTRIM(s.lupd_prog) AS sl_updated_prog,
       CAST(s.tstamp AS bigint) AS tstamp_int
FROM PJPTDSUM s
ORDER BY s.project, s.pjt_entity, s.acct
