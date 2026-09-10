# Grand Line Meta — context for Claude Code

Claude Code loads this file automatically at the start of every session in
this repo. It carries the decisions already made so they don't get re-litigated
or accidentally reversed.

---

## What this is

A free, always-current leaderboard ranking One Piece Trading Card Game leaders
by **play rate** and **win rate**, sourced from ranked simulator ladder data,
with the sample size shown next to every number.

Two purposes, both real:
1. A working product.
2. A learning project — the owner is learning Python and building a technical
   PM portfolio. **Explaining decisions matters as much as shipping them.**
   Prefer teaching over silently doing.

Full PRD and architecture design exist outside the repo. Feature codes (F1–F12),
data decisions (D1–D3) and architecture decisions (A1–A7) below refer to them.

---

## Current state: stage 1 of 6 complete

| Stage | What | State |
|---|---|---|
| 1 | Statistics — rates, intervals, sample gate | **Done** |
| 2 | SQLAlchemy models + `FixtureAdapter` | Next |
| 3 | Pipeline: ingest → normalize → rollup | Not started |
| 4 | FastAPI + Jinja2 + HTMX page | Not started |
| 5 | Swap in a real data source | Blocked on Q1 |
| 6 | Deploy + schedule | Not started |

Only `grandline.stats` exists. No database, no data source, no website.

---

## Stack

Python 3.11+, **FastAPI + Jinja2 + HTMX**, SQLAlchemy, SQLite in dev →
Postgres in prod. Server-rendered, no SPA.

Chosen because the page must be indexable (the F9 creator-citation growth loop
depends on it), and because a split Python/React stack would halve the Python
the owner writes. ~40–60 lines of JS are expected later for the F7 trend chart
and no more. Streamlit and Django were considered and rejected.

---

## Rules that must not be broken

**`grandline.stats` is pure.** No database session, no HTTP client, no
`datetime.now()`. Anything needing those belongs in `grandline.pipeline`.
This is what makes the layer exhaustively testable (A4).

**Three domain rules, each already encoded in a tested function.** Each one
produces a leaderboard that looks fine and is silently wrong if reversed:

1. **Play rate divides by `2 × matches`**, not matches. Every match seats two
   leaders. `test_play_rates_across_a_format_sum_to_one` is the guard.
2. **Draws are excluded from win rate**, not counted as losses. A draw is an
   absence of evidence, not evidence of weakness.
3. **Deltas are percentage points**, not percent. 50% → 52% is +2.0 points.

**Below n=100 decided games, publish no win rate at all** (F2). The row still
appears with its play rate and game count. This gate is the product's entire
credibility claim — do not soften it for demo purposes.

**Never scrape gated statistics** (D2). OPBounty's advanced stats are a paid
product. Scraping is a legal exposure, a fragile foundation, and it forecloses
the partnership in D1.

---

## Layout

```
src/grandline/stats/
  intervals.py   Wilson confidence intervals
  rates.py       play rate, win rate, deltas, sample gate, RateSummary
tests/
  test_intervals.py   16 assertions
  test_rates.py       24 assertions
scripts/
  preview.py     prints a fake leaderboard, stdlib only
```

Source files use conventional Python module names. The owner's general
convention is `YYYY-MM-DD-descriptive-name` for **documents**, but Python
modules can't use it — leading digits and hyphens are not valid identifiers.

---

## Verification status — read this before trusting the tests

**`pytest` has never been run on this code.** It was written in an environment
where PyPI was blocked by egress policy (403), so pytest could not be installed.

What *was* verified:
- 40 assertions mirroring the pytest suite, run via a stdlib-only harness — all
  passed.
- The `wilson_interval` doctest, via stdlib `doctest` — passed.
- Wilson values checked against **published reference tables**, not against our
  own implementation: 10/20 → (0.2993, 0.7007), 0/10 → (0.0, 0.2775),
  10/10 → (0.7225, 1.0).

**First action in a new session: run `pip install -e ".[dev]" && pytest`.**
Expect 26 tests passing. Report any discrepancy — it would mean the stdlib
harness and pytest disagree, which is worth understanding, not patching over.

---

## Q1 — the blocking open question

**Does the Limitless developer API's `/games` endpoint include One Piece?**
Public docs only show Pokémon examples. Apply for a key at
`play.limitlesstcg.com/account/settings/api`, call `/games`, and record the
answer here.

Why it blocks: there is **no open API for the OPTCG simulator**. OPBounty (the
ranked ladder, run by TCG Match Making, ~18.6k games/week) is the ideal source,
but `stats.tcgmatchmaking.com/api/*` returns **401** and its advanced stats are
a paid tier. Limitless is the fallback. If Limitless lacks One Piece, there is
no fallback and everything depends on the OPBounty partnership conversation.

**A1 is the mitigation:** all sources sit behind one `MatchSource` protocol,
including a `FixtureAdapter` reading invented matches from JSON. Stages 2–4 can
be built completely without resolving Q1. Only stage 5 needs a real source.

---

## Next: stage 2

Build `src/grandline/sources/base.py` and `fixture.py`, plus `models.py`.

The contract every source implements:

```python
class RawMatch(BaseModel):
    external_id: str
    played_at: datetime
    leader_a: str
    leader_b: str
    winner: str           # "a" | "b" | "draw"
    on_the_play: str      # "a" | "b"
    format_code: str      # "OP17"
    season: str
    payload: dict         # the untouched original

class MatchSource(Protocol):
    name: str
    def fetch(self, since: datetime) -> Iterator[RawMatch]: ...
```

Tables to model (all partitioned by `season` and `format_code` from the first
migration — season resets are a known risk):

| Table | Holds |
|---|---|
| `raw_ingests` | Untouched upstream payload + checksum. Append-only, never edited (A2). |
| `matches` | One normalised row per game. |
| `leaders` | Reference data: code, name, colour identity, set code, art URL. |
| `leader_stats` | The rollup the page reads. Mirrors `RateSummary`. |
| `matchups` | Pairwise rollup for F5, including the on-the-play split. |
| `refresh_runs` | Job history. Powers F4's freshness stamp. |

Do **not** build the web layer first. Stages 1–3 produce no website on purpose —
starting with the page is how these projects stall with a pretty shell and
nothing behind it.

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
