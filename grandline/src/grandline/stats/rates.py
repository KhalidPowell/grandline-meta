"""
WHAT THIS FILE DOES
Computes the four numbers on every leaderboard row: play rate, win rate,
the 7-day change, and whether we have enough games to publish at all.

WHY IT EXISTS
Three domain rules are easy to get backwards, and each one produces a
leaderboard that looks perfectly fine and is silently wrong. Putting them
in tested functions here means they get decided once, not re-guessed by
whoever writes the rollup job later:

  1. A match seats TWO leaders, so play rate divides by 2 x matches.
  2. Draws are excluded from win rate, not counted as losses.
  3. A change in win rate is percentage POINTS, not percent.

RULE FOR THIS FILE
Pure functions only -- same as intervals.py. No database, no network.
"""

from dataclasses import dataclass

from grandline.stats.intervals import Z_95, wilson_interval

# The sample-size gate. Below this many decided games, feature F2 hides
# the win rate entirely rather than showing a number we can't defend.
# 100 is a judgement call: it puts the ± at roughly 10 points, which is
# about the widest that is still worth reading.
MIN_SAMPLE = 100


def decided_games(games: int, draws: int = 0) -> int:
    """Games that actually produced a winner.

    WHY: win rate needs a denominator of games that could be won. Draws
    can't, so they come out of both sides of the fraction.

    WHY NOT COUNT DRAWS AS LOSSES: a draw isn't evidence a deck is worse,
    it's an absence of evidence. Counting them as losses would
    systematically punish grindy archetypes, which are real in this game.
    """
    # Same guard-clause pattern as intervals.py: impossible inputs fail here.
    if games < 0:
        raise ValueError(f"games must not be negative, got {games}")
    if draws < 0:
        raise ValueError(f"draws must not be negative, got {draws}")
    if draws > games:
        raise ValueError(f"draws ({draws}) cannot exceed games ({games})")
    return games - draws


def win_rate(wins: int, games: int, draws: int = 0) -> float:
    """Share of decided games won, as 0.0 to 1.0.

    WHY THIS FUNCTION EXISTS: it's the headline number of the product,
    and it needs exactly one definition in the codebase.

    CAREFUL: returns 0.0 when there are no decided games. That's
    convenient for rollup code but it's a trap -- 0.0 here means "no
    data", NOT "never wins". Always gate display on has_min_sample().
    """
    decided = decided_games(games, draws)
    if wins > decided:
        raise ValueError(f"wins ({wins}) cannot exceed decided games ({decided})")
    if decided == 0:
        return 0.0
    return wins / decided


def play_rate(appearances: int, total_matches: int) -> float:
    """Share of all leader slots this leader occupied, as 0.0 to 1.0.

    WHY THIS FUNCTION EXISTS: to make the doubled denominator impossible
    to forget.

    appearances   = matches this leader was in, on either side
    total_matches = all matches in the window

    THE TRAP: every match seats two leaders, so the denominator is
    2 x total_matches. Divide by total_matches instead and every rate on
    the page doubles -- the format sums to 200% of itself.

    MIRRORS: if a leader faces itself, it fills both seats and should be
    counted twice in `appearances`. Counting once makes popular decks
    look rarer than they are.
    """
    if appearances < 0:
        raise ValueError(f"appearances must not be negative, got {appearances}")
    if total_matches < 0:
        raise ValueError(f"total_matches must not be negative, got {total_matches}")

    # `slots` is the whole point of this function -- two seats per match.
    slots = 2 * total_matches

    if appearances > slots:
        raise ValueError(
            f"appearances ({appearances}) cannot exceed available leader "
            f"slots ({slots}) across {total_matches} matches"
        )
    if slots == 0:
        return 0.0
    return appearances / slots


def delta_points(current: float, previous: float) -> float:
    """Change between two rates, in percentage POINTS.

    WHY THIS FUNCTION EXISTS: to stop the most common reporting error.
    0.50 -> 0.52 returns +2.0 (points), not +4.0 (percent change). Those
    are different claims and the column header must say which one it is.

    Both inputs are rates in 0..1; the output is in points.
    """
    return (current - previous) * 100


def has_min_sample(games: int, minimum: int = MIN_SAMPLE) -> bool:
    """Whether a win rate is backed by enough games to publish.

    WHY THIS FUNCTION EXISTS: it's feature F2 made real, and it's the
    credibility of the whole product.

    NOTE: a low-sample leader still appears on the leaderboard with its
    play rate and game count -- it just shows no win rate. Hiding the row
    entirely would understate how much of the format is fringe decks.
    """
    return games >= minimum


@dataclass(frozen=True, slots=True)
class RateSummary:
    """One leader's numbers for one time window -- a single leaderboard row.

    WHY A DATACLASS: these fields always travel together, and naming them
    beats passing a 10-item tuple around.

    WHY FROZEN: nothing downstream should patch a computed statistic. If a
    number is wrong, fix the function that produced it. Freezing makes the
    wrong fix impossible rather than merely discouraged.

    This is the exact shape stage 3's rollup job will save as a
    `leader_stats` database row.
    """

    leader_code: str      # stable id, e.g. "OP11-001"
    appearances: int      # matches played in the window
    wins: int
    draws: int
    play_rate: float      # 0..1
    win_rate: float       # 0..1 -- meaningless unless show_win_rate is True
    ci_low: float         # lower confidence bound, 0..1
    ci_high: float        # upper confidence bound, 0..1
    delta_7d: float       # change vs previous window, in points
    show_win_rate: bool   # False = below the sample gate, hide the rate

    @property
    def decided(self) -> int:
        """Games that produced a winner. Derived, so it can't drift."""
        return self.appearances - self.draws


def summarize(
    leader_code: str,
    appearances: int,
    wins: int,
    total_matches: int,
    draws: int = 0,
    previous_win_rate: float | None = None,
    z: float = Z_95,
    minimum: int = MIN_SAMPLE,
) -> RateSummary:
    """Build one complete leaderboard row.

    WHY THIS FUNCTION EXISTS: it's the single entry point the rest of the
    app will call. It COMPOSES the functions above rather than
    reimplementing them, so each rate has exactly one definition.

    previous_win_rate=None means the leader is new to the format. That
    yields a delta of 0.0, and the UI should render it as "new" rather
    than "±0.0" -- no movement and no history are different things.
    """
    # Compute the pieces in dependency order, reusing the tested functions.
    decided = decided_games(appearances, draws)
    rate = win_rate(wins, appearances, draws)
    low, high = wilson_interval(wins, decided, z)

    return RateSummary(
        leader_code=leader_code,
        appearances=appearances,
        wins=wins,
        draws=draws,
        play_rate=play_rate(appearances, total_matches),
        win_rate=rate,
        ci_low=low,
        ci_high=high,
        delta_7d=(
            0.0 if previous_win_rate is None else delta_points(rate, previous_win_rate)
        ),
        # Gate on DECIDED games, not total. 101 games with 2 draws is 99
        # decided, which is below the bar.
        show_win_rate=has_min_sample(decided, minimum),
    )
