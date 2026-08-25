"""Tests for the dependency-free statistics helpers in ``scripts/statlib.py``.

These cover the "money path" math: the numbers here end up in a management
brief and in the frozen forecast ledger, so a silent regression is expensive
and invisible. Everything is stdlib-only, so this file runs in CI without the
analysis stack installed.
"""

from __future__ import annotations

import math

import pytest
from supportkit.statlib import (
    bias,
    linear_slope,
    mae,
    mape,
    naive_drift,
    naive_last,
    naive_mean,
    rmse,
    robust_z,
    smape,
    trend_forecast,
    two_proportion_z,
    wilson_interval,
)

# --------------------------------------------------------------------------
# Error metrics
# --------------------------------------------------------------------------

def test_perfect_prediction_scores_zero_everywhere():
    actual = [10.0, 20.0, 5.0]
    assert mae(actual, actual) == 0.0
    assert rmse(actual, actual) == 0.0
    assert mape(actual, actual) == 0.0
    assert smape(actual, actual) == 0.0
    assert bias(actual, actual) == 0.0


def test_mae_and_rmse_known_values():
    actual = [10.0, 10.0]
    predicted = [12.0, 6.0]  # errors +2, -4
    assert mae(actual, predicted) == 3.0
    assert rmse(actual, predicted) == pytest.approx(math.sqrt((4 + 16) / 2))


def test_bias_is_signed_so_systematic_over_forecasting_is_visible():
    """MAE alone can't distinguish 'noisy' from 'always high' — bias can."""
    actual = [10.0, 10.0, 10.0]
    always_high = [13.0, 13.0, 13.0]
    noisy = [13.0, 7.0, 13.0]
    assert mae(actual, always_high) == mae(actual, noisy) == 3.0
    assert bias(actual, always_high) == 3.0
    assert bias(actual, noisy) == pytest.approx(1.0)


def test_mape_skips_zero_actuals_instead_of_dividing_by_zero():
    """A quiet month for a small want must not make MAPE undefined."""
    assert mape([0.0, 10.0], [5.0, 11.0]) == pytest.approx(10.0)
    assert mape([0.0, 0.0], [5.0, 5.0]) == 0.0  # nothing gradeable


def test_smape_stays_defined_at_zero_and_is_bounded():
    assert smape([0.0], [0.0]) == 0.0  # predicting nothing, correctly
    assert smape([0.0], [5.0]) == pytest.approx(200.0)  # the ceiling
    assert smape([10.0], [0.0]) == pytest.approx(200.0)


def test_length_mismatch_raises_rather_than_silently_truncating():
    """zip() would grade against the wrong months without complaining."""
    with pytest.raises(ValueError):
        mae([1.0, 2.0, 3.0], [1.0, 2.0])


def test_empty_series_are_zero_not_errors():
    assert mae([], []) == rmse([], []) == mape([], []) == smape([], []) == bias([], []) == 0.0


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------

def test_naive_baselines_on_a_known_series():
    history = [10.0, 20.0, 30.0]
    assert naive_last(history) == 30.0
    assert naive_mean(history, window=2) == 25.0
    assert naive_mean(history, window=3) == 20.0
    # drift: last + average per-step change = 30 + (30-10)/2
    assert naive_drift(history) == pytest.approx(40.0)


def test_naive_baselines_handle_degenerate_history():
    assert naive_last([]) == naive_mean([]) == naive_drift([]) == 0.0
    assert naive_drift([7.0]) == 7.0  # no step to measure


# --------------------------------------------------------------------------
# Trend forecast (the anchoring fix)
# --------------------------------------------------------------------------

def test_linear_slope_matches_least_squares_by_hand():
    assert linear_slope([1.0, 2.0, 3.0, 4.0]) == pytest.approx(1.0)
    assert linear_slope([4.0, 3.0, 2.0, 1.0]) == pytest.approx(-1.0)
    assert linear_slope([5.0, 5.0, 5.0]) == pytest.approx(0.0)
    assert linear_slope([5.0]) == 0.0


