-- ptt.project_tasks — PTT phases = SL PJPENT tasks; needed to resolve ttformresponse.project_phase_id.
SELECT ph.id AS ptt_phase_pk, ph.project_id AS ptt_project_pk, p.project_id AS project_number_raw,
       ph.phase_id AS task_id, ph.description, ph.status AS ptt_record_status
FROM project_projectphase ph
JOIN project_project p ON p.id = ph.project_id
WHERE p.client_id = %(client_id)s
ORDER BY ph.id
