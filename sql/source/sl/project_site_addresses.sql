-- sl.project_site_addresses — where each project's sales orders shipped: the job-site proxy for the
-- Project Map (docs/project_map_plan.md §1). One row per (project, customer, ship-to id) with the
-- order count and latest order date; the loader picks the most-used ship-to per project. Full read.
SELECT RTRIM(h.ProjectID)      AS project_number_raw,
       RTRIM(h.CustId)         AS sl_customer_id,
       RTRIM(h.ShipToId)       AS ship_to_id,
       COUNT(*)                AS orders,
       MAX(h.OrdDate)          AS last_order_date,
       MAX(RTRIM(a.Name))      AS site_name,
       MAX(RTRIM(a.Addr1))     AS addr1,
       MAX(RTRIM(a.Addr2))     AS addr2,
       MAX(RTRIM(a.City))      AS city,
       MAX(RTRIM(a.State))     AS state,
       MAX(RTRIM(a.Zip))       AS zip,
       MAX(RTRIM(a.Country))   AS country
FROM SOHeader h
LEFT JOIN SOAddress a ON a.CustId = h.CustId AND a.ShipToId = h.ShipToId
WHERE RTRIM(h.ProjectID) <> ''
GROUP BY RTRIM(h.ProjectID), RTRIM(h.CustId), RTRIM(h.ShipToId)
