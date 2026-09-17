-- sl.projects — project master + extension. Full read every run (8,997 rows, < 1 s);
-- the ? parameter is a lupd watermark used only for the intra-day manual refresh (pass '1900-01-01' for full).
SELECT
    RTRIM(p.project)            AS project_number_raw,
    RTRIM(p.project_desc)       AS title,
    p.status_pa                 AS sl_status,               -- A active, I inactive, G template, M converted/cancelled, T test/void
    RTRIM(p.gl_subacct)         AS sl_subaccount,           -- division: 0700 = 070 Premise Security
    RTRIM(p.user1)              AS numbering_style_code,    -- SO2 (12-char ids) / SO3 (6-char) / ADM
    RTRIM(p.customer)           AS sl_customer_id,
    RTRIM(p.contract)           AS contract_ref,            -- master contract id (6 % populated)
    RTRIM(p.contract_type)      AS contract_type,           -- FPW on 99 %
    RTRIM(p.manager1)           AS project_manager_key,     -- PM (PJEMPLOY.employee)
    RTRIM(p.manager2)           AS division_head_key,       -- HUB001 / BRI002
    RTRIM(p.slsperid)           AS salesperson_code,        -- Salesperson.SlsperId; 'OT' = non-commission
    RTRIM(p.purchase_order_num) AS customer_po,             -- also literals 'TM TICKET', 'CONTRACT', 'PENDING'
    RTRIM(p.pm_id32)            AS proposal_reference,      -- 'SP 9812' / 'HD# 12706'
    p.pm_id36                   AS pm_flag_36,
    p.probability               AS probability_pct,
    p.start_date                AS planned_start,           -- setup-time plan, not a schedule
    p.end_date                  AS planned_end,
    p.crtd_datetime             AS sl_created_at,
    RTRIM(p.crtd_user)          AS sl_created_by,
    p.lupd_datetime             AS sl_updated_at,
    RTRIM(p.lupd_user)          AS sl_updated_by,
    RTRIM(p.lupd_prog)          AS sl_updated_prog,
    p.budget_type, p.budget_version,
    CAST(p.tstamp AS bigint)    AS tstamp_int,
    x.PM_ID26                   AS ptt_percent_complete_writeback,   -- 0..100, written by PTT
    x.PM_ID28                   AS pm_date_28,
    x.computed_pc, x.entered_pc, x.rev_type, x.rev_flag,
    x.lupd_datetime             AS ex_updated_at,
    RTRIM(x.lupd_user)          AS ex_updated_by
FROM PJPROJ p
LEFT JOIN PJPROJEX x ON x.project = p.project
WHERE p.lupd_datetime >= ? OR x.lupd_datetime >= ?
ORDER BY p.project
