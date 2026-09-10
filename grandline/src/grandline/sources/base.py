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

from pydantic import BaseModel


class RawMatch(BaseModel):
    """One match, exactly as a source reported it, validated at the door.

    WHY THIS CLASS EXISTS: this is the boundary. Nothing past this point
    -- the pipeline, the database, grandline.stats -- should ever need to
    handle a malformed field. If a source hands us garbage, it fails
    here, loudly, at construction time, not three layers downstream.

    TODO(you): replace every `___` below with the right type. Ask
    yourself for each one: what's the narrowest type that's still true?
    (e.g. is `winner` really any string, or only ever one of three?)
    """

    # Filled in already, as a pattern to match: a stable id from the
    # source's own system, so re-ingesting the same match is a no-op.
    external_id: str

    played_at: ___          # TODO: a specific point in time -- what stdlib type is that?

    leader_a: ___            # TODO: leader identifier, e.g. "OP11-001"
    leader_b: ___

    winner: ___               # TODO: only ever "a", "b", or "draw" -- is `str` the best fit?

    on_the_play: ___          # TODO: only ever "a" or "b" -- same question as winner

    format_code: ___          # TODO: e.g. "OP17"
    season: ___

    payload: ___               # TODO: the untouched original JSON blob -- arbitrary shape
