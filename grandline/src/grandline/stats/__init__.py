"""
WHAT THIS FILE DOES
Makes `grandline.stats` a package and re-exports its public functions, so
callers can write `from grandline.stats import win_rate` instead of
`from grandline.stats.rates import win_rate`.

WHY IT EXISTS
It defines the layer's front door. Anything listed in __all__ below is a
promise to the rest of the app; anything not listed is an internal detail
that can be renamed freely.

THE RULE THIS LAYER ENFORCES
Nothing in this package may import a database session, an HTTP client, or
read the clock. If a function needs one of those, it belongs in
`grandline.pipeline` (stage 3), not here.
"""

from grandline.stats.intervals import (
    Z_95,
    interval_width,
    margin_of_error,
    wilson_interval,
)
from grandline.stats.rates import (
    MIN_SAMPLE,
    RateSummary,
    decided_games,
    delta_points,
    has_min_sample,
    play_rate,
    summarize,
    win_rate,
)

# The public surface of this layer. Sorted so additions are easy to spot
# in a diff, and so `from grandline.stats import *` stays predictable.
__all__ = [
    "MIN_SAMPLE",
    "RateSummary",
    "Z_95",
    "decided_games",
    "delta_points",
    "has_min_sample",
    "interval_width",
    "margin_of_error",
    "play_rate",
    "summarize",
    "wilson_interval",
    "win_rate",
]
