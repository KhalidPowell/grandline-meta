"""
WHAT THIS FILE DOES
Proves `capture/combatlog.py` reads who played, which leaders, who went
first, when the game ended and -- when the file says -- who won.

THE TESTS THAT MATTER MOST
  - test_opponent_disconnect_is_a_win                 (D3, owner's call)
  - test_lethal_in_a_mirror_match_uses_the_attacker   (no guessing by card id)
  - test_a_file_with_no_verdict_has_no_outcome        (trap T4: unknown, not guessed)
  - test_reading_never_changes_the_file               (D6)

RUN IT WITH:  pytest tests/test_combatlog.py
"""

import hashlib
from datetime import datetime

import pytest

from fakelogs import (
    ME, ZW, attack, combat_log, concede, hit, life, write,
)
from grandline.capture.combatlog import ended_at_from_name, parse_combat_log, read_combat_log

STEM = "2026-10-02T11.53.41"
XEBEC = ("OP17-039", "Rocks D. Xebec")
KAIDO = ("OP17-058", "Kaido")
SHIKI = ("OP17-048", "Shiki")
QUEEN = ("EB04-032", "Queen")


def parse(lines, stem=STEM, me=ME):
    return parse_combat_log(lines, stem, me)


# --- Who, what, when --------------------------------------------------


def test_reads_players_leaders_and_names():
    # CHECKS: everything F5-F7 need comes from the combat log (D4).
    # EXPECT: both leaders with ids and names, handles without U+200B.
    g = parse(combat_log(opponent="Rival#2222")).game
    assert (g.my_handle, g.my_leader, g.my_leader_name) == (ME, "OP17-039", "Rocks D. Xebec")
    assert (g.opponent_handle, g.opponent_leader, g.opponent_leader_name) == (
        "Rival#2222", "OP17-058", "Kaido")
    assert ZW not in g.my_handle + g.opponent_handle


def test_room_and_version_when_hosting_and_when_joining():
    # CHECKS: both first-line forms give the room id.
    # EXPECT: hosted -> WROOM01, joined -> W6MF7PJ.
    assert parse(combat_log(room="WROOM01", hosted=True)).game.room_id == "WROOM01"
    joined = parse(combat_log(room="W6MF7PJ", hosted=False)).game
    assert joined.room_id == "W6MF7PJ"
    assert joined.sim_version == "1.43a.3"


def test_seat_order_does_not_decide_who_i_am():
    # CHECKS: T3 -- when the opponent hosts, they connect first.
    # EXPECT: my leader is still mine.
    g = parse(combat_log(hosted=False)).game
    assert g.my_leader == "OP17-039"
    assert g.opponent_leader == "OP17-058"


@pytest.mark.parametrize("i_go_first", [True, False])
def test_turn_order_is_read_from_the_first_don_draw(i_go_first):
    # CHECKS: F12's data -- the first player draws 1 DON, the second 2.
    # EXPECT: went_first matches.
    assert parse(combat_log(i_go_first=i_go_first)).game.went_first is i_go_first


def test_ended_at_is_the_file_name_in_local_time():
    # CHECKS: T4 is gone -- the file name is the time, made tz-aware.
    # EXPECT: same wall-clock time, with this machine's timezone.
    t = ended_at_from_name(STEM)
    assert t.tzinfo is not None
    assert t.replace(tzinfo=None) == datetime(2026, 10, 2, 11, 53, 41)


def test_a_file_not_named_as_a_time_is_skipped_with_a_warning():
    # CHECKS: a stray file in the folder can't become a game.
    # EXPECT: no game, one warning.
    r = parse(combat_log(), stem="notes")
    assert r.game is None and len(r.warnings) == 1


def test_manual_download_card_format_is_understood():
    # CHECKS: manual saves write cards as ["OP12-061">OP12-061].
    # EXPECT: the card id is still found.
    lines = [l.replace('<mark><link="OP17-058">OP17-058</link></mark>', '"OP17-058">OP17-058')
             for l in combat_log()]
    assert parse(lines).game.opponent_leader == "OP17-058"


def test_my_handle_missing_is_skipped_with_a_warning():
    # CHECKS: a wrong handle in config can't silently pick a side.
    # EXPECT: no game, a warning that says to check config.
    r = parse(combat_log(), me="Somebody#0000")
    assert r.game is None and "config" in r.warnings[0]


def test_fingerprint_is_stable_and_unique_per_file():
    # CHECKS: D2 -- same file twice, same identity; another file, another.
    # EXPECT: equal for the same stem, different for a different one.
    a = parse(combat_log()).game.fingerprint
    assert a == parse(combat_log()).game.fingerprint
    assert a != parse(combat_log(), stem="2026-10-02T12.00.00").game.fingerprint


