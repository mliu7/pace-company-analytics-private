"""PCA's estimated win rate for an open bid (Pipeline Snapshot, Owner 2026-09-10): the historical hit rate of the
estimator, client, division, work type, size band, sector and rep on decided bids, blended in log-odds and shrunk
toward the company base rate by how much evidence each factor has. Transparent by design: every prediction carries
its breakdown (factor, level, hit rate, decided bids, weight) so the page can show why a bid sits at 62 %.

    logit(p) = logit(base) + Σ_k w_k · (logit(r_k) − logit(base)),   w_k = n_k / (n_k + m),   r_k = (won_k + m·base) / (n_k + m)

m (PRIOR_STRENGTH) is the number of decided bids a factor needs before it counts half; the factor rates are smoothed
toward base with the same m. Not a fitted model — a Bayesian-flavoured average that behaves sensibly on 30 bids and
on 3,000. Calibration on a time split is computed with the model (`Model.calibration`) and shown on the page.
"""

import math
from collections import defaultdict
from datetime import date

from django.utils import timezone

from apps.ingestion.bulk import fetch_dict

from . import analytics, rules

PRIOR_STRENGTH = 12
DAMPING_GRID = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0)   # the factors overlap (estimator ≈ rep ≈ division); the summed shift is scaled by λ, chosen on a time split
SINCE = date(2019, 1, 1)
FACTORS = ("estimator", "client", "division", "work_type", "size_band", "sector", "rep")
LABELS = {"estimator": "Estimator", "client": "Client", "division": "Division", "work_type": "Work type", "size_band": "Size band", "sector": "Sector", "rep": "Sales rep"}


def logit(p):
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def inv_logit(x):
    return 1 / (1 + math.exp(-x))


def smoothed_rate(won, n, base, m=PRIOR_STRENGTH):
    return (won + m * base) / (n + m) if (n + m) else base


def combine(base, factors, m=PRIOR_STRENGTH, damping=1.0):
    """factors: [(name, level, won, n)] -> (p, breakdown). A factor with no evidence contributes nothing; the summed
    log-odds shift is scaled by `damping` (< 1 when the factors are correlated, fitted by Model.fit_damping)."""
    x0 = logit(base)
    x = x0
    breakdown = []
    for name, level, won, n in factors:
        if not n:
            breakdown.append({"factor": name, "level": level, "rate": None, "n": 0, "weight": 0.0, "shift": 0.0})
            continue
        r = smoothed_rate(won, n, base, m)
        w = n / (n + m)
        shift = w * (logit(r) - logit(base))
        x += shift
        breakdown.append({"factor": name, "level": level, "rate": round(r, 4), "n": n, "weight": round(w, 3), "shift": round(shift * damping, 4)})
    return inv_logit(x0 + damping * (x - x0)), breakdown


def factor_levels(r):
    """The factor levels of a decorated bid row (analytics.decorate + work_type)."""
    return {
        "estimator": r.get("estimator_id") or None,
        "client": r.get("client_id") or ((r.get("client_name") or "").strip().lower() or None),
        "division": r.get("division") or None,
        "work_type": r.get("work_type") or rules.work_type(r.get("project_name"), r.get("division")),
        "size_band": r.get("size_band") if r.get("value") else None,
        "sector": r.get("sector") or None,
        # the rep only counts when it is someone other than the estimator (in AV they are usually the same person,
        # and the same evidence must not be counted twice)
        "rep": "house" if r.get("house_account") else ((r.get("salesperson_id") or None) if r.get("salesperson_id") != r.get("estimator_id") else None),
    }


