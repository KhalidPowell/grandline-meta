"""
WHAT THIS FILE DOES
Proves the watcher stores every game exactly once from the combat log
folder -- finding the folder itself, filling missing results, following a
sim update -- and never writes to a simulator file.

THE TESTS THAT MATTER MOST -- one per PRD v2.3 risk or metric
  - test_existing_combat_logs_are_backfilled                (history captured)
  - test_missing_result_is_filled_from_the_player_log       (trap T4)
  - test_a_sim_update_to_a_new_folder_is_followed           (D7)
  - test_restarting_the_watcher_creates_no_duplicates       (0 duplicates)
  - test_watching_never_changes_any_sim_file                (0 writes)
  - test_a_game_with_no_combat_log_is_warned_about          (autosave off)

RUN IT WITH:  pytest tests/test_watcher.py
"""

import dataclasses
import hashlib
import logging
import threading
from datetime import datetime, timedelta

import pytest

from fakelogs import (
    ME, attack, combat_log, concede, hit, hosted, joined, life, player_log, result, stem, write,
)
from grandline.capture.config import load_config
from grandline.capture.store import count_games, heartbeat, record
from grandline.capture.watcher import Watcher, main

# Combat log names are LOCAL time, so the fake clock is local time too.
NOW = datetime(2026, 10, 4, 18, 0, 0).astimezone()
LONG_AGO = NOW - timedelta(days=3)


class FakeClock:
    """Stands still unless told to move."""

    def __init__(self, at: datetime = NOW) -> None:
        self.now = at

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def sim(tmp_path):
    """A fake machine: LocalLow logs, an install folder, a config, a db."""

    class Sim:
        localow = tmp_path / "LocalLow"
        install = tmp_path / "Downloads" / "1.43a_Windows" / "Builds_Windows"
        autosaved = install / "CombatLogs" / "AutoSaved"
        player = localow / "Player.log"
        prev = localow / "Player-prev.log"

        def __init__(self):
            self.autosaved.mkdir(parents=True)
            cfg = tmp_path / "grandline.toml"
            cfg.write_text(
                f'my_handle = "{ME}"\nlog_path = "{self.player.as_posix()}"\n'
                "result_wait_seconds = 60\n",
                encoding="utf-8",
            )
            self.cfg_path = cfg
            self.config = load_config(cfg)
            self.write_player([])

        def write_player(self, events, install=None, path=None):
            write(path or self.player, player_log(install or self.install, events))

        def game(self, at: datetime, folder=None, **kw):
            return write((folder or self.autosaved) / f"{stem(at)}.log", combat_log(**kw))

        def watcher(self, clock=None, config=None):
            from grandline.capture.store import connect
            self.conn = getattr(self, "conn", None) or connect(self.config.db_path)
            return Watcher(config or self.config, self.conn, clock=clock or FakeClock())

    s = Sim()
    yield s
    if getattr(s, "conn", None):
        s.conn.close()


def settle(w: Watcher):
    """Two polls: the first sees the file's size, the second reads it."""
    w.poll_once()
    return w.poll_once()


# --- Capture ----------------------------------------------------------


def test_existing_combat_logs_are_backfilled(sim):
    # CHECKS: on startup, history already in the folder is stored (D1).
    # EXPECT: 3 games, with their results.
    sim.game(LONG_AGO, opponent="A#1", body=concede(ME))
    sim.game(LONG_AGO + timedelta(minutes=20), opponent="B#2", body=concede("B#2"))
    sim.game(LONG_AGO + timedelta(minutes=40), opponent="C#3",
             body=["Opponent Has Disconnected!"])
    stored = settle(sim.watcher())
    assert len(stored) == 3
    assert str(record(sim.conn)) == "2-1"
    assert record(sim.conn).opponent_disconnects == 1


def test_a_file_still_being_written_is_not_read_until_it_stops_growing(sim):
    # CHECKS: a combat log mid-write is never read half-done.
    # EXPECT: nothing stored while it grows; stored once size holds.
    w = sim.watcher()
    path = sim.game(LONG_AGO, body=[])
    assert w.poll_once() == []                       # first sight
    path.write_text(path.read_text(encoding="utf-8") + "\n".join(concede(ME)) + "\n",
                    encoding="utf-8")
    assert w.poll_once() == []                       # grew: look again
    (g,) = w.poll_once()                             # stable: read
    assert record(sim.conn).concedes == 1


