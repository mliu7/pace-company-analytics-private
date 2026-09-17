-- Purchase-order LINES (PurchOrd header + PurOrdDet lines). Powers the Project Snapshot's "material ordered"
-- section and the project page's Materials section (order status, who entered the PO, promise dates, freight).
-- Param 1: earliest PODate for every PO (stock lines feed the SO-demand deduction, docs/02 §3b).
-- Param 2: earliest PODate for lines that name a project — a longer window so active jobs keep their full PO history.
SELECT RTRIM(h.PONbr)        AS po_nbr,
       h.PODate              AS po_date,
       RTRIM(h.VendID)       AS vendor_id,
       RTRIM(h.VendName)     AS vendor_name,
       RTRIM(h.Status)       AS status,
       RTRIM(h.Buyer)        AS buyer,
       RTRIM(h.Crtd_User)    AS crtd_user,
       RTRIM(h.POType)       AS po_type,
       h.Freight             AS po_freight,
       h.LastRcptDate        AS last_rcpt_date,
       RTRIM(h.ShipVia)      AS ship_via,
       RTRIM(d.LineRef)      AS line_ref,
       RTRIM(d.InvtID)       AS item_id,
       RTRIM(d.TranDesc)     AS descr,
       d.QtyOrd              AS qty_ord,
       d.QtyRcvd             AS qty_rcvd,
       d.QtyVouched          AS qty_vouched,
       d.UnitCost            AS unit_cost,
       d.ExtCost             AS ext_cost,
       d.CostVouched         AS cost_vouched,
       d.OpenLine            AS open_line,
       d.PromDate            AS prom_date,
       d.ReqdDate            AS reqd_date,
       RTRIM(d.PurchaseType) AS purchase_type,
       RTRIM(d.ProjectID)    AS project_id,
       RTRIM(d.TaskID)       AS task_id,
       RTRIM(d.SiteID)       AS site_id
FROM dbo.PurchOrd h
JOIN dbo.PurOrdDet d ON d.PONbr = h.PONbr
WHERE h.PODate >= ? OR (RTRIM(d.ProjectID) <> '' AND h.PODate >= ?)
