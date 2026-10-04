# Retired in PRD v2.3 (4 Oct 2026)

Kept for the record; nothing imports these, and pytest does not run them.
Safe to delete once you've read them.

- `parser.py` / `test_parser.py` — stage 2's Player.log match parser. It
  paired each result with the nearest *preceding* `HDR`/`PLY` header (trap
  T1 as first written). On 2 Oct 2026 a header was written *after* its
  result, and this rule credited a loss to the wrong opponent and leader.
  Replaced by `capture/combatlog.py` (players and leaders) and
  `capture/playerlog.py` (results only, paired by room).
- `tail.py` / `test_tail.py` — stage 4's live tail of Player.log. Combat
  logs are one finished file per game, so there is nothing to tail.
