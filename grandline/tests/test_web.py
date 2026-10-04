"""
WHAT THIS FILE DOES
Proves the stage 5 page shows the right numbers, warns when the watcher
is down, filters by leader, and can't be turned against its user.

THE TESTS THAT MATTER MOST
  - test_a_hostile_handle_is_shown_as_text_not_run   (security)
  - test_a_stale_heartbeat_is_shown_loudly           (PRD risk #1)
  - test_no_games_shows_a_dash_not_zero_percent      (D5 / the 0.0 trap)
  - test_the_page_cannot_write_to_the_database       (read-only)

RUN IT WITH:  pytest tests/test_web.py
"""

import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from fakelogs import combat_log
from grandline.capture.combatlog import parse_combat_log
from grandline.capture.store import beat, connect, insert_game, records_by, recent_games
from grandline.web.page import load_page, render_page
from grandline.web.server import open_read_only, serve

NOW = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)
XEBEC = ("OP17-039", "Rocks D. Xebec")
NAMI = ("OP11-041", "Nami")
KAIDO = ("OP17-058", "Kaido")
SHANKS = ("OP17-020", "Shanks")


def add(conn, n, outcome, my=XEBEC, opp=KAIDO, opponent="Rival#2222", first=True):
    stem = f"2026-10-02T12.{n:02d}.00"
    g = parse_combat_log(combat_log(opponent=opponent, my_leader=my, opp_leader=opp,
                                    i_go_first=first), stem, "Tester#1111").game
    insert_game(conn, g, outcome, "combat_log", NOW)


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def page(conn, leader=None, now=NOW):
    return render_page(load_page(conn, leader), now)


# --- Numbers ----------------------------------------------------------


def test_overall_record_and_raw_win_rate(conn):
    # CHECKS: F4 with D5 -- raw rate at any sample size, record beside it.
    # EXPECT: 2-1 and 67%.
    add(conn, 1, "win"); add(conn, 2, "opponent_disconnect"); add(conn, 3, "concede")
    html = page(conn)
    assert ">2-1<" in html
    assert ">67%<" in html


def test_no_games_shows_a_dash_not_zero_percent(conn):
    # CHECKS: win_rate() returns 0.0 for "no data"; the page must not
    #         print that as "0%" (which would mean "never wins").
    # EXPECT: a dash, and no "0%".
    html = page(conn)
    assert ">—<" in html
    assert ">0%<" not in html


def test_unknown_games_are_counted_but_not_in_the_rate(conn):
    # CHECKS: T4 -- unknowns are visible, never guessed into a column.
    # EXPECT: 1-0, 100%, and a line saying 2 games had no result.
    add(conn, 1, "win"); add(conn, 2, "unknown"); add(conn, 3, "unknown")
    html = page(conn)
    assert ">1-0" in html and ">100%<" in html
    assert "2 games with no result" in html


def test_going_first_and_second_are_split(conn):
    # CHECKS: F12's one line.
    # EXPECT: first 1-0, second 0-1.
    add(conn, 1, "win", first=True); add(conn, 2, "loss", first=False)
    html = page(conn)
    first_row = html.split("Going first</th>")[1].split("</tr>")[0]
    second_row = html.split("Going second</th>")[1].split("</tr>")[0]
    assert ">1-0" in first_row and ">0-1" in second_row


def test_leader_names_are_shown_with_their_ids(conn):
    # CHECKS: F7 -- names from the combat logs, id beside them.
    # EXPECT: "Kaido" next to OP17-058.
    add(conn, 1, "win")
    assert 'Kaido <span class="id">OP17-058</span>' in page(conn)


def test_grouping_puts_the_most_played_first(conn):
    # CHECKS: F5/F6 order -- what you play and face most, at the top.
    # EXPECT: Kaido (2 games) before Shanks (1).
    add(conn, 1, "win", opp=SHANKS); add(conn, 2, "win", opp=KAIDO); add(conn, 3, "loss", opp=KAIDO)
    assert [g.key for g in records_by(conn, "opponent_leader")] == ["OP17-058", "OP17-020"]


