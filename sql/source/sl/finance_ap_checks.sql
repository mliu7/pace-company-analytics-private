-- AP payments CUT in a trailing window: checks, hand checks, EFTs, plus void checks (negative
-- context). Powers the Project Snapshot's "AP paid out" section and the Vendor pages. Param 1: earliest DocDate.
-- cash_acct: on a payment document APDoc.Acct is the cash account the payment was drawn on (the same field the
-- bank reconciliation keys on) — 10250 checking, or 10450 the credit-card holding account, which marks a
-- payable settled by a company credit card rather than by cash (docs/06 Vendors).
SELECT RTRIM(d.RefNbr)  AS ref_nbr,
       RTRIM(d.DocType) AS doc_type,
       RTRIM(d.VendId)  AS vendor_id,
       RTRIM(v.Name)    AS vendor_name,
       d.DocDate        AS doc_date,
       d.OrigDocAmt     AS amount,
       RTRIM(d.DocDesc) AS doc_desc,
       RTRIM(d.Acct)    AS cash_acct,
       d.Crtd_DateTime  AS sl_created_at
FROM dbo.APDoc d
LEFT JOIN dbo.Vendor v ON v.VendId = d.VendId
WHERE d.DocType IN ('CK','HC','EP','VC') AND d.Rlsed = 1 AND d.DocDate >= ?
