"""
WHAT THIS FILE DOES
Turns a win/loss record into a confidence interval -- the "±" shown next
to every win rate on the leaderboard.

WHY IT EXISTS
A raw win rate lies about small samples. "7 wins from 10 games" reads as
70%, but the true skill could be anywhere from 40% to 90%. This file
measures that uncertainty so the site can display it (feature F2) instead
of pretending it isn't there.

RULE FOR THIS FILE
Pure maths only -- no database, no network, no clock. That constraint is
what makes every function here trivial to test.
"""

from math import sqrt

# The multiplier for a 95% confidence level.
# Named constant rather than a bare 1.96 in the formula so that (a) the
# code and the public methodology page quote the identical number, and
# (b) adding a 99% option later is a one-line change.
Z_95 = 1.959963984540054


def wilson_interval(wins: int, games: int, z: float = Z_95) -> tuple[float, float]:
    """Return (low, high) bounds for a win rate, each between 0.0 and 1.0.

    WHY THIS FUNCTION EXISTS
    Feature F2 promises never to publish a number the product can't
    defend. This is how "can't defend" gets measured.

    WHY WILSON, NOT THE TEXTBOOK FORMULA
    The simple formula (p ± z·√(p(1-p)/n)) can return bounds below 0% or
    above 100% at small samples -- visibly broken on a public page.
    Wilson always stays inside the range and degrades gracefully.

    Returns (0.0, 0.0) when games is 0: no data means no rate to bound.

    Examples:
        >>> low, high = wilson_interval(10, 20)
        >>> round(low, 4), round(high, 4)
        (0.2993, 0.7007)
    """
    # Guard clauses. Records that can't physically happen fail loudly here
    # rather than quietly producing a nonsense number downstream.
    if games < 0:
        raise ValueError(f"games must not be negative, got {games}")
    if wins < 0:
        raise ValueError(f"wins must not be negative, got {wins}")
    if wins > games:
        raise ValueError(f"wins ({wins}) cannot exceed games ({games})")

    # No games played -> nothing to be confident or unconfident about.
    if games == 0:
        return (0.0, 0.0)

    p = wins / games      # the observed win rate, e.g. 0.52
    z_sq = z * z          # precomputed: z appears squared three times below

    # The Wilson formula, split into named pieces so it's readable.
    # `denominator` shrinks toward 1.0 as games grow -- that's the term
    # that makes big samples produce tight intervals.
    denominator = 1 + z_sq / games
    # `centre` is p nudged toward 50%. The nudge is large at small n and
    # nearly zero at large n, which is why a 1-game record isn't trusted.
    centre = (p + z_sq / (2 * games)) / denominator
    # `margin` is the half-width of the interval.
    margin = (z / denominator) * sqrt(p * (1 - p) / games + z_sq / (4 * games * games))

    # Clamp defensively. Wilson is mathematically inside [0, 1], but
    # floating-point rounding can push a bound a hair outside at p=0 or
    # p=1, and a leaderboard reading "100.0001%" is a bug report.
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def interval_width(wins: int, games: int, z: float = Z_95) -> float:
    """How wide the interval is -- one number for "how unsure are we".

    WHY: useful for sorting, or for flagging a leader that clears the
    n>=100 gate but is still shaky.
    """
    low, high = wilson_interval(wins, games, z)
    return high - low


def margin_of_error(wins: int, games: int, z: float = Z_95) -> float:
    """Half the interval width, for rendering as "51.8% ±1.1".

    WHY A SEPARATE FUNCTION: the UI wants a single ± figure, but that's a
    simplification -- the real interval is asymmetric near 0% and 100%.
    Fine for mid-range leaders; the detail page (F5) should show the true
    bounds instead.
    """
    return interval_width(wins, games, z) / 2