class Model:
    """Counts per factor level from decided bids (awarded / won-by-SL vs lost), list + archive, since 2019."""

    def __init__(self, rows=None, today=None, damping=None):
        self.today = today or timezone.localdate()
        self.damping = damping
        rows = rows if rows is not None else self.decided_rows()
        self.counts = {f: defaultdict(lambda: [0, 0]) for f in FACTORS}      # level -> [won, n]
        won = n = 0
        for r in rows:
            n += 1
            won += 1 if r["won"] else 0
            for f, level in factor_levels(r).items():
                if level is None:
                    continue
                c = self.counts[f][level]
                c[0] += 1 if r["won"] else 0
                c[1] += 1
        self.n, self.won = n, won
        self.base = (won / n) if n else 0.5
        self.rows = rows
        if self.damping is None:
            self.damping = self.fit_damping() if rows and n >= 200 else 0.6

    @staticmethod
    def decided_rows(since=SINCE, until=None):
        where = "(b.stage IN ('awarded','lost') OR b.won_by_sl) AND COALESCE(b.awarded_on, b.submitted_on, b.bid_due, b.portal_created::date) >= %s"
        params = [since]
        if until:
            where += " AND COALESCE(b.awarded_on, b.submitted_on, b.bid_due, b.portal_created::date) < %s"
            params.append(until)
        rows = analytics.bids(where, params)
        for r in rows:
            r["work_type"] = rules.work_type(r["project_name"], r["division"])
        return rows

    def predict(self, r):
        """(p, breakdown) for a decorated open-bid row."""
        levels = factor_levels(r)
        factors = []
        for f in FACTORS:
            level = levels[f]
            c = self.counts[f].get(level) if level is not None else None
            factors.append((f, self._label(f, level, r), c[0] if c else 0, c[1] if c else 0))
        return combine(self.base, factors, damping=self.damping)

    def _label(self, f, level, r):
        if level is None:
            return "—"
        if f == "estimator":
            return r.get("estimator") or str(level)
        if f == "client":
            return r.get("client_label") or r.get("client_name") or str(level)
        if f == "rep":
            return "House" if level == "house" else (r.get("rep") or str(level))
        return str(level)

    @staticmethod
    def _decided_on(r):
        return r["awarded_on"] or r["submitted_on"] or r["bid_due"] or (r["portal_created"].date() if r.get("portal_created") else date(1900, 1, 1))

    def _split(self, split=None):
        split = split or date(self.today.year, 1, 1)
        train = [r for r in self.rows if self._decided_on(r) < split]
        test = [r for r in self.rows if self._decided_on(r) >= split]
        return split, train, test

    def fit_damping(self, split=None):
        """λ that minimises the Brier score on the time split (train before Jan 1 of this year, test after)."""
        split, train, test = self._split(split)
        if len(train) < 100 or len(test) < 30:
            return 0.6
        tm = Model(rows=train, today=self.today, damping=1.0)
        best, best_b = 0.6, None
        for lam in DAMPING_GRID:
            tm.damping = lam
            b = sum((tm.predict(r)[0] - (1.0 if r["won"] else 0.0)) ** 2 for r in test) / len(test)
            if best_b is None or b < best_b - 1e-9:
                best, best_b = lam, b
        return best

    def calibration(self, split=None):
        """Train on decided bids before `split`, score the rest with the fitted damping: Brier score, base-rate
        Brier, and 5 buckets of predicted probability vs actual win rate. The page's self-check."""
        split, train_rows, test = self._split(split)
        if not test or len(train_rows) < 30:
            return None
        train = Model(rows=train_rows, today=self.today, damping=self.damping)
        buckets = defaultdict(lambda: [0, 0, 0.0])
        brier = brier0 = 0.0
        for r in test:
            p, _ = train.predict(r)
            y = 1.0 if r["won"] else 0.0
            brier += (p - y) ** 2
            brier0 += (train.base - y) ** 2
            b = min(int(p * 5), 4)
            buckets[b][0] += 1; buckets[b][1] += int(y); buckets[b][2] += p
        out = [{"bucket": "%d–%d %%" % (b * 20, b * 20 + 20), "n": v[0], "won": v[1], "predicted": round(v[2] / v[0], 3), "actual": round(v[1] / v[0], 3)} for b, v in sorted(buckets.items())]
        return {"split": split, "train": train.n, "test": len(test), "brier": round(brier / len(test), 4), "brier_base": round(brier0 / len(test), 4), "damping": self.damping, "buckets": out}
