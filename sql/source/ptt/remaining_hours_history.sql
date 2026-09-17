-- ptt.remaining_hours_history — raw JSON with the PM remaining-hours revision history.
-- Parsed locally into RemainingHoursRevision (history[] items: person_id, "1", "2", datetime[Y,M,D,h,m,s] UTC).
SELECT p.id AS ptt_project_pk, p.project_id AS project_number_raw,
       p.estimated_hours_to_completion AS remaining_hours_json,
       p.hours_budgets AS hours_budgets_json
FROM project_project p
WHERE p.client_id = %(client_id)s
ORDER BY p.id
