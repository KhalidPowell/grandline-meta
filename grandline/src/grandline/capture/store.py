"""
WHAT THIS FILE DOES
The game store (F3): one SQLite table, one row per game, plus the
win-loss record (F4) read back out of it and the watcher's heartbeat.

WHY IT EXISTS
Decision D2: the logs are feeds, not the record. The moment a game is
read it has to land somewhere permanent. This file is that somewhere.

THREE GUARANTEES, EACH ENFORCED BY THE DATABASE ITSELF
  1. No duplicates. `fingerprint` is UNIQUE, and inserts use
     ON CONFLICT DO NOTHING. Re-scanning the same files is harmless.
  2. Append-only. Triggers reject every UPDATE and DELETE. The record
     is a history; correcting it means adding, never rewriting.
  3. Only known outcomes. A CHECK constraint lists them, so a bad value
     fails at insert time instead of skewing a win rate.
Enforcing these in the schema rather than in Python means no future
code path -- a script, a REPL session, a bug -- can get around them.

WHY STDLIB sqlite3 AND NOT SQLALCHEMY
One table, a handful of queries, one user. sqlite3 ships with Python,
adds no dependency, and keeps the SQL visible.

RULE FOR THIS FILE
No clock. Every time is passed in by the caller (the watcher), which
keeps every function here testable with a fixed time.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from grandline.capture.combatlog import CombatGame
from grandline.capture.outcomes import (
    LOSING_OUTCOMES,
    OUTCOMES,
    UNKNOWN,
    WINNING_OUTCOMES,
)
from grandline.stats.intervals import wilson_interval
from grandline.stats.rates import win_rate

# --- Schema -----------------------------------------------------------
# SQLite stores the schema version in the file header (PRAGMA
# user_version). To change the schema: append a step to _MIGRATIONS.
# Never edit a step that has shipped -- a database out there has already
# run it, and editing it would make two databases with the same version
# number disagree about their shape. That's why each step below spells
# out its own outcome list instead of reading the current constants.

# v1 (stage 3): matches parsed from Player.log. RETIRED in PRD v2.3 --
# its opponent/leader columns could be wrong (trap T1). Kept so an old
# database still opens; nothing writes to it any more.
_V1_MATCHES = """
CREATE TABLE matches (
    id               INTEGER PRIMARY KEY,
    fingerprint      TEXT NOT NULL UNIQUE,
    captured_at      TEXT NOT NULL,
    sim_version      TEXT NOT NULL,
    my_handle        TEXT NOT NULL,
    my_leader        TEXT NOT NULL,
    opponent_handle  TEXT NOT NULL,
    opponent_leader  TEXT NOT NULL,
    outcome          TEXT NOT NULL CHECK (outcome IN
        ('concede', 'loss', 'opponent_concede', 'win'))
);
CREATE INDEX matches_my_leader       ON matches (my_leader);
CREATE INDEX matches_opponent_leader ON matches (opponent_leader);
CREATE INDEX matches_captured_at     ON matches (captured_at);
CREATE TRIGGER matches_no_update BEFORE UPDATE ON matches
BEGIN SELECT RAISE(ABORT, 'matches is append-only'); END;
CREATE TRIGGER matches_no_delete BEFORE DELETE ON matches
BEGIN SELECT RAISE(ABORT, 'matches is append-only'); END;
"""

# v2 (stage 4): one row saying the watcher is alive. Deliberately NOT
# append-only -- it is a status light, not history.
_V2_HEARTBEAT = """
CREATE TABLE watcher_heartbeat (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    started_at    TEXT NOT NULL,
    last_poll_at  TEXT NOT NULL,
    log_path      TEXT NOT NULL
);
"""

# v3 (PRD v2.3): games read from combat logs -- the record from now on.
_V3_GAMES = """
CREATE TABLE games (
    id                    INTEGER PRIMARY KEY,
    fingerprint           TEXT NOT NULL UNIQUE,  -- D2: file + room
    source_file           TEXT NOT NULL,         -- combat log name stem
    ended_at              TEXT NOT NULL,         -- UTC; from the file name
    captured_at           TEXT NOT NULL,         -- UTC; when the watcher stored it
    room_id               TEXT,
    sim_version           TEXT,
    my_handle             TEXT NOT NULL,
    my_leader             TEXT NOT NULL,
    my_leader_name        TEXT NOT NULL,
    opponent_handle       TEXT NOT NULL,
    opponent_leader       TEXT NOT NULL,
    opponent_leader_name  TEXT NOT NULL,
    went_first            INTEGER CHECK (went_first IN (0, 1)),  -- NULL = unknown
    outcome               TEXT NOT NULL CHECK (outcome IN
        ('concede', 'loss', 'opponent_concede', 'opponent_disconnect', 'unknown', 'win')),
    outcome_source        TEXT NOT NULL CHECK (outcome_source IN
        ('combat_log', 'manual_combat_log', 'player_log', 'none'))
);
CREATE INDEX games_ended_at        ON games (ended_at);
CREATE INDEX games_my_leader       ON games (my_leader);
CREATE INDEX games_opponent_leader ON games (opponent_leader);
CREATE TRIGGER games_no_update BEFORE UPDATE ON games
BEGIN SELECT RAISE(ABORT, 'games is append-only'); END;
CREATE TRIGGER games_no_delete BEFORE DELETE ON games
BEGIN SELECT RAISE(ABORT, 'games is append-only'); END;
"""

# Step N takes a database from version N-1 to version N.
_MIGRATIONS: list[str] = [_V1_MATCHES, _V2_HEARTBEAT, _V3_GAMES]
SCHEMA_VERSION = len(_MIGRATIONS)

OUTCOME_SOURCES = frozenset({"combat_log", "manual_combat_log", "player_log", "none"})


def _utc(moment: datetime) -> str:
    """Store every time as UTC ISO-8601.

    WHY: times are compared as TEXT in SQL (MAX, ORDER BY). That is only
    correct if every value uses the same offset -- "+00:00" everywhere.
    """
    if moment.tzinfo is None:
        # A naive datetime is ambiguous across DST and machine moves.
        raise ValueError("times must be timezone-aware")
    return moment.astimezone(timezone.utc).isoformat()


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the store, creating or upgrading the schema as needed.

    WHY THIS FUNCTION EXISTS: so nothing else ever opens the database
    without the schema in place. Pass ":memory:" for a throwaway store.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # WAL lets the stage 5 page read while the watcher writes.
    conn.execute("PRAGMA journal_mode = WAL")
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"{path} has schema version {version}; this code only knows up "
            f"to {SCHEMA_VERSION}. Update the code before opening it."
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        conn.executescript(
            f"BEGIN; {_MIGRATIONS[target - 1]} PRAGMA user_version = {target}; COMMIT;"
        )
    return conn


# --- Writing ----------------------------------------------------------


def insert_game(
    conn: sqlite3.Connection,
    game: CombatGame,
    outcome: str,
    outcome_source: str,
    captured_at: datetime,
) -> bool:
    """Store one game. Returns True if new, False if already stored.

    WHY THIS FUNCTION EXISTS: it is the only write path into the store,
    and it is idempotent -- the same game twice is a no-op, not an error.

    `outcome` is passed separately from `game.outcome` because the
    watcher may have filled it from another source (trap T4).
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    if outcome_source not in OUTCOME_SOURCES:
        raise ValueError(f"unknown outcome_source {outcome_source!r}")
    with conn:  # commits on success, rolls back on error
        cur = conn.execute(
            """
            INSERT INTO games (fingerprint, source_file, ended_at, captured_at,
                room_id, sim_version, my_handle, my_leader, my_leader_name,
                opponent_handle, opponent_leader, opponent_leader_name,
                went_first, outcome, outcome_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (fingerprint) DO NOTHING
            """,
            (
                game.fingerprint, game.source_file, _utc(game.ended_at),
                _utc(captured_at), game.room_id, game.sim_version,
                game.my_handle, game.my_leader, game.my_leader_name,
                game.opponent_handle, game.opponent_leader, game.opponent_leader_name,
                None if game.went_first is None else int(game.went_first),
                outcome, outcome_source,
            ),
        )
    return cur.rowcount == 1


