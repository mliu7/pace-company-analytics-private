-- ptt.time_entries_since — one row per TTFormResponse (person x day x project x form), with the
-- Job Report elements pivoted into columns. Incremental on submitted/edited/removed timestamps;
-- pass since = watermark - 45 days. Hours arrive as text ('8.0') and are parsed locally.
-- Element ids are Pace-specific (client 7) and are cross-checked nightly by ptt.form_elements.
SELECT
    r.id                                   AS ptt_response_pk,
    r.person_id                            AS ptt_person_pk,
    pe.employee_id                         AS employee_key,
    r.submitted_by_id                      AS ptt_submitted_by_pk,
    sb.employee_id                         AS submitted_by_employee_key,
    r.date_of_work                         AS work_date,
    r.submitted_time                       AS submitted_at,
    r.last_edited_time                     AS last_edited_at,
    r.last_edited_by_id                    AS ptt_last_edited_by_pk,
    r.status                               AS record_status,       -- 1 live, 2 removed
    r.removed_time                         AS removed_at,
    r.form_id                              AS ptt_form_pk,
    f.form_type                            AS ptt_form_type,       -- 1 time report, 2 time off, 3 other, 4 check-in
    r.project_id                           AS ptt_project_pk,
    p.project_id                           AS project_number_raw,
    r.project_phase_id                     AS ptt_phase_pk,
    ph.phase_id                            AS task_id,             -- SL PJPENT.pjt_entity
    r.project_phase_other                  AS task_other_text,
    sc.shift                               AS shift_code,
    sc.multiplier                          AS shift_multiplier,
    MAX(CASE WHEN e.form_element_id = 5  THEN e.entry END) AS hours_onsite_raw,
    MAX(CASE WHEN e.form_element_id = 10 THEN e.entry END) AS hours_ot_raw,
    MAX(CASE WHEN e.form_element_id = 6  THEN e.entry END) AS hours_offsite_raw,
    MAX(CASE WHEN e.form_element_id = 12 THEN e.entry END) AS hours_service_ticket_raw,   -- retired form 6
    MAX(CASE WHEN e.form_element_id = 13 THEN e.entry END) AS hours_time_off_raw,        -- form 5, never project labor
    MAX(CASE WHEN e.form_element_id = 2  THEN e.entry END) AS system_choice,
    MAX(CASE WHEN e.form_element_id = 1  THEN e.entry END) AS work_type_choice,
    MAX(CASE WHEN e.form_element_id = 4  THEN e.entry END) AS completed_raw,             -- 'True'/'False'
    MAX(CASE WHEN e.form_element_id = 3  THEN e.entry END) AS activity_note,
    MAX(CASE WHEN e.form_element_id = 7  THEN e.entry END) AS open_issues_note
FROM time_tracking_ttformresponse r
JOIN time_tracking_ttform f            ON f.id = r.form_id
JOIN person_person pe                  ON pe.id = r.person_id
LEFT JOIN person_person sb             ON sb.id = r.submitted_by_id
LEFT JOIN project_project p            ON p.id = r.project_id
LEFT JOIN project_projectphase ph      ON ph.id = r.project_phase_id
LEFT JOIN union_shiftcode sc           ON sc.id = r.work_shift_id
LEFT JOIN time_tracking_ttformentry e  ON e.form_response_id = r.id
                                      AND e.form_element_id IN (1, 2, 3, 4, 5, 6, 7, 10, 12, 13)
WHERE f.client_id = %(client_id)s
  AND (   r.submitted_time   >= %(since)s
       OR r.last_edited_time >= %(since)s
       OR r.removed_time     >= %(since)s )
GROUP BY r.id, pe.employee_id, sb.employee_id, f.form_type, p.project_id, ph.phase_id, sc.shift, sc.multiplier
ORDER BY r.id
