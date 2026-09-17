-- sl.cnet_shipper_serials — serial numbers shipped, per SOShipLine, for shippers on CNET-linked orders.
-- SOShipLot carries the ShipperID but a blank OrdNbr, so the order link goes through SOShipHeader.
-- Rows with a real LotSerNbr only (lot-less rows are skipped). Read-only; full replace.
SELECT RTRIM(s.ShipperID)      AS shipper_id,
       RTRIM(s.LineRef)        AS line_ref,
       RTRIM(sh.OrdNbr)        AS ord_nbr,
       RTRIM(s.InvtId)         AS invt_id,
       RTRIM(s.LotSerNbr)      AS serial,
       s.QtyShip               AS qty_ship,
       RTRIM(s.RMADisposition) AS rma_disposition,
       s.Crtd_DateTime         AS sl_created_at
FROM dbo.SOShipLot s
JOIN dbo.SOShipHeader sh ON sh.ShipperID = s.ShipperID
WHERE RTRIM(s.LotSerNbr) <> ''
  AND EXISTS (SELECT 1 FROM dbo.SOHeader h WHERE h.OrdNbr = sh.OrdNbr AND RTRIM(h.User2) <> '')