def count_games(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]


def stored_sources(conn: sqlite3.Connection) -> set[str]:
    """Combat log file names already stored.

    WHY THIS FUNCTION EXISTS: the watcher skips these without re-reading
    them, so a folder with hundreds of old games costs nothing per poll.
    """
    return {row[0] for row in conn.execute("SELECT source_file FROM games")}


def stored_rooms(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT room_id FROM games WHERE room_id IS NOT NULL")}


# --- The record (F4) --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Record:
    """A win-loss record, with the early endings inside it still visible.

    WHY THIS CLASS EXISTS: D3 in one place. `wins` INCLUDES opponent
    concedes and disconnects; `losses` INCLUDES my concedes. Games with
    no verdict (`unknown`) are in NEITHER -- counted, shown, never guessed.
    """

    wins: int
    losses: int
    concedes: int             # of `losses`: me scooping
    opponent_concedes: int    # of `wins`: them scooping
    opponent_disconnects: int  # of `wins`: them leaving
    unknown: int              # in neither column (trap T4)

    @property
    def games(self) -> int:
        """Decided games -- the win-rate denominator."""
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        """0..1. CAREFUL: 0.0 with no games means "no data" (see rates.py)."""
        return win_rate(self.wins, self.games)

    @property
    def interval(self) -> tuple[float, float]:
        """95% Wilson interval on the win rate."""
        return wilson_interval(self.wins, self.games)

    def __str__(self) -> str:
        """The raw record, e.g. '3-2'. D5 shows it beside the win rate."""
        return f"{self.wins}-{self.losses}"


_FILTERABLE = {"my_leader", "opponent_leader", "opponent_handle", "went_first"}


def record(conn: sqlite3.Connection, **filters: str | int) -> Record:
    """The record, optionally filtered by column.

    WHY THIS FUNCTION EXISTS: the one number the product exists to show
    (F4), and the building block for F5 (`my_leader=`), F6
    (`opponent_leader=`), F11 (`opponent_handle=`) and F12 (`went_first=`).
    """
    if unknown := set(filters) - _FILTERABLE:
        # Column names can't be bound as SQL parameters, so whitelist them.
        raise ValueError(f"cannot filter on {sorted(unknown)}; allowed: {sorted(_FILTERABLE)}")
    where = " AND ".join(f"{col} = ?" for col in filters) or "1"
    counts = dict(
        conn.execute(
            f"SELECT outcome, COUNT(*) FROM games WHERE {where} GROUP BY outcome",
            tuple(filters.values()),
        ).fetchall()
    )
    return Record(
        wins=sum(counts.get(o, 0) for o in WINNING_OUTCOMES),
        losses=sum(counts.get(o, 0) for o in LOSING_OUTCOMES),
        concedes=counts.get("concede", 0),
        opponent_concedes=counts.get("opponent_concede", 0),
        opponent_disconnects=counts.get("opponent_disconnect", 0),
        unknown=counts.get(UNKNOWN, 0),
    )


# --- Is the watcher alive? (PRD risk #1) ------------------------------


@dataclass(frozen=True, slots=True)
class Heartbeat:
    started_at: datetime
    last_poll_at: datetime
    log_path: str


def beat(
    conn: sqlite3.Connection, started_at: datetime, now: datetime, log_path: str
) -> None:
    """Record that the watcher just polled.

    WHY THIS FUNCTION EXISTS: a stale `last_poll_at` is how the page
    tells "no games played" apart from "nothing is listening".
    """
    with conn:
        conn.execute(
            """
            INSERT INTO watcher_heartbeat (id, started_at, last_poll_at, log_path)
            VALUES (1, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                started_at = excluded.started_at,
                last_poll_at = excluded.last_poll_at,
                log_path = excluded.log_path
            """,
            (_utc(started_at), _utc(now), log_path),
        )


def heartbeat(conn: sqlite3.Connection) -> Heartbeat | None:
    """The watcher's last heartbeat, or None if it has never run."""
    row = conn.execute(
        "SELECT started_at, last_poll_at, log_path FROM watcher_heartbeat"
    ).fetchone()
    if row is None:
        return None
    return Heartbeat(
        started_at=datetime.fromisoformat(row[0]),
        last_poll_at=datetime.fromisoformat(row[1]),
        log_path=row[2],
    )


def last_capture(conn: sqlite3.Connection) -> datetime | None:
    """When the most recently stored game was captured, or None."""
    (value,) = conn.execute("SELECT MAX(captured_at) FROM games").fetchone()
    return None if value is None else datetime.fromisoformat(value)


# --- Reading for the page (F5, F6, F8) --------------------------------

# Columns a record can be grouped by, with the name column that goes with
# each. Whitelisted because column names can't be SQL parameters.
_GROUPABLE = {
    "my_leader": "my_leader_name",
    "opponent_leader": "opponent_leader_name",
    "opponent_handle": "opponent_handle",
}


@dataclass(frozen=True, slots=True)
class Group:
    key: str     # e.g. "OP17-058"
    label: str   # e.g. "Kaido" -- the most recently seen name for that key
    record: Record


def records_by(conn: sqlite3.Connection, column: str, **filters: str | int) -> list[Group]:
    """One Record per distinct value of `column`, most-played first.

    WHY THIS FUNCTION EXISTS: F5 (by my leader) and F6 (by opposing
    leader) are the same question grouped differently. Reusing `record()`
    per group keeps D3's win/loss mapping in exactly one place.
    """
    if column not in _GROUPABLE:
        raise ValueError(f"cannot group by {column!r}; allowed: {sorted(_GROUPABLE)}")
    if unknown := set(filters) - _FILTERABLE:
        raise ValueError(f"cannot filter on {sorted(unknown)}; allowed: {sorted(_FILTERABLE)}")
    name_col = _GROUPABLE[column]
    where = " AND ".join(f"{col} = ?" for col in filters) or "1"
    rows = conn.execute(
        f"""
        SELECT {column} AS key,
               (SELECT {name_col} FROM games g2 WHERE g2.{column} = g.{column}
                ORDER BY ended_at DESC LIMIT 1) AS label,
               COUNT(*) AS n, MAX(ended_at) AS latest
        FROM games g WHERE {where}
        GROUP BY {column}
        ORDER BY n DESC, latest DESC
        """,
        tuple(filters.values()),
    ).fetchall()
    return [Group(r["key"], r["label"], record(conn, **filters, **{column: r["key"]}))
            for r in rows]


@dataclass(frozen=True, slots=True)
class StoredGame:
    ended_at: datetime
    my_leader: str
    my_leader_name: str
    opponent_handle: str
    opponent_leader: str
    opponent_leader_name: str
    went_first: bool | None
    outcome: str
    outcome_source: str


def recent_games(conn: sqlite3.Connection, limit: int = 20, **filters: str | int) -> list[StoredGame]:
    """The most recent games, newest first (F8)."""
    if unknown := set(filters) - _FILTERABLE:
        raise ValueError(f"cannot filter on {sorted(unknown)}; allowed: {sorted(_FILTERABLE)}")
    where = " AND ".join(f"{col} = ?" for col in filters) or "1"
    rows = conn.execute(
        f"""
        SELECT ended_at, my_leader, my_leader_name, opponent_handle, opponent_leader,
               opponent_leader_name, went_first, outcome, outcome_source
        FROM games WHERE {where} ORDER BY ended_at DESC, id DESC LIMIT ?
        """,
        (*filters.values(), limit),
    ).fetchall()
    return [
        StoredGame(
            ended_at=datetime.fromisoformat(r["ended_at"]),
            my_leader=r["my_leader"], my_leader_name=r["my_leader_name"],
            opponent_handle=r["opponent_handle"], opponent_leader=r["opponent_leader"],
            opponent_leader_name=r["opponent_leader_name"],
            went_first=None if r["went_first"] is None else bool(r["went_first"]),
            outcome=r["outcome"], outcome_source=r["outcome_source"],
        )
        for r in rows
    ]
