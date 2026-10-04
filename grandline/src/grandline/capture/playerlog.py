"""
WHAT THIS FILE DOES
Reads the two things PRD v2.3 still trusts the player log for:
  1. Each game's result (OPB_RESULT), paired with the room it was played in.
  2. Where the simulator is installed, so the combat logs can be found (D7).

WHY IT EXISTS -- AND WHY IT TAKES SO LITTLE
Player.log's OPB_RESULT line is the sim's own verdict, so it fills the
gap when a combat log stops before the result (trap T4). Nothing else in
the file is trusted: it is destroyed on relaunch, has no timestamps, and
its player header is sometimes written AFTER the result (trap T1 -- on
2 Oct a game was credited to the previous opponent that way). So players
and leaders always come from the combat log; this file contributes only
a result, matched by room.

HOW A RESULT IS PAIRED WITH A ROOM
    match ready on Room ID WF9BCF timed: True    I hosted room WF9BCF
    Joined relay 6MF7PJ                          I joined room W6MF7PJ (T5)
    OPB_RESULT|win                               result of the latest room above
Checked against all six games in the 2 Oct logs: correct every time,
including the one the old header rule got wrong. Lobbies that never
became games leave a room marker with no result, which is harmless.

RULES FOR THIS FILE
The parse functions are pure. read_player_log opens read-only (D6).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

_HOSTED = re.compile(r"^match ready on Room ID (?P<room>\S+)")
_JOINED = re.compile(r"^Joined relay (?P<room>\S+)")
_RESULT = re.compile(r"^OPB_RESULT\|(?P<outcome>\S+)\s*$")
_MONO = re.compile(r"^Mono path\[0\] = '(?P<path>.+)'")

# OPB_RESULT values seen in real logs, mapped onto the shared outcomes.
# Anything else is reported, never guessed into a column.
RESULT_OUTCOMES = {
    "win": "win",
    "loss": "loss",
    "concede": "concede",
    "opponent_concede": "opponent_concede",
}


@dataclass(frozen=True, slots=True)
class RoomResult:
    room_id: str
    outcome: str
    line: int


@dataclass(slots=True)
class PlayerLog:
    """Everything this app takes from one player log."""

    install_root: Path | None = None
    results: list[RoomResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def result_for(self, room_id: str | None) -> str | None:
        """The outcome recorded for this room, if exactly one exists.

        WHY "EXACTLY ONE": if a room somehow holds two results (a rematch
        in the same lobby), there's no reliable way to tell which game
        each belongs to. Unknown beats wrong.
        """
        if not room_id:
            return None
        matches = [r for r in self.results if same_room(room_id, r.room_id)]
        return matches[0].outcome if len(matches) == 1 else None


def same_room(combat_room: str, player_room: str) -> bool:
    """Do these two spellings name the same room?

    WHY THIS FUNCTION EXISTS: trap T5. Hosting, both logs agree
    (WF9BCF / WF9BCF). Joining, the combat log says W6MF7PJ and the
    player log says "Joined relay 6MF7PJ" -- the leading W is dropped.
    """
    return combat_room == player_room or combat_room == "W" + player_room


def parse_player_log(lines: Iterable[str]) -> PlayerLog:
    """Read results-by-room and the install folder. Pure."""
    log = PlayerLog()
    room: str | None = None
    for n, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        if log.install_root is None and (m := _MONO.match(line)):
            log.install_root = install_root_from_mono(m["path"])
        elif m := _HOSTED.match(line) or _JOINED.match(line):
            room = m["room"]
        elif m := _RESULT.match(line):
            outcome = RESULT_OUTCOMES.get(m["outcome"])
            if outcome is None:
                log.warnings.append(f"line {n}: unrecognised result '{m['outcome']}'; ignored")
            elif room is None:
                log.warnings.append(f"line {n}: result with no room before it; ignored")
            else:
                log.results.append(RoomResult(room, outcome, n))
    return log


def install_root_from_mono(mono_path: str) -> Path | None:
    """'C:/.../Builds_Windows/OPTCGSim_Data/Managed' -> 'C:/.../Builds_Windows'.

    WHY THIS FUNCTION EXISTS: decision D7. Unity prints where the game's
    managed code lives on every launch; two levels up is the install
    folder, which holds CombatLogs. After a sim update this line names
    the new folder, so the watcher follows without a config change.
    """
    p = Path(mono_path)
    if p.name != "Managed" or not p.parent.name.endswith("_Data"):
        return None
    return p.parent.parent


def read_player_log(path: Path) -> PlayerLog | None:
    """Read a player log from disk, read-only (D6). None if it isn't there."""
    try:
        with open(path, mode="r", encoding="utf-8", errors="replace", newline="\n") as f:
            lines = f.readlines()  # closed on exit: never held open
    except FileNotFoundError:
        return None
    return parse_player_log(lines)
