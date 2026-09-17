-- The SL sales-tax master (SalesTax): one row per ZIP-based tax ID (TaxId = state code + ZIP, Descr = state code +
-- locality, TaxRate = combined %), loaded nationwide on 2022-07-19 and refreshed 2025-10-14 (13,648 rows), plus the
-- two hand-made IDs that predate it: TAX2 = "ILLINOIS SALES TAX" 7.75 % (Pace's flat Illinois rate since 2016) and
-- NONE. Every ID posts to 20500 / 0000. ~41k rows, full replace. Powers the Sales Tax page's rate checks.
SELECT RTRIM(t.TaxId)        AS tax_id,
       RTRIM(t.Descr)        AS descr,
       t.TaxRate             AS rate,
       RTRIM(t.TaxType)      AS tax_type,
       RTRIM(t.SlsTaxAcct)   AS gl_account,
       RTRIM(t.SlsTaxSub)    AS gl_subaccount,
       t.OldTaxRate          AS old_rate,
       t.NewTaxRate          AS new_rate,
       t.NewRateDate         AS new_rate_date,
       t.TaxRvsdDate         AS revised_date,
       t.Crtd_DateTime       AS sl_created_at,
       t.LUpd_DateTime       AS sl_updated_at,
       RTRIM(t.LUpd_User)    AS sl_updated_by
FROM dbo.SalesTax t
