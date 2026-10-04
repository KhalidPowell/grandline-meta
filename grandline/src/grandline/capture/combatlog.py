"""
WHAT THIS FILE DOES
Turns one OPTCGSim combat log -- one file per game -- into a CombatGame:
who played, which leaders (with names), who went first, when it ended,
and the result if the file records one.

WHY IT EXISTS
PRD v2.3 makes combat logs the record. Unlike Player.log they survive a
relaunch, are named with the time the game ended, and name both leaders.
Their one weakness is trap T4: some files stop before the result. This
module reports that honestly (outcome=None) rather than guessing; the
watcher then asks the player log.

WHAT A COMBAT LOG LOOKS LIKE
    Waiting for a Connection with Room ID:WF9BCF       (I hosted)
    Attempting to connect to W6MF7PJ                    (I joined)
    Me\\u200b#1234 Has Connected
    Version is 1.43a.3
    [Me\\u200b#1234] Leader is Rocks D. Xebec [<mark><link="OP17-039">OP17-039</link></mark>]
    [Opponent\\u200b#5678] Leader is Kaido [...OP17-058...]
    [Me\\u200b#1234] Draw 1 Don                  first to draw 1 DON went first
    [X] Life: 3                                         life totals, printed now and then
    [X] Shiki [...] attacking Kaido [...]
    Kaido [...] hit for 1 damage                        a leader took damage
    [Opponent\\u200b#5678] Concedes!                       the result, when present

HOW A RESULT IS READ -- the first of these to happen ends the game:
    [me] Concedes!                 -> concede            (loss)
    [them] Concedes!               -> opponent_concede   (win)
    Opponent Has Disconnected!     -> opponent_disconnect (win)
    a leader hit while at Life 0   -> loss if mine, win if theirs
Whose leader was hit is taken from the attack line before it, NOT from
the card id: in a mirror match both leaders have the same id.

RULES FOR THIS FILE
parse_combat_log is pure (lines in, game out). read_combat_log is the
only function that touches disk, and it opens the file read-only (D6).
"""

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from grandline.capture.outcomes import Outcome, normalize_handle

# File names are the local time the game ended: 2026-10-02T11.53.41.log
FILENAME_TIME = "%Y-%m-%dT%H.%M.%S"

_ROOM = re.compile(r"^(?:Waiting for a Connection with Room ID:|Attempting to connect to )\s*(?P<room>\S+)")
_VERSION = re.compile(r"^Version is (?P<version>\S+)")
_LEADER = re.compile(r"^\[(?P<handle>[^\]]+)\] Leader is (?P<name>.+?) \[(?P<rest>.*)\]\s*$")
_CARD_ID = re.compile(r"\b([A-Z]+\d*-\d+)\b")
_FIRST_DON = re.compile(r"^\[(?P<handle>[^\]]+)\] Draw 1 Don\s*$")
_LIFE = re.compile(r"^\[(?P<handle>[^\]]+)\] Life: (?P<life>\d+)\s*$")
_ATTACK = re.compile(r"^\[(?P<handle>[^\]]+)\] .+ attacking .+")
_HIT = re.compile(r"^(?P<target>.+?) hit for (?P<damage>\d+) damage\s*$")
_CONCEDE = re.compile(r"^\[(?P<handle>[^\]]+)\] Concedes!\s*$")
_DISCONNECT = "Opponent Has Disconnected!"


@dataclass(frozen=True, slots=True)
class CombatGame:
    """One game, from my point of view.

    WHY THIS CLASS EXISTS: it is the contract between the combat-log
    reader, the watcher and the store. `outcome` may be None (trap T4);
    the watcher resolves that before storing.
    """

    source_file: str   # file name stem, e.g. "2026-10-02T11.53.41"
    ended_at: datetime  # from the file name, local time, timezone-aware
    room_id: str | None
    sim_version: str | None
    my_handle: str
    my_leader: str
    my_leader_name: str
    opponent_handle: str
    opponent_leader: str
    opponent_leader_name: str
    went_first: bool | None  # None if the log never shows the first DON draw
    outcome: Outcome | None  # None = this file has no verdict (T4)

    @property
    def fingerprint(self) -> str:
        """Stable identity (D2): the file it came from plus its room.

        WHY NOT HASH THE CONTENT: the same game can be saved twice (the
        AutoSaved copy and a manual "Download Combat Log"). Only the
        AutoSaved folder is read, and each file there is one game.
        """
        key = f"{self.source_file}|{self.room_id or ''}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


@dataclass(slots=True)
class CombatParse:
    """What reading one file produced. A game, or a reason there isn't one."""

    game: CombatGame | None
    warnings: list[str] = field(default_factory=list)


