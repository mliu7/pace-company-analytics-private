-- sl.cnet_shippers — per-shipment realized economics for CNET-linked orders: invoiced revenue, shipped cost,
-- freight invoiced AND actual freight cost (SOShipHeader). Full read; local table full-replaced.
SELECT RTRIM(s.ShipperID) AS shipper_id, RTRIM(s.OrdNbr) AS ord_nbr, s.ShipDateAct AS ship_date,
       RTRIM(s.InvcNbr) AS invc_nbr, s.InvcDate AS invc_date, RTRIM(s.Status) AS status,
       s.TotInvc AS tot_invc, s.TotCost AS tot_cost, s.TotFrtInvc AS tot_frt, s.TotMerch AS tot_merch
FROM SOShipHeader s
WHERE EXISTS (SELECT 1 FROM SOHeader h WHERE h.OrdNbr = s.OrdNbr AND RTRIM(h.User2) <> '')
