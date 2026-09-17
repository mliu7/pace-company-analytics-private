-- Posted GL transactions, recent window (ACTUAL ledger): income/expense accounts (3I/4E) plus
-- asset accounts (1A) and liability accounts (2L below 30000 — equity and 39999 excluded).
-- 3I/4E rows power the daily-snapshot P&L: month-to-date and prior-month revenue/cost, the
-- "posted on the previous business day" split (by Crtd_DateTime), and posting-level drill-downs.
-- Revenue convention: CrAmt - DrAmt on 3I accounts; cost: DrAmt - CrAmt on 4E.
-- 1A/2L rows power the assets / liabilities breakdowns (ledger activity per account, day change
-- between pulls, sales tax by jurisdiction); an asset increases by DrAmt - CrAmt, a liability by CrAmt - DrAmt.
-- Params: 1 = earliest PerPost period ('YYYYMM'), 2 = earliest Crtd_DateTime.
SELECT RTRIM(t.Acct)     AS acct,
       RTRIM(t.Sub)      AS sub,
       RTRIM(a.AcctType) AS acct_type,
       RTRIM(a.Descr)    AS acct_descr,
       t.TranDate        AS tran_date,
       t.Crtd_DateTime   AS sl_created_at,
       RTRIM(t.PerPost)  AS per_post,
       t.DrAmt           AS dr_amt,
       t.CrAmt           AS cr_amt,
       RTRIM(t.Module)   AS module,
       RTRIM(t.BatNbr)   AS batch_nbr,
       RTRIM(t.RefNbr)   AS ref_nbr,
       RTRIM(t.TranDesc) AS tran_desc,
       RTRIM(t.JrnlType) AS jrnl_type
FROM dbo.GLTran t
JOIN dbo.Account a ON a.Acct = t.Acct
WHERE t.LedgerID = 'ACTUAL' AND t.Posted = 'P'
  AND (a.AcctType IN ('3I','4E','1A') OR (a.AcctType = '2L' AND t.Acct < '30000'))
  AND (t.PerPost >= ? OR t.Crtd_DateTime >= ?)
