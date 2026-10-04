"""
WHAT THIS FILE DOES
Makes `grandline.capture` a package and re-exports its public names.

WHY IT EXISTS
Capture is the product's hard part: getting every game into the store.
Since PRD v2.3 the record comes from the sim's combat logs (one file per
game, combatlog.py); the player log only fills in missing results
(playerlog.py). store.py keeps games permanently; watcher.py does it all
unattended and is run as a command, so it isn't re-exported here.
"""

from grandline.capture.combatlog import CombatGame, parse_combat_log, read_combat_log
from grandline.capture.outcomes import (
    LOSING_OUTCOMES,
    OUTCOMES,
    UNKNOWN,
    WINNING_OUTCOMES,
    Outcome,
    normalize_handle,
)
from grandline.capture.playerlog import PlayerLog, parse_player_log, read_player_log
from grandline.capture.store import (
    Record,
    connect,
    count_games,
    insert_game,
    record,
)

__all__ = [
    "LOSING_OUTCOMES",
    "OUTCOMES",
    "UNKNOWN",
    "WINNING_OUTCOMES",
    "CombatGame",
    "Outcome",
    "PlayerLog",
    "Record",
    "connect",
    "count_games",
    "insert_game",
    "normalize_handle",
    "parse_combat_log",
    "parse_player_log",
    "read_combat_log",
    "read_player_log",
    "record",
]
