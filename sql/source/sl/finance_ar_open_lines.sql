-- Lines of the OPEN AR documents (ARTran joined to the same ARDoc population as sl.finance_ar_open):
-- what each open invoice / credit memo billed — description, project / task, item, qty x price, amount —
-- plus the tax lines (20500). The AR control line (11000) is left out. Powers the AR page's detail panel.
-- Full read each finance refresh (~4-5k rows).
SELECT RTRIM(t.RefNbr)    AS ref_nbr,
       RTRIM(t.TranType)  AS doc_type,
       RTRIM(t.CustId)    AS customer_id,
       t.LineNbr          AS line_nbr,
       RTRIM(t.Acct)      AS gl_account,
       RTRIM(t.Sub)       AS gl_subaccount,
       RTRIM(t.TranDesc)  AS tran_desc,
       RTRIM(t.ProjectID) AS project_id,
       RTRIM(t.TaskID)    AS task_id,
       RTRIM(t.InvtID)    AS invt_id,
       t.Qty              AS qty,
       t.UnitPrice        AS unit_price,
       t.TranAmt          AS amount,
       t.TranDate         AS tran_date
FROM dbo.ARTran t
JOIN dbo.ARDoc d ON d.RefNbr = t.RefNbr AND d.DocType = t.TranType AND d.CustId = t.CustId
WHERE ((d.Rlsed = 1 AND ABS(d.DocBal) > 0.005) OR (d.Rlsed = 0 AND d.DocType = 'IN'))
  AND t.Acct <> '11000'
