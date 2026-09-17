-- AP distribution lines (APTran): the expense side of every voucher / adjustment (GL account + subaccount =
-- the division an expense was charged to, project / task / PO when tied to a job) and the cash side of every
-- payment (the bank or holding account a check, EFT or void hit). The AP control account (20000) offset lines
-- are left out. Powers the vendor page's "where the money went" by division / GL account / job and the
-- "how paid" column. Incremental on Crtd_DateTime (RecordID is the stable key). Param 1: earliest Crtd_DateTime.
SELECT t.RecordID         AS record_id,
       RTRIM(t.RefNbr)    AS ref_nbr,
       RTRIM(t.TranType)  AS tran_type,
       t.LineNbr          AS line_nbr,
       RTRIM(t.BatNbr)    AS batch_nbr,
       RTRIM(t.VendId)    AS vendor_id,
       t.TranDate         AS tran_date,
       RTRIM(t.PerPost)   AS per_post,
       RTRIM(t.Acct)      AS gl_account,
       RTRIM(t.Sub)       AS gl_subaccount,
       RTRIM(t.DrCr)      AS dr_cr,
       t.TranAmt          AS amount,
       RTRIM(t.TranDesc)  AS tran_desc,
       RTRIM(t.ProjectID) AS project_id,
       RTRIM(t.TaskID)    AS task_id,
       RTRIM(t.PONbr)     AS po_nbr,
       RTRIM(t.RcptNbr)   AS rcpt_nbr,
       t.Qty              AS qty,
       t.UnitPrice        AS unit_price,
       RTRIM(t.InvtID)    AS invt_id,
       t.Crtd_DateTime    AS sl_created_at
FROM dbo.APTran t
WHERE t.Rlsed = 1 AND t.Acct <> '20000' AND t.TranDate >= '2013-01-01' AND t.Crtd_DateTime >= ?
