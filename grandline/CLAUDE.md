# Grand Line Meta — context for Claude Code

Claude Code loads this file automatically at the start of every session in
this repo. It carries the decisions already made so they don't get re-litigated
or accidentally reversed.

---

## What this is

A **personal match tracker** for the One Piece TCG simulator (OPTCGSim). It
reads the sim's per-game **combat logs**, stores every game, and shows which of
my leaders and which matchups I actually win.

Two purposes, both real:
1. A working tool, used daily.
2. A learning project — the owner is learning Python and building a technical
   PM portfolio. **Explaining decisions matters as much as shipping them.**
   Prefer teaching over silently doing.

**The PRD is v2.4 (4 Oct 2026)**, a claude.ai artifact outside the repo:
https://claude.ai/artifact/Qg9GDDNk8Aa3NpxPbFNgKK. Trap codes (T1–T5),
decisions (D1–D7) and features (F1–F14) below refer to it.

History: v1 (Sep) was a public leaderboard, blocked on data access — retired.
v2.0 read only `Player.log`; v2.3 switched to combat logs after a `Player.log`
game was credited to the wrong opponent (see T1). Don't go back to either
without the owner asking.

---

## Current state

| Stage | What | State |
|---|---|---|
| 1 | Statistics — rates, Wilson intervals | **Done** |
| 2–4 | Capture: parsers, store, watcher | **Done**, reworked for combat logs (v2.3) |
| 5 | Local web page — F4–F8 (+ F12 turn-order line) | **Done** |
| 6 | Run it for real for a week against a hand tally | Next |

Dry run on 4 Oct against the real logs (scratch DB): 13 games backfilled from
24 Sep–2 Oct, 11 with results, 2 `unknown`, every cross-checkable result
correct. **The owner's real database has not been created yet.**

---

## The data

All read-only (D6). Two sources:

**Primary — combat logs**, one file per game, persistent:
```
<install>\CombatLogs\AutoSaved\2026-10-02T11.53.41.log   (name = local end time)
<install>\CombatLogs\*.log                                (manual saves: results only)
```
`<install>` is e.g. `C:\Users\Khalid\Downloads\1.43a_Windows\Builds_Windows`
and **moves when the sim is updated**. It is never hard-coded: it's read from
`Mono path[0] = '…/OPTCGSim_Data/Managed'` at the top of both player logs (D7),
or set with `combat_log_dir` in config.

