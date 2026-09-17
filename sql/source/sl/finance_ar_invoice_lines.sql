-- Lines of every AR invoice / credit memo / debit memo issued in the trailing window (the same
-- population as sl.finance_ar_invoices): what each document billed — description, project / task,
-- item, qty x price, GL account + subaccount (the division the revenue posted to), the shipper and
-- order behind a sales-order invoice. The 11000 AR-control line is left out; sales-tax (20500) and
-- customer-deposit (21000 / 24000) lines stay so the document total can be explained.
-- NOTE: credit-memo lines are stored POSITIVE in SL; the loader signs them. ~46k rows for 430 days.
-- Param 1: earliest DocDate.
SELECT RTRIM(t.RefNbr)     AS ref_nbr,
       RTRIM(t.TranType)   AS doc_type,
       RTRIM(t.CustId)     AS customer_id,
       t.LineNbr           AS line_nbr,
       RTRIM(t.Acct)       AS gl_account,
       RTRIM(t.Sub)        AS gl_subaccount,
       RTRIM(t.TranDesc)   AS tran_desc,
       RTRIM(t.ProjectID)  AS project_id,
       RTRIM(t.TaskID)     AS task_id,
       RTRIM(t.InvtID)     AS invt_id,
       t.Qty               AS qty,
       t.UnitPrice         AS unit_price,
       t.TranAmt           AS amount,
       t.TranDate          AS tran_date,
       RTRIM(t.ShipperID)  AS shipper_id,
       RTRIM(t.OrdNbr)     AS order_nbr
FROM dbo.ARTran t
JOIN dbo.ARDoc d ON d.RefNbr = t.RefNbr AND d.DocType = t.TranType AND d.CustId = t.CustId
WHERE d.DocType IN ('IN','CM','DM') AND d.Rlsed = 1 AND d.DocDate >= ?
  AND t.Acct <> '11000'
