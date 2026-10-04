"""
WHAT THIS FILE DOES
Proves `capture/store.py` keeps every game exactly once, never lets a
stored game change, counts the record the way D3 says, and upgrades old
databases in place.

THE TESTS THAT MATTER MOST
  - test_storing_the_same_game_twice_keeps_one_row        (D2)
  - test_record_puts_every_outcome_in_the_right_column    (D3, incl. disconnect and unknown)
  - test_stored_games_cannot_be_updated_or_deleted        (append-only)

RUN IT WITH:  pytest tests/test_store.py
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from fakelogs import combat_log
from grandline.capture.combatlog import parse_combat_log
from grandline.capture.store import (
    SCHEMA_VERSION,
    connect,
    count_games,
    heartbeat,
    insert_game,
    last_capture,
    record,
    stored_rooms,
    stored_sources,
)

T = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def game(n: int = 0, **kw):
    """A parsed game; n picks a distinct file name (so a distinct game)."""
    stem = f"2026-10-02T11.{n:02d}.00"
    return parse_combat_log(combat_log(**kw), stem, "Tester#1111").game


def store(conn, outcomes):
    for n, o in enumerate(outcomes):
        insert_game(conn, game(n), o, "combat_log", T)


# --- Idempotent insert (D2) -------------------------------------------


def test_storing_the_same_game_twice_keeps_one_row(conn):
    # CHECKS: a re-scan of the folder is harmless.
    # EXPECT: True then False; one row.
    g = game()
    assert insert_game(conn, g, "win", "combat_log", T) is True
    assert insert_game(conn, g, "win", "combat_log", T) is False
    assert count_games(conn) == 1


def test_identical_looking_games_in_different_files_are_both_stored(conn):
    # CHECKS: same opponent, leaders and result -- but two files, two games.
    # EXPECT: 2 rows.
    store(conn, ["concede", "concede"])
    assert count_games(conn) == 2


def test_stored_sources_and_rooms_are_listed(conn):
    # CHECKS: what the watcher uses to skip files and spot orphan results.
    # EXPECT: the stem and room of the stored game.
    insert_game(conn, game(room="WXYZ"), "win", "combat_log", T)
    assert stored_sources(conn) == {"2026-10-02T11.00.00"}
    assert stored_rooms(conn) == {"WXYZ"}


def test_store_survives_reopening(tmp_path):
    # CHECKS: data is on disk, and reopening doesn't rerun migrations.
    # EXPECT: 1 row after close and reopen; a repeat insert adds none.
    path = tmp_path / "grandline.db"
    c = connect(path)
    insert_game(c, game(), "win", "combat_log", T)
    c.close()
    c = connect(path)
    assert insert_game(c, game(), "win", "combat_log", T) is False
    assert count_games(c) == 1
    c.close()


# --- What the database refuses ----------------------------------------


def test_stored_games_cannot_be_updated_or_deleted(conn):
    # CHECKS: append-only is enforced by the schema, not by politeness.
    # EXPECT: UPDATE and DELETE both raise; the row is unchanged.
    store(conn, ["loss"])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE games SET outcome = 'win'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM games")
    assert str(record(conn)) == "0-1"


def test_unknown_outcome_value_is_rejected(conn):
    # CHECKS: a typo can't reach the database, from Python or raw SQL.
    # EXPECT: ValueError in Python; IntegrityError from the CHECK.
    with pytest.raises(ValueError):
        insert_game(conn, game(), "draw", "combat_log", T)
    with pytest.raises(ValueError):
        insert_game(conn, game(), "win", "a_hunch", T)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO games (fingerprint, source_file, ended_at, captured_at, my_handle,"
            " my_leader, my_leader_name, opponent_handle, opponent_leader,"
            " opponent_leader_name, outcome, outcome_source)"
            " VALUES ('x','s','t','t','a','b','c','d','e','f','draw','none')"
        )


def test_naive_times_are_rejected(conn):
    # CHECKS: every stored time carries its timezone.
    # EXPECT: ValueError.
    with pytest.raises(ValueError):
        insert_game(conn, game(), "win", "combat_log", datetime(2026, 10, 4, 18, 0))


def test_times_are_stored_as_utc(conn):
    # CHECKS: SQL compares times as text, so one offset everywhere.
    # EXPECT: a +02:00 capture time comes back as the same instant in UTC.
    plus_two = timezone(timedelta(hours=2))
    insert_game(conn, game(), "win", "combat_log", datetime(2026, 10, 4, 20, 0, tzinfo=plus_two))
    (stored,) = conn.execute("SELECT captured_at FROM games").fetchone()
    assert stored == "2026-10-04T18:00:00+00:00"
    assert last_capture(conn) == T


def test_went_first_is_stored_including_unknown(conn):
    # CHECKS: F12's column round-trips, and None stays None.
    # EXPECT: 1, 0 and NULL.
    insert_game(conn, game(0, i_go_first=True), "win", "combat_log", T)
    insert_game(conn, game(1, i_go_first=False), "win", "combat_log", T)
    g = game(2)
    g = type(g)(**{**{f: getattr(g, f) for f in g.__slots__}, "went_first": None})
    insert_game(conn, g, "win", "combat_log", T)
    values = [r[0] for r in conn.execute("SELECT went_first FROM games ORDER BY id")]
    assert values == [1, 0, None]


# --- The record (F4, D3) ----------------------------------------------


def test_record_puts_every_outcome_in_the_right_column(conn):
    # CHECKS: all six outcomes together.
    # EXPECT: win + opponent_concede + opponent_disconnect = 3 wins;
    #         loss + concede = 2 losses; unknown in neither.
    store(conn, ["win", "opponent_concede", "opponent_disconnect", "loss", "concede", "unknown"])
    r = record(conn)
    assert (r.wins, r.losses, r.games, r.unknown) == (3, 2, 5, 1)
    assert (r.concedes, r.opponent_concedes, r.opponent_disconnects) == (1, 1, 1)
    assert str(r) == "3-2"
    assert r.win_rate == pytest.approx(0.6)
    low, high = r.interval
    assert low < 0.6 < high


def test_opponent_disconnect_counts_as_a_win(conn):
    # CHECKS: the owner's 4 Oct ruling.
    # EXPECT: 1-0.
    store(conn, ["opponent_disconnect"])
    assert str(record(conn)) == "1-0"


def test_unknown_games_do_not_move_the_win_rate(conn):
    # CHECKS: a game with no verdict is never guessed into a column.
    # EXPECT: 1-0 and 100%, with 2 unknown alongside.
    store(conn, ["win", "unknown", "unknown"])
    r = record(conn)
    assert (str(r), r.win_rate, r.unknown) == ("1-0", 1.0, 2)


def test_record_filters(conn):
    # CHECKS: the F5/F6/F12 building blocks.
    # EXPECT: each filter narrows to the right games.
    insert_game(conn, game(0, opp_leader=("OP17-020", "Shanks"), i_go_first=True), "win", "combat_log", T)
    insert_game(conn, game(1, opp_leader=("OP17-058", "Kaido"), i_go_first=False), "loss", "combat_log", T)
    assert str(record(conn, opponent_leader="OP17-020")) == "1-0"
    assert str(record(conn, went_first=0)) == "0-1"
    assert str(record(conn, my_leader="OP17-039")) == "1-1"


def test_record_rejects_unknown_filter_columns(conn):
    # CHECKS: filter names can't be used to inject SQL.
    # EXPECT: ValueError.
    with pytest.raises(ValueError):
        record(conn, **{"1=1; DROP TABLE games; --": "x"})


def test_empty_store_has_an_empty_record(conn):
    r = record(conn)
    assert (r.games, r.unknown, r.interval) == (0, 0, (0.0, 0.0))


# --- Schema -----------------------------------------------------------


def test_a_stage_3_database_is_upgraded_in_place(tmp_path):
    # CHECKS: a v1 database (Player.log matches) gains the heartbeat and
    #         games tables, and its old rows are kept, not deleted.
    # EXPECT: version is current; the old row is still in `matches`.
    path = tmp_path / "grandline.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE matches (id INTEGER PRIMARY KEY, fingerprint TEXT, captured_at TEXT,"
        " sim_version TEXT, my_handle TEXT, my_leader TEXT, opponent_handle TEXT,"
        " opponent_leader TEXT, outcome TEXT);"
        "INSERT INTO matches VALUES (1,'f','2026-10-04T18:00:00+00:00','1.43a','a','b','c','d','win');"
        "PRAGMA user_version = 1;"
    )
    old.close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    assert count_games(conn) == 0
    assert heartbeat(conn) is None
    conn.close()


def test_newer_schema_version_refuses_to_open(tmp_path):
    # CHECKS: old code can't silently write into a newer database.
    # EXPECT: RuntimeError naming the version.
    path = tmp_path / "grandline.db"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(RuntimeError, match="99"):
        connect(path)
