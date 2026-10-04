"""
WHAT THIS FILE DOES
Turns the simulator's Player.log into a list of completed matches: my
leader, the opponent's handle and leader, and the outcome. This is
feature F2 in the v2 PRD.

WHY IT EXISTS
Player.log is the only record the simulator keeps, and it is destroyed
on the next launch. Everything the product shows depends on this file
being read correctly, and every trap in it fails silently -- the parser
produces a plausible-looking record that is wrong. So the four known
traps are handled here, once, in one tested place:

  T1  Headers outnumber results. A header with no result after it is a
      re-sync or an unfinished game, not a match. Pair each result with
      the nearest PRECEDING header; discard headers that never get one.
  T2  Every handle contains an invisible zero-width space before the
      '#'. Strip it, or one opponent becomes two people.
  T3  Seat 1 is not reliably me. Identify me by my configured handle.
  T4  The log has no timestamps. This parser does not invent one --
      the capture time is stamped by whoever calls it (the watcher).

WHAT A MATCH LOOKS LIKE IN THE LOG
    [ReplaySync] RZ1|HDR|1.43a|2|RZ1            header: sim version
    [ReplaySync] RZ1|PLY|1|Name\\u200b#1234|OP17-039   seat 1
    [ReplaySync] RZ1|PLY|2|Other\\u200b#5678|EB03-001  seat 2
    ... ~1,400 lines of gameplay ...
    OPB_RESULT|win                               outcome, from MY view

RULES FOR THIS FILE
- MatchParser and parse_lines are pure: no file, no clock, no database.
  They take lines and return matches, which is what makes them testable.
- parse_file is the only function that touches disk, and it opens the
  log READ-ONLY (D6). The log belongs to the simulator; writing to it
  could break the game.
"""

import hashlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# --- What the parser recognises ---------------------------------------

# Every outcome string seen in real logs. "opponent_concede" is NOT in the
# v2 PRD -- it was found in Player-prev.log on 4 Oct 2026, after the PRD
# was written. Following the logic of D3, it is a win that keeps its own
# tag, so "games where they scooped" can be filtered later (F9).
Outcome = Literal["win", "loss", "concede", "opponent_concede"]
OUTCOMES: frozenset[str] = frozenset({"win", "loss", "concede", "opponent_concede"})

# Which outcomes count in which column. D3: a concede is a loss.
WINNING_OUTCOMES: frozenset[str] = frozenset({"win", "opponent_concede"})
LOSING_OUTCOMES: frozenset[str] = frozenset({"loss", "concede"})

# Sim versions whose log format has been checked by hand against a real
# file. A match from any other version is still captured -- the log is
# destroyed on next launch, so skipping it would lose it forever -- but
# it carries a warning so a format change gets noticed (PRD risk table).
VERIFIED_VERSIONS: frozenset[str] = frozenset({"1.43a"})

# Invisible characters that have no business in a handle. U+200B is the
# one actually observed (T2); the others are its close relatives and
# cost nothing to strip as well.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))

# The three line shapes that matter. Everything else in the file is
# gameplay or Unity engine noise, and is only used for the fingerprint.
_HEADER = re.compile(r"^\[ReplaySync\] [^|]+\|HDR\|(?P<version>[^|]+)\|")
_PLAYER = re.compile(
    r"^\[ReplaySync\] [^|]+\|PLY\|(?P<seat>\d+)\|(?P<handle>[^|]+)\|(?P<leader>[^|]+)$"
)
_RESULT = re.compile(r"^OPB_RESULT\|(?P<outcome>\S+)$")


