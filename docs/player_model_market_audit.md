# Player model market audit

Status: read-only verification. No coefficients were refit, no backtest was
run, and no environment variable was read.

Standing rule being checked (Harrison, 2026-10-22): projections are
market-blind. Lines, odds, spreads, implied team totals, props, and
prediction-market prices are not inputs, priors, blend weights, or fit or
calibration targets. Market data is allowed only after a projection exists,
to compute edge and to grade.

The shipped player model is `player-opp-v1`
(`backend/app/services/player_opportunity_model.py`). It breaks that rule.
The break is in the fitted team-volume coefficients and in a prop-anchor
shift applied to the quantile function before the number is published.
`over_prob` itself does not read a price. It prices a line off whatever
quantile function it is handed, including one the anchor has already moved.

## (a) Market data as a feature, a fit target, or a source of coefficients

### Shipped team-volume coefficients

The model writes team volume as

```text
team volume = a + b·team EWMA + c·(expected margin) + d·(implied points)
```

at `backend/app/services/player_opportunity_model.py:20-25`. The fitted
numbers are hardcoded. The comment names the two market-shaped regressors
and the sample they were fit on:

```python
# backend/app/services/player_opportunity_model.py:53-59
# Team volume regressions (OLS, 2016–2025 team-games). spread_team is the
# team's expected margin (+ = favoured); imp_pts its implied points.
TEAM_VOLUME = {
    "t_tgt": {"const": 1.4668, "ewm": 0.6372, "spread_team": -0.2128, "imp_pts": 0.4501},
    "t_car": {"const": 19.6224, "ewm": 0.4934, "spread_team": 0.3015, "imp_pts": -0.2724},
    "t_att": {"const": 3.299, "ewm": 0.594, "spread_team": -0.2251, "imp_pts": 0.4593},
}
```

All three `spread_team` and `imp_pts` coefficients are nonzero. They are not
a dormant column. `team_volume` applies every term:

```python
# backend/app/services/player_opportunity_model.py:216-218
def team_volume(kind: str, ewm: float, spread_team: float, imp_pts: float) -> float:
    c = TEAM_VOLUME[kind]
    return max(0.0, c["const"] + c["ewm"] * ewm + c["spread_team"] * spread_team + c["imp_pts"] * imp_pts)
```

`structural` multiplies the player's EWMA share by that volume for targets,
carries, and pass attempts (`player_opportunity_model.py:228-247`).
`project` feeds the same two arguments into `structural`
(`player_opportunity_model.py:293-305`).

The quantile coefficients sit on top of that structural projection. The
comment says they are linear quantile regressions of the stat on the
structural hat, fit 2016–2024 and validated 2025–26
(`player_opportunity_model.py:62-63`, coefficients at lines 64–92). The fit
target of those regressions is the player stat, not a price. The feature is
the structural hat, and the hat is a function of `spread_team` and
`imp_pts`. Market information that entered the team-volume fit is inside
every shipped quantile coefficient. Zeroing the two coefficients at runtime
would not remove it: the intercept and the EWMA slope were estimated jointly
with them.

### The training script is not in the repo

