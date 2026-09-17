-- sl.project_tasks — PJPENT tasks (phases). Full read (22,307 rows).
SELECT RTRIM(t.project) AS project_number_raw, RTRIM(t.pjt_entity) AS task_id, RTRIM(t.pjt_entity_desc) AS description,
       t.status_pa AS sl_status, RTRIM(t.manager1) AS task_manager_key, RTRIM(t.contract_type) AS contract_type,
       RTRIM(t.labor_class_cd) AS labor_class_cd, t.start_date AS planned_start, t.end_date AS planned_end,
       t.crtd_datetime AS sl_created_at, t.lupd_datetime AS sl_updated_at,
       RTRIM(t.crtd_user) AS sl_created_by, RTRIM(t.lupd_user) AS sl_updated_by
FROM PJPENT t
ORDER BY t.project, t.pjt_entity