A combat log gives: room id, both handles, both leaders **with names**
(`Leader is Kaido [OP17-058]` — this is F7's name source, D4), who went first
(first `Draw 1 Don`), `Life: N` lines, and sometimes the result.

**Secondary — player logs**, results only:
```
C:\Users\Khalid\AppData\LocalLow\Batsu\OPTCGSim\Player.log       (overwritten each launch)
C:\Users\Khalid\AppData\LocalLow\Batsu\OPTCGSim\Player-prev.log
```
Only `OPB_RESULT|<outcome>` is trusted, paired with the latest
`match ready on Room ID X` / `Joined relay X` above it.

**Outcomes** (`capture/outcomes.py`, D3): `win`, `loss`, `concede` (I
scooped → loss), `opponent_concede` (→ win), `opponent_disconnect` (→ win,
owner's ruling 4 Oct), `unknown` (no verdict → in **neither** column).

**Reading a result from a combat log** — the *first* decisive event wins:
my/their `Concedes!`, `Opponent Has Disconnected!`, or a leader hit while its
owner is at Life 0. "First" matters: in a real 24 Sep game the opponent
disconnected *after* landing lethal — that's a loss, not a disconnect win.
Whose leader was hit comes from the preceding `[attacker] … attacking` line,
never the card id (mirror matches share one).

**Traps**, each with tests:
- **T1** `Player.log`'s HDR/PLY header can be written *after* its result. The
  old "nearest preceding header" rule misattributed a real game. Never take
  players/leaders from `Player.log`; pair results by room. Old code is in
  `retired/`.
- **T2** Every handle has an invisible U+200B before the `#`, in both logs.
  `normalize_handle()` strips it.
- **T3** Seat/connection order isn't me. Identify me by configured handle.
- **T4** A combat log can stop before the result (3 of 13 real ones). Fill
  from a manual save of the same room, then the player logs; wait
  `result_wait_seconds` for a just-finished game; else store `unknown`.
  Mirror-match/cut-off games stay `unknown` (owner, 4 Oct). Open hypothesis in
  the PRD: a file that ends mid-attack on a 0-life leader = attacker won.
  Don't implement until measured against player-log verdicts over weeks.
- **T5** Joining, the combat log says `W6MF7PJ` but `Player.log` says
  `Joined relay 6MF7PJ`. `same_room()` handles it.

---

## Rules that must not be broken

**Never write to any sim file** (D6). Open read-only, read, close — never hold
a file open (on Windows that blocks the sim renaming its logs).

**The store is append-only** (D2). Triggers reject UPDATE and DELETE on
`games`. Fingerprint = hash of combat log file stem + room; UNIQUE, so
re-scans never duplicate. Manual saves are a result *source*, never a game.

**Never guess a result.** No verdict → `unknown`, counted and shown, excluded
from win rate. Two results on one room in the player log → no answer.

**Pure layers:** `grandline.stats`, `parse_combat_log`, `parse_player_log` and
`store.py` take everything as arguments. Only `watcher.py` reads the clock,
through an injectable `clock`.

**Stored times are UTC ISO-8601** (`store._utc`). SQL compares them as text.

**Schema changes are append-only too.** Add a step to `_MIGRATIONS` in
`store.py`; never edit a shipped step (each step spells out its own outcome
list for that reason). Now version 3: `matches` (v1, retired Player.log rows,
kept so old DBs open), `watcher_heartbeat`, `games` (the record).

**Stats domain rules:** draws excluded from win rate; deltas are percentage
points.

**No minimum sample (D5).** Raw record (`3–2`) beside raw win rate at any
sample size. Don't reintroduce a gate without the owner asking. Render a
0-game row as "—", not "0%".

---

## Layout

```
src/grandline/
  stats/             intervals.py, rates.py (pure maths)
  capture/
    outcomes.py      outcomes, win/loss columns, normalize_handle
    combatlog.py     combat log -> CombatGame (players, leaders, names, turn order, result)
    playerlog.py     player log -> results by room, install root (D7)
    store.py         SQLite: games, heartbeat, Record, migrations
    config.py        grandline.toml
    watcher.py       Watcher + CLI: --once, --status, or run forever (F1)
  web/
    page.py          load_page (store -> PageData) + render_page (-> HTML), pure
    server.py        stdlib http.server on 127.0.0.1:8765, read-only DB per request
retired/             stage 2/4 Player.log parser and tailer, replaced in v2.3
tests/
  fakelogs.py        builds synthetic logs in the real formats
  test_combatlog.py, test_playerlog.py, test_store.py, test_watcher.py
  test_intervals.py, test_rates.py, test_web.py
scripts/preview.py   v1 fake leaderboard printer; superseded
grandline.example.toml
```

**Test fixtures are synthetic** (`tests/fakelogs.py`). Real logs contain
other players' handles and local paths and are never committed.

---

## Running

```
pip install -e ".[dev]"
pytest                    # 129 tests, all passing as of 4 Oct 2026
python -m grandline.capture.watcher --once      # backfill every combat log, exit
python -m grandline.capture.watcher --status    # record + is it alive?
python -m grandline.capture.watcher             # run until Ctrl+C
python -m grandline.web                          # the page: http://127.0.0.1:8765/
```

(`grandline-watch` isn't on PATH on this machine; use `python -m`.) Diagnostic
log: `grandline-watcher.log` next to the database.

---

## The page (stage 5)

No framework: stdlib `http.server`, one route (`/`, optional `?leader=<id>`),
bound to **127.0.0.1 only** (no login, so never expose it). Each request
opens the DB **read-only** (`mode=ro`); only the watcher writes. Auto-refresh
every 30s. Every DB value is HTML-escaped — opponent handles are chosen by
other players (XSS test in `test_web.py`).

Shows: watcher health (loud banner if heartbeat > 60s old — PRD risk #1),
record + raw win rate + 95% range (D5; "—" for 0 games, never "0%"), unknown
count, going first/second, by my leader (click to filter), matchups, last 20.
Grouping/recent queries: `records_by()`, `recent_games()` in `store.py`.

## Next: stage 6 — run it for real

1. Owner runs `--once` to create the real DB (not done yet), then leaves the
   watcher running (auto-start: README, owner's call).
2. One week alongside a hand tally; compare. That's the north-star metric.
3. Track: unknown rate (<5% target), and the cut-off-file hypothesis.

Unconfirmed: whether combat log autosave is a setting that could be off; the
watcher warns if a player-log result has no combat log.

---

## Open design question, not yet ruled on

`win_rate()` returns `0.0` when there are no decided games. Convenient for
rollup code, but `0.0` means "no data", not "never wins". The alternative is
raising `ValueError` and making the trap impossible. Flagged in a `CAREFUL:`
comment at the call site. **The owner has not decided — ask before changing it.**

---

## Working preferences

- Outline a plan and get approval before executing multi-step work.
- Summarise what was done and what's next after each major step.
- Show what will change before overwriting or deleting anything.
- List files created or modified, with paths, at the end of a task.
- Comment style in this repo: every file opens with `WHAT THIS FILE DOES` and
  `WHY IT EXISTS`; every function has a `WHY THIS FUNCTION EXISTS` line; every
  test has `CHECKS:` and `EXPECT:`. Match it.
