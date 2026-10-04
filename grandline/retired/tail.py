"""
WHAT THIS FILE DOES
Follows a growing log file: each call to `read_new_lines()` returns the
complete lines appended since the last call, and says whether the file
was replaced in between.

WHY IT EXISTS
Stage 4's hard parts live here, kept apart from the parser and the store
so each can be tested on its own:

  PARTIAL LINES   The sim may be halfway through writing a line when we
                  look. Only lines ending in a newline are returned; the
                  unfinished tail is left on disk and read next time.
  ROTATION        On relaunch the sim replaces Player.log (the old one is
                  renamed Player-prev.log). Detected two ways: the file's
                  identity changed, or it got smaller than where we were.
  READ-ONLY       D6. Opened in "rb" -- read, binary -- and CLOSED after
                  every read. Holding it open would, on Windows, stop the
                  sim from renaming the file on its next launch.

WHY BYTE OFFSETS, NOT LINE COUNTS
A byte offset is exact: "everything before byte 391,204 has been read".
Reading in binary and decoding each complete line separately also means
a multi-byte character (like the U+200B in every handle) can never be
cut in half between two reads.
"""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TailRead:
    """What one poll found.

    WHY THIS CLASS EXISTS: `rotated` has to travel with the lines. When it
    is True the caller must throw away any half-parsed match from the old
    file before feeding these lines in.
    """

    lines: list[str]
    rotated: bool = False
    missing: bool = False  # the file isn't there right now (sim mid-relaunch)


class LogTailer:
    """Tracks how far into one log file we have read.

    WHY THIS CLASS EXISTS: the watcher polls forever; this holds the only
    state that has to survive between polls -- the offset, and which file
    the offset belongs to.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.offset = 0
        self._identity: tuple[int, int] | None = None

    def read_new_lines(self) -> TailRead:
        """Return complete lines appended since the last call."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return TailRead(lines=[], missing=True)

        identity = (st.st_dev, st.st_ino)
        rotated = False
        if self._identity is not None and (
            identity != self._identity or st.st_size < self.offset
        ):
            # A different file, or the same file truncated: either way,
            # start again from its first byte.
            rotated = True
            self.offset = 0
        self._identity = identity

        if st.st_size == self.offset:
            return TailRead(lines=[], rotated=rotated)

        with open(self.path, "rb") as f:  # D6: read-only, closed on exit
            f.seek(self.offset)
            chunk = f.read()

        # Everything up to and including the last newline is complete.
        # Anything after it is a line still being written: leave it.
        end = chunk.rfind(b"\n") + 1
        if end == 0:
            return TailRead(lines=[], rotated=rotated)
        self.offset += end
        text = chunk[:end].decode("utf-8", errors="replace")
        # Split on "\n" only. str.splitlines() would also split on rarer
        # characters (\x0b,  , ...), giving different lines -- and so
        # different fingerprints -- than the parser sees reading the file.
        lines = [line + "\n" for line in text.split("\n")[:-1]]
        return TailRead(lines=lines, rotated=rotated)