# --- What the parser produces -----------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedMatch:
    """One completed match, from my point of view.

    WHY THIS CLASS EXISTS: it is the contract between the parser and the
    store (stage 3). Note what is missing on purpose: there is no date,
    because the log has none (T4). The watcher stamps capture time.
    """

    sim_version: str
    my_handle: str
    my_leader: str  # card id, e.g. "OP17-039"; names are resolved later (F7)
    opponent_handle: str
    opponent_leader: str
    outcome: Outcome

    # Stable identity for this match, so re-reading the same log can
    # never create a duplicate (D2). See _Block.fingerprint for how.
    fingerprint: str

    # 1-based line numbers in the source, purely for debugging -- they
    # are NOT identity, because the log restarts at line 1 every launch.
    header_line: int
    result_line: int

    @property
    def is_win(self) -> bool:
        """WHY THIS PROPERTY EXISTS: so no caller re-derives the D3 mapping."""
        return self.outcome in WINNING_OUTCOMES


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """Something in the log the parser saw and could not trust.

    WHY THIS CLASS EXISTS: a skipped block must be visible, never
    silent. A tracker that quietly drops games is worse than none.
    """

    line: int
    message: str


@dataclass(slots=True)
class ParseResult:
    """Everything one pass over a log produced.

    WHY THIS CLASS EXISTS: matches alone would hide what was thrown away.
    The counts let a caller (and a test) check T1 is behaving.
    """

    matches: list[ParsedMatch] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)
    orphan_headers: int = 0  # T1: headers discarded with no result


# --- The parser -------------------------------------------------------


def normalize_handle(handle: str) -> str:
    """Remove invisible characters and surrounding whitespace from a handle.

    WHY THIS FUNCTION EXISTS: trap T2. "Name\\u200b#1234" and "Name#1234"
    look identical on screen and compare unequal in code. Every handle --
    from the log and from config -- goes through here before comparison.
    """
    return handle.translate(_ZERO_WIDTH).strip()


@dataclass(slots=True)
class _Block:
    """A header and everything after it, while waiting for a result."""

    header_line: int
    version: str
    seats: dict[int, tuple[str, str]] = field(default_factory=dict)  # seat -> (handle, leader)
    _hash: "hashlib._Hash" = field(default_factory=hashlib.sha256)

    def add(self, line: str) -> None:
        self._hash.update(line.encode("utf-8"))
        self._hash.update(b"\n")

    def fingerprint(self) -> str:
        """A hash of every line from header to result, inclusive.

        WHY THE WHOLE BLOCK, NOT JUST THE HEADER: two real consecutive
        matches in Player-prev.log share opponent, both leaders AND
        outcome. A fingerprint built from those fields would merge two
        games into one. The gameplay between header and result -- every
        draw, every play -- differs between any two real games, and is
        identical every time the same file is re-read. That is exactly
        the property D2 needs.
        """
        return self._hash.hexdigest()[:32]


