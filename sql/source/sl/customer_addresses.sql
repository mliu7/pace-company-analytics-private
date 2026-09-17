-- sl.customer_addresses — customer master street addresses (fallback location for projects without
-- sales orders; docs/project_map_plan.md §1). Full read (2,081 rows).
SELECT RTRIM(c.CustId)    AS sl_customer_id,
       RTRIM(c.Name)      AS name,
       RTRIM(c.Addr1)     AS addr1,
       RTRIM(c.Addr2)     AS addr2,
       RTRIM(c.City)      AS city,
       RTRIM(c.State)     AS state,
       RTRIM(c.Zip)       AS zip,
       RTRIM(c.Country)   AS country,
       RTRIM(c.BillAddr1) AS bill_addr1,
       RTRIM(c.BillCity)  AS bill_city,
       RTRIM(c.BillState) AS bill_state,
       RTRIM(c.BillZip)   AS bill_zip
FROM Customer c