def test_missing_result_is_filled_from_the_player_log(sim):
    # CHECKS: trap T4 -- a real 2 Oct game: combat log cut off,
    #         player log says win. Joined room, so T5's dropped W too.
    # EXPECT: stored as a win, sourced from the player log.
    sim.game(LONG_AGO, room="W6MF7PJ", hosted=False, body=[attack(ME, ("OP17-048", "Shiki"),
                                                                 ("OP17-058", "Kaido"))])
    sim.write_player([joined("6MF7PJ"), result("win")])
    settle(sim.watcher())
    row = sim.conn.execute("SELECT outcome, outcome_source FROM games").fetchone()
    assert tuple(row) == ("win", "player_log")


def test_previous_player_log_is_consulted_too(sim):
    # CHECKS: the sim relaunched since the game; its result is in -prev.
    # EXPECT: filled from Player-prev.log.
    sim.game(LONG_AGO, room="WPREV1")
    sim.write_player([hosted("WPREV1"), result("loss")], path=sim.prev)
    settle(sim.watcher())
    assert str(record(sim.conn)) == "0-1"


def test_missing_result_is_filled_from_a_manual_save(sim):
    # CHECKS: a real 24 Sep case -- AutoSaved copy cut off,
    #         a manual "Download Combat Log" of the same room has the end.
    # EXPECT: opponent_disconnect, sourced from the manual save; and the
    #         manual save is NOT stored as a second game.
    sim.game(LONG_AGO, opponent="Capt#9404", room="WCPRGLP")
    sim.game(LONG_AGO + timedelta(seconds=24), folder=sim.autosaved.parent,
             opponent="Capt#9404", room="WCPRGLP", body=["Opponent Has Disconnected!"])
    settle(sim.watcher())
    rows = sim.conn.execute("SELECT outcome, outcome_source FROM games").fetchall()
    assert [tuple(r) for r in rows] == [("opponent_disconnect", "manual_combat_log")]


def test_a_just_finished_game_waits_for_its_result(sim):
    # CHECKS: the combat log can land a moment before the player log's
    #         OPB_RESULT. Don't store "unknown" in that gap.
    # EXPECT: held back; then stored as a win once the result appears.
    clock = FakeClock()
    w = sim.watcher(clock)
    sim.game(NOW - timedelta(seconds=5), room="WLIVE1")
    assert settle(w) == []
    assert "2026-10-04T17.59.55" in w.pending
    sim.write_player([hosted("WLIVE1"), result("win")])
    clock.advance(3)
    (g,) = w.poll_once()
    assert str(record(sim.conn)) == "1-0"


def test_no_result_anywhere_is_stored_as_unknown_after_the_wait(sim):
    # CHECKS: T4 with no rescue -- a real 25 Sep game.
    # EXPECT: held for result_wait_seconds, then stored as unknown.
    clock = FakeClock()
    w = sim.watcher(clock)
    sim.game(NOW - timedelta(seconds=5), room="WNONE1")
    assert settle(w) == []
    clock.advance(61)
    (g,) = w.poll_once()
    row = sim.conn.execute("SELECT outcome, outcome_source FROM games").fetchone()
    assert tuple(row) == ("unknown", "none")
    assert record(sim.conn).games == 0


def test_old_game_with_no_result_is_stored_as_unknown_straight_away(sim):
    # CHECKS: backfill doesn't wait -- old player logs won't change.
    # EXPECT: stored on the second poll.
    sim.game(LONG_AGO, room="WOLD1")
    assert len(settle(sim.watcher())) == 1
    assert record(sim.conn).unknown == 1


def test_lethal_result_is_read_from_the_combat_log(sim):
    # CHECKS: a real 2 Oct lethal, end to end.
    # EXPECT: a loss, sourced from the combat log.
    xebec, shanks = ("OP17-039", "Rocks D. Xebec"), ("OP17-020", "Shanks")
    sim.game(LONG_AGO, opp_leader=shanks,
             body=[life(ME, 0), attack("Rival#2222", shanks, xebec), hit(xebec)])
    settle(sim.watcher())
    row = sim.conn.execute("SELECT outcome, outcome_source FROM games").fetchone()
    assert tuple(row) == ("loss", "combat_log")


