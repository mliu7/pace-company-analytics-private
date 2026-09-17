-- sl.project_ship_lines — shipper lines that name a real project (SOShipLine.ProjectID): the stock actually sent
-- from the warehouse to the job, with the ship date, who shipped it, the AR invoice it produced and freight.
-- The shipper/invoice is what posts MATERIALS (OM/IN) to the project. Param 1: earliest shipper creation.
SELECT RTRIM(s.ShipperID)   AS shipper_id,
       s.ShipDateAct        AS ship_date,
       s.ShipDatePlan       AS ship_date_plan,
       RTRIM(s.Status)      AS status,
       RTRIM(s.OrdNbr)      AS so_nbr,
       RTRIM(s.InvcNbr)     AS invc_nbr,
       s.InvcDate           AS invc_date,
       RTRIM(s.Crtd_User)   AS crtd_user,
       RTRIM(s.ShipViaID)   AS ship_via,
       RTRIM(s.TrackingNbr) AS tracking_nbr,
       s.TotFrtCost         AS tot_frt_cost,
       s.TotFrtInvc         AS tot_frt_invc,
       RTRIM(l.LineRef)     AS line_ref,
       RTRIM(l.OrdLineRef)  AS ord_line_ref,
       RTRIM(l.InvtID)      AS item_id,
       RTRIM(l.Descr)       AS descr,
       l.QtyShip            AS qty_ship,
       l.Cost               AS unit_cost,
       l.TotCost            AS tot_cost,
       l.SlsPrice           AS sls_price,
       l.TotInvc            AS tot_invc,
       RTRIM(l.TaskID)      AS task_id,
       RTRIM(l.ProjectID)   AS project_id
FROM dbo.SOShipLine l
JOIN dbo.SOShipHeader s ON s.CpnyID = l.CpnyID AND s.ShipperID = l.ShipperID
WHERE RTRIM(l.ProjectID) NOT IN ('', 'ZZ') AND s.Crtd_DateTime >= ?
