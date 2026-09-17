-- ptt.form_elements — guards the hard-coded element ids used by time_entries_since.
-- The loader asserts: 5="Hours Spent Onsite"(6), 10="OT Hours Spent Onsite"(8), 6="Hours Spent Offsite"(6),
-- 2="System"(4), 1="Work Type"(4), 3="Describe Activity"(2), 4="Completed?"(3), 7="Describe Open Issues..."(2).
SELECT f.id AS ptt_form_pk, f.name AS form_name, f.form_type, f.status AS form_status,
       e.id AS ptt_element_pk, e.name AS element_name, e.form_type AS element_type, e.status AS element_status,
       e.ordinal_number, e.form_element_choices
FROM time_tracking_ttform f
JOIN time_tracking_ttformelement e ON e.form_id = f.id
WHERE f.client_id = %(client_id)s
ORDER BY f.id, e.ordinal_number
