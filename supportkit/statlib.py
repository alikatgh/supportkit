#!/usr/bin/env python3
"""Dependency-free statistics helpers shared by the scoring/insight stages.

Why a separate module (and why pure stdlib):

* ``common.py`` is the canonical home for *pipeline plumbing* helpers
  (``latest_run``, ``parse_json_object``) and its docstring asks callers not to
  grow it casually. Statistics are a different concern, so they live here.
* Every function below is pure Python — no pandas, numpy, or scipy. That means
  the forecast-scoring and share-interval logic can be unit-tested in CI on a
  bare ``actions/setup-python`` runner without installing the heavy analysis
  stack, which is exactly where silent math regressions would otherwise hide.

The three families here:

1. **Forecast error metrics** (:func:`mae`, :func:`mape`, :func:`smape`,
   :func:`rmse`) — used to grade ``forecast_ledger.json`` predictions once a
   target month closes.
2. **Naive baselines** (:func:`naive_last`, :func:`naive_mean`,
   :func:`naive_drift`) — the "obvious alternative" a real forecast must beat.
   A model that cannot beat last-month-repeated is not a model.
3. **Proportion intervals and change detection** (:func:`wilson_interval`,
   :func:`robust_z`, :func:`two_proportion_z`) — so a want's monthly share is
   reported with uncertainty and a "spike" claim has a threshold behind it.
"""

from __future__ import annotations

import math
from statistics import median

__all__ = [
    "linear_slope",
    "trend_forecast",
    "mae",
    "rmse",
    "mape",
    "smape",
    "bias",
    "naive_last",
    "naive_mean",
    "naive_drift",
    "wilson_interval",
    "robust_z",
    "two_proportion_z",
    "normal_sf",
]


# --------------------------------------------------------------------------
# Forecast error metrics
# --------------------------------------------------------------------------

def _pairs(actual: list[float], predicted: list[float]) -> list[tuple[float, float]]:
    """Zip and validate two equal-length numeric series.

    Raises:
        ValueError: if the lengths differ. Silently truncating (what ``zip``
            does by default) would quietly grade a forecast against the wrong
            months, so the mismatch is made loud.
    """
    if len(actual) != len(predicted):
        raise ValueError(f"length mismatch: {len(actual)} actual vs {len(predicted)} predicted")
    return list(zip([float(a) for a in actual], [float(p) for p in predicted]))


def mae(actual: list[float], predicted: list[float]) -> float:
    """Mean absolute error. Same units as the series (tickets/month here)."""
    pairs = _pairs(actual, predicted)
    if not pairs:
        return 0.0
    return sum(abs(a - p) for a, p in pairs) / len(pairs)


def rmse(actual: list[float], predicted: list[float]) -> float:
    """Root mean squared error. Punishes single large misses harder than MAE."""
    pairs = _pairs(actual, predicted)
    if not pairs:
        return 0.0
    return math.sqrt(sum((a - p) ** 2 for a, p in pairs) / len(pairs))


def mape(actual: list[float], predicted: list[float]) -> float:
    """Mean absolute percentage error over entries with non-zero actuals.

    Zero-actual months are skipped rather than treated as infinite error. This
    matters for the long tail of small wants, where a month with zero tickets
    is common and would otherwise make MAPE undefined for the whole run.

    Returns:
        Percentage (e.g. ``12.5`` for 12.5%), or ``0.0`` when every actual is
        zero. Read alongside :func:`smape`, which stays defined at zero.
    """
    pairs = [(a, p) for a, p in _pairs(actual, predicted) if a != 0]
    if not pairs:
        return 0.0
    return 100.0 * sum(abs(a - p) / abs(a) for a, p in pairs) / len(pairs)


