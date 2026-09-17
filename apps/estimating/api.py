"""What other apps may call (SharePoint spec §8 "What PCA adds"): estimate summaries for a bid page, and the
BOM / labor approval bridge into apps.planning (Phase D) — guarded so this app works before Phase D ships."""

from django.urls import reverse

from .models import Estimate


def estimate_summary(e):
    return {"id": e.id, "title": str(e), "status": e.status, "status_label": e.get_status_display(),
            "total_cost": e.total_cost, "total_sell": e.total_sell, "profit": e.profit, "margin": e.margin,
            "labor_hours": e.labor_hours, "line_count": e.line_count, "room_count": e.room_count,
            "owner": e.owner.display_name if e.owner_id and e.owner else "", "updated_at": e.updated_at,
            "version_no": e.version_no, "url": reverse("estimating:estimate_detail", args=[e.id])}


def estimates_for_bid(bid_id):
    """Summaries of every estimate attached to a bid (newest first) — for the bid page's Estimates card."""
    qs = Estimate.objects.filter(bid_id=bid_id).select_related("owner").order_by("-updated_at")
    return [estimate_summary(e) for e in qs]


def estimates_for_project(project_id):
    qs = Estimate.objects.filter(project_id=project_id).select_related("owner").order_by("-updated_at")
    return [estimate_summary(e) for e in qs]


def approval_available():
    try:
        from apps.planning.api import create_approval_request  # noqa: F401
        return True
    except Exception:  # noqa - ImportError until Phase D, or a signature the bridge does not know
        return False


def raise_approval(estimate, account, note="", kind="BOM", needed_by=None, department=""):
    """Raise a BOM / labor approval request from an estimate through apps.planning.api.create_approval_request
    (Phase D, BA-01): kind BOM | Labor, amount = the estimate's totals in the notes, estimate_id + bid_id linked.
    Returns (ok, message). Never raises: without Phase D the message says so and nothing is written."""
    try:
        from apps.planning.api import create_approval_request
    except Exception:  # noqa
        return False, "BOM / labor approvals arrive with the Production phase (D); nothing was sent."
    from datetime import date
    if isinstance(needed_by, str):
        try:
            needed_by = date.fromisoformat(needed_by)
        except ValueError:
            needed_by = None
    summary = "Estimate #%d %s — sell $%s · cost $%s · labor %s h · %d line%s in %d room%s" % (
        estimate.id, str(estimate), "{:,.2f}".format(estimate.total_sell), "{:,.2f}".format(estimate.total_cost), estimate.labor_hours,
        estimate.line_count, "" if estimate.line_count == 1 else "s", estimate.room_count, "" if estimate.room_count == 1 else "s")
    notes = (note.strip() + "\n\n" if note and note.strip() else "") + summary
    name = ""
    if not estimate.project_id:
        name = (str(estimate) + ((" — " + estimate.client_name) if estimate.client_name and estimate.client_name != str(estimate) else ""))[:150]
    try:
        req = create_approval_request(project_id=estimate.project_id, kind=kind or "BOM", requested_by_user=getattr(account, "user", None),
                                      needed_by=needed_by, department=department or "Estimating", notes=notes, estimate_id=estimate.id,
                                      project_name=name, bid_id=estimate.bid_id)
    except TypeError as e:
        return False, "The approvals API does not accept this request (%s)." % e
    except Exception as e:  # noqa
        return False, "Approval request failed: %s" % e
    ref = getattr(req, "pk", None) or req
    estimate.approval_ref = str(ref)[:64]
    estimate.save(update_fields=["approval_ref"])
    return True, "%s approval request #%s raised — Production › Approvals." % ("Labor" if str(kind).lower().startswith("l") else "BOM", ref)
