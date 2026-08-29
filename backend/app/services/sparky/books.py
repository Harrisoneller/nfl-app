"""Which sportsbooks Sparky is allowed to price from.

The Odds API returns every book in the region, including offshore and sharp
shops (Pinnacle, Bovada, LowVig, BetOnline) whose numbers a typical user
cannot actually bet. Pricing those as the "best available" manufactures
edges that do not exist at DraftKings or FanDuel. Sparky therefore only
reads the major US retail books.

Matching is on the stored title *or* the Odds API key, case/punctuation
insensitive, so "DraftKings", "draftkings", and "dk" all pass.
"""
from __future__ import annotations

# Canonical tokens. Titles like "DraftKings" and keys like "draftkings"
# collapse to the same string after `_norm`. Short aliases cover how this
# codebase itself labels books in tests and snapshots.
_MAJOR_TOKENS: frozenset[str] = frozenset({
    "draftkings", "dk",
    "fanduel", "fd",
    "betmgm", "mgm",
    "caesars", "williamhillus", "williamhill",
    "espnbet",
    "fanatics", "fanaticssportsbook",
    "hardrockbet", "hardrock",
    "bet365", "bet365us",
    "betrivers",
    "pointsbet", "pointsbetus",
    "superbook",
    "unibet", "unibetus",
    "twinspires",
    "wynnbet",
})

# Prefixes long enough that "DraftKings NJ" / "FanDuel Sportsbook" still hit.
_MAJOR_PREFIXES: tuple[str, ...] = tuple(
    t for t in sorted(_MAJOR_TOKENS, key=len, reverse=True) if len(t) >= 5
)


def _norm(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def is_major_book(name: str | None) -> bool:
    """True when ``name`` is a major US retail sportsbook Sparky may quote."""
    if not name:
        return False
    n = _norm(name)
    if not n:
        return False
    if n in _MAJOR_TOKENS:
        return True
    return any(n.startswith(p) for p in _MAJOR_PREFIXES)