class MatchParser:
    """Reads log lines one at a time and emits each match as it completes.

    WHY THIS CLASS EXISTS: stage 4's watcher sees the log a few lines at
    a time as the sim writes it. Making the parser incremental now means
    the watcher reuses this exact tested code instead of a second parser.
    For a whole file at once, use parse_lines or parse_file.
    """

    def __init__(self, my_handle: str) -> None:
        self.my_handle = normalize_handle(my_handle)
        if not self.my_handle:
            raise ValueError("my_handle must not be empty")
        self.result = ParseResult()
        self._block: _Block | None = None
        self._line_no = 0

    def feed(self, raw_line: str) -> ParsedMatch | None:
        """Process one line. Returns a match if this line completed one.

        Lines must be complete -- the caller is responsible for not
        feeding half a line that the sim is still writing.
        """
        self._line_no += 1
        # rstrip only line endings, so the fingerprint is the same whether
        # the file was read with Windows or Unix line endings.
        line = raw_line.rstrip("\r\n")

        if m := _HEADER.match(line):
            return self._on_header(line, m.group("version"))
        if self._block is not None:
            self._block.add(line)
        if m := _PLAYER.match(line):
            self._on_player(m)
            return None
        if m := _RESULT.match(line):
            return self._on_result(m.group("outcome"))
        return None

    def finish(self) -> ParseResult:
        """Close out the pass. A block still open here never got a result.

        WHY THIS IS AN ORPHAN AND NOT AN ERROR: the sample log ends on
        exactly this -- a re-sync header after the last game (T1).
        """
        if self._block is not None:
            self.result.orphan_headers += 1
            self._block = None
        return self.result

    # -- one handler per line shape --

    def _on_header(self, line: str, version: str) -> None:
        # T1: a new header while one is open means the open one never
        # finished. Throw it away; the new header is the one that counts.
        if self._block is not None:
            self.result.orphan_headers += 1
        self._block = _Block(header_line=self._line_no, version=version)
        self._block.add(line)
        return None

    def _on_player(self, m: re.Match[str]) -> None:
        if self._block is None:
            self._warn("player line with no header before it; ignored")
            return
        seat = int(m.group("seat"))
        handle = normalize_handle(m.group("handle"))
        self._block.seats[seat] = (handle, m.group("leader").strip())

    def _on_result(self, outcome: str) -> ParsedMatch | None:
        block, self._block = self._block, None
        if block is None:
            self._warn(f"result '{outcome}' with no header before it; skipped")
            return None
        if outcome not in OUTCOMES:
            # Unknown outcome = possible format change. Do not guess
            # which column it belongs in.
            self._warn(f"unrecognised outcome '{outcome}'; match skipped")
            return None
        if len(block.seats) != 2:
            self._warn(f"expected 2 players, found {len(block.seats)}; match skipped")
            return None

        # T3: find me by handle, never by seat number.
        mine = [s for s, (h, _) in block.seats.items() if h == self.my_handle]
        if len(mine) != 1:
            self._warn(
                f"handle '{self.my_handle}' not found exactly once in this match "
                "(check config); match skipped"
            )
            return None
        my_seat = mine[0]
        (opp_seat,) = (s for s in block.seats if s != my_seat)

        if block.version not in VERIFIED_VERSIONS:
            self._warn(
                f"sim version {block.version} has not been verified; "
                "match captured, but check the log format"
            )

        match = ParsedMatch(
            sim_version=block.version,
            my_handle=block.seats[my_seat][0],
            my_leader=block.seats[my_seat][1],
            opponent_handle=block.seats[opp_seat][0],
            opponent_leader=block.seats[opp_seat][1],
            outcome=outcome,  # type: ignore[arg-type]  # checked against OUTCOMES above
            fingerprint=block.fingerprint(),
            header_line=block.header_line,
            result_line=self._line_no,
        )
        self.result.matches.append(match)
        return match

    def _warn(self, message: str) -> None:
        self.result.warnings.append(ParseWarning(self._line_no, message))


# --- Whole-file entry points ------------------------------------------


def parse_lines(lines: Iterable[str], my_handle: str) -> ParseResult:
    """Parse a complete log already in memory.

    WHY THIS FUNCTION EXISTS: the pure, one-call version, for tests and
    for re-parsing a saved log offline (stage 2).
    """
    parser = MatchParser(my_handle)
    for line in lines:
        parser.feed(line)
    return parser.finish()


def read_log_lines(path: Path) -> Iterator[str]:
    """Yield lines from a log file, opened strictly read-only.

    WHY THIS FUNCTION EXISTS: decision D6, enforced in one place. Mode
    "r" cannot write, truncate or create. errors="replace" means a single
    corrupt byte costs one garbled character, not the whole session.
    newline="\\n" splits on "\\n" only -- the same rule as tail.py -- so a
    file read here and the same file tailed live produce identical lines,
    and therefore identical fingerprints.
    """
    with open(path, mode="r", encoding="utf-8", errors="replace", newline="\n") as f:
        yield from f


def parse_file(path: Path | str, my_handle: str) -> ParseResult:
    """Parse a log file on disk. Never writes to it (D6)."""
    return parse_lines(read_log_lines(Path(path)), my_handle)
