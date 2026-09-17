"""Nightly derivation of Employee.is_field_hourly (Access Spec v1 §7.2).

True only if ALL hold:  (a) ≥1 live PTT Job Report entry as the worker in the trailing 24 months;
(b) PTT role is 'regular' (not head_pm/pm); (c) derived classification is not admin;
(d) not currently a Project.project_manager or division_head anywhere.  Everything else — including every
salaried/office person who appears in SL charge entry — stays False.
"""

from datetime import timedelta

from django.db import connection
from django.utils import timezone


def derive_field_hourly(run=None):
    since = timezone.localdate() - timedelta(days=730)
    with connection.cursor() as cur:
        cur.execute("""
            UPDATE core_employee e SET is_field_hourly = calc.v
            FROM (
              SELECT e2.id,
                     (COALESCE(e2.ptt_employee_role,'') NOT IN ('head_pm','pm')
                      AND COALESCE(e2.classification_code,'') NOT IN ('admin')
                      AND EXISTS (SELECT 1 FROM operations_timeentry te WHERE te.employee_id = e2.id
                                    AND te.source_status = 1 AND te.form_type = 1 AND te.work_date >= %s)
                      AND NOT EXISTS (SELECT 1 FROM core_project p
                                      WHERE p.project_manager_id = e2.id OR p.division_head_id = e2.id)) AS v
              FROM core_employee e2
            ) calc
            WHERE calc.id = e.id AND e.is_field_hourly IS DISTINCT FROM calc.v""", [since])
        n = cur.rowcount
    return {"employees_updated": n}
