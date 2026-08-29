"""Context providers — what is true *now*, as opposed to what happened before.

Each module here answers one question the historical model cannot:

* ``player_value``  — what is each player worth, in points per game, above the
  player who would actually replace him?
* ``availability``  — who is not playing, and what does that cost?

Quarterbacks are deliberately *not* valued here. They are priced by
``services/qb_adjustment_service.py`` as starter-minus-next-man-up, because a
QB's value above replacement is a different question from a receiver's: the
backup is a specific known person, not a positional average, and the swing is
several times larger than anything else on the roster.

Every provider is best-effort and returns an empty result rather than raising:
context refines a prediction, it must never be able to prevent one.
"""
