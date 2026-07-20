#!/usr/bin/env python3
"""Seed a custom fantasy RankingSet from a ranked cheat sheet.

Reads a parsed cheat sheet (scripts/data/full_ppr_top300_2026.json), matches
each player to a Sleeper ``player_id`` in the live ``players`` table, derives
tiers from ADP cliffs, and loads the result as a *draft* RankingSet through the
existing ``rankings_service`` — same code path (and audit trail) the /admin
"Fantasy Rankings" editor uses. Nothing is published; review + publish in /admin.

Why a script (not a one-shot insert): player IDs are Sleeper IDs that only exist
in your DB, so name→id matching has to run against your real roster. The matcher
normalizes names, disambiguates on team+position, and applies a small override
map for known nickname/spelling mismatches. Unmatched players are reported, not
silently dropped, so you can add overrides and re-run.

Idempotent: re-running finds the set by (season, name) and replaces its entries.

Usage
-----
    # from repo root, with the backend venv active and DATABASE_URL pointing at
    # the same Postgres the API uses:
    python scripts/seed_ranking_board.py                 # RB/WR/TE/QB, draft
    python scripts/seed_ranking_board.py --dry-run       # match report only, no writes
    python scripts/seed_ranking_board.py --positions RB,WR,TE,QB,K,DST  # everything
    python scripts/seed_ranking_board.py --publish       # also publish when done
    python scripts/seed_ranking_board.py --tier-pct 0.25 # more (tighter) tiers

Exit codes: 0 on success (board loaded or dry-run clean), 2 if the match rate is
below --min-match-rate (default 0.9) — a guard against loading a half-matched
board because a roster sync hasn't run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DATA_FILE = REPO_ROOT / "scripts" / "data" / "full_ppr_top300_2026.json"

DEFAULT_SET_NAME = "Full PPR Top 300 (2026)"
DEFAULT_DESCRIPTION = "Full-PPR redraft board (Top 300), ADP-cliff tiers."
DEFAULT_POSITIONS = ("RB", "WR", "TE", "QB")

# Cheat-sheet team codes → Sleeper team_id where they differ. Used only to break
# ties when two rostered players share a normalized name; never a hard filter.
TEAM_ALIASES = {
    "LA": "LAR",   # cheat sheet uses LA for the Rams
    "LAR": "LAR",
    "LV": "LV",
    "OAK": "LV",
    "WAS": "WAS",
    "WSH": "WAS",
    "JAX": "JAX",
    "JAC": "JAX",
    "SD": "LAC",
    "STL": "LAR",
}

# Known name mismatches: normalized cheat-sheet name → the player's real name.
# Both sides are normalize()'d at compare time, so generational suffixes (Jr/III)
# are already stripped on both — only genuine nickname/spelling differences
# belong here. Extend as roster sources drift.
NAME_OVERRIDES = {
    "hollywood brown": "marquise brown",
    "marquise hollywood brown": "marquise brown",
    "hollywood": "marquise brown",
    "gabe davis": "gabriel davis",
    "chig okonkwo": "chigoziem okonkwo",
    "cam ward": "cameron ward",
    "tank dell": "nathaniel dell",
    "josh palmer": "joshua palmer",
    "mike thomas": "michael thomas",
}

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize(name: str) -> str:
    """Lowercase, strip accents/punctuation and generational suffixes."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = n.lower().replace(".", " ").replace("'", "").replace("-", " ")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    parts = [p for p in n.split() if p and p not in SUFFIXES]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Tiering
# ---------------------------------------------------------------------------


def derive_tiers(players: list[dict], pct: float, floor: float) -> list[int]:
    """Assign 1-based, non-decreasing tiers from ADP cliffs down the board.

    A new tier starts whenever the ADP gap to the *next* player exceeds
    ``max(floor, pct * current_adp)`` — a threshold that grows with draft
    depth. That's deliberate: consecutive ADPs are packed tightly at the top
    (a 3-4 pick gap there is a real cliff) and spread out late (a 4-pick gap in
    the 200s is noise). A fixed absolute gap would dump the whole first round
    into one tier and shred the back; the percentage rule instead yields tight,
    ascending tiers up top — Tier 1 = the elite handful — widening downward.

    Tiers only ever increment, so the sequence is non-decreasing by
    construction (a hard requirement of rankings_service). Players missing ADP
    inherit the current tier.
    """
    tiers: list[int] = []
    tier = 1
    n = len(players)
    for i, p in enumerate(players):
        tiers.append(tier)
        adp = p.get("adp")
        nxt = players[i + 1].get("adp") if i + 1 < n else None
        if adp is None or nxt is None:
            continue
        threshold = max(floor, pct * float(adp))
        if (float(nxt) - float(adp)) > threshold:
            tier += 1
    return tiers


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def build_player_index(db):
    """normalized full_name → list of Player rows (dupes kept for disambig)."""
    from app.models.player import Player

    idx: dict[str, list] = {}
    for p in db.query(Player).all():
        idx.setdefault(normalize(p.full_name), []).append(p)
    return idx