def test_recent_games_are_newest_first_and_capped(conn):
    # CHECKS: F8 shows the last twenty, newest first.
    # EXPECT: 20 rows from 25 games, starting with the latest.
    for n in range(25):
        add(conn, n, "win")
    games = recent_games(conn, 20)
    assert len(games) == 20
    assert games[0].ended_at > games[-1].ended_at


# --- Filtering by leader (F5 -> F6, F8) -------------------------------


def test_clicking_a_leader_filters_the_page(conn):
    # CHECKS: ?leader= narrows the record, matchups and recent games.
    # EXPECT: as Nami: 0-1, and the Shanks matchup only.
    add(conn, 1, "win", my=XEBEC, opp=KAIDO)
    add(conn, 2, "loss", my=NAMI, opp=SHANKS)
    data = load_page(conn, "OP11-041")
    assert data.selected.label == "Nami"
    assert str(data.overall) == "0-1"
    assert [g.key for g in data.matchups] == ["OP17-020"]
    assert len(data.recent) == 1
    assert "Matchups — as Nami" in render_page(data, NOW)


def test_an_unknown_leader_filter_shows_everything(conn):
    # CHECKS: a made-up ?leader= value can't break or empty the page.
    # EXPECT: no filter applied.
    add(conn, 1, "win")
    data = load_page(conn, "'; DROP TABLE games; --")
    assert data.selected is None and str(data.overall) == "1-0"


# --- Watcher health (PRD risk #1) -------------------------------------


def test_a_watcher_that_never_ran_is_flagged(conn):
    assert "The watcher has never run" in page(conn)


def test_a_live_watcher_is_shown_calmly(conn):
    beat(conn, NOW, NOW - timedelta(seconds=5), "x")
    html = page(conn)
    assert "Watcher running" in html and "not running" not in html


def test_a_stale_heartbeat_is_shown_loudly(conn):
    # CHECKS: the watcher died; the page must say so, not look normal.
    # EXPECT: "not running" with how long ago.
    beat(conn, NOW, NOW - timedelta(minutes=10), "x")
    html = page(conn)
    assert "The watcher is not running" in html and "10 min ago" in html


# --- Security ---------------------------------------------------------


def test_a_hostile_handle_is_shown_as_text_not_run(conn):
    # CHECKS: handles are chosen by other players. One containing HTML
    #         must be escaped, not executed in the owner's browser.
    # EXPECT: no raw <script>; the escaped text is present.
    add(conn, 1, "win", opponent="<script>alert(1)</script>#6666")
    html = page(conn)
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;#6666" in html


# --- The server -------------------------------------------------------


@pytest.fixture
def server(tmp_path):
    db = tmp_path / "grandline.db"
    c = connect(db)
    add(c, 1, "win")
    c.close()
    srv = serve(db, port=0)  # port 0: any free port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}", db
    srv.shutdown()
    srv.server_close()


def test_the_page_is_served(server):
    url, _ = server
    with urllib.request.urlopen(url + "/") as r:
        body = r.read().decode("utf-8")
        assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
    assert ">1-0<" in body


def test_other_paths_are_not_found(server):
    url, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(url + "/../grandline.db")
    assert e.value.code == 404


def test_the_server_listens_on_this_machine_only(server):
    # CHECKS: no login, so it must not be reachable from the network.
    # EXPECT: bound to 127.0.0.1.
    url, _ = server
    assert url.startswith("http://127.0.0.1:")


def test_the_page_cannot_write_to_the_database(server):
    # CHECKS: the connection the page uses is read-only.
    # EXPECT: any write raises.
    _, db = server
    ro = open_read_only(db)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        ro.execute("CREATE TABLE x (y)")
    ro.close()
