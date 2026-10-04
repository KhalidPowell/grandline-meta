"""
WHAT THIS FILE DOES
The long-running process (F1): watches the sim's combat log folder and
stores every game, filling in a missing result from the other logs.

WHY IT EXISTS
Decision D1: a background watcher, not a manual import. This is the only
part of the app that runs unattended, and the PRD's north-star metric --
share of completed games that reach the database -- measures this file.

ONE POLL, STEP BY STEP
  1. FIND THE FOLDER (D7). From config if set; otherwise from the install
     path printed at the top of Player.log and Player-prev.log. After a
     sim update the new path appears there, and the watcher follows.
  2. PICK UP NEW FILES. A file is read once its size has stopped changing
     between two polls -- so a file still being written is never read
     half-done. Files already stored are skipped without being opened.
  3. FILL A MISSING RESULT (trap T4). If the combat log has no verdict,
     try, in order: a manual "Download Combat Log" save of the same room,
     then the player logs' OPB_RESULT for that room. A game that ended in
     the last `result_wait_seconds` is held back to give the player log
     time to catch up; after that it is stored as `unknown`.
  4. STORE, then beat the heartbeat.
On startup the first polls backfill every combat log already in the
folder. Re-reading is always safe: the store dedupes on fingerprint (D2).

THE ONLY CLOCK IN THE APP
`clock` is injectable so tests can fix it.

RUN IT WITH
    python -m grandline.capture.watcher            # run until Ctrl+C
    python -m grandline.capture.watcher --once     # catch up, then exit
    python -m grandline.capture.watcher --status   # is it working?
"""

import argparse
import logging
import os
import sqlite3
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from grandline.capture.combatlog import CombatGame, CombatParse, read_combat_log
from grandline.capture.config import Config, load_config_or_exit
from grandline.capture.outcomes import UNKNOWN
from grandline.capture.playerlog import PlayerLog, read_player_log, same_room
from grandline.capture.store import (
    beat,
    connect,
    count_games,
    heartbeat,
    insert_game,
    last_capture,
    record,
    stored_rooms,
    stored_sources,
)

log = logging.getLogger("grandline.watcher")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class _Pending:
    game: CombatGame


class _FileCache:
    """Re-parse a file only when its size or modification time changes.

    WHY THIS CLASS EXISTS: Player.log is ~400 KB and is consulted every
    poll. Re-reading it only when it has actually changed keeps a poll
    nearly free while the sim is idle.
    """

    def __init__(self, parse: Callable[[Path], object]) -> None:
        self._parse = parse
        self._cache: dict[Path, tuple[tuple[int, int], object]] = {}

    def get(self, path: Path):
        try:
            st = os.stat(path)
        except FileNotFoundError:
            self._cache.pop(path, None)
            return None
        key = (st.st_size, st.st_mtime_ns)
        hit = self._cache.get(path)
        if hit is None or hit[0] != key:
            hit = (key, self._parse(path))
            self._cache[path] = hit
        return hit[1]


