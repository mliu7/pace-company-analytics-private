-- Voucher applications behind AP payments (APAdjust joined to the paid voucher's APDoc):
-- what each check/EFT paid, with the vendor's invoice date, due date and terms, so the UI can
-- show how long Pace held the invoice before paying. Param 1: earliest check date (AdjgDocDate).
SELECT RTRIM(a.AdjgRefNbr)  AS check_ref,
       RTRIM(a.AdjgDocType) AS check_type,
       a.AdjgDocDate        AS check_date,
       a.DateAppl           AS date_appl,
       RTRIM(a.VendId)      AS vendor_id,
       a.AdjAmt             AS adj_amount,
       a.AdjDiscAmt         AS disc_amount,
       RTRIM(a.AdjdRefNbr)  AS voucher_ref,
       RTRIM(a.AdjdDocType) AS voucher_type,
       d.DocDate            AS voucher_date,
       d.InvcDate           AS invoice_date,
       d.DueDate            AS due_date,
       RTRIM(d.InvcNbr)     AS invoice_nbr,
       RTRIM(d.DocDesc)     AS doc_desc,
       d.OrigDocAmt         AS voucher_amount,
       RTRIM(d.PONbr)       AS po_nbr,
       RTRIM(d.Terms)       AS terms
FROM dbo.APAdjust a
LEFT JOIN dbo.APDoc d ON d.RefNbr = a.AdjdRefNbr AND d.DocType = a.AdjdDocType AND d.VendId = a.VendId
WHERE a.AdjgDocType IN ('CK','HC','EP','VC') AND a.AdjgDocDate >= ?
