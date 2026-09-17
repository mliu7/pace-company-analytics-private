-- One-off history reconstruction feed for AP aging backfill (mirror of finance_ar_backfill).
WITH docs AS (
  SELECT RTRIM(d.RefNbr) AS ref, RTRIM(d.DocType) AS dt, d.DueDate AS due,
         d.OrigDocAmt AS amt, d.Crtd_DateTime AS ts
  FROM dbo.APDoc d
  WHERE d.Rlsed = 1 AND d.DocType IN ('VO','AD','PP') AND (ABS(d.DocBal) > 0.005 OR d.LUpd_DateTime >= ?
     OR EXISTS (SELECT 1 FROM dbo.APAdjust x WHERE x.AdjdRefNbr = d.RefNbr AND x.AdjdDocType = d.DocType AND x.Crtd_DateTime >= ?)
     OR EXISTS (SELECT 1 FROM dbo.APAdjust g WHERE g.AdjgRefNbr = d.RefNbr AND g.AdjgDocType = d.DocType AND g.Crtd_DateTime >= ?)))
SELECT 'doc' AS kind, ref, dt, due, amt, ts FROM docs
UNION ALL
SELECT 'adj', RTRIM(a.AdjdRefNbr), RTRIM(a.AdjdDocType), NULL, a.AdjAmt + a.AdjDiscAmt, a.Crtd_DateTime
FROM dbo.APAdjust a JOIN docs ON docs.ref = RTRIM(a.AdjdRefNbr) AND docs.dt = RTRIM(a.AdjdDocType)
UNION ALL
SELECT 'adjg', RTRIM(a.AdjgRefNbr), RTRIM(a.AdjgDocType), NULL, a.CuryAdjgAmt, a.Crtd_DateTime
FROM dbo.APAdjust a JOIN docs ON docs.ref = RTRIM(a.AdjgRefNbr) AND docs.dt = RTRIM(a.AdjgDocType)