# --- Finding the folder (D7) ------------------------------------------


def test_the_combat_log_folder_is_found_from_the_player_log(sim):
    # CHECKS: no folder in config -- it comes from Player.log's first line.
    # EXPECT: the AutoSaved folder under the install.
    assert sim.watcher().combat_dirs() == [sim.autosaved]


def test_a_sim_update_to_a_new_folder_is_followed(sim, tmp_path):
    # CHECKS: the owner's 4 Oct concern. The sim is updated into a new
    #         folder: Player.log now names the new install, Player-prev.log
    #         the old one. Games in BOTH must be captured.
    # EXPECT: both folders watched; one game from each stored.
    new_install = tmp_path / "Downloads" / "1.44a_Windows" / "Builds_Windows"
    new_auto = new_install / "CombatLogs" / "AutoSaved"
    new_auto.mkdir(parents=True)
    sim.write_player([], install=sim.install, path=sim.prev)
    sim.write_player([], install=new_install)
    sim.game(LONG_AGO, opponent="Old#1", body=concede(ME))
    sim.game(LONG_AGO + timedelta(hours=1), folder=new_auto, opponent="New#2", body=concede(ME))
    w = sim.watcher()
    assert set(w.combat_dirs()) == {sim.autosaved, new_auto}
    assert len(settle(w)) == 2


def test_config_override_wins(sim, tmp_path):
    # CHECKS: combat_log_dir in config beats discovery.
    # EXPECT: only the configured folder is watched.
    other = tmp_path / "elsewhere"
    other.mkdir()
    cfg = dataclasses.replace(sim.config, combat_log_dir=other)
    assert sim.watcher(config=cfg).combat_dirs() == [other]


def test_no_folder_found_is_survivable(sim, caplog):
    # CHECKS: before the sim has ever run, there's no Player.log.
    # EXPECT: a warning, an empty poll, a heartbeat -- no crash.
    sim.player.unlink()
    w = sim.watcher()
    with caplog.at_level(logging.WARNING, logger="grandline.watcher"):
        assert w.poll_once() == []
    assert "no combat log folder" in caplog.text
    assert heartbeat(sim.conn) is not None


# --- Exactly once -----------------------------------------------------


def test_restarting_the_watcher_creates_no_duplicates(sim):
    # CHECKS: stop and start -- everything is re-scanned.
    # EXPECT: still 2 rows; the new watcher skips stored files unread.
    sim.game(LONG_AGO, opponent="A#1", body=concede(ME))
    sim.game(LONG_AGO + timedelta(minutes=9), opponent="B#2", body=concede(ME))
    settle(sim.watcher())
    second = Watcher(sim.config, sim.conn, clock=FakeClock())
    assert second.seen == {stem(LONG_AGO), stem(LONG_AGO + timedelta(minutes=9))}
    assert settle(second) == []
    assert count_games(sim.conn) == 2


def test_an_unreadable_file_is_warned_about_once_and_skipped(sim, caplog):
    # CHECKS: a combat log without my handle (wrong config, or a spectated
    #         game) doesn't stop the watcher or spam the log.
    # EXPECT: one warning across many polls; nothing stored.
    write(sim.autosaved / f"{stem(LONG_AGO)}.log", ["Version is 1.43a.3"])
    w = sim.watcher()
    with caplog.at_level(logging.WARNING, logger="grandline.watcher"):
        for _ in range(4):
            w.poll_once()
    assert caplog.text.count("no leader line") == 1
    assert count_games(sim.conn) == 0


# --- Risks ------------------------------------------------------------


def test_a_game_with_no_combat_log_is_warned_about(sim, caplog):
    # CHECKS: PRD risk "autosave turned off" -- a result in the player log
    #         whose room never gets a combat log.
    # EXPECT: no warning at first; a warning once the wait has passed.
    clock = FakeClock()
    w = sim.watcher(clock)
    sim.write_player([hosted("WGHOST"), result("win")])
    with caplog.at_level(logging.WARNING, logger="grandline.watcher"):
        w.poll_once()
        assert "autosave" not in caplog.text
        clock.advance(61)
        w.poll_once()
    assert "WGHOST" in caplog.text and "autosave" in caplog.text


