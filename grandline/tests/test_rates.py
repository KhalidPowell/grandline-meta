"""
WHAT THIS FILE DOES
Proves `rates.py` computes the four leaderboard numbers correctly.

THE THREE TESTS THAT MATTER MOST
Each one guards a domain rule that, if backwards, produces a leaderboard
that looks fine and is silently wrong:
  - test_play_rates_across_a_format_sum_to_one  (the doubled denominator)
  - test_a_draw_is_not_a_loss                   (draw handling)
  - test_delta_is_percentage_points_not_percent (points vs percent)

Everything else is edge cases and input validation.

RUN IT WITH:  pytest tests/test_rates.py
"""

import dataclasses

import pytest

from grandline.stats.rates import (
    MIN_SAMPLE,
    decided_games,
    delta_points,
    has_min_sample,
    play_rate,
    summarize,
    win_rate,
)


# --- decided_games ---------------------------------------------------
# Helper that strips draws out of a game count.


def test_draws_are_removed_from_the_denominator():
    # CHECKS: draws come out of the total.
    # EXPECT: 100 games with 4 draws -> 96 decided.
    assert decided_games(100, draws=4) == 96


def test_decided_games_defaults_to_no_draws():
    # CHECKS: the draws argument is optional.
    # EXPECT: 100 games, no draws given -> 100 decided.
    assert decided_games(100) == 100


@pytest.mark.parametrize(
    ("games", "draws"),
    [(-1, 0), (10, -1), (10, 11)],  # negative games, negative draws, draws > games
)
def test_decided_games_rejects_impossible_inputs(games, draws):
    # CHECKS: nonsense inputs fail immediately.
    # EXPECT: ValueError in all three cases.
    with pytest.raises(ValueError):
        decided_games(games, draws)


# --- win_rate --------------------------------------------------------
# The headline number of the product.


def test_win_rate_is_wins_over_decided_games():
    # CHECKS: the basic arithmetic.
    # EXPECT: 50 from 100 -> 0.5.
    assert win_rate(50, 100) == pytest.approx(0.5)


def test_a_draw_is_not_a_loss():
    # CHECKS: draws leave BOTH sides of the fraction, not just the top.
    # EXPECT: 50 wins from 100 games with 20 draws -> 50/80 = 62.5%.
    # If draws were counted as losses this would read 50%, which would
    # systematically punish grindy decks.
    assert win_rate(50, 100, draws=20) == pytest.approx(0.625)


def test_win_rate_with_no_decided_games_returns_zero():
    # CHECKS: the no-data path doesn't divide by zero.
    # EXPECT: 0.0 -- which means "no data", NOT "never wins". Callers
    # must gate on has_min_sample before showing this to anyone.
    assert win_rate(0, 0) == 0.0
    assert win_rate(0, 5, draws=5) == 0.0


def test_win_rate_rejects_more_wins_than_decided_games():
    # CHECKS: wins are validated against DECIDED games, not total games.
    # EXPECT: ValueError -- 95 wins is impossible when 10 of 100 drew.
    with pytest.raises(ValueError):
        win_rate(95, 100, draws=10)


# --- play_rate -------------------------------------------------------
# Share of the format. The doubled denominator lives here.


def test_play_rate_denominator_counts_two_leaders_per_match():
    # CHECKS: the denominator is 2 x matches, not matches.
    # EXPECT: 20 appearances across 50 matches -> 20% (100 slots), not 40%.
    assert play_rate(20, 50) == pytest.approx(0.20)


def test_play_rates_across_a_format_sum_to_one():
    # CHECKS: THE guard against the doubled-denominator bug.
    # EXPECT: every leader's share adds up to exactly 1.0. If this ever
    # fails, the leaderboard is claiming the format is 200% of itself.
    total_matches = 5
    appearances = {"red": 5, "green": 3, "blue": 2}
    # Sanity-check the fixture itself: 10 appearances across 5 matches.
    assert sum(appearances.values()) == 2 * total_matches

    rates = [play_rate(a, total_matches) for a in appearances.values()]
    assert sum(rates) == pytest.approx(1.0)


def test_a_leader_can_occupy_every_slot():
    # CHECKS: an all-mirror window is legal, not an error.
    # EXPECT: 20 appearances in 10 matches (both seats, every match) -> 1.0.
    assert play_rate(20, 10) == pytest.approx(1.0)


def test_play_rate_with_no_matches_returns_zero():
    # CHECKS: an empty window doesn't divide by zero.
    # EXPECT: 0.0.
    assert play_rate(0, 0) == 0.0