def match_player(row: dict, idx: dict[str, list]) -> tuple[str | None, str]:
    """Return (player_id, reason). reason is 'exact' / 'team' / 'pos' /
    'override' / 'ambiguous' / 'missing'."""
    key = normalize(row["name"])
    key = normalize(NAME_OVERRIDES.get(key, key))
    cands = idx.get(key, [])
    if not cands:
        return None, "missing"
    if len(cands) == 1:
        return cands[0].id, "exact"
    # Disambiguate: prefer same team, then same position.
    want_team = TEAM_ALIASES.get(row.get("team") or "", row.get("team"))
    by_team = [c for c in cands if (c.team_id or "").upper() == (want_team or "").upper()]
    if len(by_team) == 1:
        return by_team[0].id, "team"
    pool = by_team or cands
    by_pos = [c for c in pool if (c.position or "").upper() == (row.get("position") or "").upper()]
    if len(by_pos) == 1:
        return by_pos[0].id, "pos"
    return None, "ambiguous"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default=str(DATA_FILE), help="Parsed cheat-sheet JSON board")
    ap.add_argument("--name", default=DEFAULT_SET_NAME, help="RankingSet name")
    ap.add_argument("--season", type=int, default=None, help="Override season")
    ap.add_argument(
        "--positions", default=",".join(DEFAULT_POSITIONS),
        help="Comma list of positions to include (default RB,WR,TE,QB)",
    )
    ap.add_argument(
        "--tier-pct", type=float, default=0.30,
        help="ADP-cliff sensitivity: new tier when the gap to the next player "
             "exceeds this fraction of current ADP (lower = more tiers)",
    )
    ap.add_argument("--tier-floor", type=float, default=2.0, help="Minimum ADP gap for a tier break")
    ap.add_argument(
        "--adp-note", action="store_true",
        help="Fill each player's note with their pos rank + ADP "
             "(off by default — the board shows a live positional rank now)",
    )
    ap.add_argument("--min-match-rate", type=float, default=0.9)
    ap.add_argument("--publish", action="store_true", help="Publish after loading")
    ap.add_argument("--dry-run", action="store_true", help="Report matches, write nothing")
    args = ap.parse_args()

    positions = {p.strip().upper() for p in args.positions.split(",") if p.strip()}

    with open(args.data) as f:
        board = json.load(f)
    season = args.season or board.get("meta", {}).get("season")

    rows = [r for r in board["players"] if r["position"].upper() in positions]
    rows.sort(key=lambda r: r["rank"])
    print(f"Loaded {len(rows)} rows ({', '.join(sorted(positions))}) for season {season}.")

    from app.db import SessionLocal
    from app.services import rankings_service
    from app.utils.seasons import current_or_upcoming_season

    season = season or current_or_upcoming_season()
    db = SessionLocal()
    try:
        idx = build_player_index(db)

        entries: list[dict] = []
        unmatched: list[dict] = []
        tiers = derive_tiers(rows, args.tier_pct, args.tier_floor)
        for row, tier in zip(rows, tiers):
            pid, reason = match_player(row, idx)
            if pid is None:
                unmatched.append({**row, "reason": reason})
                continue
            if args.adp_note:
                note = f"{row['pos_rank']} · ADP {row['adp']:.1f}" if row.get("adp") else f"{row['pos_rank']}"
            else:
                note = ""
            entries.append({"player_id": pid, "tier": tier, "note": note})

        matched = len(entries)
        total = len(rows)
        rate = matched / total if total else 0.0
        n_tiers = len({e["tier"] for e in entries})
        print(f"\nMatched {matched}/{total} ({rate:.0%}) into {n_tiers} tiers.")
        if unmatched:
            print(f"\nUnmatched ({len(unmatched)}) — add to NAME_OVERRIDES and re-run:")
            for u in unmatched:
                print(f"  #{u['rank']:>3}  {u['name']:<24} {u['position']:<3} {u['team'] or '':<4} [{u['reason']}]")

        if args.dry_run:
            print("\n--dry-run: no writes.")
            return 0

        if rate < args.min_match_rate:
            print(
                f"\nAborting: match rate {rate:.0%} < --min-match-rate {args.min_match_rate:.0%}. "
                "Sync rosters or add overrides, then re-run.",
                file=sys.stderr,
            )
            return 2

        # Upsert the set, then full-board replace (dense ranks from list order).
        existing = next(
            (s for s in rankings_service.list_sets(db, season=season) if s["name"] == args.name),
            None,
        )
        actor = os.environ.get("SEED_ACTOR", "seed_ranking_board.py")
        if existing:
            set_id = existing["id"]
            print(f"\nUpdating existing set #{set_id} '{args.name}'.")
        else:
            s = rankings_service.create_set(
                db, name=args.name, season=season, format="ppr",
                description=DEFAULT_DESCRIPTION, created_by=actor,
            )
            set_id = s.id
            print(f"\nCreated set #{set_id} '{args.name}' (season {season}, ppr).")

        rankings_service.replace_entries(db, set_id, entries, actor=actor)
        print(f"Loaded {matched} entries as a draft.")

        if args.publish:
            res = rankings_service.publish(db, set_id, actor=actor)
            print(f"Published version {res.get('version')}.")
        else:
            print("Left as draft — review and publish in /admin › Fantasy Rankings.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