# --- Results (D3) -----------------------------------------------------


def test_my_concede_is_a_concede():
    assert parse(combat_log(body=concede(ME))).game.outcome == "concede"


def test_their_concede_is_an_opponent_concede():
    assert parse(combat_log(body=concede("Rival#2222"))).game.outcome == "opponent_concede"


def test_opponent_disconnect_is_a_win():
    # CHECKS: the owner's 4 Oct ruling -- they leave, I win, tagged.
    # EXPECT: opponent_disconnect.
    lines = combat_log(body=["Opponent Has Disconnected!", "GameOver"])
    assert parse(lines).game.outcome == "opponent_disconnect"


def test_hit_on_my_leader_at_zero_life_is_a_loss():
    # CHECKS: lethal on me, shaped like a real 2 Oct game.
    # EXPECT: loss.
    body = [life(ME, 0), life("Rival#2222", 4), attack("Rival#2222", KAIDO, XEBEC), hit(XEBEC)]
    assert parse(combat_log(body=body)).game.outcome == "loss"


def test_hit_on_their_leader_at_zero_life_is_a_win():
    body = [life("Rival#2222", 0), attack(ME, SHIKI, KAIDO), hit(KAIDO)]
    assert parse(combat_log(body=body)).game.outcome == "win"


def test_life_counts_down_between_life_lines():
    # CHECKS: two hits in one turn with no Life line between them.
    # EXPECT: 1 life, hit (-> 0), hit again -> lethal.
    body = [life("Rival#2222", 1), attack(ME, SHIKI, KAIDO), hit(KAIDO),
            attack(ME, SHIKI, KAIDO), hit(KAIDO)]
    assert parse(combat_log(body=body)).game.outcome == "win"


def test_a_hit_with_life_left_is_not_lethal():
    body = [life("Rival#2222", 3), attack(ME, SHIKI, KAIDO), hit(KAIDO)]
    assert parse(combat_log(body=body)).game.outcome is None


def test_a_hit_on_a_character_is_not_lethal():
    # CHECKS: only LEADER hits can end the game.
    # EXPECT: no outcome even with both players at 0.
    body = [life(ME, 0), life("Rival#2222", 0), attack(ME, SHIKI, QUEEN), hit(QUEEN)]
    assert parse(combat_log(body=body)).game.outcome is None


def test_lethal_in_a_mirror_match_uses_the_attacker():
    # CHECKS: the real 24 Sep mirror -- both leaders are OP17-039, so the
    #         card id can't say whose leader was hit. I'm at 0 life; I
    #         attack; THEIR leader is hit (they're at 1). Not my loss.
    # EXPECT: no outcome (they had life left) -- certainly not "loss".
    body = [life(ME, 0), life("Rival#2222", 1), attack(ME, SHIKI, XEBEC), hit(XEBEC)]
    g = parse(combat_log(opp_leader=XEBEC, body=body)).game
    assert g.outcome is None


def test_a_hit_with_life_never_printed_is_not_lethal():
    # CHECKS: unknown life is never treated as zero.
    # EXPECT: no outcome.
    body = [attack(ME, SHIKI, KAIDO), hit(KAIDO)]
    assert parse(combat_log(body=body)).game.outcome is None


def test_a_file_with_no_verdict_has_no_outcome():
    # CHECKS: trap T4 -- a real 2 Oct file stops mid-counter.
    # EXPECT: outcome None, so the watcher asks the player log.
    body = [attack(ME, SHIKI, KAIDO), "[Rival​#2222] Discard Queen for Counter 1000"]
    g = parse(combat_log(body=body)).game
    assert g is not None and g.outcome is None


def test_the_first_decisive_event_wins():
    # CHECKS: lines after the game ended can't change the result.
    # EXPECT: their concede stands, despite my concede line after it.
    body = concede("Rival#2222") + concede(ME)
    assert parse(combat_log(body=body)).game.outcome == "opponent_concede"


# --- Disk (D6) --------------------------------------------------------


def test_reading_never_changes_the_file(tmp_path):
    # CHECKS: combat logs belong to the sim -- read-only.
    # EXPECT: same bytes and mtime after reading; the file can be renamed
    #         straight after (no handle held open, which on Windows would
    #         block it).
    path = write(tmp_path / f"{STEM}.log", combat_log(body=concede(ME)))
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
    assert read_combat_log(path, ME).game.outcome == "concede"
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) == before
    path.rename(tmp_path / "moved.log")
