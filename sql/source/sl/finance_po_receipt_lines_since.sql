-- PO receipt distribution lines (POTran type R): what each receipt debited — inventory (12000) or a project
-- cost account — with the SUBACCOUNT (= the division the material belongs to) and the project / task, for
-- every vendor since 2013. This is where the division of material bought on POs is stamped: the voucher for
-- a PO purchase only clears PO clearing (20001), so the vendor page reads material spend from here.
-- Incremental on Crtd_DateTime (date precision); RcptNbr + LineRef is the stable key. Param 1: earliest Crtd_DateTime.
SELECT RTRIM(t.RcptNbr)   AS rcpt_nbr,
       RTRIM(t.LineRef)   AS line_ref,
       RTRIM(t.PONbr)     AS po_nbr,
       RTRIM(t.VendId)    AS vendor_id,
       t.RcptDate         AS rcpt_date,
       RTRIM(t.PerPost)   AS per_post,
       RTRIM(t.Acct)      AS gl_account,
       RTRIM(t.Sub)       AS gl_subaccount,
       RTRIM(t.ProjectID) AS project_id,
       RTRIM(t.TaskID)    AS task_id,
       RTRIM(t.InvtID)    AS invt_id,
       RTRIM(t.TranDesc)  AS descr,
       t.RcptQty          AS qty,
       t.UnitCost         AS unit_cost,
       t.ExtCost          AS ext_cost,
       RTRIM(t.SOOrdNbr)  AS so_ord_nbr,
       RTRIM(t.SiteID)    AS site_id,
       t.Crtd_DateTime    AS sl_created_at
FROM dbo.POTran t
WHERE t.TranType = 'R' AND t.RcptDate >= '2013-01-01' AND t.Crtd_DateTime >= ?
