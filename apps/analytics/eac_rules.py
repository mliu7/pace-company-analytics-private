"""Pure EAC rules (no Django) — imported by apps/analytics/eac.py and by the prediction-history backfill, and unit-tested
in tests/unit/test_eac_remaining.py.

The burn-down here is the fix for the defect found on 2026-09-14 (job 265296 Palmer House Fiber Install): the EAC added
the PM's remaining-hours estimate to *today's* actual hours, so every hour worked between the estimate and now was
counted twice — once as spent, once as still to come.
"""

from decimal import Decimal

D0 = Decimal("0")


def burn_down_remaining(rem, worked_since, bud_h, ptt_h):
    """The PM's remaining-hours estimate is a statement made on a date, not a standing figure: every hour worked after it
    was saved is an hour of that estimate already spent. (265296 on 2026-09-13: the PM said 440 h left on Aug 30, 319 h
    were worked before he revised it, and the EAC still carried all 440 h forward — $31k of labor that will never happen,
    which dragged a healthy job to a $7.2k projected GP against PTT's $59k.)

    Returns (remaining hours, source, warning).

    When the work done since the estimate exceeds it, the estimate no longer says anything and the job falls back to
    budget-minus-actual — but **capped at the PM's own estimate**. Uncapped, that fallback can claim far more remaining
    work than the PM ever did (a service agreement with a 9,200 h container budget and a 100 h estimate would jump to
    thousands of hours), which is the same double-count in a different disguise. Capping keeps the correction monotone:
    the remaining hours it returns are never more than the estimate the job was already carrying.
    """
    rem = rem or D0
    worked = worked_since or D0
    if worked <= 0:
        return rem, "pm_estimate", None
    left = rem - worked
    if left > 0:
        return left, "pm_estimate_burned", ("%.0f h worked since the PM's %.0f h estimate; %.0f h carried forward"
                                            % (worked, rem, left))
    fallback = min(max((bud_h or D0) - (ptt_h or D0), D0), rem)
    return fallback, "estimate_exhausted", ("the PM's %.0f h estimate was used up (%.0f h worked since); %.0f h carried "
                                            "forward from the budget" % (rem, worked, fallback))


def risk_assessment(eac_rev, eac_gp, eac_pct, change_pts, overrun, lifecycle_state, unposted_h, no_estimate,
                    estimate_exhausted=False):
    """(score 0-100, level, reasons) for one projection — the heuristic is versioned here so the nightly build and any
    backfill of the prediction history score identically."""
    score, reasons = 0, []
    if eac_rev > 0:
        if eac_gp < 0:
            score += 45; reasons.append("projected loss")
        elif eac_pct is not None and eac_pct < Decimal("0.10"):
            score += 25; reasons.append("projected margin under 10%")
        if change_pts is not None and change_pts < Decimal("-0.05"):
            score += 25; reasons.append("margin more than 5 pts below sold")
        if change_pts is not None and change_pts < Decimal("-0.15"):
            score += 10; reasons.append("margin more than 15 pts below sold")
    if overrun is not None and overrun > Decimal("1.10"):
        score += 15; reasons.append("labor hours EAC exceeds budget by >10%")
    if overrun is not None and overrun > Decimal("1.30"):
        score += 10; reasons.append("labor hours EAC exceeds budget by >30%")
    if lifecycle_state == "dormant":
        score += 10; reasons.append("dormant: no field work in 45+ days with hours remaining")
    if no_estimate and lifecycle_state == "in_progress":
        score += 5; reasons.append("no PM remaining-hours estimate")
    if estimate_exhausted:
        reasons.append("PM remaining-hours estimate used up by work since it was made")
    if unposted_h and unposted_h > 40:
        reasons.append("%.0f PTT hours not yet posted in SL" % unposted_h)
    score = min(score, 100)
    level = "low" if score < 25 else "moderate" if score < 50 else "high" if score < 75 else "critical"
    if eac_rev > 0 and eac_gp < -50000:
        level = "critical"; reasons.append("projected loss over $50k")
    return score, level, reasons
