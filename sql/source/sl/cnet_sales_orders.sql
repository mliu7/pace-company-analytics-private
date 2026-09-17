-- sl.cnet_sales_orders — every SL sales order carrying a ChannelOnline document number (SOHeader.User2,
-- written by PTT's CNET importer). Full read (~53k rows, <5s); local table is full-replaced.
SELECT RTRIM(h.OrdNbr) AS ord_nbr, RTRIM(h.User2) AS cnet_number, RTRIM(h.SOTypeID) AS so_type,
       RTRIM(h.CustID) AS cust_id, RTRIM(h.SlsperID) AS slsper_id, h.OrdDate AS ord_date,
       RTRIM(h.Status) AS status, h.Cancelled AS cancelled, h.CuryTotOrd AS tot_ord, h.TotMerch AS tot_merch,
       h.TotFrt AS tot_frt, h.TotTax AS tot_tax, RTRIM(h.CustOrdNbr) AS cust_ord_nbr,
       RTRIM(h.ShipName) AS ship_name, RTRIM(h.ShipCity) AS ship_city, RTRIM(h.ShipState) AS ship_state
FROM SOHeader h
WHERE RTRIM(h.User2) <> ''
