-- AR payment documents (DocType PA) in a trailing window. A wire batch shares one RefNbr across
-- many customers (e.g. WT08262026 for every Bechtel entity), so payment identity = (RefNbr, CustId).
-- Param 1: earliest DocDate.
SELECT RTRIM(d.RefNbr)   AS ref_nbr,
       RTRIM(d.CustId)   AS customer_id,
       RTRIM(c.Name)     AS customer_name,
       d.DocDate         AS doc_date,
       RTRIM(d.BatNbr)   AS batch_nbr,
       d.OrigDocAmt      AS orig_amt,
       d.DocBal          AS balance,
       RTRIM(d.PerPost)  AS per_post,
       d.Crtd_DateTime   AS sl_created_at,
       RTRIM(d.DocDesc)  AS doc_desc
FROM dbo.ARDoc d
LEFT JOIN dbo.Customer c ON c.CustId = d.CustId
WHERE d.DocType = 'PA' AND d.Rlsed = 1 AND d.DocDate >= ?
