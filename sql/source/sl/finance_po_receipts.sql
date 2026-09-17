-- PO receipt LINES (POTran TranType 'R' + the receipt header + vendor). Powers the Project Snapshot's "material
-- received" section and the project page's Materials section (who received it, the vendor's packing-slip/invoice).
-- Param 1: earliest RcptDate for every receipt; Param 2: earliest RcptDate for lines that name a project (longer window).
SELECT RTRIM(t.RcptNbr)     AS rcpt_nbr,
       t.RcptDate           AS rcpt_date,
       RTRIM(t.PONbr)       AS po_nbr,
       RTRIM(t.POLIneRef)   AS po_line_ref,
       RTRIM(t.VendId)      AS vendor_id,
       RTRIM(v.Name)        AS vendor_name,
       RTRIM(r.VendInvcNbr) AS vend_invc_nbr,
       RTRIM(t.Crtd_User)   AS crtd_user,
       RTRIM(t.InvtID)      AS item_id,
       RTRIM(t.TranDesc)    AS descr,
       t.Qty                AS qty,
       t.QtyVouched         AS qty_vouched,
       t.UnitCost           AS unit_cost,
       t.ExtCost            AS ext_cost,
       RTRIM(t.ProjectID)   AS project_id,
       RTRIM(t.TaskID)      AS task_id
FROM dbo.POTran t
LEFT JOIN dbo.Vendor v ON v.VendId = t.VendId
LEFT JOIN dbo.POReceipt r ON r.RcptNbr = t.RcptNbr
WHERE t.TranType = 'R' AND (t.RcptDate >= ? OR (RTRIM(t.ProjectID) <> '' AND t.RcptDate >= ?))