def test_trend_forecast_extrapolates_a_clean_trend_exactly():
    """The regression guard for the 2026-06-14 r6 anchoring bug.

    On a perfectly linear series the next value is knowable: 10,20,30,40,50
    must forecast 60. The old ``mean(y[-3:]) + slope`` returned 50 — one whole
    month behind — because the trailing-mean level sits at the centre of its
    window, not at its end.
    """
    forecast, slope = trend_forecast([10.0, 20.0, 30.0, 40.0, 50.0], window=3)
    assert slope == pytest.approx(10.0)
    assert forecast == pytest.approx(60.0)


def test_trend_forecast_is_symmetric_for_a_falling_series():
    forecast, slope = trend_forecast([50.0, 40.0, 30.0, 20.0, 10.0], window=3)
    assert slope == pytest.approx(-10.0)
    assert forecast == pytest.approx(0.0)


def test_trend_forecast_never_predicts_negative_tickets():
    forecast, _ = trend_forecast([30.0, 20.0, 10.0, 5.0, 1.0], window=3)
    assert forecast >= 0.0


def test_trend_forecast_falls_back_to_the_mean_on_short_history():
    forecast, _ = trend_forecast([10.0, 20.0], window=3)
    assert forecast == pytest.approx(15.0)
    assert trend_forecast([], window=3) == (0.0, 0.0)


def test_trend_forecast_is_robust_to_one_noisy_final_month():
    """A trailing-mean level shouldn't chase a single outlier the way an
    endpoint-anchored line would."""
    steady, _ = trend_forecast([20.0, 20.0, 20.0, 20.0], window=3)
    spiked, _ = trend_forecast([20.0, 20.0, 20.0, 60.0], window=3)
    assert steady == pytest.approx(20.0)
    assert spiked < 60.0


# --------------------------------------------------------------------------
# Wilson interval
# --------------------------------------------------------------------------

def test_wilson_interval_brackets_the_point_estimate():
    low, high = wilson_interval(6, 300)
    assert low < 0.02 < high
    assert 0.005 < low and high < 0.05  # the honest band for 6/300


def test_wilson_interval_stays_inside_zero_one_at_the_edges():
    """The reason Wilson is used instead of Wald: Wald goes out of bounds."""
    low, high = wilson_interval(0, 50)
    assert low == 0.0 and 0.0 < high < 1.0
    low, high = wilson_interval(50, 50)
    assert high == 1.0 and 0.0 < low < 1.0


def test_wilson_interval_narrows_as_the_sample_grows():
    small = wilson_interval(10, 100)
    large = wilson_interval(1000, 10000)
    assert (large[1] - large[0]) < (small[1] - small[0])


def test_wilson_interval_handles_an_empty_month():
    assert wilson_interval(3, 0) == (0.0, 0.0)


# --------------------------------------------------------------------------
# Change detection
# --------------------------------------------------------------------------

def test_robust_z_flags_a_spike_against_a_stable_history():
    history = [100.0, 98.0, 102.0, 101.0, 99.0]
    assert robust_z(160.0, history) > 3
    assert abs(robust_z(100.0, history)) < 1


def test_robust_z_is_not_blinded_by_a_single_past_outlier():
    """Mean/stdev would inflate the scale after one spike and miss the next."""
    history = [100.0, 98.0, 102.0, 101.0, 400.0]
    assert robust_z(160.0, history) > 3


def test_robust_z_degenerate_cases():
    assert robust_z(5.0, []) == 0.0
    assert robust_z(5.0, [5.0, 5.0, 5.0]) == 0.0
    assert robust_z(9.0, [5.0, 5.0, 5.0]) == math.inf


def test_two_proportion_z_detects_a_real_move_and_ignores_noise():
    _, p_big = two_proportion_z(300, 1000, 200, 1000)
    assert p_big < 0.001
    _, p_small = two_proportion_z(21, 100, 20, 100)
    assert p_small > 0.05


def test_two_proportion_z_returns_no_claim_on_empty_or_degenerate_samples():
    assert two_proportion_z(0, 0, 5, 10) == (0.0, 1.0)
    assert two_proportion_z(0, 10, 0, 10) == (0.0, 1.0)  # pooled p == 0