class Watcher:
    """Ties the folder, the log readers and the store together.

    WHY THIS CLASS EXISTS: one poll is a small, testable unit
    (`poll_once`); running forever is just that in a loop (`run`).
    """

    def __init__(
        self,
        config: Config,
        conn: sqlite3.Connection,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.config = config
        self.conn = conn
        self.clock = clock
        self.started_at = clock()
        self.seen: set[str] = stored_sources(conn)  # stored, or unreadable
        self.pending: dict[str, _Pending] = {}
        self._sizes: dict[Path, int] = {}
        self._player_logs = _FileCache(read_player_log)
        self._manual = _FileCache(lambda p: read_combat_log(p, config.my_handle))
        # None, not []: so "no folder" on the very first poll still counts
        # as a change and gets logged.
        self._dirs: list[Path] | None = None
        self._warned: set[str] = set()
        self._orphan_rooms: dict[str, datetime] = {}

    # -- step 1: where are the combat logs? --

    def player_logs(self) -> list[PlayerLog]:
        logs = [self._player_logs.get(p) for p in (self.config.log_path, self.config.prev_log_path)]
        for pl, name in zip(logs, ("Player.log", "Player-prev.log")):
            for w in pl.warnings if pl else []:
                self._warn_once(f"{name} {w}")
        return [pl for pl in logs if pl is not None]

    def combat_dirs(self) -> list[Path]:
        """The AutoSaved folder(s) to watch (D7).

        Both player logs are asked, so on the day of a sim update both
        the old and the new install are watched -- games played on the
        old version just before updating are still picked up.
        """
        if self.config.combat_log_dir is not None:
            dirs = [self.config.combat_log_dir]
        else:
            roots = {pl.install_root for pl in self.player_logs() if pl.install_root}
            dirs = sorted(r / "CombatLogs" / "AutoSaved" for r in roots)
        dirs = [d for d in dirs if d.is_dir()]
        if dirs != self._dirs:
            if dirs:
                log.info("watching %s", ", ".join(map(str, dirs)))
            else:
                log.warning(
                    "no combat log folder found -- start the sim once, or set "
                    "combat_log_dir in the config"
                )
            self._dirs = dirs
        return dirs

    # -- step 2: new files --

    def _new_files(self, dirs: list[Path], settle: bool) -> list[Path]:
        ready = []
        for d in dirs:
            for path in sorted(d.glob("*.log")):
                if path.stem in self.seen or path.stem in self.pending:
                    continue
                try:
                    size = path.stat().st_size
                except FileNotFoundError:
                    continue
                if settle and self._sizes.get(path) != size:
                    self._sizes[path] = size  # look again next poll
                    continue
                self._sizes.pop(path, None)
                ready.append(path)
        return ready

    # -- step 3: fill a missing result --

    def _resolve(self, game: CombatGame) -> tuple[str, str] | None:
        if game.outcome is not None:
            return game.outcome, "combat_log"
        for d in self._dirs or []:
            for manual in sorted(d.parent.glob("*.log")):  # CombatLogs\, not AutoSaved\
                parsed: CombatParse | None = self._manual.get(manual)
                m = parsed.game if parsed else None
                if (m and m.outcome and m.room_id and game.room_id
                        and m.room_id == game.room_id
                        and m.opponent_handle == game.opponent_handle):
                    return m.outcome, "manual_combat_log"
        for pl in self.player_logs():
            if (outcome := pl.result_for(game.room_id)) is not None:
                return outcome, "player_log"
        return None

    # -- one poll --

    def poll_once(self, settle: bool = True) -> list[CombatGame]:
        """Look once. Returns the games newly stored by this poll."""
        now = self.clock()
        dirs = self.combat_dirs()

        for path in self._new_files(dirs, settle):
            parsed = read_combat_log(path, self.config.my_handle)
            for w in parsed.warnings:
                self._warn_once(w)
            if parsed.game is None:
                self.seen.add(path.stem)  # unreadable: warned once, never retried
            else:
                self.pending[path.stem] = _Pending(parsed.game)

        stored: list[CombatGame] = []
        for stem, item in list(self.pending.items()):
            game = item.game
            resolved = self._resolve(game)
            if resolved is None:
                age = (now - game.ended_at).total_seconds()
                if age < self.config.result_wait_seconds:
                    continue  # just ended: give the player log time to catch up
                resolved = (UNKNOWN, "none")
                log.warning("%s: no result in any log; stored as unknown", stem)
            outcome, source = resolved
            if insert_game(self.conn, game, outcome, source, self.clock()):
                stored.append(game)
                log.info(
                    "captured %s: %s vs %s (%s) -> %s [%s]",
                    game.ended_at.strftime("%Y-%m-%d %H:%M"),
                    game.my_leader_name, game.opponent_handle,
                    game.opponent_leader_name, outcome, source,
                )
            self.seen.add(stem)
            del self.pending[stem]

        self._check_missing_combat_logs(now)
        beat(self.conn, self.started_at, self.clock(), ", ".join(map(str, dirs)) or "(none)")
        return stored

    def _check_missing_combat_logs(self, now: datetime) -> None:
        """Warn when the player log shows a game with no combat log.

        WHY THIS FUNCTION EXISTS: PRD risk "combat log autosave turned
        off". If that happens, games would silently stop being captured;
        this makes it loud instead.
        """
        known = stored_rooms(self.conn) | {
            p.game.room_id for p in self.pending.values() if p.game.room_id
        }
        for pl in self.player_logs():
            for r in pl.results:
                if any(same_room(k, r.room_id) for k in known):
                    continue
                first = self._orphan_rooms.setdefault(r.room_id, now)
                if (now - first).total_seconds() >= self.config.result_wait_seconds:
                    self._warn_once(
                        f"room {r.room_id} has a result ({r.outcome}) in the player log "
                        "but no combat log -- is combat log autosave switched off?"
                    )

    def _warn_once(self, message: str) -> None:
        if message not in self._warned:
            self._warned.add(message)
            log.warning(message)

    def run(self, stop: threading.Event | None = None) -> None:
        """Poll until `stop` is set (or forever).

        WHY ERRORS ARE CAUGHT HERE: L3 -- 30 days unattended. A locked
        file or a busy database for one poll must cost one poll, not the
        whole watcher. Errors are logged with a traceback and retried.
        """
        stop = stop or threading.Event()
        log.info("started as %s, polling every %.1fs -> %s",
                 self.config.my_handle, self.config.poll_seconds, self.config.db_path)
        while not stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("poll failed; will retry")
            stop.wait(self.config.poll_seconds)


# --- Command line ------------------------------------------------------


def _setup_logging(config: Config) -> None:
    # Console output on Windows defaults to a code page that can't print
    # every handle; replace what it can't show rather than crash.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="backslashreplace")
    # Replace, don't stack: calling main() twice must not double every line.
    for old in list(log.handlers):
        log.removeHandler(old)
        old.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    console = logging.StreamHandler()
    # The file is what you read when the watcher ran with no window open.
    file = RotatingFileHandler(
        config.watcher_log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    for handler in (console, file):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def _age(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "never"
    seconds = int((now - moment).total_seconds())
    if seconds < 120:
        return f"{seconds}s ago"
    if seconds < 7200:
        return f"{seconds // 60}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def print_status(conn: sqlite3.Connection, now: datetime) -> None:
    """WHY THIS FUNCTION EXISTS: a one-command answer to "is it working?"."""
    r = record(conn)
    hb = heartbeat(conn)
    rate = f"{r.win_rate:.0%}" if r.games else "-"
    print(f"games stored   : {count_games(conn)}  ({r.unknown} with no result)")
    print(f"record         : {r}  ({rate})  "
          f"[scoops: {r.concedes} mine, {r.opponent_concedes} theirs; "
          f"{r.opponent_disconnects} disconnects]")
    print(f"last capture   : {_age(last_capture(conn), now)}")
    print(f"watcher polled : {_age(hb.last_poll_at if hb else None, now)}")
    if hb:
        print(f"watching       : {hb.log_path}")
    if hb and (now - hb.last_poll_at).total_seconds() > 60:
        print("WARNING: the watcher is not running -- games played now will not be captured.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grandline-watch", description="Capture OPTCGSim games into the Grand Line Meta store."
    )
    parser.add_argument("--config", default="grandline.toml", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="store every finished game, then exit")
    mode.add_argument("--status", action="store_true", help="print the record and watcher health")
    args = parser.parse_args(argv)

    config = load_config_or_exit(args.config)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(config.db_path)
    try:
        if args.status:
            print_status(conn, utc_now())
            return 0
        _setup_logging(config)
        watcher = Watcher(config, conn)
        if args.once:
            before = count_games(conn)
            watcher.poll_once(settle=False)
            log.info("done: %d new game(s), %d stored in total%s",
                     count_games(conn) - before, count_games(conn),
                     f", {len(watcher.pending)} just-finished game(s) left for the "
                     "running watcher" if watcher.pending else "")
            return 0
        try:
            watcher.run()
        except KeyboardInterrupt:
            log.info("stopped")
        return 0
    finally:
        conn.close()
        for handler in list(log.handlers):  # release the log file
            log.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    sys.exit(main())
