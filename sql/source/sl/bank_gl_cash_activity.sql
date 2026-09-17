-- Posted GL transactions on ONE cash (bank) account, ACTUAL ledger, for the Bank Reconciliation
-- page: a date window around the statement period plus the fiscal periods it spans (payroll and
-- corrections are often dated outside the period they post to, e.g. an Aug 4 payroll posted to July).
-- Params: 1 = GL account, 2 = earliest TranDate, 3 = latest TranDate, 4 = earliest PerPost, 5 = latest PerPost.
SELECT RTRIM(t.Acct)      AS acct,
       RTRIM(t.Sub)       AS sub,
       t.TranDate         AS tran_date,
       RTRIM(t.PerPost)   AS per_post,
       t.DrAmt            AS dr_amt,
       t.CrAmt            AS cr_amt,
       RTRIM(t.Module)    AS module,
       RTRIM(t.BatNbr)    AS batch_nbr,
       RTRIM(t.RefNbr)    AS ref_nbr,
       RTRIM(t.TranDesc)  AS tran_desc,
       RTRIM(t.JrnlType)  AS jrnl_type,
       RTRIM(t.Crtd_User) AS created_by,
       t.Crtd_DateTime    AS sl_created_at
FROM dbo.GLTran t
WHERE t.LedgerID = 'ACTUAL' AND t.Posted = 'P' AND t.Acct = ?
  AND ((t.TranDate BETWEEN ? AND ?) OR (t.PerPost BETWEEN ? AND ?))
