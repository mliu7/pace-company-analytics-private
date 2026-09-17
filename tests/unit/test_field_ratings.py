"""Field-crew RAPM math tests (plan §7.1/§7.4): synthetic recovery and placebo.

Pure numpy against apps.analytics.field_ratings' math core — no database, no sources.
"""

import os
import unittest

import numpy as np

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()

from apps.analytics import field_ratings as fr  # noqa: E402


def simulate(n_projects=600, n_people=50, pods=5, theta_sd=0.10, noise_sd=0.10, seed=7, shuffle_crews=False):
    rng = np.random.default_rng(seed)
    theta = rng.normal(0, theta_sd, n_people)
    pod_of = rng.integers(0, pods, n_people)
    controls = rng.normal(0, 1, (n_projects, 3))
    ctrl_beta = np.array([0.30, -0.15, 0.05])
    S = np.zeros((n_projects, n_people))
    for j in range(n_projects):
        pod = rng.integers(0, pods)
        members = np.where(pod_of == pod)[0]
        outsiders = np.where(pod_of != pod)[0]
        crew = list(rng.choice(members, size=min(rng.integers(2, 5), len(members)), replace=False))
        if rng.random() < 0.35:   # cross-pod mixing keeps the graph connected
            crew.append(rng.choice(outsiders))
        hours = rng.gamma(2.0, 1.0, len(crew))
        S[j, crew] = hours / hours.sum()
    y = controls @ ctrl_beta + S @ theta + rng.normal(0, noise_sd, n_projects)
    if shuffle_crews:
        S = S[rng.permutation(n_projects)]
    X = np.hstack([np.ones((n_projects, 1)), controls, S])
    alpha = np.full(X.shape[1], fr.CONTROL_ALPHA)
    alpha[0] = 0.0
    people_cols = np.arange(4, 4 + n_people)
    return X, y, S, theta, alpha, people_cols


class SyntheticRecoveryTests(unittest.TestCase):
    def test_recovers_true_effects(self):
        X, y, S, theta, alpha, people_cols = simulate()
        w = np.ones(len(y))
        a = fr.cv_alpha(X, y, w, alpha, people_cols, grid=(4.0, 25.0, 150.0), folds=4)
        av = alpha.copy(); av[people_cols] = a
        beta, G, s2 = fr.ridge_solve(X, y, w, av)
        est = beta[people_cols]
        exposed = S.sum(axis=0) >= 3          # people with meaningful exposure
        corr = np.corrcoef(np.argsort(np.argsort(est[exposed])), np.argsort(np.argsort(theta[exposed])))[0, 1]
        self.assertGreater(corr, 0.75, "rank correlation with true effects too low: %.2f" % corr)

    def test_placebo_collapses(self):
        X, y, S, theta, alpha, people_cols = simulate(shuffle_crews=True)
        Xp = np.hstack([X[:, :4], S])
        w = np.ones(len(y))
        av = alpha.copy(); av[people_cols] = 25.0
        beta, _, _ = fr.ridge_solve(Xp, y, w, av)
        est = beta[people_cols]
        corr = abs(np.corrcoef(est, theta)[0, 1])
        self.assertLess(corr, 0.30, "placebo still correlates with truth: %.2f" % corr)
        self.assertLess(np.std(est), 0.6 * np.std(theta), "placebo effects did not shrink")

    def test_loo_matches_refit(self):
        X, y, S, theta, alpha, people_cols = simulate(n_projects=120, n_people=12, seed=3)
        w = np.ones(len(y))
        av = alpha.copy(); av[people_cols] = 25.0
        beta, G, _ = fr.ridge_solve(X, y, w, av)
        col = int(people_cols[0]); row = int(np.argmax(X[:, col]))
        delta = fr.loo_delta(X, y, w, beta, G, row, col)
        keep = np.ones(len(y), bool); keep[row] = False
        beta2, _, _ = fr.ridge_solve(X[keep], y[keep], w[keep], av)
        self.assertAlmostEqual(beta[col] - delta, beta2[col], places=6)

    def test_eb_and_display(self):
        eff = np.array([0.10, -0.10, 0.0, 0.05])
        se = np.array([0.02, 0.02, 0.3, 0.02])
        shrunk, f, tau2 = fr.eb_shrink(eff, se)
        self.assertLess(abs(shrunk[2]), abs(0.0) + 1e-9 + 0.01)     # noisy one shrinks hard
        self.assertGreater(f[0], f[2])
        self.assertAlmostEqual(fr.hours_display(0.0), 0.0)
        self.assertLess(fr.hours_display(0.1), 0)                    # more hours than budget = negative (bad)
        self.assertGreater(fr.hours_display(-0.1), 0)

    def test_components_and_pairs(self):
        comps = fr.connected_components([(1, 2), (2, 3), (7, 8)], [1, 2, 3, 7, 8, 9])
        sizes = sorted(len(c) for c in comps)
        self.assertEqual(sizes, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
