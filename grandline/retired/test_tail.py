"""
WHAT THIS FILE DOES
Proves `capture/tail.py` returns every appended line exactly once, never
returns half a line, and notices when the log is replaced.

THE TESTS THAT MATTER MOST
  - test_a_half_written_line_waits_until_it_is_finished   (partial writes)
  - test_replaced_file_is_detected_and_read_from_the_top  (sim relaunch)
  - test_tailer_does_not_hold_the_file_open                (D6 / rotation)

RUN IT WITH:  pytest tests/test_tail.py
"""

import os

from grandline.capture.tail import LogTailer


def append(path, data: bytes) -> None:
    with open(path, "ab") as f:
        f.write(data)


def test_reads_complete_lines_once(tmp_path):
    # CHECKS: lines come back once, then never again.
    # EXPECT: two lines on the first read, nothing on the second.
    log = tmp_path / "Player.log"
    log.write_bytes(b"one\ntwo\n")
    t = LogTailer(log)
    assert t.read_new_lines().lines == ["one\n", "two\n"]
    assert t.read_new_lines().lines == []


def test_only_appended_lines_are_returned(tmp_path):
    # CHECKS: the offset advances, so old lines are not re-read.
    # EXPECT: just the new line.
    log = tmp_path / "Player.log"
    log.write_bytes(b"one\n")
    t = LogTailer(log)
    t.read_new_lines()
    append(log, b"two\n")
    assert t.read_new_lines().lines == ["two\n"]


def test_a_half_written_line_waits_until_it_is_finished(tmp_path):
    # CHECKS: the sim is mid-write when we look.
    # EXPECT: nothing until the newline lands, then the whole line once.
    log = tmp_path / "Player.log"
    log.write_bytes(b"OPB_RES")
    t = LogTailer(log)
    assert t.read_new_lines().lines == []
    append(log, b"ULT|win\n")
    assert t.read_new_lines().lines == ["OPB_RESULT|win\n"]


def test_a_multibyte_character_split_across_reads_survives(tmp_path):
    # CHECKS: U+200B is 3 bytes in UTF-8. If a read stops inside it, the
    #         character must not be garbled.
    # EXPECT: the full handle with its U+200B intact.
    log = tmp_path / "Player.log"
    zw = "​".encode("utf-8")
    log.write_bytes(b"Name" + zw[:1])
    t = LogTailer(log)
    assert t.read_new_lines().lines == []
    append(log, zw[1:] + b"#1234\n")
    assert t.read_new_lines().lines == ["Name​#1234\n"]


def test_windows_line_endings_are_kept_for_the_parser(tmp_path):
    # CHECKS: \r\n is split on \n; the parser strips the \r.
    # EXPECT: two lines, each ending \r\n.
    log = tmp_path / "Player.log"
    log.write_bytes(b"one\r\ntwo\r\n")
    assert LogTailer(log).read_new_lines().lines == ["one\r\n", "two\r\n"]


def test_only_newline_splits_lines(tmp_path):
    # CHECKS: rarer line-break characters don't split a line (they would
    #         change the fingerprint vs. reading the file whole).
    # EXPECT: one line.
    log = tmp_path / "Player.log"
    log.write_bytes("a\x0bb c\n".encode("utf-8"))
    assert LogTailer(log).read_new_lines().lines == ["a\x0bb c\n"]


def test_replaced_file_is_detected_and_read_from_the_top(tmp_path):
    # CHECKS: the sim relaunches -- old log renamed, a new one created.
    # EXPECT: rotated=True and the new file's lines from byte 0.
    log = tmp_path / "Player.log"
    log.write_bytes(b"old session line one\nold session line two\n")
    t = LogTailer(log)
    t.read_new_lines()
    os.replace(log, tmp_path / "Player-prev.log")
    log.write_bytes(b"new\n")
    read = t.read_new_lines()
    assert read.rotated is True
    assert read.lines == ["new\n"]


def test_truncated_file_is_treated_as_rotated(tmp_path):
    # CHECKS: same file, but shorter than where we were.
    # EXPECT: rotated=True, read from the top.
    log = tmp_path / "Player.log"
    log.write_bytes(b"a long first session line\n")
    t = LogTailer(log)
    t.read_new_lines()
    with open(log, "r+b") as f:
        f.truncate(0)
        f.write(b"short\n")
    read = t.read_new_lines()
    assert read.rotated is True
    assert read.lines == ["short\n"]


def test_missing_file_is_reported_not_raised(tmp_path):
    # CHECKS: between the rename and the new file, there is no log.
    # EXPECT: missing=True, no exception, then normal reading resumes.
    log = tmp_path / "Player.log"
    t = LogTailer(log)
    assert t.read_new_lines().missing is True
    log.write_bytes(b"hello\n")
    read = t.read_new_lines()
    assert (read.missing, read.lines) == (False, ["hello\n"])


def test_tailer_does_not_hold_the_file_open(tmp_path):
    # CHECKS: between reads the file can be renamed. On Windows an open
    #         handle would block this -- and block the sim's relaunch.
    # EXPECT: os.replace succeeds right after a read.
    log = tmp_path / "Player.log"
    log.write_bytes(b"line\n")
    LogTailer(log).read_new_lines()
    os.replace(log, tmp_path / "Player-prev.log")  # raises if still open
