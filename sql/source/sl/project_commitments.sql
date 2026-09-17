-- sl.project_commitments — PJCOMDET, SL's commitment detail: the rows behind PJPTDSUM.com_amount / PJPTDROL.com_amount.
-- Full read every run (~6.8k rows, ~10 s). SL regenerates the whole table (all rows re-created by PAPOT), so there is no
-- stable key and no watermark; the local copy is replaced in full.
--
-- Two kinds of rows (docs/02_data_sources.md §3b):
--   system_cd 'PO'             open purchase-order lines for the project (qty ordered - received x unit cost): real future cost.
--   system_cd 'IN', type 'PI'  project-inventory allocations (InvProjAlloc) created when a project PO line is *received* at the
--                              warehouse. At Pace the stock then ships to the job through Order Management (OM/IN posts the
--                              cost to the project) but the allocation is NOT relieved, so these rows mostly duplicate cost that
--                              is already in actuals. qty_shipped (sales-order lines for the same project + item) lets the
--                              loader net them; receipt_date/receipt_po_number come from InvProjAlloc.
SELECT RTRIM(c.project)              AS project_number_raw,
       RTRIM(c.pjt_entity)           AS task_id,
       RTRIM(c.acct)                 AS sl_acct,
       RTRIM(c.system_cd)            AS system_cd,
       RTRIM(c.batch_type)           AS batch_type,
       c.amount                      AS amount,
       c.units                       AS units,
       RTRIM(c.part_number)          AS item_id,
       RTRIM(c.purchase_order_num)   AS po_number,
       RTRIM(c.vendor_num)           AS vendor_id,
       c.po_date                     AS po_date,
       c.promise_date                AS promise_date,
       RTRIM(c.projinv_receipt_num)  AS receipt_number,
       RTRIM(c.projinv_lineref)      AS receipt_line,
       a.SrcDate                     AS receipt_date,
       RTRIM(a.PONbr)                AS receipt_po_number,
       a.QtyRemainToIssue            AS alloc_qty_remaining,
       RTRIM(c.tr_comment)           AS comment,
       RTRIM(c.gl_acct)              AS gl_account,
       c.crtd_datetime               AS sl_created_at,
       s.qty_shipped                 AS qty_shipped
FROM PJCOMDET c
LEFT JOIN InvProjAlloc a
       ON c.system_cd = 'IN' AND a.ProjectID = c.project AND a.InvtID = c.part_number
      AND a.SrcNbr = c.projinv_receipt_num AND a.SrcLineRef = c.projinv_lineref
LEFT JOIN (SELECT l.ProjectID, l.InvtID, SUM(l.QtyShip) AS qty_shipped
           FROM SOLine l
           JOIN SOHeader h ON h.OrdNbr = l.OrdNbr
           JOIN SOType t ON t.SOTypeID = h.SOTypeID
           WHERE t.Behavior = 'SO' AND l.ProjectID <> ''
           GROUP BY l.ProjectID, l.InvtID) s
       ON c.system_cd = 'IN' AND s.ProjectID = c.project AND s.InvtID = c.part_number
ORDER BY c.project, c.pjt_entity, c.acct, a.SrcDate, c.projinv_receipt_num, c.po_date, c.purchase_order_num
