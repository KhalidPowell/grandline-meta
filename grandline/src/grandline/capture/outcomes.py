"""
WHAT THIS FILE DOES
Defines every outcome a game can have, which column each one counts in,
and how a player handle is cleaned before comparison.

WHY IT EXISTS
Two readers (combat logs, the player log) and the store all need the same
answers to "is this a win?" and "are these two handles the same person?".
Defining them once here means decision D3 and trap T2 each have exactly
one implementation.
"""

from typing import Literal

# D3 (PRD v2.3). Each way a game can end keeps its own tag, so games
# decided early can be filtered out later (F9) without a schema change.
Outcome = Literal[
    "win",                  # their leader took lethal damage
    "loss",                 # my leader took lethal damage
    "concede",              # I scooped
    "opponent_concede",     # they scooped
    "opponent_disconnect",  # they left -- counts as my win (owner's call, 4 Oct 2026)
    "unknown",              # neither log says (trap T4) -- in NEITHER column
]
OUTCOMES: frozenset[str] = frozenset(
    {"win", "loss", "concede", "opponent_concede", "opponent_disconnect", "unknown"}
)
WINNING_OUTCOMES: frozenset[str] = frozenset({"win", "opponent_concede", "opponent_disconnect"})
LOSING_OUTCOMES: frozenset[str] = frozenset({"loss", "concede"})
UNKNOWN = "unknown"

# Invisible characters that have no business in a handle. U+200B is the
# one actually observed, in both logs (T2); the rest are its relatives.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))


def normalize_handle(handle: str) -> str:
    """Remove invisible characters and surrounding whitespace from a handle.

    WHY THIS FUNCTION EXISTS: trap T2. "Name\\u200b#1234" and "Name#1234"
    look identical on screen and compare unequal in code. Every handle --
    from either log and from config -- goes through here first.
    """
    return handle.translate(_ZERO_WIDTH).strip()
