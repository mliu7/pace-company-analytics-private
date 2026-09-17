-- ptt.customers — PTT's mirror of SL Customer (id/name only). Used to resolve project.customer_id.
SELECT c.id AS ptt_customer_pk, c.customer_id AS sl_customer_id, c.name, c.status AS ptt_record_status
FROM project_customer c
WHERE c.client_id = %(client_id)s
ORDER BY c.id
