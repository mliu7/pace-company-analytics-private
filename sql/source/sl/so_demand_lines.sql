-- Sales-order line demand (SOType Behavior 'SO'), used to DEDUCE the job or customer behind
-- "stock" PO / receipt lines: Pace cuts warehouse POs against sales-order demand without using
-- SL's SO->PO link fields (PurOrdDet.SOOrdNbr is always blank), so big job-bound hardware
-- receipts carry no ProjectID. SOLine.ProjectID holds the real job when there is one;
-- 'ZZ' / blank = product resale to a customer with no SL project (ChannelOnline flow; the
-- quote number rides in SOHeader.User2). Param 1: earliest OrdDate.
SELECT RTRIM(l.OrdNbr)    AS so_nbr,
       h.OrdDate          AS ord_date,
       RTRIM(h.CustID)    AS customer_id,
       RTRIM(h.ShipName)  AS ship_name,
       RTRIM(h.Status)    AS status,
       RTRIM(l.InvtID)    AS item_id,
       l.QtyOrd           AS qty_ord,
       l.QtyShip          AS qty_ship,
       RTRIM(l.ProjectID) AS project_id,
       RTRIM(h.User2)     AS co_quote
FROM dbo.SOLine l
JOIN dbo.SOHeader h ON h.CpnyID = l.CpnyID AND h.OrdNbr = l.OrdNbr
JOIN dbo.SOType  t ON t.CpnyID = h.CpnyID AND t.SOTypeID = h.SOTypeID
WHERE t.Behavior = 'SO' AND l.InvtID <> '' AND h.OrdDate >= ?