def ended_at_from_name(stem: str) -> datetime | None:
    """The file name is the local time the game ended; make it timezone-aware.

    WHY THIS FUNCTION EXISTS: trap T4 is solved by the file name, so the
    parsing of it deserves one tested place. `.astimezone()` on a naive
    time attaches this machine's local timezone.
    """
    try:
        return datetime.strptime(stem, FILENAME_TIME).astimezone()
    except ValueError:
        return None


def parse_combat_log(lines: Iterable[str], source_file: str, my_handle: str) -> CombatParse:
    """Read one combat log. Pure: no disk, no clock."""
    me = normalize_handle(my_handle)
    ended_at = ended_at_from_name(source_file)
    if ended_at is None:
        return CombatParse(None, [f"{source_file}: name is not a timestamp; skipped"])

    room = version = first = None
    leaders: dict[str, tuple[str, str]] = {}   # handle -> (card id, name)
    life: dict[str, int] = {}
    last_attacker: str | None = None
    outcome: Outcome | None = None

    for raw in lines:
        line = raw.rstrip("\r\n")
        if outcome is not None:
            break  # the first decisive event ends the game

        if room is None and (m := _ROOM.match(line)):
            room = m["room"]
        elif version is None and (m := _VERSION.match(line)):
            version = m["version"]
        elif m := _LEADER.match(line):
            ids = _CARD_ID.findall(m["rest"])
            if ids:
                leaders[normalize_handle(m["handle"])] = (ids[0], m["name"].strip())
        elif first is None and (m := _FIRST_DON.match(line)):
            first = normalize_handle(m["handle"])
        elif m := _LIFE.match(line):
            life[normalize_handle(m["handle"])] = int(m["life"])
        elif m := _CONCEDE.match(line):
            outcome = "concede" if normalize_handle(m["handle"]) == me else "opponent_concede"
        elif line.strip() == _DISCONNECT:
            outcome = "opponent_disconnect"
        elif m := _HIT.match(line):
            outcome = _lethal(m["target"], leaders, life, last_attacker, int(m["damage"]), me)
        elif m := _ATTACK.match(line):
            last_attacker = normalize_handle(m["handle"])

    if me not in leaders:
        return CombatParse(None, [f"{source_file}: handle '{me}' has no leader line; skipped (check config)"])
    others = [h for h in leaders if h != me]
    if len(others) != 1:
        return CombatParse(None, [f"{source_file}: expected 1 opponent, found {len(others)}; skipped"])
    opp = others[0]

    game = CombatGame(
        source_file=source_file,
        ended_at=ended_at,
        room_id=room,
        sim_version=version,
        my_handle=me,
        my_leader=leaders[me][0],
        my_leader_name=leaders[me][1],
        opponent_handle=opp,
        opponent_leader=leaders[opp][0],
        opponent_leader_name=leaders[opp][1],
        went_first=None if first is None else first == me,
        outcome=outcome,
    )
    return CombatParse(game)


def _lethal(
    target: str,
    leaders: dict[str, tuple[str, str]],
    life: dict[str, int],
    attacker: str | None,
    damage: int,
    me: str,
) -> Outcome | None:
    """Was this hit the last one? Returns the outcome if so, else None.

    WHY THIS FUNCTION EXISTS: a hit on a leader with no life left ends
    the game -- but only a hit on a LEADER, and only the right one.
      - Hits on characters are ignored: the target's card id must be a
        leader's.
      - Whose leader: the defender of the most recent attack. Card ids
        can't say, because a mirror match has the same id on both sides.
      - Life is tracked from the last "Life: N" line, minus hits since.
        If it is unknown, this never declares lethal -- unknown beats wrong.
    """
    ids = _CARD_ID.findall(target)
    if not ids or ids[0] not in {cid for cid, _ in leaders.values()}:
        return None
    defenders = [h for h in leaders if h != attacker] if attacker in leaders else [
        h for h, (cid, _) in leaders.items() if cid == ids[0]
    ]
    if len(defenders) != 1:
        return None  # can't tell whose leader -- don't guess
    defender = defenders[0]
    before = life.get(defender)
    if before is None:
        return None
    if before == 0:
        return "loss" if defender == me else "win"
    life[defender] = max(0, before - damage)
    return None


def read_combat_log(path: Path, my_handle: str) -> CombatParse:
    """Read a combat log from disk, read-only (D6), and parse it."""
    with open(path, mode="r", encoding="utf-8", errors="replace", newline="\n") as f:
        lines = f.readlines()  # file closed on exit: never held open
    return parse_combat_log(lines, path.stem, my_handle)
