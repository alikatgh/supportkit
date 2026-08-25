"""Pin ``statlib`` against reference implementations, not against my arithmetic.

``scripts/statlib.py`` is deliberately stdlib-only: it runs in the orchestrator
and in scripts that must import without pandas or scipy present. That is the
right constraint for shipping, and it also means every formula in it was typed
out by hand — Wilson's interval, the pooled two-proportion z, the modified
z-score, a least-squares slope. ``tests/test_statlib.py`` checks those against
worked examples, which catches a wrong answer only where someone thought to
write the example down.

This file checks them against scipy and numpy across thousands of random
inputs instead. It found nothing when it was written — every function matched
to 1e-9 or better — which is the point: it is a tripwire for the next edit, and
a clean run of it is the evidence that a refactor of these formulas was safe.

scipy and numpy are dev-only, so the whole module skips where they are absent
and CI's minimal environment stays honest about what it can prove.
"""

from __future__ import annotations

import math
import random

import pytest

np = pytest.importorskip("numpy", reason="dev-only reference implementation")
stats = pytest.importorskip("scipy.stats", reason="dev-only reference implementation")

import supportkit.statlib as S

# Randomised, but seeded: a failure has to be reproducible to be actionable.
CASES = 300


@pytest.fixture(autouse=True)
def _seeded():
    random.seed(20260809)


def _series(lo=-50.0, hi=200.0, n_max=12):
    n = random.randint(1, n_max)
    return ([random.uniform(lo, hi) for _ in range(n)],
            [random.uniform(lo, hi) for _ in range(n)])


def test_error_metrics_match_numpy():
    for _ in range(CASES):
        actual, predicted = _series()
        a, p = np.array(actual), np.array(predicted)
        assert S.mae(actual, predicted) == pytest.approx(float(np.mean(np.abs(a - p))))
        assert S.rmse(actual, predicted) == pytest.approx(float(np.sqrt(np.mean((a - p) ** 2))))
        assert S.bias(actual, predicted) == pytest.approx(float(np.mean(p - a)))


def test_linear_slope_matches_least_squares():
    """The slope drives every forecast; a sign or /n error here is invisible
    downstream because the output is still a plausible-looking number."""
    for _ in range(CASES):
        n = random.randint(2, 15)
        history = [random.uniform(-20, 90) for _ in range(n)]
        expected = float(np.polyfit(np.arange(n), np.array(history), 1)[0])
        assert S.linear_slope(history) == pytest.approx(expected, abs=1e-7)


def test_normal_sf_matches_scipy_into_the_far_tail():
    """``math.erfc`` keeps precision where ``1 - cdf`` would collapse to 0."""
    for z in (-8.0, -6.0, -3.5, -1.96, -0.5, 0.0, 0.5, 1.96, 3.5, 6.0, 8.0):
        assert S.normal_sf(z) == pytest.approx(float(stats.norm.sf(z)), rel=1e-12)


def test_two_proportion_z_matches_the_pooled_test():
    for _ in range(CASES):
        n1, n2 = random.randint(1, 400), random.randint(1, 400)
        s1, s2 = random.randint(0, n1), random.randint(0, n2)
        z, p = S.two_proportion_z(s1, n1, s2, n2)
        pooled = (s1 + s2) / (n1 + n2)
        if pooled <= 0 or pooled >= 1:
            # No evidence is available; the function must say so rather than
            # divide by a zero standard error.
            assert (z, p) == (0.0, 1.0)
            continue
        se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
        expected_z = (s1 / n1 - s2 / n2) / se
        assert z == pytest.approx(expected_z, rel=1e-12)
        assert p == pytest.approx(float(2 * stats.norm.sf(abs(expected_z))), rel=1e-12)


def test_wilson_interval_matches_the_closed_form_and_stays_in_range():
    for _ in range(CASES):
        n = random.randint(1, 500)
        s = random.randint(0, n)
        low, high = S.wilson_interval(s, n)
        z, phat = 1.96, s / n
        denom = 1 + z * z / n
        centre = (phat + z * z / (2 * n)) / denom
        margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
        assert low == pytest.approx(max(0.0, centre - margin), abs=1e-12)
        assert high == pytest.approx(min(1.0, centre + margin), abs=1e-12)
        assert 0.0 <= low <= phat <= high <= 1.0


def test_robust_z_matches_the_modified_z_score():
    for _ in range(CASES):
        history = [random.uniform(0, 100) for _ in range(random.randint(1, 15))]
        value = random.uniform(-20, 140)
        median = float(np.median(history))
        mad = float(np.median(np.abs(np.array(history) - median)))
        if mad <= 0:
            continue   # documented fallback path, covered in test_statlib.py
        assert S.robust_z(value, history) == pytest.approx(
            0.6745 * (value - median) / mad, rel=1e-12)


# --------------------------------------------------------------------------
# Properties that no reference library can arbitrate
# --------------------------------------------------------------------------

@pytest.mark.parametrize("window", [1, 2, 3, 4, 5, 6])
def test_trend_forecast_is_exact_on_a_straight_line_for_any_window(window):
    """The anchoring bug, stated as a property instead of an example.

    On a perfectly linear series the one-step-ahead forecast must land exactly
    on the next value. The original ``mean(y[-3:]) + slope`` landed one month
    short, which is a plausible-looking number — it under-shot every rising
    want and over-shot every falling one, and the error scales with the slope,
    so the biggest movers were wrongest.

    Parametrising the window matters: the fix is ``steps = (window + 1) / 2``,
    and a hardcoded "2 steps" passes the default-window example while staying
    wrong everywhere else.
    """
    for intercept in (0.0, 5.0, -3.5):
        for slope in (0.0, 1.0, 2.5, -4.0):
            for n in range(max(window, 2), 14):
                history = [intercept + slope * i for i in range(n)]
                truth = intercept + slope * n
                if truth <= 0:
                    continue   # clamped at zero by design; covered elsewhere
                forecast, got_slope = S.trend_forecast(history, window=window)
                assert forecast == pytest.approx(truth, abs=1e-9), (
                    f"window={window} intercept={intercept} slope={slope} n={n}"
                )
                assert got_slope == pytest.approx(slope, abs=1e-9)


def test_smape_never_exceeds_its_bound():
    """sMAPE is quoted as "bounded at 200%"; the half-denominator form is what
    makes that true, and it is easy to lose in a refactor to the ``/2``-free
    variant, which doubles every reported error."""
    worst = 0.0
    for _ in range(2000):
        actual, predicted = _series(-100, 100, n_max=6)
        value = S.smape(actual, predicted)
        assert 0.0 <= value <= 200.0 + 1e-9
        worst = max(worst, value)
    # The bound must be reachable, or it is describing a different formula.
    assert worst > 150.0
