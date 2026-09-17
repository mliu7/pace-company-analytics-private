-- AR payment applications (ARAdjust rows where the adjusting doc is a PA payment), joined to the
-- adjusted invoice for its date/terms/project — the rows behind SL report 08820
-- "Payment Applications - Detail" (the daily email). Param 1: earliest DateAppl.
SELECT RTRIM(a.AdjgRefNbr) AS payment_ref,
       RTRIM(a.CustId)     AS customer_id,
       a.DateAppl          AS date_appl,
       a.Crtd_DateTime     AS sl_created_at,
       RTRIM(a.AdjBatNbr)  AS batch_nbr,
       RTRIM(a.PerAppl)    AS per_appl,
       a.AdjAmt            AS applied,
       a.AdjDiscAmt        AS discount,
       RTRIM(a.AdjdRefNbr) AS invoice_ref,
       RTRIM(a.AdjdDocType) AS invoice_type,
       i.DocDate           AS invoice_date,
       i.DueDate           AS due_date,
       RTRIM(i.Terms)      AS terms,
       i.OrigDocAmt        AS invoice_amt,
       i.DocBal            AS invoice_balance,
       RTRIM(i.ProjectID)  AS project_id,
       RTRIM(i.DocDesc)    AS invoice_desc
FROM dbo.ARAdjust a
LEFT JOIN dbo.ARDoc i ON i.RefNbr = a.AdjdRefNbr AND i.DocType = a.AdjdDocType AND i.CustId = a.CustId
WHERE a.AdjgDocType = 'PA' AND a.DateAppl >= ?
