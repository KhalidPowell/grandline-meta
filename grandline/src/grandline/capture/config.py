"""
WHAT THIS FILE DOES
Loads the watcher's settings from a small TOML file: my handle, where the
sim's player log is, an optional override for the combat log folder, where
the database goes, and how often to look.

WHY IT EXISTS
T3 says I'm identified by handle, and the PRD says the user is happy to
"edit a config file" -- but nothing else, and nothing per game. Keeping
these out of the code means a handle change or a moved install is an
edit to one file, not a code change.

WHY TOML
It's the format pyproject.toml already uses, it allows comments (JSON
doesn't), and Python 3.11+ reads it with the standard library (tomllib).

Relative paths in the file are resolved against the file's own folder,
so the config works no matter which directory the watcher is started in.
"""

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Where OPTCGSim writes its player log on Windows. Unity puts it under
# AppData\LocalLow\<company>\<product>. Unlike the install folder, this
# does NOT move when the sim is updated.
DEFAULT_LOG_PATH = Path.home() / "AppData" / "LocalLow" / "Batsu" / "OPTCGSim" / "Player.log"


@dataclass(frozen=True, slots=True)
class Config:
    my_handle: str
    log_path: Path                   # Player.log
    db_path: Path
    combat_log_dir: Path | None = None  # None = find it from Player.log (D7)
    poll_seconds: float = 2.0
    result_wait_seconds: float = 120.0

    @property
    def prev_log_path(self) -> Path:
        """Where Unity moves the old player log on relaunch: Player-prev.log."""
        return self.log_path.with_name(f"{self.log_path.stem}-prev{self.log_path.suffix}")

    @property
    def watcher_log_path(self) -> Path:
        """The watcher's own diagnostic log, next to the database."""
        return self.db_path.with_name("grandline-watcher.log")


_ALLOWED = {
    "my_handle", "log_path", "combat_log_dir", "db_path", "poll_seconds", "result_wait_seconds",
}


def load_config(path: Path | str) -> Config:
    """Read and validate a config file.

    WHY THIS FUNCTION EXISTS: so a missing or misspelt setting fails at
    startup with a clear message, not an hour into a session.
    """
    path = Path(path)
    with open(path, "rb") as f:  # tomllib requires binary mode
        raw = tomllib.load(f)

    if unknown := set(raw) - _ALLOWED:
        raise ValueError(f"{path}: unknown setting(s) {sorted(unknown)}; allowed: {sorted(_ALLOWED)}")

    handle = raw.get("my_handle", "")
    if not isinstance(handle, str) or not handle.strip():
        raise ValueError(f'{path}: my_handle is required, e.g. my_handle = "Name#1234"')

    poll = float(raw.get("poll_seconds", 2.0))
    if not 0.1 <= poll <= 30:
        # Above 30s would break the PRD's <30s capture-latency target.
        raise ValueError(f"{path}: poll_seconds must be between 0.1 and 30, got {poll}")

    wait = float(raw.get("result_wait_seconds", 120.0))
    if not 0 <= wait <= 3600:
        raise ValueError(f"{path}: result_wait_seconds must be between 0 and 3600, got {wait}")

    base = path.parent

    def resolve(value: str | None) -> Path | None:
        return None if value is None else base / Path(value).expanduser()

    return Config(
        my_handle=handle,
        log_path=resolve(raw.get("log_path")) or DEFAULT_LOG_PATH,
        db_path=resolve(raw.get("db_path")) or base / "grandline.db",
        combat_log_dir=resolve(raw.get("combat_log_dir")),
        poll_seconds=poll,
        result_wait_seconds=wait,
    )


def load_config_or_exit(path: Path | str) -> Config:
    """load_config for the command line: a short message instead of a traceback.

    WHY THIS FUNCTION EXISTS: the commonest mistake is running a command
    from the wrong folder, and a 15-line traceback buries the one-line
    answer. Exit code 2 is the convention for "you called it wrong".
    """
    path = Path(path)
    try:
        return load_config(path)
    except FileNotFoundError:
        problem = (f"No config file at {path.resolve()}.\n"
                   "Run this from the folder that holds grandline.toml, or pass "
                   "--config <path to grandline.toml>.")
    except tomllib.TOMLDecodeError as e:
        problem = f"{path} isn't valid TOML: {e}"
    except ValueError as e:
        problem = str(e)
    print(f"grandline: {problem}", file=sys.stderr)
    raise SystemExit(2)
