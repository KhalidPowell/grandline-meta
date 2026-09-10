# Grand Line Meta

A One Piece TCG metagame leaderboard: which leaders are played most, which
actually win, and how confident we are in either number.

See the PRD and the architecture design for the full picture. This README
covers what is built so far.

## Status — Stage 1 of 6

Only the statistics layer exists. There is no database, no data source, and
no website yet. That is deliberate: the maths is what the rest depends on,
and it is the part worth getting right before anything can hide it.

| Stage | What | State |
|---|---|---|
| 1 | Statistics — rates, intervals, sample gate | **Done** |
| 2 | Models and fixture source | Not started |
| 3 | Pipeline: ingest → normalize → rollup | Not started |
| 4 | FastAPI + templates | Not started |
| 5 | Swap in a real data source | Blocked on Q1 |
| 6 | Deploy and schedule | Not started |

## Layout

```
src/grandline/stats/
  intervals.py   Wilson confidence intervals
  rates.py       play rate, win rate, deltas, sample gate
tests/
  test_intervals.py
  test_rates.py
scripts/
  preview.py     prints a leaderboard from invented numbers
```

`grandline.stats` is pure by rule: no database session, no HTTP client, no
reading the clock. Anything needing those belongs in `grandline.pipeline`
when stage 3 arrives.

## Running it

Preview the maths with no dependencies at all:

```bash
python3 scripts/preview.py
```

Run the test suite (needs pytest):

```bash
pip install -e ".[dev]"
pytest
```

## Three decisions encoded in the code

These are the ones that produce a wrong leaderboard silently if you get
them backwards, so they live in tested functions rather than in whoever
writes the rollup job later.

**Play rate divides by two leaders per match.** A match seats two leaders,
so the denominator is `2 × matches`. Divide by matches and every rate on
the page doubles — the format sums to 200% of itself.

**Draws are excluded, not counted as losses.** A draw is not evidence a
deck is worse; it is an absence of evidence. Counting draws as losses would
systematically punish grindy archetypes.

**Deltas are percentage points.** 50% → 52% is +2.0 points, not +4%. These
are different claims and the column header should say which one it is.

## Why Wilson intervals

The textbook normal approximation produces bounds outside [0, 1] at small
samples — a deck at 10 wins from 10 games gets nonsense bounds. Wilson stays
inside the range and degrades gracefully, which matters because small
samples are exactly where a metagame site is most tempted to publish
something it cannot defend.

Below 100 decided games, the product shows a game count and no win rate at
all. That gate is feature F2 and it is the credibility of the whole thing.
