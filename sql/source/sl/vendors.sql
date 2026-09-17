-- Vendor master (small, full read every refresh). Powers the vendor pages (/finance/vendors/).
SELECT RTRIM(v.VendId)    AS vendor_id,
       RTRIM(v.Name)      AS name,
       RTRIM(v.Status)    AS status,
       RTRIM(v.Terms)     AS terms,
       RTRIM(v.ClassID)   AS class_id,
       RTRIM(v.City)      AS city,
       RTRIM(v.State)     AS state,
       RTRIM(v.Phone)     AS phone,
       RTRIM(v.EMailAddr) AS email,
       RTRIM(v.PmtMethod) AS pmt_method,
       v.Crtd_DateTime    AS sl_created_at
FROM dbo.Vendor v
