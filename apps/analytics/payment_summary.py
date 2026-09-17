"""Pure summaries for the Payments Received page (views.finance_payments; tests/unit/test_payment_summary.py).

A payment row (collapsed or not) carries the same columns as its invoice rows, filled in from them (Owner, 2026-09-11):
one value when every invoice agrees, "(multiple)" for different invoice / due dates, "N late (avg.)" for different
paid timeframes, the project when every invoice bills the same job, "(SO1 Order)" when every invoice is a job-less SO1
hardware order, and the sums of the invoice amounts and of what is still open.
"""

from decimal import Decimal

D0 = Decimal(0)


def late_label(days):
    """Days between the due date and the day the cash was applied → 'on time' (≤ 0) / 'N days late' / 'N weeks late' /
    'N months late' / 'N years late'; '' when either date is missing."""
    if days is None:
        return ""
    if days <= 0:
        return "on time"

    def unit(n, word):
        return "%d %s%s late" % (n, word, "" if n == 1 else "s")
    if days < 7:
        return unit(days, "day")
    if days < 30:
        return unit(days // 7, "week")
    if days < 365:
        return unit(days // 30, "month")
    return unit(days // 365, "year")


def _single(values):
    """(the value when every non-empty value is the same, whether they differ)."""
    distinct = {v for v in values if v is not None}
    return (next(iter(distinct)) if len(distinct) == 1 else None), len(distinct) > 1


def summarize_payment(lines):
    """Summary of one payment's invoice lines. Each line needs: invoice_ref, invoice_type, invoice_date, due_date,
    days_late (applied − due, None when unknown), invoice_amt, invoice_balance, project_id, project (with display_number)
    and is_so1 (a job-less SO1 hardware order). Returns {"n", "inv_date", "inv_multi", "inv_range", "due", "due_multi",
    "paid": {"label", "cls", "title"}, "project_mode" (one / so1 / none / multiple / ''), "project", "projects_title",
    "inv_amt", "open"}. Amounts are summed once per invoice (an invoice paid by two applications counts once)."""
    out = {"n": len(lines), "inv_date": None, "inv_multi": False, "inv_range": "", "due": None, "due_multi": False,
           "paid": {"label": "", "cls": "", "title": ""}, "project_mode": "", "project": None, "projects_title": "",
           "inv_amt": D0, "open": D0}
    if not lines:
        return out
    out["inv_date"], out["inv_multi"] = _single(a.invoice_date for a in lines)
    dates = sorted({a.invoice_date for a in lines if a.invoice_date})
    if len(dates) > 1:
        out["inv_range"] = "%d invoices dated %s – %s" % (len(lines), dates[0].strftime("%b %-d, %Y"), dates[-1].strftime("%b %-d, %Y"))
    out["due"], out["due_multi"] = _single(a.due_date for a in lines)
    # paid: one label when every invoice lands in the same timeframe, else the simple average of days late (on time = 0)
    dl = [a.days_late for a in lines if a.days_late is not None]
    if dl:
        labels = {late_label(d) for d in dl}
        late = [max(d, 0) for d in dl]
        avg = sum(late) / len(late)
        n_late = sum(1 for d in late if d > 0)
        detail = "%d of %d invoice%s paid late" % (n_late, len(dl), "" if len(dl) == 1 else "s")
        if n_late:
            detail += " (%d–%d days past due)" % (min(d for d in late if d > 0), max(late))
        if len(labels) == 1:
            label = labels.pop()
            out["paid"] = {"label": label, "cls": "neg" if max(late) > 0 else "pos", "title": detail + "."}
        else:
            days = int(avg + 0.5)
            out["paid"] = {"label": late_label(days) + " (avg.)", "cls": "neg" if days > 0 else "pos",
                           "title": "%s; average %.1f days late across the %d invoices (on-time invoices count as 0)." % (detail, avg, len(dl))}
    # project: the one job every invoice bills, or every invoice a job-less SO1 order
    keys = []
    for a in lines:
        if a.project_id:
            keys.append(("one", a.project_id))
        elif a.is_so1:
            keys.append(("so1", None))
        else:
            keys.append(("none", None))
    if len(set(keys)) == 1:
        out["project_mode"] = keys[0][0]
        out["project"] = lines[0].project if keys[0][0] == "one" else None
    else:
        out["project_mode"] = "multiple"
        names = []
        for a in lines:
            n = a.project.display_number if a.project_id and a.project else ("SO1 order" if a.is_so1 else "no job")
            if n not in names:
                names.append(n)
        out["projects_title"] = "Invoices on: " + ", ".join(names)
    seen = set()
    for a in lines:
        k = (a.invoice_ref, a.invoice_type)
        if k in seen:
            continue
        seen.add(k)
        out["inv_amt"] += a.invoice_amt or D0
        out["open"] += a.invoice_balance or D0
    return out