def smape(actual: list[float], predicted: list[float]) -> float:
    """Symmetric MAPE — defined when actuals are zero, bounded at 200%.

    Uses the ``|a-p| / ((|a|+|p|)/2)`` form. A pair where both values are zero
    contributes 0 (a perfect prediction of nothing), not a division error.
    """
    pairs = _pairs(actual, predicted)
    if not pairs:
        return 0.0
    total = 0.0
    for a, p in pairs:
        denom = (abs(a) + abs(p)) / 2.0
        total += 0.0 if denom == 0 else abs(a - p) / denom
    return 100.0 * total / len(pairs)


def bias(actual: list[float], predicted: list[float]) -> float:
    """Mean signed error (predicted - actual).

    Positive means the forecast systematically over-predicts. Reported next to
    MAE because a forecast can have acceptable magnitude error while being
    consistently high — which is the failure mode of a level/slope model whose
    level and slope are anchored at different points in the series.
    """
    pairs = _pairs(actual, predicted)
    if not pairs:
        return 0.0
    return sum(p - a for a, p in pairs) / len(pairs)


# --------------------------------------------------------------------------
# Trend forecast
# --------------------------------------------------------------------------

def linear_slope(history: list[float]) -> float:
    """Ordinary least-squares slope of ``history`` against index 0..n-1.

    Equivalent to ``numpy.polyfit(range(n), y, 1)[0]`` but stdlib-only, so the
    forecast that ends up in a management brief can be unit-tested in CI
    without the analysis stack installed.

    Returns:
        0.0 for fewer than two points, or when every x is identical.
    """
    y = [float(v) for v in history]
    n = len(y)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(y) / n
    numerator = sum((i - mean_x) * (y[i] - mean_y) for i in range(n))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    return numerator / denominator if denominator else 0.0


def trend_forecast(history: list[float], window: int = 3) -> tuple[float, float]:
    """One-step-ahead forecast from a trailing level plus the series slope.

    **The anchoring fix.** The original formula was
    ``mean(y[-3:]) + slope`` — but the mean of the last three points is the
    level at index ``n-2`` (their centre), while the month being predicted is
    index ``n``. Adding one slope step lands on ``n-1``: the forecast was
    systematically one month behind the trend, under-shooting every rising
    want and over-shooting every falling one, and that number was frozen into
    the ledger and shown to management (audit 2026-06-14 r6). The correct
    extrapolation advances from the level's own anchor to the target:
    ``steps = n - 1 - (n - 1 - (window - 1) / 2) = (window + 1) / 2``, i.e. two
    steps for the default 3-month window.

    Using a trailing-mean level rather than the fitted line's endpoint is
    deliberate: it keeps the forecast robust to a single noisy final month,
    which matters on short count series.

    Args:
        history: Monthly counts, oldest first.
        window: How many trailing months form the level anchor.

    Returns:
        ``(forecast, slope)``. The forecast is clamped at 0 — a negative
        ticket count is not a prediction. Slope is returned so callers can
        report trend direction without recomputing it.
    """
    y = [float(v) for v in history]
    if not y:
        return (0.0, 0.0)
    slope = linear_slope(y)
    if len(y) < window:
        return (max(0.0, sum(y) / len(y)), slope)
    tail = y[-window:]
    level = sum(tail) / len(tail)
    steps = (window + 1) / 2.0
    return (max(0.0, level + slope * steps), slope)


# --------------------------------------------------------------------------
# Naive baselines
# --------------------------------------------------------------------------

def naive_last(history: list[float]) -> float:
    """Repeat the last observed value ("random walk" / persistence forecast).

    The hardest baseline to beat on short, noisy monthly count series, and the
    one management intuitively applies anyway ("same as last month").
    """
    return float(history[-1]) if history else 0.0


def naive_mean(history: list[float], window: int = 3) -> float:
    """Mean of the trailing ``window`` observations (default 3 months)."""
    if not history:
        return 0.0
    tail = history[-window:] if window > 0 else history
    return sum(float(v) for v in tail) / len(tail)


def naive_drift(history: list[float]) -> float:
    """Last value plus the average per-step change across the whole series.

    The classic drift method: ``y[-1] + (y[-1] - y[0]) / (n - 1)``. Unlike
    :func:`naive_mean` it extrapolates trend, so it is the fair comparison for
    the pipeline's own slope-based forecast.
    """
    if not history:
        return 0.0
    if len(history) < 2:
        return float(history[-1])
    step = (float(history[-1]) - float(history[0])) / (len(history) - 1)
    return float(history[-1]) + step


