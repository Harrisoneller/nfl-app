# Cheat sheet → Fantasy Rankings board

Loads a ranked cheat sheet into a custom `RankingSet` (the /admin "Fantasy
Rankings" board), matching players to your live roster and deriving tiers from
ADP cliffs. It uses `rankings_service` directly, so every write is audited and
the board lands as an editable **draft** — nothing goes public until you publish
it in /admin.

## Files
- `data/full_ppr_top300_2026.json` — parsed cheat sheet (all 300, source of truth).
- `seed_ranking_board.py` — matcher + tierer + loader.
- `clear_board_notes.py` — empties per-player notes on an existing board.

## Notes column
The board editor shows a **live positional rank** (RB5, WR12…) computed from
board order, so per-player notes are left empty by default. Pass `--adp-note` to
the seed to fill them with `<pos rank> · ADP <n>` instead. To wipe notes on a
board you've already built (order and tiers untouched):

```bash
python scripts/clear_board_notes.py --dry-run   # show what would change
python scripts/clear_board_notes.py             # clear the default board
```

Run it after saving edits in /admin (it operates on the saved draft), then
re-publish.

## Run it
From the repo root, with the backend venv active and `DATABASE_URL` pointing at
the same Postgres the API uses:

```bash
python scripts/seed_ranking_board.py --dry-run   # match report, no writes
python scripts/seed_ranking_board.py             # load RB/WR/TE/QB as a draft
```

Then open **/admin › Fantasy Rankings**, review "Full PPR Top 300 (2026)",
reshape as you like, and hit Publish.

## Options
| Flag | Default | Purpose |
|------|---------|---------|
| `--positions` | `RB,WR,TE,QB` | Add `K,DST` for the full 300 |
| `--tier-pct` | `0.30` | Lower = more, tighter tiers |
| `--tier-floor` | `2.0` | Minimum ADP gap to break a tier |
| `--min-match-rate` | `0.9` | Abort if fewer than this fraction match |
| `--dry-run` | — | Report matches only |
| `--publish` | — | Publish immediately after loading |

## Matching
Names are normalized (accents, punctuation and Jr/III suffixes stripped) and
disambiguated by team then position. Genuine nickname/spelling gaps live in the
`NAME_OVERRIDES` map (e.g. Tank Dell → Nathaniel Dell). Unmatched players are
printed, never silently dropped — add an override and re-run. If the overall
match rate falls below `--min-match-rate` the load aborts, so a stale roster
sync can't quietly produce a half-empty board.

## Tiers
Tiers come from ADP cliffs: a new tier starts when the gap to the next player
exceeds `max(tier_floor, tier_pct × current_ADP)`. The threshold grows with
draft depth, so the top of the board gets tight ascending tiers (Tier 1 = the
elite handful) while the sparse late rounds don't shatter into noise. Tiers are
always non-decreasing down the board.

## Refreshing next season
Drop a new cheat-sheet export in, re-parse to a JSON board, and re-run — the
loader is idempotent (finds the set by season+name and replaces its entries).
