"""
WHAT THIS FILE DOES
Proves `capture/parser.py` pulls exactly the right matches out of a
simulator log -- no phantoms, no duplicates, no missing games.

WHY THE LOG IS SYNTHETIC
The real Player.log contains other players' handles and local machine
paths, so it is not committed. SAMPLE_LOG below reproduces the real
file's STRUCTURE line-for-line in shape -- same header, player and
result lines, same zero-width spaces, same orphan headers in the same
places -- with invented handles and trimmed gameplay. Layout mirrors
the 4 Oct 2026 Player.log: five headers, three results.

THE TESTS THAT MATTER MOST -- one per trap in the PRD
  - test_t1_headers_without_results_are_not_matches
  - test_t2_zero_width_space_is_stripped_from_handles
  - test_t3_local_player_is_found_by_handle_not_seat
  - test_t4_parser_does_not_invent_a_date
  - test_identical_looking_matches_get_different_fingerprints  (D2)
  - test_parse_file_never_writes_to_the_log                    (D6)

RUN IT WITH:  pytest tests/test_parser.py
"""

import dataclasses
import hashlib

import pytest

from grandline.capture.parser import (
    MatchParser,
    normalize_handle,
    parse_file,
    parse_lines,
)

ZW = "\u200b"  # the invisible character from trap T2
ME = "Tester#1111"


def header(version: str = "1.43a") -> str:
    return f"[ReplaySync] RZ1|HDR|{version}|2|RZ1"


def player(seat: int, name: str, tag: str, leader: str) -> str:
    # Real logs put a zero-width space between the name and the '#'.
    return f"[ReplaySync] RZ1|PLY|{seat}|{name}{ZW}#{tag}|{leader}"


def gameplay(seed: int) -> list[str]:
    # Stand-in for the ~1,400 lines of real gameplay. Different seeds
    # mean different games, the way different shuffles do for real.
    return [
        f"[ReplaySync] RZ1|{i}|2|OP{seed:02d}-{i:03d}|0|{seed + i}|0|0|0|0|0|0|0"
        for i in range(1, 4)
    ]


# Mirrors the real Player.log: 5 headers, 3 results.
SAMPLE_LOG: list[str] = [
    "Mono path[0] = 'C:/somewhere/Managed'",  # Unity startup noise
    "Initialize engine version: 6000.0.58f2",
    header(),
    player(1, "Tester", "1111", "OP17-039"),
    player(2, "Alpha", "2222", "EB03-001"),
    *gameplay(1),
    "OPB_RESULT|concede",  # match 1
    "Unity noise between games",
    header(),
    player(1, "Tester", "1111", "OP17-039"),
    player(2, "Bravo", "3333", "OP17-020"),
    *gameplay(2),
    "OPB_RESULT|loss",  # match 2
    header(),  # re-sync against Bravo -- no result follows (T1)
    player(1, "Tester", "1111", "OP17-039"),
    player(2, "Bravo", "3333", "OP17-020"),
    header(),
    player(1, "Tester", "1111", "OP17-039"),
    player(2, "Charlie", "4444", "OP17-058"),
    *gameplay(3),
    "OPB_RESULT|win",  # match 3
    header(),  # trailing re-sync at end of file -- no result (T1)
    player(1, "Tester", "1111", "OP17-039"),
    player(2, "Charlie", "4444", "OP17-058"),
]


def match_block(opponent: str, leader: str, outcome: str, seed: int) -> list[str]:
    return [
        header(),
        player(1, "Tester", "1111", "OP17-039"),
        player(2, opponent, "9999", leader),
        *gameplay(seed),
        f"OPB_RESULT|{outcome}",
    ]


# --- The sample log end to end ----------------------------------------


def test_sample_log_yields_exactly_three_matches():
    # CHECKS: the stage 2 acceptance test from the PRD.
    # EXPECT: 3 matches with the right leaders and outcomes, in order.
    result = parse_lines(SAMPLE_LOG, ME)
    got = [(m.my_leader, m.opponent_handle, m.opponent_leader, m.outcome)
           for m in result.matches]
    assert got == [
        ("OP17-039", "Alpha#2222", "EB03-001", "concede"),
        ("OP17-039", "Bravo#3333", "OP17-020", "loss"),
        ("OP17-039", "Charlie#4444", "OP17-058", "win"),
    ]
    assert result.warnings == []


# --- T1: headers outnumber results ------------------------------------


