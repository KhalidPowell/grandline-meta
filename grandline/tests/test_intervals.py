"""
WHAT THIS FILE DOES
Proves `intervals.py` computes correct confidence intervals.

HOW IT'S STRUCTURED
Two kinds of test:
  1. Reference values -- hard-coded published Wilson numbers. These catch
     a refactor that quietly changes the maths.
  2. Properties -- rules that must hold for ANY input, like "bounds never
     leave 0..1". These catch cases nobody thought to write down.

RUN IT WITH:  pytest tests/test_intervals.py
"""

import pytest

from grandline.stats.intervals import (
    Z_95,
    interval_width,
    margin_of_error,
    wilson_interval,
)


@pytest.mark.parametrize(
    ("wins", "games", "expected_low", "expected_high"),
    [
        (10, 20, 0.2993, 0.7007),  # even record, moderate sample
        (0, 10, 0.0000, 0.2775),   # never won -- lower bound pinned at 0
        (10, 10, 0.7225, 1.0000),  # never lost -- upper bound pinned at 1
        (1, 2, 0.0945, 0.9055),    # almost no information -- huge interval
    ],
)
def test_matches_published_reference_values(wins, games, expected_low, expected_high):
    # CHECKS: our formula agrees with the textbook Wilson interval.
    # EXPECT: e.g. 10 wins from 20 games -> 29.93% to 70.07%.
    # These numbers come from published tables, NOT from running our own
    # code -- otherwise the test would just confirm our bugs back to us.
    low, high = wilson_interval(wins, games)
    assert low == pytest.approx(expected_low, abs=1e-4)
    assert high == pytest.approx(expected_high, abs=1e-4)


def test_zero_games_returns_zero_interval():
    # CHECKS: the no-data case doesn't crash on division by zero.
    # EXPECT: exactly (0.0, 0.0).
    assert wilson_interval(0, 0) == (0.0, 0.0)


@pytest.mark.parametrize("wins", [0, 1])
def test_single_game_stays_inside_unit_range(wins):
    # CHECKS: n=1, the case that breaks the simple textbook formula.
    # EXPECT: bounds stay inside 0..1, and the interval is very wide
    # (>75 points) because one game tells you almost nothing.
    low, high = wilson_interval(wins, 1)
    assert 0.0 <= low <= high <= 1.0
    # Not the full 0..1 range: clamping trims the end that would otherwise
    # run past the boundary.
    assert high - low > 0.75


def test_perfect_record_never_exceeds_one():
    # CHECKS: an undefeated deck at any sample size.
    # EXPECT: upper bound <= 1.0. A page showing "100.4%" is a bug report.
    for n in (1, 5, 50, 500, 5000):
        _, high = wilson_interval(n, n)
        assert high <= 1.0


def test_winless_record_never_drops_below_zero():
    # CHECKS: the mirror image -- a deck that never won.
    # EXPECT: lower bound >= 0.0. Negative win rates are not a thing.
    for n in (1, 5, 50, 500, 5000):
        low, _ = wilson_interval(0, n)
        assert low >= 0.0


def test_interval_narrows_as_sample_grows():
    # CHECKS: more games produce more certainty -- the core promise of F2.
    # EXPECT: widths strictly decrease as n goes 20 -> 200 -> 2k -> 20k,
    # and a realistic ladder sample (2,000 games) lands under 5 points.
    widths = [interval_width(n // 2, n) for n in (20, 200, 2000, 20000)]
    assert widths == sorted(widths, reverse=True)
    assert interval_width(1000, 2000) < 0.05


def test_interval_brackets_the_observed_rate_at_reasonable_n():
    # CHECKS: the interval actually contains the rate it describes.
    # EXPECT: for 520 wins from 1000, low < 0.52 < high.
    low, high = wilson_interval(520, 1000)
    assert low < 0.52 < high


def test_wider_z_gives_wider_interval():
    # CHECKS: the z parameter does what it claims.
    # EXPECT: a lower confidence level (z=1.0) is narrower than 95%.
    narrow = interval_width(50, 100, z=1.0)
    default = interval_width(50, 100, z=Z_95)
    assert narrow < default


def test_margin_of_error_is_half_the_width():
    # CHECKS: the two helpers stay consistent with each other.
    # EXPECT: margin_of_error == interval_width / 2, exactly.
    assert margin_of_error(50, 100) == pytest.approx(interval_width(50, 100) / 2)


@pytest.mark.parametrize(
    ("wins", "games"),
    [
        (-1, 10),   # negative wins
        (5, -10),   # negative games
        (11, 10),   # more wins than games played
    ],
)
def test_rejects_impossible_inputs(wins, games):
    # CHECKS: bad data fails loudly at the boundary.
    # EXPECT: ValueError. Silently returning a number here would let a
    # corrupt upstream feed poison the leaderboard unnoticed.
    with pytest.raises(ValueError):
        wilson_interval(wins, games)
