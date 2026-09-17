"""Server-side redaction helpers (Access Spec v1 §7.2, §12). One helper is the law for compensation."""


def rate_visible(acc, employee_or_flag):
    """May this viewer see wages/$-per-hour/loaded-labor-cost for this employee?

    Salaried/overhead pay is NEVER visible below superadmin, in any form (Owner, answer 5).
    The viewer half of the rule — a field-hourly person never holds rates.field.view at all — lives in
    context._caps_for (Access Spec §7.2; Owner again on the Permissions page, 2026-09-11).
    """
    if acc is None:
        return False
    if acc.is_superadmin:
        return True
    if not acc.rates_field:
        return False
    flag = getattr(employee_or_flag, "is_field_hourly", employee_or_flag)
    return bool(flag)
