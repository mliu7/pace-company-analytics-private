-- AR invoices/credit memos ISSUED in a trailing window, regardless of whether they're still open
-- (the open-docs pull can't see an invoice billed Monday and paid Thursday). Powers the Project
-- Snapshot's "billing out" section, the Receivables page's billed-12-months figures and the Billings
-- page (who keyed each document, when, on which screen; the date on the invoice; the fiscal period).
-- Crtd_Prog 40690 = sales-order invoicing (shipper release), 08010 = AR Invoice & Memo entry.
-- Param 1: earliest DocDate.
SELECT RTRIM(d.RefNbr)     AS ref_nbr,
       RTRIM(d.DocType)    AS doc_type,
       RTRIM(d.CustId)     AS customer_id,
       RTRIM(c.Name)       AS customer_name,
       RTRIM(d.ProjectID)  AS project_id,
       RTRIM(d.TaskID)     AS task_id,
       RTRIM(d.OrdNbr)     AS order_nbr,
       RTRIM(h.SOTypeID)   AS so_type,
       d.DocDate           AS doc_date,
       d.DueDate           AS due_date,
       RTRIM(d.PerPost)    AS per_post,
       RTRIM(d.BatNbr)     AS batch_nbr,
       d.OrigDocAmt        AS amount,
       d.DocBal            AS balance,
       RTRIM(d.DocDesc)    AS doc_desc,
       RTRIM(d.CustOrdNbr) AS cust_po,
       RTRIM(d.Terms)      AS terms,
       RTRIM(d.SlsperId)   AS slsper_id,
       d.Crtd_DateTime     AS sl_created_at,
       RTRIM(d.Crtd_User)  AS crtd_user,
       RTRIM(d.Crtd_Prog)  AS crtd_prog,
       d.LUpd_DateTime     AS lupd_at,
       RTRIM(d.LUpd_User)  AS lupd_user
FROM dbo.ARDoc d
LEFT JOIN dbo.Customer c ON c.CustId = d.CustId
LEFT JOIN dbo.SOHeader h ON h.OrdNbr = d.OrdNbr AND RTRIM(d.OrdNbr) <> ''
WHERE d.DocType IN ('IN','CM','DM') AND d.Rlsed = 1 AND d.DocDate >= ?
