# Grand Line Meta

A personal match tracker for the One Piece TCG simulator. It reads the
simulator's combat logs, keeps every game, and tells you which leaders and
which matchups you actually win.

See the PRD (v2.4) for the full picture. This README covers what is built
so far.

## Why it exists

OPTCGSim keeps no record you can query. Without one, "am I losing this
matchup?" gets answered by memory, which remembers the losses that stung and
forgets the rest.

## Status

| Stage | What | State |
|---|---|---|
| 1 | Statistics — win rate, Wilson intervals | **Done** |
| 2–4 | Capture — read the logs, store every game, watch for new ones | **Done** |
| 5 | The page — record, by leader, matchups | **Done** |
| 6 | Run it for a week against a hand tally | Next |

Run the watcher to capture games and the page to see them.

## Where the data comes from

**Combat logs (the record).** The sim saves one file per game to
`<install>\CombatLogs\AutoSaved\`, named with the time the game ended. Each
names both players, both leaders, who went first, and usually the result.
These files are kept across launches, so on first run the tracker backfills
every game you've already played.

**The player log (results only).** About one combat log in four stops just
before the result. For those, the tracker looks for a manual "Download
Combat Log" save of the same game, then the sim's own verdict in
`Player.log` / `Player-prev.log`. If no source has one, the game is stored
as `unknown`: counted and shown, but in neither the win nor the loss column.

**The install folder moves when the sim is updated.** You don't configure
it: the tracker reads the current location from the top of `Player.log` on
every check, and watches both the old and new folders on update day.

## Layout

```
src/grandline/
  stats/          win rate and confidence intervals (pure maths)
  capture/
    combatlog.py  combat log -> game
    playerlog.py  player log -> results by room, install folder
    store.py      SQLite game store and the win-loss record
    config.py     reads grandline.toml
    watcher.py    the long-running capture process
  web/            the local page
retired/          the earlier Player.log-only parser (see retired/README.md)
tests/            129 tests, on synthetic logs
```

## Running it

```bash
pip install -e ".[dev]"
pytest
```

### Set up

Copy `grandline.example.toml` to `grandline.toml` and set your handle:

```toml
my_handle = "YourName#1234"
```

`grandline.toml`, the database and the watcher's log are all gitignored.

### Run the watcher

```bash
python -m grandline.capture.watcher --once     # backfill every combat log, then exit
python -m grandline.capture.watcher            # watch until Ctrl+C
python -m grandline.capture.watcher --status   # record, last capture, is it alive?
```

Pass `--config path\to\grandline.toml` if you're not in this folder.

Each stored game is logged to the console and to `grandline-watcher.log`
next to the database, with where its result came from:

```
INFO    captured 2026-10-02 16:46: Rocks D. Xebec vs Opponent#1234 (Kaido) -> win [player_log]
```

### Open the page

```bash
python -m grandline.web
```

Opens `http://127.0.0.1:8765/` in your browser. It shows whether the watcher
is running, your record and raw win rate (with a 95% range), going first vs
second, your leaders (click one to filter), every matchup, and your last 20
games. It refreshes every 30 seconds. It's reachable from this computer only,
and it never writes to the database.

### Start it with Windows

The watcher only helps if it's running when you play. To start it at login
with no console window, put a shortcut in your Startup folder (`Win+R`,
`shell:startup`) whose target is:

```
pythonw -m grandline.capture.watcher --config "C:\full\path\to\grandline.toml"
```

`--status` warns if the watcher hasn't polled in the last minute.

## Decisions encoded in the code

**No simulator file is ever written to.** Every log is opened read-only and
closed straight after reading.

**The database is the record; the logs are feeds.** Games are append-only —
the database itself rejects updates and deletes — and each has a fingerprint
so re-scanning never creates duplicates.

**Early endings count for whoever stayed.** If I concede it's a loss; if my
opponent concedes or disconnects it's a win. Each is stored with its own tag,
so games decided early can be filtered out later. The *first* thing that ends
the game decides it: an opponent who disconnects after landing lethal still
beat you.

**Results are never guessed.** No verdict from any source means `unknown`.

**Draws are excluded from win rate, not counted as losses.**

## Why Wilson intervals

At personal volumes — five games against a leader, not five hundred — the
textbook formula produces nonsense bounds. Wilson stays inside [0, 1] and
degrades gracefully.

There is no minimum sample size. Every row shows its raw win rate, at any
number of games, with the raw record (`3–2`) beside it — so "60%" always
reads as the five games it is.
