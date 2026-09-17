-- sl.project_po_vouchers — AP documents (vouchers VO, adjustments AD) that reference a purchase order: what the
-- vendor actually billed against each PO, with the freight the vendor charged (APDoc.FreightAmt). Joined locally to
-- the project's PO lines for the Materials section's "extra costs". Param 1: earliest DocDate.
SELECT RTRIM(a.RefNbr)    AS ref_nbr,
       RTRIM(a.DocType)   AS doc_type,
       a.DocDate          AS doc_date,
       RTRIM(a.VendId)    AS vendor_id,
       RTRIM(a.VendName)  AS vendor_name,
       a.OrigDocAmt       AS amount,
       a.FreightAmt       AS freight_amt,
       RTRIM(a.PONbr)     AS po_nbr,
       RTRIM(a.Status)    AS status,
       RTRIM(a.Crtd_User) AS crtd_user
FROM dbo.APDoc a
WHERE RTRIM(a.PONbr) <> '' AND a.DocDate >= ?
