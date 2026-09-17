-- Open AR documents (plus unreleased invoices) with customer name and sales-order type.
-- Doc types: IN/DM/FC/NS/SB owed to Pace (positive); CM/PA reduce AR (negative).
-- Single currency (DocBal = CuryDocBal on all open docs) and single company (PACE), verified.
-- SOHeader.SOTypeID: SO1/SO2 order books, RM1/RM2/RMS2 = RMA. Project billings have ProjectID, no order.
SELECT RTRIM(d.RefNbr)    AS ref_nbr,
       RTRIM(d.DocType)   AS doc_type,
       RTRIM(d.CustId)    AS customer_id,
       RTRIM(c.Name)      AS customer_name,
       RTRIM(d.ProjectID) AS project_id,
       RTRIM(d.OrdNbr)    AS order_nbr,
       RTRIM(COALESCE(h.SOTypeID, '')) AS so_type,
       d.DocDate          AS doc_date,
       d.DueDate          AS due_date,
       d.DocBal           AS doc_bal,
       d.OrigDocAmt       AS orig_amt,
       d.Rlsed            AS released,
       RTRIM(d.DocDesc)   AS doc_desc,
       RTRIM(d.SlsperId)  AS slsper_id,
       RTRIM(d.Terms)     AS terms,
       RTRIM(d.PerPost)   AS per_post,
       RTRIM(d.CustOrdNbr) AS cust_po
FROM dbo.ARDoc d
LEFT JOIN dbo.Customer c ON c.CustId = d.CustId
LEFT JOIN dbo.SOHeader h ON h.OrdNbr = d.OrdNbr AND RTRIM(d.OrdNbr) <> ''
WHERE (d.Rlsed = 1 AND ABS(d.DocBal) > 0.005)
   OR (d.Rlsed = 0 AND d.DocType = 'IN')
