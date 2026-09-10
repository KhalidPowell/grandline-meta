"""
WHAT THIS FILE DOES
Defines the shape every match record must have before anything else in
the app is allowed to touch it (RawMatch), and — next, once RawMatch is
filled in — the contract every data source must implement (MatchSource).

WHY IT EXISTS
There is no open API for the OPTCG simulator yet (Q1 is still unresolved).
Everything downstream is built against this one contract instead of
against any particular source, so stages 2-4 can be built and tested
today against a fixture, and a real source can be swapped in later in
stage 5 without touching anything upstream (A1).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class RawMatch(BaseModel):
    """One match, exactly as a source reported it, validated at the door.

    WHY THIS CLASS EXISTS: this is the boundary. Nothing past this point
    -- the pipeline, the database, grandline.stats -- should ever need to
    handle a malformed field. If a source hands us garbage, it fails
    here, loudly, at construction time, not three layers downstream.
    """

    # A stable id from the source's own system, so re-ingesting the same
    # match is a no-op rather than a duplicate row.
    external_id: str

    # datetime, not str: "when" needs to support comparison (`since` in
    # MatchSource.fetch) and arithmetic (season windows, the F4 freshness
    # stamp). Pydantic parses ISO-8601 strings into this automatically,
    # so a source handing us "2026-09-10T17:05:00Z" just works.
    played_at: datetime

    # Plain str. There's no fixed, known-in-advance set of leader codes
    # to enumerate -- new leaders release every set -- so unlike
    # `winner` below, a closed type would be actively wrong here.
    leader_a: str
    leader_b: str

    # Literal, not str: `winner` only ever takes one of three exact
    # values. `str` would accept "yes", "Player A", or a typo like "a "
    # and only fail later, deep in stats, in a confusing way. Literal
    # makes the invalid states unrepresentable -- pydantic rejects
    # anything outside the set right here, at construction.
    winner: Literal["a", "b", "draw"]

    # Same reasoning as `winner`, but only two values -- there's no
    # "draw" for who went first.
    on_the_play: Literal["a", "b"]

    # Plain str, same logic as leader codes: formats are an open,
    # growing set ("OP17", "OP18", ...), not a fixed enum to check
    # against in code that ships before the next set does.
    format_code: str
    season: str

    # dict[str, Any], not dict: this is the untouched original payload,
    # by definition unknown shape -- it might be a Limitless JSON blob
    # today and an OPBounty one after Q1 resolves. `Any` says "I'm not
    # validating what's inside, on purpose," rather than silently
    # assuming every value happens to be, say, a string.
    payload: dict[str, Any]