def test_play_rate_rejects_more_appearances_than_slots():
    # CHECKS: impossible data fails loudly.
    # EXPECT: ValueError -- 21 appearances can't fit in 10 matches (20 slots).
    with pytest.raises(ValueError):
        play_rate(21, 10)


# --- delta_points ----------------------------------------------------
# Week-over-week movement, in points.


def test_delta_is_percentage_points_not_percent():
    # CHECKS: the units of the "Δ 7d" column.
    # EXPECT: 0.50 -> 0.52 gives +2.0 (points). Reporting +4% would be a
    # different claim about the same movement.
    assert delta_points(0.52, 0.50) == pytest.approx(2.0)


def test_delta_is_negative_when_a_leader_falls():
    # CHECKS: direction is preserved.
    # EXPECT: 0.51 -> 0.48 gives -3.0.
    assert delta_points(0.48, 0.51) == pytest.approx(-3.0)


def test_delta_is_zero_for_no_movement():
    # CHECKS: a flat week.
    # EXPECT: 0.0.
    assert delta_points(0.5, 0.5) == pytest.approx(0.0)


# --- has_min_sample --------------------------------------------------
# Feature F2's gate.


def test_sample_gate_is_inclusive_at_the_threshold():
    # CHECKS: the boundary, where off-by-one errors live.
    # EXPECT: exactly 100 games passes; 99 does not.
    assert has_min_sample(MIN_SAMPLE) is True
    assert has_min_sample(MIN_SAMPLE - 1) is False


def test_sample_gate_threshold_is_overridable():
    # CHECKS: the minimum is a parameter, not hard-wired.
    # EXPECT: 50 games passes when the bar is lowered to 25.
    assert has_min_sample(50, minimum=25) is True


# --- summarize -------------------------------------------------------
# The single entry point that builds a whole leaderboard row.


def test_summarize_builds_a_complete_leaderboard_row():
    # CHECKS: all fields populate and agree with the individual functions.
    # EXPECT: a healthy leader (2,651 games) shows its win rate, the rate
    # sits inside its own confidence interval, and the delta is computed.
    row = summarize(
        leader_code="OP11-001",
        appearances=2651,
        wins=1373,
        total_matches=9330,
        draws=0,
        previous_win_rate=0.504,
    )

    assert row.leader_code == "OP11-001"
    assert row.play_rate == pytest.approx(2651 / (2 * 9330))
    assert row.win_rate == pytest.approx(1373 / 2651)
    assert row.ci_low < row.win_rate < row.ci_high
    assert row.delta_7d == pytest.approx(delta_points(1373 / 2651, 0.504))
    assert row.show_win_rate is True


def test_summarize_suppresses_win_rate_below_the_gate():
    # CHECKS: feature F2 in action on a brand-new fringe deck.
    # EXPECT: show_win_rate is False at 74 games. The rate is still
    # COMPUTED (so F5 and debugging can see it) -- it's just not fit to
    # publish, and the template must respect the flag.
    row = summarize(
        leader_code="OP17-002",
        appearances=74,
        wins=55,
        total_matches=9330,
    )
    assert row.show_win_rate is False
    assert row.win_rate == pytest.approx(55 / 74)


def test_summarize_counts_draws_against_the_sample_gate():
    # CHECKS: the gate uses DECIDED games, not total games played.
    # EXPECT: 101 played with 2 draws = 99 decided -> below the bar.
    row = summarize(
        leader_code="OP09-004",
        appearances=101,
        wins=50,
        total_matches=1000,
        draws=2,
    )
    assert row.decided == 99
    assert row.show_win_rate is False


def test_summarize_treats_a_new_leader_as_no_movement():
    # CHECKS: the previous_win_rate=None path.
    # EXPECT: delta of 0.0. The UI should render this as "new" rather
    # than "±0.0" -- no history and no movement are different things.
    row = summarize(
        leader_code="OP17-009",
        appearances=200,
        wins=100,
        total_matches=1000,
        previous_win_rate=None,
    )
    assert row.delta_7d == 0.0


def test_summary_is_immutable():
    # CHECKS: the frozen dataclass actually refuses writes.
    # EXPECT: FrozenInstanceError. A computed statistic should never be
    # patched after the fact -- fix the function that produced it.
    row = summarize("OP11-001", 200, 100, 1000)
    with pytest.raises(dataclasses.FrozenInstanceError):
        row.win_rate = 0.99
