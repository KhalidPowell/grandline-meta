"""
WHAT THIS FILE DOES
Proves `capture/playerlog.py` pairs each OPB_RESULT with the right room,
and finds the sim's install folder.

THE TESTS THAT MATTER MOST
  - test_a_header_written_after_the_result_cannot_misattribute  (trap T1 regression)
  - test_joined_room_matches_despite_the_dropped_w              (trap T5)
  - test_install_root_is_read_from_the_mono_path                (D7)

RUN IT WITH:  pytest tests/test_playerlog.py
"""

from pathlib import Path

from fakelogs import h, hosted, joined, player_log, result
from grandline.capture.playerlog import (
    install_root_from_mono,
    parse_player_log,
    read_player_log,
    same_room,
)

ROOT = Path("C:/Games/1.43a_Windows/Builds_Windows")


def test_results_are_paired_with_the_latest_room():
    # CHECKS: an abandoned lobby (6KNNWT) before the real one (WF9BCF) --
    #         the real 2 Oct Player-prev.log shape.
    # EXPECT: the result belongs to WF9BCF; 6KNNWT has none.
    pl = parse_player_log(player_log(ROOT, [
        hosted("6KNNWT"), hosted("WF9BCF"), result("opponent_concede")]))
    assert pl.result_for("WF9BCF") == "opponent_concede"
    assert pl.result_for("6KNNWT") is None


def test_a_header_written_after_the_result_cannot_misattribute():
    # CHECKS: trap T1 as found on 2 Oct. Game 2's HDR/PLY block came AFTER
    #         its result, so the old parser credited game 2 to game 1's
    #         opponent. Rooms don't move, so pairing by room is immune.
    # EXPECT: each room gets its own result.
    pl = parse_player_log(player_log(ROOT, [
        hosted("D7PFBN"),
        "[ReplaySync] RZ1|HDR|1.43a|2|RZ1",
        f"[ReplaySync] RZ1|PLY|2|{h('First#7724')}|OP15-058",
        result("concede"),
        f"[ReplaySync] RZ1|PLY|2|{h('First#7724')}|OP15-058",  # stale re-sync
        hosted("9P8GKG"),
        result("concede"),
        "[ReplaySync] RZ1|HDR|1.43a|2|RZ1",                    # header AFTER result
        f"[ReplaySync] RZ1|PLY|2|{h('Second#50403')}|OP09-062",
    ]))
    assert [(r.room_id, r.outcome) for r in pl.results] == [
        ("D7PFBN", "concede"), ("9P8GKG", "concede")]


def test_joined_room_matches_despite_the_dropped_w():
    # CHECKS: T5 -- combat log "W6MF7PJ", player log "Joined relay 6MF7PJ".
    # EXPECT: same room; and an unrelated room does not match.
    pl = parse_player_log(player_log(ROOT, [joined("6MF7PJ"), result("win")]))
    assert pl.result_for("W6MF7PJ") == "win"
    assert same_room("WF9BCF", "WF9BCF")
    assert not same_room("WABC", "XYZ")


def test_two_results_for_one_room_are_not_guessed_between():
    # CHECKS: a rematch in the same lobby would put two results on one
    #         room -- there's no telling which game is which.
    # EXPECT: None.
    pl = parse_player_log(player_log(ROOT, [
        hosted("WSAME"), result("win"), result("loss")]))
    assert pl.result_for("WSAME") is None


def test_unrecognised_result_is_ignored_with_a_warning():
    pl = parse_player_log(player_log(ROOT, [hosted("WR"), result("draw_by_timeout")]))
    assert pl.results == []
    assert "draw_by_timeout" in pl.warnings[0]


def test_result_before_any_room_is_ignored_with_a_warning():
    pl = parse_player_log(player_log(ROOT, [result("win")]))
    assert pl.results == [] and len(pl.warnings) == 1


def test_install_root_is_read_from_the_mono_path():
    # CHECKS: D7 -- the install folder comes from Unity's first line.
    # EXPECT: two levels above .../OPTCGSim_Data/Managed.
    assert parse_player_log(player_log(ROOT, [])).install_root == ROOT


def test_an_unexpected_mono_path_gives_no_install_root():
    # CHECKS: a format change can't send the watcher somewhere random.
    # EXPECT: None.
    assert install_root_from_mono("C:/Somewhere/Else") is None


def test_missing_player_log_reads_as_none(tmp_path):
    assert read_player_log(tmp_path / "Player.log") is None
