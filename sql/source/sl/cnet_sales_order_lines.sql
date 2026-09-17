-- sl.cnet_sales_order_lines — SL line detail for CNET-linked orders (per-line cost for drift vs CNET quoted cost).
SELECT RTRIM(l.OrdNbr) AS ord_nbr, RTRIM(l.LineRef) AS line_ref, RTRIM(l.InvtID) AS invt_id,
       RTRIM(l.Descr) AS descr, l.QtyOrd AS qty_ord, l.QtyShip AS qty_ship, l.QtyBO AS qty_bo,
       l.Cost AS unit_cost, l.TotCost AS tot_cost, l.SlsPrice AS sls_price, l.TotOrd AS tot_ord
FROM SOLine l
WHERE EXISTS (SELECT 1 FROM SOHeader h WHERE h.OrdNbr = l.OrdNbr AND RTRIM(h.User2) <> '')
