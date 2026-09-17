-- sl.project_so_lines — Order Management lines that name a real project (SOLine.ProjectID): the warehouse → job leg
-- of Pace's material path (docs/02 §3b). Every order line, return (RM*) and credit that carries a project, with the
-- header's status, the ChannelOnline document number (SOHeader.User2), who entered it and the salesperson.
-- Powers the project page's "Materials on the job" section. 'ZZ' / blank = resale with no project (excluded).
-- Param 1: earliest OrdDate.
SELECT RTRIM(l.OrdNbr)     AS so_nbr,
       RTRIM(l.LineRef)    AS line_ref,
       h.OrdDate           AS ord_date,
       RTRIM(h.SOTypeID)   AS so_type,
       RTRIM(t.Behavior)   AS behavior,
       RTRIM(h.Status)     AS status,
       RTRIM(l.Status)     AS line_status,
       RTRIM(h.User2)      AS cnet_quote,
       RTRIM(h.Crtd_User)  AS crtd_user,
       RTRIM(h.SlsperID)   AS slsper_id,
       RTRIM(h.CustOrdNbr) AS cust_ord_nbr,
       RTRIM(h.ShipName)   AS ship_name,
       h.TotFrt            AS tot_frt,
       RTRIM(l.InvtID)     AS item_id,
       RTRIM(l.Descr)      AS descr,
       l.QtyOrd            AS qty_ord,
       l.QtyShip           AS qty_ship,
       l.QtyBO             AS qty_bo,
       l.Cost              AS unit_cost,
       l.TotCost           AS tot_cost,
       l.SlsPrice          AS sls_price,
       l.TotOrd            AS tot_ord,
       RTRIM(l.TaskID)     AS task_id,
       RTRIM(l.SiteID)     AS site_id,
       l.PromDate          AS prom_date,
       l.ReqDate           AS req_date,
       l.DropShip          AS drop_ship,
       RTRIM(l.ProjectID)  AS project_id
FROM dbo.SOLine l
JOIN dbo.SOHeader h ON h.CpnyID = l.CpnyID AND h.OrdNbr = l.OrdNbr
LEFT JOIN dbo.SOType t ON t.CpnyID = h.CpnyID AND t.SOTypeID = h.SOTypeID
WHERE RTRIM(l.ProjectID) NOT IN ('', 'ZZ') AND h.OrdDate >= ?
