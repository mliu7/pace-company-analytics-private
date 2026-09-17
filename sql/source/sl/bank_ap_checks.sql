-- AP check register for ONE cash account: checks (CK), voids (VC) and zero-dollar checks (ZC), with
-- SL's own cleared date from its bank-reconciliation module. Powers the outstanding-check list and
-- the check-number match (the bank prints Pace's 6-digit check numbers without the leading digit).
-- Pace's "01xxxx" check numbers are ACH/EFT payments, never printed — they settle as lump ACH debits.
-- Params: 1 = GL cash account, 2 = earliest DocDate.
SELECT RTRIM(d.RefNbr)    AS ref_nbr,
       RTRIM(d.DocType)   AS doc_type,
       d.DocDate          AS doc_date,
       RTRIM(d.VendId)    AS vendor_id,
       RTRIM(d.VendName)  AS vendor_name,
       d.OrigDocAmt       AS amount,
       d.ClearDate        AS clear_date,
       d.ClearAmt         AS clear_amt,
       RTRIM(d.BatNbr)    AS batch_nbr,
       RTRIM(d.Crtd_User) AS created_by
FROM dbo.APDoc d
WHERE d.DocType IN ('CK', 'VC', 'ZC') AND d.Acct = ? AND d.DocDate >= ?