# --------------------------------------------------------------------------
# Proportion intervals and change detection
# --------------------------------------------------------------------------

def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal ("Wald") interval for the same reason the 101
    course prefers it: at small counts or shares near 0/1 the Wald interval
    produces bounds outside [0, 1] and badly under-covers. A want with 6
    tickets out of 300 should not be reported as a bare 2.0% share when the
    honest read is "somewhere between 0.9% and 4.3%".

    Args:
        successes: Count of the category (e.g. tickets for one want).
        total: Denominator (e.g. all tickets that month).
        z: Normal quantile; 1.96 = 95%, 2.576 = 99%.

    Returns:
        ``(low, high)`` clamped to [0, 1]. Returns ``(0.0, 0.0)`` when
        ``total <= 0`` so callers can render an empty month without a guard.
    """
    if total <= 0:
        return (0.0, 0.0)
    successes = max(0, min(int(successes), int(total)))
    n = float(total)
    phat = successes / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    # The Wilson interval always contains phat; at phat = 0 and phat = 1 the
    # relevant bound equals it exactly. In floating point those endpoints come
    # out a hair inside — 6/6 successes gave an upper bound of
    # 0.9999999999999999 — so the interval failed to bracket its own point
    # estimate. Widening to phat repairs the rounding without moving the
    # statistic: it only ever acts at the two boundaries.
    low = max(0.0, min(center - margin, phat))
    high = min(1.0, max(center + margin, phat))
    return (low, high)


def robust_z(value: float, history: list[float]) -> float:
    """Modified z-score of ``value`` against ``history`` using median/MAD.

    Uses the median and median absolute deviation instead of mean/stdev so a
    single past spike doesn't inflate the scale and mask the next one. The
    0.6745 constant rescales MAD to be comparable to a standard deviation for
    normally-distributed data, which keeps the familiar "|z| > 3 is unusual"
    reading intact.

    Returns:
        0.0 when history is empty or has zero spread *and* ``value`` matches
        the median; otherwise a signed score. When MAD is exactly zero but the
        value differs, falls back to standard deviation, and if that is also
        zero returns ``+/-inf`` — a genuinely unprecedented value in a
        perfectly flat series.
    """
    clean = [float(v) for v in history]
    if not clean:
        return 0.0
    med = median(clean)
    deviations = [abs(v - med) for v in clean]
    mad = median(deviations)
    if mad > 0:
        return 0.6745 * (float(value) - med) / mad
    if float(value) == med:
        return 0.0
    mean = sum(clean) / len(clean)
    variance = sum((v - mean) ** 2 for v in clean) / len(clean)
    sd = math.sqrt(variance)
    if sd > 0:
        return (float(value) - mean) / sd
    return math.inf if float(value) > med else -math.inf


def normal_sf(z: float) -> float:
    """Upper-tail probability of the standard normal, via ``math.erfc``."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def two_proportion_z(s1: int, n1: int, s2: int, n2: int) -> tuple[float, float]:
    """Pooled two-proportion z-test: is share 1 different from share 2?

    Used to answer "did this want's share really move between months, or is
    that sample noise?" — the same test the 101 statistics module teaches,
    applied to the month-over-month share comparison.

    Returns:
        ``(z, two_sided_p)``. Returns ``(0.0, 1.0)`` when either sample is
        empty or the pooled proportion is degenerate (0 or 1), i.e. when the
        data cannot support a claim of difference.
    """
    if n1 <= 0 or n2 <= 0:
        return (0.0, 1.0)
    p1 = s1 / n1
    p2 = s2 / n2
    pooled = (s1 + s2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return (0.0, 1.0)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return (0.0, 1.0)
    z = (p1 - p2) / se
    return (z, 2.0 * normal_sf(abs(z)))
