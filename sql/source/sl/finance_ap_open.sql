-- Open AP documents with vendor name. VO vouchers / PP prepayments owed by Pace (positive);
-- AD debit adjustments reduce AP (negative). DocType VT is recurring-voucher template garbage
-- (e.g. $1.2M AMC001 rows dated 2012) and is excluded everywhere.
SELECT RTRIM(d.RefNbr)  AS ref_nbr,
       RTRIM(d.DocType) AS doc_type,
       RTRIM(d.VendId)  AS vendor_id,
       RTRIM(v.Name)    AS vendor_name,
       d.DocDate        AS doc_date,
       d.DueDate        AS due_date,
       RTRIM(d.InvcNbr) AS invoice_nbr,
       d.DocBal         AS doc_bal,
       d.OrigDocAmt     AS orig_amt,
       RTRIM(d.DocDesc) AS doc_desc,
       RTRIM(d.PONbr)   AS po_nbr,
       RTRIM(d.PerPost) AS per_post
FROM dbo.APDoc d
LEFT JOIN dbo.Vendor v ON v.VendId = d.VendId
WHERE d.Rlsed = 1 AND d.DocType IN ('VO','AD','PP') AND ABS(d.DocBal) > 0.005
