-- sl.financial_transactions_since — PJTran rows created since the watermark (rows are append-only; PK
-- fiscalno+system_cd+batch_id+detail_num). Pass since = last watermark - 30 days; dedupe locally by source_key.
-- crtd_datetime is not indexed but a full scan of 899k rows completes in ~1 s.
SELECT RTRIM(t.fiscalno) AS fiscal_period, RTRIM(t.system_cd) AS system_cd, RTRIM(t.batch_id) AS batch_id, t.detail_num,
       RTRIM(t.project) AS project_number_raw, RTRIM(t.pjt_entity) AS task_id, RTRIM(t.acct) AS sl_acct,
       t.trans_date AS transaction_date, t.post_date AS posting_date, t.crtd_datetime AS source_created_at,
       RTRIM(t.crtd_user) AS source_created_by, RTRIM(t.crtd_prog) AS source_created_prog,
       t.amount, t.units, RTRIM(t.unit_of_measure) AS unit_of_measure,
       RTRIM(t.batch_type) AS batch_type, RTRIM(t.tr_status) AS tr_status,
       RTRIM(t.employee) AS employee_key, RTRIM(t.vendor_num) AS vendor_num,
       RTRIM(t.gl_acct) AS gl_account, RTRIM(t.gl_subacct) AS gl_subaccount,
       RTRIM(t.tr_comment) AS comment, RTRIM(t.voucher_num) AS voucher_num, t.voucher_line,
       RTRIM(t.bill_batch_id) AS bill_batch_id, RTRIM(t.tr_id01) AS tr_id01, RTRIM(t.tr_id02) AS tr_id02,
       RTRIM(t.Subcontract) AS subcontract_ref, RTRIM(t.SubTask_Name) AS subtask_name,
       CAST(t.tstamp AS bigint) AS tstamp_int
FROM PJTran t
WHERE t.crtd_datetime >= ?
ORDER BY t.crtd_datetime, t.fiscalno, t.system_cd, t.batch_id, t.detail_num