def test_watching_never_changes_any_sim_file(sim):
    # CHECKS: D6 -- every file belongs to the sim. 0 writes.
    # EXPECT: identical bytes and mtimes after a full backfill.
    files = [
        sim.game(LONG_AGO, room="W1"),
        sim.game(LONG_AGO + timedelta(seconds=30), folder=sim.autosaved.parent, room="W1",
                 body=concede(ME)),
    ]
    sim.write_player([hosted("W1"), result("loss")])
    sim.write_player([], path=sim.prev)
    files += [sim.player, sim.prev]

    def snapshot():
        return [(hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_mtime_ns) for f in files]

    before = snapshot()
    w = sim.watcher()
    for _ in range(3):
        w.poll_once()
    assert snapshot() == before


def test_run_loop_survives_a_failing_poll(sim, monkeypatch):
    # CHECKS: L3 -- one bad poll must not end a 30-day run.
    # EXPECT: after a poll raises, the loop keeps going until stopped.
    stop = threading.Event()
    w = sim.watcher(config=dataclasses.replace(sim.config, poll_seconds=0.01))
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("file locked")
        if len(calls) == 3:
            stop.set()
        return []

    monkeypatch.setattr(w, "poll_once", flaky)
    w.run(stop)
    assert len(calls) == 3


# --- Config -----------------------------------------------------------


def test_config_resolves_relative_paths_against_its_own_folder(tmp_path):
    cfg = tmp_path / "grandline.toml"
    cfg.write_text('my_handle = "A#1"\nlog_path = "logs/Player.log"\ncombat_log_dir = "cl"\n',
                   encoding="utf-8")
    c = load_config(cfg)
    assert c.db_path == tmp_path / "grandline.db"
    assert c.prev_log_path == tmp_path / "logs" / "Player-prev.log"
    assert c.combat_log_dir == tmp_path / "cl"


@pytest.mark.parametrize(
    "body",
    [
        "",
        'my_handle = "   "',
        'my_handle = "A#1"\nmy_handel = "typo"',
        'my_handle = "A#1"\npoll_seconds = 60',
        'my_handle = "A#1"\nresult_wait_seconds = -1',
    ],
)
def test_bad_config_fails_at_startup(tmp_path, body):
    cfg = tmp_path / "grandline.toml"
    cfg.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(cfg)


# --- Command line -----------------------------------------------------


def test_once_and_status(sim, capsys):
    # CHECKS: `--once` backfills and exits; `--status` reports it.
    # EXPECT: exit 0, 2 games, a 1-0 record with 1 unknown.
    sim.game(LONG_AGO, opponent="A#1", body=concede("A#1"))
    sim.game(LONG_AGO + timedelta(minutes=5), opponent="B#2", room="WNOPE")
    assert main(["--config", str(sim.cfg_path), "--once"]) == 0
    assert main(["--config", str(sim.cfg_path), "--status"]) == 0
    out = capsys.readouterr().out
    assert "games stored   : 2  (1 with no result)" in out
    assert "1-0" in out


@pytest.mark.parametrize("module_main", ["watcher", "web"])
@pytest.mark.parametrize(
    ("body", "expect"),
    [(None, "No config file"), ("my_handle = [", "isn't valid TOML"), ("", "my_handle is required")],
)
def test_config_problems_give_a_short_message_not_a_traceback(tmp_path, capsys, module_main, body, expect):
    # CHECKS: running from the wrong folder (the owner hit this on 4 Oct),
    #         or with a broken config, explains itself in one message.
    # EXPECT: exit code 2, the explanation on stderr, no traceback.
    from grandline.web.server import main as web_main
    run = main if module_main == "watcher" else web_main
    cfg = tmp_path / "grandline.toml"
    if body is not None:
        cfg.write_text(body, encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run(["--config", str(cfg)])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert expect in err and "Traceback" not in err
