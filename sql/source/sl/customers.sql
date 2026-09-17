-- sl.customers — Customer master with the maintained market sector in User2. Full read (2,060 rows).
SELECT RTRIM(c.CustId) AS sl_customer_id, RTRIM(c.Name) AS name, RTRIM(c.ClassId) AS class_id, RTRIM(c.Status) AS sl_status,
       RTRIM(c.User2) AS market_sector, RTRIM(c.User1) AS user1_tax_id, RTRIM(c.User5) AS user5, RTRIM(c.User6) AS user6,
       RTRIM(c.City) AS city, RTRIM(c.State) AS state, RTRIM(c.Zip) AS zip, RTRIM(c.Country) AS country,
       RTRIM(c.SlsperId) AS default_salesperson_code, RTRIM(c.Territory) AS territory, RTRIM(c.Terms) AS terms,
       c.SetupDate AS setup_date, c.Crtd_DateTime AS sl_created_at, c.LUpd_DateTime AS sl_updated_at
FROM Customer c
ORDER BY c.CustId
