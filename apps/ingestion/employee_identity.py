"""Resolve mutable PTT employee codes without moving an established person identity.

PTT's person PK is stable; its employee_id is an editable link to SL. Matching
only the latter can either violate the unique PTT ID constraint or silently put
one person's history on someone else. Existing PTT links always win. Ambiguous
code changes are reported for review, never resolved by merging on a name.
"""

import hashlib
import re
from collections import Counter

from django.utils import timezone

from .models import DataQualityIssue


IDENTITY_ISSUE = "ptt_employee_code_changed"
# refresh_all already serializes whole refreshes. This transaction lock also
# protects this loader when called directly, including when no employees exist
# yet (SELECT FOR UPDATE alone cannot lock an absent row).
PEOPLE_LOCK_KEY = 815071


def resolve_employee_keys(rows, employees):
    by_person = {e.ptt_person_id: e for e in employees if e.ptt_person_id is not None}
    by_key = {e.employee_key: e for e in employees}
    owners = {key: e.ptt_person_id for key, e in by_key.items()}
    source_keys = {r["ptt_person_pk"]: (r["employee_key"] or "").strip() for r in rows}
    if len(source_keys) != len(rows):
        # The registered SELECT reads the person table's primary key directly.
        # A future broken extract must not choose an arbitrary conflicting row.
        raise ValueError("PTT employees extract returned duplicate person primary keys")
    reserved = set(source_keys.values()) - {""}
    keys, conflicts = {}, {}

    for row in sorted(rows, key=lambda r: r["ptt_person_pk"]):
        person_id = row["ptt_person_pk"]
        source_key = source_keys[person_id]
        existing = by_person.get(person_id)
        if existing:
            key = existing.employee_key
            # Historical duplicate PTT records intentionally use PTT-* keys.
            # Keeping those separate is normal, not a new code-change warning.
            is_fallback = key == f"PTT-{person_id}" or re.fullmatch(r"PTT[0-9a-f]{7}", key)
            expected_fallback = is_fallback and (
                not source_key or owners.get(source_key) not in (None, person_id)
            )
            if key != source_key and not expected_fallback:
                target = by_key.get(source_key)
                conflicts[person_id] = {
                    "ptt_person_id": person_id,
                    "employee_id": existing.id,
                    "retained_employee_key": key,
                    "source_employee_key": source_key,
                    "source_key_employee_id": target.id if target else None,
                    "source_key_ptt_person_id": target.ptt_person_id if target else None,
                    "action": "Preserved the existing employee and history; review the PTT/SL code mapping.",
                }
        elif source_key and len(source_key) <= 10 and owners.get(source_key) is None:
            # A genuinely new PTT person can attach to an unlinked SL employee.
            key = source_key
        else:
            key = _fallback_key(person_id, owners, reserved)
            if len(source_key) > 10:
                conflicts[person_id] = {
                    "ptt_person_id": person_id, "source_employee_key": source_key,
                    "retained_employee_key": key,
                    "action": "Used a PTT identity because the supplied employee code exceeds 10 characters.",
                }
        keys[person_id] = key
        owners[key] = person_id

    counts = Counter(source_keys.values())
    stats = {
        "existing_ptt_links": sum(pid in by_person for pid in keys),
        "new_ptt_links": sum(pid not in by_person for pid in keys),
        "duplicate_source_codes": sum(bool(key) and count > 1 for key, count in counts.items()),
        "identity_warnings": len(conflicts),
    }
    return keys, conflicts, stats


def _fallback_key(person_id, owners, reserved):
    candidate = "PTT-%d" % person_id
    attempt = 0
    while len(candidate) > 10 or candidate in reserved or candidate in owners:
        # Keep within Employee.employee_key's width even for a large source PK,
        # or when an SL code/another person already occupies PTT-<pk>. Deterministic
        # candidates plus the loader lock avoid both random churn and collisions.
        digest = hashlib.sha256(f"ptt:{person_id}:{attempt}".encode()).hexdigest()
        candidate = "PTT" + digest[:7]
        attempt += 1
    return candidate


def record_identity_issues(run, person_ids, conflicts):
    """One visible issue per PTT person; retries must not flood Data Quality.

    project_id is NULL here, so the shared raw upsert's PostgreSQL unique key
    would not prevent duplicates. Use explicit lookup under the people lock.
    Preserve acknowledgements until the conflict disappears; reopen on recurrence.
    """
    now = timezone.now()
    scope = DataQualityIssue.objects.filter(
        code=IDENTITY_ISSUE, source_system="ptt", project__isnull=True, field_name="employee_key",
    )
    for person_id, details in conflicts.items():
        item, created = scope.get_or_create(
            source_key=str(person_id),
            defaults={
                "code": IDENTITY_ISSUE, "source_system": "ptt", "field_name": "employee_key",
                "severity": "warning", "detected_run": run, "details": details,
            },
        )
        if not created:
            item.details = details
            item.detected_run = run
            if item.status == "resolved":
                item.status = "open"
                item.resolved_at = None
                item.resolution_note = ""
            item.save(update_fields=["details", "detected_run", "status", "resolved_at", "resolution_note", "updated_at"])
    # A missing person/empty extract does not prove their discrepancy was fixed.
    scope.filter(source_key__in=[str(pid) for pid in person_ids]).exclude(
        source_key__in=[str(pid) for pid in conflicts],
    ).exclude(status="resolved").update(
        status="resolved", resolved_at=now, updated_at=now,
        resolution_note="PTT employee code no longer conflicts with the retained local identity.",
    )
