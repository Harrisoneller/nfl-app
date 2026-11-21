# Sim panel plan

The game page keeps the existing v5 projection as the headline. Under it, a secondary panel can show three numbers from the drive-level sim: projected total, projected margin from the home team's perspective, and home win probability. The panel is labeled `Sim (research, drive-level simulation)`, shows when the row was frozen, and says the numbers are experimental and not the official projection.

The panel is market-blind. It does not show lines, odds, edges, picks, or recommendations, and it does not compare the sim to the market. The v5 model code and the v5 headline fields are unchanged. The sim payload is a sibling of the prediction, not a field inside it.

## Current research output

The sim lives on `research/nfl-drive-sim` (draft PR 53). This app does not modify that branch and does not import the simulator.

At `0a3212dbf5c48125e2f6ac6be4476bef774acebe`, `backend/research/nfl_drive_sim/freeze_week.py` is not in the tree yet. The per-game writer is `run_p3.py`. Its CSV columns that this panel can publish are:

| Research column | Stored field | Meaning |
| --- | --- | --- |
| `sim_total_mean` | `sim_total` | Mean of simulated home score plus away score |
| `sim_margin_mean` | `sim_margin_home` | Mean of simulated home score minus away score |
| `sim_p_home` | `sim_home_wp` | Share of draws with margin greater than 0 |

`game_id` is `season_week_away_home`, the same id the week board uses. `v5_margin`, `v5_total`, and `v5_p_home` may sit on the freeze file. The importer ignores them. The headline continues to come from the served v5 path.

When `freeze_week.py` lands, it is expected to commit one week of those sim columns, plus the frozen v5 projections and the input hashes, before that week's first kickoff. The importer already accepts that week as JSON (`games` list, research column names or the stored names) or as CSV with the same header. One file is one season-week, one row per `game_id`.

## Storage choice

The store is static JSON at `backend/app/data/sim_freeze/games.json`.

A Postgres table would match the rest of the live app, and it would also need an Alembic migration plus a production write. This batch is a locked file, the same shape as the research freeze. The JSON document is written only by the import script. Request handlers only read it. There is no sim engine in the API process, and there is no request that rebuilds a row.

An empty `games` object ships with the code, so a game with no freeze returns null and the panel stays hidden.

## Data flow

1. On the research branch, before the week's first kickoff, the freeze step writes one week's sim file and commits it with its sha256. That work stays on `research/nfl-drive-sim`.
2. After that commit, and still before kickoff, publish the file from `backend/`:

```
python scripts/import_sim_freeze.py \
  --file frozen_week.json \
  --sha256 <sha256 of that file> \
  --source-commit <research commit> \
  --frozen-at 2026-11-20T18:00:00Z
```

3. The script hashes the file bytes. If the hash differs, it writes nothing.
4. It rejects market columns (lines, odds, moneylines, vegas fields, edges, picks).
5. It writes one record per game: `game_id`, `season`, `week`, `sim_total`, `sim_margin_home`, `sim_home_wp`, `frozen_at`, `source_commit`, `sha256`.
6. `GET /predictions/sim/{game_id}` returns `{ "sim": null }` when that id is absent, and the record otherwise. `/predictions/games`, `/predictions/week-slate`, and `/h2h/{away}/{home}` add the same object as `sim` on the game. A hypothetical H2H matchup is null.
7. The home featured game, the week preview, the week slate, and the H2H prediction card render the panel only when `sim` is non-null. The v5 headline above it is the existing prediction.

## Weekly timing

The freeze commit happens before the week's first kickoff. The import runs after that commit and still before kickoff, so the file the API serves is the pre-kickoff freeze. The API does not look at the clock and does not run the sim after kickoff. Refreshing the page after kickoff shows the same stored numbers.

## Why publishing cannot change the frozen numbers

The API has no writer and no sim import. The only writer is the import, and it checks the sha256 before it reads rows. A mismatch leaves the store untouched.

If a `game_id` is already stored, the import accepts the file only when every published field is identical, including the numbers, the timestamp, the source commit, and the hash. Any difference is rejected and the file on disk is not replaced. A second import of the same record is a no-op. Serving the panel, opening the game page, or deploying again without a new import does not recompute the sim.

The v5 headline is produced by the existing model path. It is not read from this JSON file, and the import does not call v5.

## What has to happen later to show this in production

This draft does not deploy and does not change Railway or environment variables.

To show the panel on the live site later:

- Deploy this API and frontend. No new environment variable and no database migration.
- Each week's numbers reach the API only when `backend/app/data/sim_freeze/games.json` from a successful import is in the deployed build. The import itself is a local or CI batch against the frozen research file. It is not a Railway job and it must stay before that week's first kickoff.
