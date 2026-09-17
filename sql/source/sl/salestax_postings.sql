-- Every posted GL line on 20500 ACCRUED SALES TAX since SL began (~6.8k rows), with what each one is about:
--   AR module  = tax charged on an invoice (credit) or credit memo (debit); the posting description is the jurisdiction
--                ("WA OLYMPIA") or a hand-keyed adjustment ("BEC012 - ADJ SALES TAX 70505"). Joined to the invoice
--                (customer, order, project), to its shipper's tax record (SOShipTax: the ZIP-based tax ID, the rate
--                applied, the taxable base) and to the customer master (state, exemption number).
--   GL module  = journals: the remittances ("TN SALES TAX MAR", "RECORD SALES TAX PAYMENT"), reclasses, year-end
--                true-ups, fees debited here.
--   AP module  = a few payments to state agencies (vouchers) and zero "EXEMPT" lines.
-- The loader classifies each line (apps/analytics/salestax_parse.py). Full replace each finance refresh.
SELECT g.TranDate            AS tran_date,
       RTRIM(g.PerPost)      AS per_post,
       RTRIM(g.Module)       AS module,
       RTRIM(g.JrnlType)     AS jrnl_type,
       RTRIM(g.BatNbr)       AS batch_nbr,
       RTRIM(g.RefNbr)       AS ref_nbr,
       RTRIM(g.Sub)          AS gl_subaccount,
       RTRIM(g.TranDesc)     AS tran_desc,
       g.DrAmt               AS dr_amt,
       g.CrAmt               AS cr_amt,
       RTRIM(g.Crtd_User)    AS crtd_user,
       g.Crtd_DateTime       AS sl_created_at,
       RTRIM(d.DocType)      AS doc_type,
       RTRIM(d.CustId)       AS customer_id,
       RTRIM(d.OrdNbr)       AS order_nbr,
       RTRIM(d.ProjectID)    AS project_id,
       d.DocDate             AS doc_date,
       d.OrigDocAmt          AS doc_amount,
       RTRIM(c.State)        AS cust_state,
       RTRIM(c.TaxExemptNbr) AS cust_exempt_nbr,
       RTRIM(h.ShipperID)    AS shipper_id,
       RTRIM(h.ShipState)    AS ship_state,
       RTRIM(h.ShipZip)      AS ship_zip,
       RTRIM(h.ShipCity)     AS ship_city,
       RTRIM(s.TaxID)        AS tax_id,
       s.TaxRate             AS tax_rate,
       s.TotTxbl             AS taxable,
       s.TotTax              AS ship_tax
FROM dbo.GLTran g
OUTER APPLY (SELECT TOP 1 x.DocType, x.CustId, x.OrdNbr, x.ProjectID, x.DocDate, x.OrigDocAmt
             FROM dbo.ARDoc x WHERE g.Module = 'AR' AND x.RefNbr = g.RefNbr AND x.DocType IN ('IN','CM','DM')
             ORDER BY CASE x.DocType WHEN 'IN' THEN 0 WHEN 'CM' THEN 1 ELSE 2 END) d
LEFT JOIN dbo.Customer c ON c.CustId = d.CustId
OUTER APPLY (SELECT TOP 1 y.ShipperID, y.ShipState, y.ShipZip, y.ShipCity
             FROM dbo.SOShipHeader y WHERE g.Module = 'AR' AND y.InvcNbr = g.RefNbr ORDER BY y.ShipperID) h
OUTER APPLY (SELECT TOP 1 z.TaxID, z.TaxRate, z.TotTxbl, z.TotTax
             FROM dbo.SOShipTax z WHERE z.ShipperID = h.ShipperID ORDER BY ABS(z.TotTax) DESC) s
WHERE g.LedgerID = 'ACTUAL' AND g.Posted = 'P' AND g.Acct = '20500'
