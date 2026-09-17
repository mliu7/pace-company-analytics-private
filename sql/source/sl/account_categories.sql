-- sl.account_categories — PJACCT (19 rows). Seeds AccountCategory; the category mapping is local (§4.2).
SELECT RTRIM(a.acct) AS sl_acct, RTRIM(a.acct_desc) AS description, RTRIM(a.acct_type) AS sl_acct_type,
       RTRIM(a.acct_group_cd) AS sl_group_cd, a.acct_status, a.sort_num, a.lupd_datetime AS sl_updated_at
FROM PJACCT a
ORDER BY a.sort_num, a.acct
