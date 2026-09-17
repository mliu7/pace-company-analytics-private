-- sl.cnet_shipper_lines — one row per SOShipLine of every shipper on a CNET-linked order (SOHeader.User2
-- set): what actually shipped, item by item, with SL's cost and price per line. Read-only; full replace.
SELECT RTRIM(l.ShipperID)  AS shipper_id,
       RTRIM(l.LineRef)    AS line_ref,
       RTRIM(l.OrdNbr)     AS ord_nbr,
       RTRIM(l.OrdLineRef) AS ord_line_ref,
       RTRIM(l.InvtID)     AS invt_id,
       RTRIM(l.Descr)      AS descr,
       l.QtyShip           AS qty_ship,
       l.Cost              AS unit_cost,
       l.SlsPrice          AS sls_price,
       l.TotCost           AS tot_cost,
       l.TotInvc           AS tot_invc,
       l.TotMerch          AS tot_merch,
       RTRIM(l.SiteID)     AS site_id,
       l.Crtd_DateTime     AS sl_created_at
FROM dbo.SOShipLine l
WHERE EXISTS (SELECT 1 FROM dbo.SOHeader h WHERE h.OrdNbr = l.OrdNbr AND RTRIM(h.User2) <> '')
