-- sl.project_account_rollup — PJPTDROL, project-level rollup of PJPTDSUM. Used as the nightly checksum
-- against SUM(PJTran) and against our own task-level rollup. Full read (86,366 rows).
SELECT RTRIM(r.project) AS project_number_raw, RTRIM(r.acct) AS sl_acct,
       r.act_amount, r.act_units, r.com_amount, r.eac_amount, r.fac_amount,
       r.total_budget_amount AS budget_amount, r.total_budget_units AS budget_units,
       r.lupd_datetime AS sl_updated_at
FROM PJPTDROL r
ORDER BY r.project, r.acct