def test_t1_headers_without_results_are_not_matches():
    # CHECKS: five headers, three results -> two orphans, not five matches.
    # EXPECT: 3 matches, 2 orphan headers counted.
    result = parse_lines(SAMPLE_LOG, ME)
    assert len(result.matches) == 3
    assert result.orphan_headers == 2


def test_t1_result_pairs_with_nearest_preceding_header():
    # CHECKS: after a re-sync header for Bravo, the next real header is
    #         Charlie's, and the win belongs to Charlie -- not Bravo.
    # EXPECT: match 3 is against Charlie and starts at Charlie's header.
    result = parse_lines(SAMPLE_LOG, ME)
    third = result.matches[2]
    assert third.opponent_handle == "Charlie#4444"
    assert SAMPLE_LOG[third.header_line - 1] == header()
    assert "Charlie" in SAMPLE_LOG[third.header_line + 1]


def test_result_with_no_header_is_skipped_with_a_warning():
    # CHECKS: a stray result can't invent a match.
    # EXPECT: no matches, one warning naming the line.
    result = parse_lines(["noise", "OPB_RESULT|win"], ME)
    assert result.matches == []
    assert len(result.warnings) == 1
    assert result.warnings[0].line == 2


# --- T2: invisible character in handles -------------------------------


def test_t2_zero_width_space_is_stripped_from_handles():
    # CHECKS: the handle stored is the one a human would type.
    # EXPECT: no U+200B anywhere in parsed handles.
    result = parse_lines(SAMPLE_LOG, ME)
    for m in result.matches:
        assert ZW not in m.my_handle
        assert ZW not in m.opponent_handle


def test_t2_same_opponent_is_one_person():
    # CHECKS: two games against the same player compare equal by handle.
    # EXPECT: one distinct opponent handle across both matches.
    lines = match_block("Delta", "OP15-058", "win", 1) + match_block("Delta", "OP15-058", "loss", 2)
    result = parse_lines(lines, ME)
    assert {m.opponent_handle for m in result.matches} == {"Delta#9999"}


def test_normalize_handle_removes_invisible_characters():
    # CHECKS: the helper strips U+200B and its relatives, plus whitespace.
    # EXPECT: all variants collapse to the plain handle.
    for raw in ["Name\u200b#1", "Name#1", " Name\ufeff#1 ", "Na\u200dme#1"]:
        assert normalize_handle(raw) == "Name#1"


def test_configured_handle_with_zero_width_space_still_matches():
    # CHECKS: a handle pasted from the log (invisible char and all)
    #         into config still works.
    # EXPECT: same 3 matches as with a clean handle.
    assert len(parse_lines(SAMPLE_LOG, f"Tester{ZW}#1111").matches) == 3


# --- T3: seat 1 is not reliably me ------------------------------------


def test_t3_local_player_is_found_by_handle_not_seat():
    # CHECKS: when I'm in seat 2, my leader is still my leader.
    # EXPECT: my_leader is seat 2's card, opponent is seat 1.
    lines = [
        header(),
        player(1, "Echo", "5555", "OP09-062"),
        player(2, "Tester", "1111", "OP17-039"),
        "OPB_RESULT|win",
    ]
    (m,) = parse_lines(lines, ME).matches
    assert m.my_leader == "OP17-039"
    assert m.opponent_handle == "Echo#5555"
    assert m.opponent_leader == "OP09-062"


def test_t3_unknown_handle_skips_with_a_warning():
    # CHECKS: a misconfigured handle can't silently pick a seat.
    # EXPECT: no matches captured, a warning for each result.
    result = parse_lines(SAMPLE_LOG, "Somebody#0000")
    assert result.matches == []
    assert len(result.warnings) == 3


def test_empty_handle_is_rejected():
    # CHECKS: an unset config value fails at startup, not mid-session.
    # EXPECT: ValueError.
    with pytest.raises(ValueError):
        MatchParser(f"  {ZW} ")


# --- T4: no timestamps ------------------------------------------------


def test_t4_parser_does_not_invent_a_date():
    # CHECKS: there is no date field to fill with a guess. Capture time
    #         is the watcher's job.
    # EXPECT: no field on ParsedMatch looks like a time.
    (m, *_) = parse_lines(SAMPLE_LOG, ME).matches
    names = {f.name for f in dataclasses.fields(m)}
    assert not names & {"played_at", "captured_at", "timestamp", "date"}