`TEAM_VOLUME` and `QUANTILE_COEFS` first appear in commit `432197eb`
("Model v5: QB-aware game ratings, median-first player props, nflverse data
fixes"). That commit adds `player_opportunity_model.py` with the coefficients
already pasted in. The diff has no notebook, no player-fit script, and no
design matrix. `git ls-files` has no `*.ipynb`. `backend/scripts/` contains
`backtest_v5.py`, which refits the **game** model's stage-2 coefficients
(`MARGIN_COEFS` / `TOTAL_COEFS`), not the player team-volume or quantile
coefficients. Its own header says so (`backend/scripts/backtest_v5.py:1-13`).

What the fit actually regressed on is therefore not recoverable from the
repo. The only provenance is the comment: OLS on 2016–2025 team-games, with
a column called `spread_team` ("expected margin") and a column called
`imp_pts` ("implied points"). Those names are the market quantities the
standing rule bans as fit inputs. There is no checked-in code that shows a
market-blind design matrix for these numbers.

### What production passes into those slots

Production does not pass this week's sportsbook line into `team_volume`.
It passes the game model's predicted scores.

`league_game_environments` calls `predictions_service.predict_game` and
stores `predicted_home_score` / `predicted_away_score` as `exp_pts_for` /
`exp_pts_against` (`player_predictions_service.py:478-502`). The opportunity
path then sets:

```python
# backend/app/services/player_predictions_service.py:783-786
    proj = opp_model.project(
        opp["profile"], stat, opp.get("team_ewm") or {},
        imp_pts=exp_for, spread_team=exp_for - exp_against, scale=scale,
    )
```

`exp_for - exp_against` is the model margin, and `exp_for` is the model
team total. The service's own module doc calls these "implied points"
(`player_predictions_service.py:11-13`). The response field is
`team_implied_pts` (`player_predictions_service.py:970-974` and
`1904-1908`).

Those model scores are not market-blind. When v5 is on (committed default
`game.v5_enabled = 1`, `param_registry.py:220-222`), `predict_game` takes
margin and total from the v5 payload
(`predictions_service.py:583-588` and `610-613`). v5's margin coefficients
include a past-closing-spread prior:

```python
# backend/app/services/team_ratings_v5.py:83-89
MARGIN_COEFS: dict[str, float] = {
    "mkt_d": 0.669, "epa_d": 6.266, "pts_d": 0.644, "st_d": 0.961, "qb_d": 34.977,
    "hfa": 1.824,
}
# Fallback when no historical lines are available (no market prior).
MARGIN_COEFS_NO_MKT: dict[str, float] = {
```

`market_ratings` least-squares-fits team ratings to previous games'
`spread_line` (`team_ratings_v5.py:459-498`). `WeekModel.features` writes
`mkt_d` whenever that fit exists (`team_ratings_v5.py:537-538`).
`WeekModel.predict` then uses `MARGIN_COEFS` rather than
`MARGIN_COEFS_NO_MKT` (`team_ratings_v5.py:550-565`). The live weight is
`v5.coef_market_prior`, default 0.669 (`param_registry.py:236-238`,
applied in `game_model_v5_service.py:70-75`). `TOTAL_COEFS`
(`team_ratings_v5.py:93-96`) has no market column, but implied points are
`(total ± margin) / 2`, so the margin prior enters both `imp_pts` and
`spread_team`.

The no-market coefficient vector exists and is used only when historical
lines are missing. It is not the production default.

`backend/scripts/backtest_v5.py` is the script that produces those game-model
coefficients. It fits stage-2 OLS with `mkt_d` in the margin design and
prints coefficients to paste into `MARGIN_COEFS`. That is a market feature
in a fitted game model that the player layer then consumes. Grading v5
against the closing line inside that script is a post-projection use. Fitting
`mkt_d` as a regressor is not.

### The published "out of sample" window overlaps the fit window

`docs/MODEL_V5.md:145-153` reports player-opp-v1 MAE on 2021–26 as out of
sample. The coefficient comments say team volume was fit on 2016–2025 and
the quantile map was fit on 2016–2024 (`player_opportunity_model.py:53-63`).
2021–2024 is inside both fit windows. 2025 is inside the team-volume fit
and is the stated quantile validation year. The repo contains no script that
would let a reader check whether those windows were walk-forward or a single
in-sample OLS. The table should not be treated as market-blind or as
fully out of sample for the shipped player coefficients.

## (b) `over_prob` and every caller

The function that prices a prop off the opportunity model's quantile grid is
`player_opportunity_model.over_prob`:

```python
# backend/app/services/player_opportunity_model.py:271-272
def over_prob(q: dict[Any, float], line: float) -> float:
    """P(X > line) by linear interpolation of the CDF through the quantile grid."""
```

The body (`player_opportunity_model.py:273-290`) interpolates the CDF. It
takes a quantile dict and a line. It does not take an odds price, a spread,
or a total. Using it to turn a finished projection into P(over) for an edge
is allowed. Using it on a quantile function that was already shifted toward
a prop (section c) makes the edge circular.

### Direct caller

`player_projection_engine.projection_over_prob` calls it whenever the
projection dict carries quantiles:

```python
# backend/app/services/player_projection_engine.py:479-484
    q = projected.get("quantiles") if isinstance(projected, dict) else None
    if q:
        from . import player_opportunity_model as opp

        return opp.over_prob({float(k): float(v) for k, v in q.items()}, float(line))
    return stat_over_prob(float(projected["mean"]), float(projected["sd"]), float(line))
```

If there is no quantile grid, it falls through to
`player_projection_engine.stat_over_prob` (`player_projection_engine.py:452`),
a truncated normal. That function also does not read a price. It is the
pricing function for the posterior path (TDs, thin samples, admin levers).

### Callers of `projection_over_prob`

| Caller | Lines | Role |
|---|---|---|
| `player_predictions_service.stat_over_probability` | `player_predictions_service.py:1166-1193` | Next-game P(over) for an arbitrary line. Reads `games[0]`, which is the anchored game. |
| `player_props_service._attach_model` | `player_props_service.py:292-322` | Consensus prop row: model P(over) minus `market_over_prob`. The subtraction is an edge. The distribution it reads may already be anchored. |
| `player_props_service.prop_board` (`_model_over`) | `player_props_service.py:465-472` | Same probability at each book's line. Falls back to `stat_over_prob` only when the projection has no quantile grid. |
| `backend/tests/test_model_v5.py` | `test_model_v5.py:335` | Test. Not a production path. |

`prop_edges` (`player_props_service.py:356-374`) calls `_attach_model`, so it
is an indirect caller.

### API routes

Registered on the players router (`backend/app/routers/players.py`):

- `GET /players/{player_id}/over-prob` → `stat_over_probability` (`players.py:193-209`)
- `GET /players/{player_id}/props` → `props_for_player` → `_attach_model` (`players.py:183-190`)
- `GET /players/props/board` → `prop_board` (`players.py:74-87`)
- `GET /players/props/edges` → `prop_edges` (`players.py:90-100`)

### Frontend callers

- `frontend/lib/api.ts:1962-1964` `playerOverProb` → `/players/{id}/over-prob`
- `frontend/lib/api.ts:1908` `playerProps` → `/players/{id}/props`
- `frontend/lib/api.ts:1926-1933` `propBoard` → `/players/props/board`
- `frontend/components/players/PlayerPropsCard.tsx:62-67` and `:135-138` (the calculator)
- `frontend/components/players/PropFinderTab.tsx:50-58` and `:307-311`
- Both cards are on the player page Predictions tab (`frontend/app/players/[id]/page.tsx:156-159`) and the players page props tab (`frontend/app/players/page.tsx:94-96`)

### Same name, different functions

These are not callers of `player_opportunity_model.over_prob`:

- `prediction_dist.over_prob` and `dist_model.GameDistribution.over_prob` price game totals.
- `model_lab/game_model.py:105` and `model_lab/player_model.py:296` are lab copies. The lab player function is the truncated normal, not the quantile CDF.
- `overrides_service.py:320` calls `engine.stat_over_prob` to turn an overridden spread back into a win probability. That is a game-margin use of the normal helper.

## (c) Quantile shift toward a prop anchor

The shift is in `_project_stat_opportunity`, after the market-blind-looking
structural projection and before the dict is returned:

```python
# backend/app/services/player_predictions_service.py:794-803
    if anchor is not None and mean > 0:
        target = _anchor_target(stat, anchor, sd)
        if target is not None:
            target_mean, cap = target
            from . import param_registry as _pr
            k = min(cap, _pr.value("props.anchor_weight_per_book") * anchor["books"])
            shift = k * (target_mean - mean)
            # Shift the whole quantile function: the market moves the center,
            # the skew stays ours.
            q = {lvl: max(0.0, v + shift) for lvl, v in q.items()}
```

The headline `predicted` is then the shifted median (`q[0.5]`, line 814).
Every later `over_prob` call on this dict prices the shifted grid.

### What triggers it

All of the following have to be true:

1. The opportunity path is selected. `_project_stat_for_game` delegates when `opp` is set and the stat is one of the eight supported stats (`player_predictions_service.py:722-724`). `_opp_inputs` returns `None` when the player has no profile, no GSIS id, or an admin input lever (`player_predictions_service.py:145-160`). Those players stay on the posterior path.
2. An anchor dict is passed. Anchors come from `_market_anchors` / `_bulk_market_anchors`, which read the latest `PlayerPropSnapshot` rows and build a consensus (`player_predictions_service.py:631-671` and `1746-1764`; consensus math in `player_props_service.py:268-289`).
3. `_anchor_from_consensus_item` keeps the row only when the market maps to a volume, yardage, or scoring stat, the line is present, and at least two books quote it (`player_predictions_service.py:631-652`).
4. The projection mean is positive.
5. `_anchor_target` returns a target (`player_predictions_service.py:685-701`). For volume and yardage that target is `market_implied_mean(line, over_prob, sd)` (`player_projection_engine.py:510-526`): `m = line + sd·Φ⁻¹(P(over))`, falling back to the bare line when the de-vigged probability is missing or outside `[0.08, 0.92]`, and clamped by `props.price_shift_cap_sd` (default 0.80). Scoring stats invert a Poisson rate (`player_projection_engine.py:529-560`). Scoring stats are not in `SUPPORTED_STATS`, so they do not take this quantile shift. They take the mean shift below.
6. The weight `k` is positive.

### Where the anchor is attached

- Player game projections: only the first remaining game (`gi == 0`). Later games in the same response are unanchored (`player_predictions_service.py:906-908` and `941-944`).
- Weekly board: every player on the requested slate week (`player_predictions_service.py:1808` and `1857-1870`).
- Season projections do not call `_project_stat_opportunity`. They scale the posterior mean (`player_predictions_service.py:1084-1096`). No prop quantile shift there.

### Sibling mean shift (posterior path)

When the opportunity model is not used, `_project_stat_for_game` still pulls the mean toward the same market target. It does not shift a quantile grid, because that path has none:

```python
# backend/app/services/player_predictions_service.py:737-743
    if anchor is not None and mean > 0:
        target = _anchor_target(stat, anchor, sd)
        if target is not None:
            target_mean, cap = target
            from . import param_registry as _pr
            k = min(cap, _pr.value("props.anchor_weight_per_book") * anchor["books"])
            mean = mean + k * (target_mean - mean)
```

This is how TD and INT props, and any volume stat that fell off the opportunity path, still move toward the book.

### Defaults

Committed registry defaults (`param_registry.py:351-362`):

| Key | Default | Meaning |
|---|---|---|
| `props.anchor_weight_per_book` | 0.12 | Pull per book |
| `props.anchor_weight_cap` | 0.40 | Cap for volume and yardage |
| `props.anchor_weight_cap_scoring` | 0.30 | Cap for the Poisson TD/INT target |
| `props.price_shift_cap_sd` | 0.80 | Cap on the price-implied mean, in SDs |

With those defaults and two books, `k = 0.24`. At four or more books, `k` hits the 0.40 cap. There is no committed value of 0.

Module-level copies of the same three weights sit at
`player_predictions_service.py:680-682` and are not read. The live values
are `param_registry.value`, which is preview overlay, then DB override, then
the code default (`param_registry.py:875-884`).

`model_lab/player_model.py:282-291` is an older line-only anchor (weight
0.12 per book, cap 0.40, no de-vigged price, volume/yardage only). It is not
imported by the backend request path.

### Routes and screens that publish the shifted distribution

The shift is inside the projection dict, so any client of that dict displays
it. `over_prob` is only the pricing step.

| Surface | Route | Frontend |
|---|---|---|
| Next-game projections | `GET /predictions/players/{id}/games` (`predictions.py:157-165`) | `PlayerGamePredictionsCard` via `api.playerGamePredictions` (`PlayerProjections.tsx:187-192`). Rendered on the player Predictions tab (`players/[id]/page.tsx:158`). An anchor draws a mark (`PlayerProjections.tsx:283-293`). |
| Weekly start/sit board | `GET /players/projections/weekly` (`players.py:45-58`) | `WeeklyBoardTab` (`WeeklyBoardTab.tsx:49-56`), players page weekly tab (`players/page.tsx:91-92`). Also the admin overrides tab (`PlayerOverridesTab.tsx:91`). |
| Prop card, prop board, over-prob calculator | section (b) | They price the next-game distribution, which is the shifted one. |

Season leaderboard (`GET /players/projections/leaderboard`) does not apply
this shift. It applies the ADP anchor in section "other paths".

## (d) Is the quantile shift active in prod?

Yes, on the production code path. Confidence is high for the code and the
committed defaults, and is not a statement about the live database.

Evidence that it is on, from code only:

- The shift is not behind a feature flag. Nothing in the request path checks a boolean before applying it. The condition is "an anchor exists and the weight is positive."
- Committed defaults make the weight positive (0.12 per book, cap 0.40). No committed config sets them to 0.
- The frontend calls the routes that apply it: the player Predictions tab and the weekly board. Both are ordinary pages, not an admin-only preview.
- Ingestion that fills `player_prop_snapshots` is on the odds cron (`scheduler.py:186-194`) and calls `refresh_player_props`.

What this audit did not establish:

- `param_registry.value` will use a DB override if one is stored. The prod rows for `props.anchor_weight_per_book`, `props.anchor_weight_cap`, `props.anchor_weight_cap_scoring`, and `props.price_shift_cap_sd` were not read. They are not env vars.
- New snapshots are fetched only when `player_props_enabled` is true and `odds_api_key` is non-empty (`player_props_service.py:100-103`). Committed defaults: `player_props_enabled = True` (`config.py:200`, env `PLAYER_PROPS_ENABLED`) and `odds_api_key = ""` (`config.py:128`, env `ODDS_API_KEY`). Prod values of both are unverified. This audit did not read them.
- Whether the prod table currently holds a two-book consensus for any player is unverified. With no snapshot, `k` is never applied and the published quantiles are the unshifted model. The code that would shift them is still the code that serves the page.

There is no env var whose value turns the shift off. The closest env vars gate ingestion (`ODDS_API_KEY`, `PLAYER_PROPS_ENABLED`), and their prod values are unverified.

## Other market-touching paths in the player pipeline

These are outside the two suspects above. They are still inside the player
projection path.

1. **v5 past-close prior inside every opportunity projection.** Covered in
   section (a). `spread_team` and `imp_pts` at runtime are model scores that
   include `mkt_d` unless historical lines are missing. Default weight 0.669.
   `game.v5_enabled` default is 1, so this is the path the player environment
   actually builds (`player_predictions_service.py:461-485`).

2. **Posterior game-environment multiplier.** Stats that miss the opportunity
   model (TDs, interceptions, thin histories, admin levers) are scaled by
   `game_environment_multiplier` (`player_projection_engine.py:403-440`).
   The elasticities (`player.script_pass_per_pt` default −0.008,
   `player.script_rush_per_pt` default 0.012, scoring elasticities at
   `param_registry.py:287-328`) are hand-set registry defaults, not a fitted
   market regression. The margin and team points they multiply are the same
   v5 outputs, so the market prior still enters. The season projection uses
   this multiplier and does not use `player-opp-v1`
   (`player_predictions_service.py:1084-1096`).

3. **Neutral-environment display ratio.** The opportunity path reports
   `env_multiplier` against a hardcoded "22 implied pts, pick'em"
   (`player_predictions_service.py:823-826`). The constant is not a live
   line. It is a display baseline. Listed so it is not mistaken for one.

4. **ADP season-board anchor.** `projection_leaderboard` shrinks fantasy
   means toward an ADP rank before sorting
   (`player_predictions_service.py:1283-1300`). `apply_adp_anchor`
   (`fantasy_market_service.py:203-259`) does
   `anchored = (1 - w) * model + w * market_pg` with `w = 0.55` preseason,
   decaying 0.045 per week to a 0.15 floor
   (`param_registry.py:374-378`, `fantasy_market_service.py:54`). That is a
   market prior on a published player projection. The players page season tab
   calls `projectionLeaderboard` (`frontend/app/players/page.tsx:128`).
   ADP is a draft-market price, not a sportsbook prop. The standing rule's
   list is lines, odds, spreads, implied totals, props, and prediction-market
   prices. ADP is adjacent: the code calls it a market anchor and it changes
   the number users see. It is not the weekly player-opp-v1 distribution.

5. **Prop edge after the fact.** `player_props_service` subtracting
   `market_over_prob` from model P(over) (`player_props_service.py:319-323`
   and `480-483`) is the use the rule allows, provided the model
   distribution was not already fit or shifted toward that price. Today the
   distribution was. The edge is not an independent check.

6. **Lab anchor.** `model_lab/player_model.py:288-291` and the methodology
   note at `model_lab/METHODOLOGY.md:188`. Not on the request path.

## What is not a violation

- Reading a closing line to grade a finished projection, or to compute an
  edge beside it. `docs/MODELS.md:431-445` describes game headlines as
  unblended, with the line shown next to them. That pattern is the one the
  player layer does not follow.
- `backtest_player_projections` (`player_predictions_service.py:2002-2111`)
  scores the v2 posterior against actual stats. It does not fit to a line.
  It also does not exercise `player-opp-v1`.