# --- Outcomes (D3) ----------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "is_win"),
    [("win", True), ("opponent_concede", True), ("loss", False), ("concede", False)],
)
def test_every_known_outcome_keeps_its_tag_and_its_column(outcome, is_win):
    # CHECKS: concede is a loss and opponent_concede is a win, but each
    #         keeps its own tag so it can be filtered later (D3, F9).
    # EXPECT: outcome stored verbatim; is_win reflects the column.
    (m,) = parse_lines(match_block("Foxtrot", "OP01-001", outcome, 1), ME).matches
    assert m.outcome == outcome
    assert m.is_win is is_win


def test_unknown_outcome_is_skipped_not_guessed():
    # CHECKS: a new outcome string after a sim update isn't forced into
    #         a column.
    # EXPECT: no match, one warning naming the outcome.
    result = parse_lines(match_block("Golf", "OP01-001", "draw_by_timeout", 1), ME)
    assert result.matches == []
    assert "draw_by_timeout" in result.warnings[0].message


def test_unverified_version_is_captured_with_a_warning():
    # CHECKS: a sim update doesn't lose games (the log dies on relaunch),
    #         but it is flagged.
    # EXPECT: 1 match, 1 warning mentioning the version.
    lines = [header("1.44a"), *match_block("Hotel", "OP01-001", "win", 1)[1:]]
    result = parse_lines(lines, ME)
    assert len(result.matches) == 1
    assert "1.44a" in result.warnings[0].message


# --- Fingerprints (D2) ------------------------------------------------


def test_same_log_parsed_twice_gives_same_fingerprints():
    # CHECKS: re-reading a file is idempotent once stage 3 dedupes on this.
    # EXPECT: identical fingerprint lists.
    first = [m.fingerprint for m in parse_lines(SAMPLE_LOG, ME).matches]
    second = [m.fingerprint for m in parse_lines(SAMPLE_LOG, ME).matches]
    assert first == second


def test_identical_looking_matches_get_different_fingerprints():
    # CHECKS: the real Player-prev.log case -- two back-to-back games,
    #         same opponent, same leaders, same outcome. They are two games.
    # EXPECT: two matches, two distinct fingerprints.
    lines = (match_block("India", "OP15-058", "concede", 1)
             + match_block("India", "OP15-058", "concede", 2))
    a, b = parse_lines(lines, ME).matches
    assert (a.opponent_handle, a.opponent_leader, a.outcome) == \
           (b.opponent_handle, b.opponent_leader, b.outcome)
    assert a.fingerprint != b.fingerprint


def test_fingerprint_ignores_line_endings():
    # CHECKS: a file read with Windows line endings fingerprints the same.
    # EXPECT: identical fingerprints for \n and \r\n input.
    unix = [m.fingerprint for m in parse_lines([l + "\n" for l in SAMPLE_LOG], ME).matches]
    win = [m.fingerprint for m in parse_lines([l + "\r\n" for l in SAMPLE_LOG], ME).matches]
    assert unix == win


# --- Incremental use (for the stage 4 watcher) ------------------------


def test_feed_returns_each_match_on_its_result_line():
    # CHECKS: the watcher gets a match the moment the result line arrives.
    # EXPECT: feed() returns a match exactly 3 times, on result lines.
    parser = MatchParser(ME)
    emitted = [(i, m) for i, line in enumerate(SAMPLE_LOG) if (m := parser.feed(line))]
    assert [SAMPLE_LOG[i] for i, _ in emitted] == [
        "OPB_RESULT|concede", "OPB_RESULT|loss", "OPB_RESULT|win",
    ]


# --- Reading from disk (D6) -------------------------------------------


def test_parse_file_reads_a_log_from_disk(tmp_path):
    # CHECKS: the file wrapper gives the same answer as the in-memory one.
    # EXPECT: 3 matches with identical fingerprints.
    log = tmp_path / "Player.log"
    log.write_text("\n".join(SAMPLE_LOG) + "\n", encoding="utf-8")
    from_disk = [m.fingerprint for m in parse_file(log, ME).matches]
    in_memory = [m.fingerprint for m in parse_lines(SAMPLE_LOG, ME).matches]
    assert from_disk == in_memory


def test_parse_file_never_writes_to_the_log(tmp_path):
    # CHECKS: D6 -- the log is byte-for-byte unchanged after parsing.
    # EXPECT: identical checksum and modification time before and after.
    log = tmp_path / "Player.log"
    log.write_text("\r\n".join(SAMPLE_LOG) + "\r\n", encoding="utf-8")
    before = (hashlib.sha256(log.read_bytes()).hexdigest(), log.stat().st_mtime_ns)
    parse_file(log, ME)
    after = (hashlib.sha256(log.read_bytes()).hexdigest(), log.stat().st_mtime_ns)
    assert before == after
