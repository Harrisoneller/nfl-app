# Prediction Models — Reference Spec

A complete inventory of every prediction surface, the math behind each, the
inputs it consumes, and where it lives in the codebase.

---

## 1. Team Elo Ratings

**File:** `backend/app/services/elo_service.py`

The foundation. Every team-level prediction (game outcomes, season sims, H2H,
betting edge) ultimately reads from these ratings.

**Constants**
| Param | Value | Rationale |
|---|---|---|
| `K_FACTOR` | 20 | Standard NFL Elo update rate. Lower than NBA (~25) since fewer games = less noise per outcome. |
| `HOME_FIELD_ADVANTAGE` | 55 | ≈ +2.2 spread points; near league-historical HFA. |
| `INITIAL_RATING` | 1500 | League average. |
| `SEASON_REGRESSION` | 0.75 | Carry forward 75% of last year's rating, blend 25% toward 1500 — so good teams cool off, bad teams heat up. |
| `ELO_PER_POINT` | 25 | Elo difference per point of expected margin. |

**Math**

Win probability of home team:
```
diff = home_rating - away_rating + HFA
win_prob = 1 / (1 + 10^(-diff/400))
```

Predicted spread (sportsbook convention; negative = home favored):
```
spread = -(diff / 25)
```

Update after a played game (538-style margin-of-victory dampening):
```
expected_home = win_probability(home_rating, away_rating)
actual_home = 1 if home_margin > 0 else 0 (0.5 for ties)
mov_mult = ln(|margin| + 1) × (2.2 / (|elo_diff| × 0.001 + 2.2))
delta = K × mov_mult × (actual_home - expected_home)
new_home_rating = home_rating + delta
new_away_rating = away_rating - delta
```

**Persistence**
- Stored per `(team_id, season, week)` in `team_elo_ratings` table.
- `Week 0` = pre-season baseline (after season regression applied).
- `Week N` = rating after game N.
- Backfilled for the last 6 seasons on first boot; refreshed weekly.

**Inputs**
- Completed game scores (`home_score`, `away_score`) from `nfl-data-py` schedules.
- Home/away assignment, neutral-site flag (Super Bowl, international games).

**Letter grade** (casual-fan display):
```
A+: ≥1660    B+: ≥1550    C+: ≥1460    D: ≥1370
A:  ≥1620    B:  ≥1520    C:  ≥1430    F: <1370
A-: ≥1580    B-: ≥1490    C-: ≥1400
```

---

## 2. Game-Level Predictions (Elo)

**File:** `backend/app/services/predictions_service.py` → `predict_game`

The simplest user-visible prediction: win probability, predicted spread,
predicted total, predicted score, and a one-word game-script label.

**Inputs**
- `home_rating`, `away_rating` — current Elo ratings
- `home_off_ppg`, `away_off_ppg` — season points-per-game (offense)
- `home_def_ppg_allowed`, `away_def_ppg_allowed` — season points-allowed
- `neutral_site` flag

**Outputs**
- `home_win_prob` and `away_win_prob` — from Elo formula
- `predicted_spread` — from Elo diff
- `predicted_total` — `(home_off + away_def_allowed)/2 + (away_off + home_def_allowed)/2`
- `predicted_home_score` / `predicted_away_score` — derived from total + spread
- `game_script` — text label:
  - `total ≥ 48` → **Shootout**
  - `total ≤ 40` → **Defensive grind**
  - `|spread| ≥ 7` → **Blowout potential**
  - `|spread| ≤ 2.5` → **Toss-up**
  - else → **Methodical**

**Where this surfaces**
- Home page hero card (most compelling game of the week)
- Team page → Overview → "Next game" card
- Team page → Predictions tab (full slate)
- Betting tab → Edge vs market
- H2H page → Prediction hero
- AI tool: `get_current_scoreboard`

---

## 3. Monte Carlo Season Simulator

**File:** `backend/app/services/predictions_service.py` → `simulate_season`

Powers the "what's our predicted record / playoff %" headline. Runs 10,000
trials of the remaining season schedule.

**Algorithm**
```
For each simulation (10,000 of them):
    For each unplayed game on the schedule:
        wp = win_probability(home_elo, away_elo)
        if random() < wp: home wins
        else: away wins
    Compute division winners (top of each 4-team div)
    Compute wildcards (top 3 non-winners per conf)
    Mark playoff appearances
    Mark "best record in conference" as SB appearance proxy
    Record total wins per team
```

**Output per team**
- `mean_wins`, `p5_wins`, `median_wins`, `p95_wins`
- `division_winner_pct`
- `playoff_pct`
- `sb_appearance_pct` (crude — "best record in conference," not a real bracket sim)

**Inputs**
- Current Elo ratings (held constant within a single sim — not re-updated game-to-game)
- Remaining schedule
- Banked W/L from already-completed games

**Known limitations**
- Elo doesn't update during a sim, which slightly under-states variance.
- SB appearance % is best-record-in-conference, not bracket simulation.

**Cache:** 30 minutes; pre-warmed on boot.

**Where this surfaces**
- Home page → Projected standings
- Team page → Overview → Season outlook card
- Team page → Predictions tab

---

## 4. ML Layer — XGBoost Margin Model

**File:** `backend/app/services/ml_predictions_service.py`

A second-opinion model that runs alongside Elo predictions. Surfaces on the
team Predictions tab when the model disagrees with Elo by ≥0.5 points.

**Algorithm**
- **Type:** XGBoost regression
- **Target:** `home_margin = home_score - away_score`
- **Hyperparameters:** `n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.85, colsample_bytree=0.85`
- **Training data:** Last 4 completed seasons (configurable via `/predictions/admin/ml/train?seasons=...`)

**Features (10)**
| Feature | Source |
|---|---|
| `home_elo`, `away_elo` | Pre-game Elo |
| `home_off_epa`, `away_off_epa` | Rolling 4-week offensive EPA/play |
| `home_def_epa`, `away_def_epa` | Rolling 4-week defensive EPA/play |
| `rest_days_home`, `rest_days_away` | Days since last game (clamped 3-21) |
| `is_division_game` | 1/0 |
| `week_number` | Schedule week |

**Performance**
- In-sample MAE typically ~9.4 points (NFL spread MAE is ~10-13 for sharp models).
- **Not yet validated out-of-sample** — see `MODELS.md > priorities` below.

**Persistence**
- Trained model saved to `/tmp/nfl_margin_xgb.json` (configurable via `ML_MODEL_DIR`).
- Reloaded into memory on first inference call after restart.

**Trigger**
- Manual via `POST /predictions/admin/ml/train`. Not retrained automatically yet.

---

## 5. Player Game Predictions

**File:** `backend/app/services/player_predictions_service.py` → `player_game_predictions`

Predicts stat lines for a player's next ~8 games. Used on the player Predictions tab.

**Formula (per stat)**
```
baseline = rolling_4_week_mean(player, stat)
volatility = rolling_4_week_std(player, stat)

matchup_adj = 1.0 + 0.10 × opponent_def_epa_z_score
weather_mult = weather_multiplier(forecast, stat)
injury_mult  = injury_multiplier(sleeper_status)

predicted = baseline × matchup_adj × weather_mult × injury_mult
low  = max(0, predicted - 0.67 × volatility)   # ~25th percentile
high = predicted + 0.67 × volatility            # ~75th percentile
```

**Inputs**
- Player's last 4 weeks of game logs (`nfl-data-py` weekly data)
- Opponent's defensive EPA z-score (vs league)
- Weather forecast for the game (Open-Meteo) — temp, wind, precipitation, indoor flag
- Player's current injury status (Sleeper metadata)

**Per-stat coverage by position**
- **QB:** attempts, completions, passing_yards, passing_tds, interceptions, carries, rushing_yards, rushing_tds, fantasy_points_ppr
- **RB:** carries, rushing_yards, rushing_tds, targets, receptions, receiving_yards, receiving_tds, fantasy_points_ppr
- **WR / TE:** targets, receptions, receiving_yards, receiving_tds, fantasy_points_ppr

**Matchup grade**
```
opp_def_z ≥ 1.0 → A  (offense feasts vs weak defense)
opp_def_z ≥ 0.5 → B
opp_def_z ≥ -0.5 → C
opp_def_z ≥ -1.0 → D
opp_def_z < -1.0 → F  (elite defense)
```

**Weather multipliers**
| Condition | Passing | Receiving | Rushing | Fantasy |
|---|---|---|---|---|
| Indoor / dome | ×1.00 | ×1.00 | ×1.00 | ×1.00 |
| Wind ≥ 25 mph | ×0.85 | ×0.88 | (+4% bonus) | weighted blend |
| Wind 15–24 mph | ×0.92 | ×0.94 | unchanged | weighted blend |
| Precip ≥ 0.4 in | ×0.85 | ×0.88 | (+4% bonus) | weighted blend |
| Precip 0.15–0.4 in | ×0.93 | ×0.95 | unchanged | weighted blend |
| Temp ≤ 25 °F | ×0.95 | ×1.00 | unchanged | weighted blend |

**Injury multipliers**
| Status | Multiplier |
|---|---|
| `OUT` / `IR` / `PUP` / `NFI` / `SUSPENDED` | 0.00 |
| `DOUBTFUL` | 0.30 |
| `QUESTIONABLE` | 0.85 |
| `PROBABLE` / `ACTIVE` / `HEALTHY` / unknown | 1.00 |

---

## 6. Player Season Projections

**File:** `backend/app/services/player_predictions_service.py` → `player_season_projection`

YTD totals + projected remaining + final-season totals with confidence bands.

**Formula**
```
ytd_total = sum of weekly stats so far in current season
per_game_pace = rolling_4_week_mean(player, stat)
games_remaining = 17 - games_played

projected_remaining = per_game_pace × games_remaining
projected_final = ytd_total + projected_remaining

# Confidence band assumes weekly independence
season_std = weekly_std × sqrt(games_remaining)
low_final  = ytd + max(0, projected_remaining - 0.67 × season_std)
high_final = ytd + projected_remaining + 0.67 × season_std
```

**Inputs**
- Same as game predictions
- Current season YTD weekly data (or fallback to last completed season in offseason)

---

## 7. Award Race Odds

**File:** `backend/app/services/awards_service.py`

MVP + OPOY leaderboards derived from existing player percentile data.

**MVP composite (QB-only)**
| Metric | Weight |
|---|---|
| passing_yards | 0.18 |
| passing_tds | 0.18 |
| epa_per_play | 0.18 |
| fantasy_points_ppr | 0.16 |
| passer_rating | 0.12 |
| yards_per_attempt | 0.10 |
| success_rate | 0.08 |

**OPOY composite (RB/WR/TE)**
| Metric | Weight |
|---|---|
| fantasy_points_ppr | 0.20 |
| receiving_yards | 0.18 |
| rushing_yards | 0.18 |
| receiving_tds | 0.12 |
| rushing_tds | 0.12 |
| wopr | 0.10 |
| epa_per_play | 0.10 |

**Math**
```
For each player:
    composite = Σ (percentile_rank(player, metric) × weight)
For top 10:
    odds_pct = softmax(composite_scores, temperature=8.0)
```

Tempered softmax (temperature 8) sharpens the distribution so the leader gets
realistic odds (~30-40% for a dominant season) instead of being flat.

**Output**
- Sorted top 10 by composite
- Each with `composite_score` (0-100) and `odds_pct` (normalized via softmax)

**Caveat:** This is a *performance* ranking, not actual betting odds. The
formula doesn't account for narrative bias (winning team often gets MVP
boost), defensive players (DPOY skipped because we don't track them deeply),
or rookies.

---

## 8. Betting / Edge Detection

**File:** `backend/app/services/betting_service.py`

**Historical ATS/O/U records**
- Computed from `spread_line` and `total_line` in `nfl-data-py` schedules (closing lines, free).
- 5 seasons back by default.
- Splits: SU, ATS, O/U, as favorite/underdog, home/away.

**ATS math (home perspective)**
```
home_covered = (home_score - away_score) > spread_line
home_push    = (home_score - away_score) == spread_line
away_covered = (away_score - home_score) > -spread_line
```

**Edge vs market**
- Pulls current sportsbook odds from The Odds API.
- Takes median of all book spreads + totals per game (the "consensus").
- Compares to our predicted spread/total.

```
edge_spread = market_spread_home - our_predicted_spread
edge_total  = our_predicted_total - market_total
```

- `edge_spread > 0` → we think home is even more favored than market does
- `|edge_spread| ≥ 2.0` → flagged as a "value bet"

**Where this surfaces**
- Team page → Betting tab → Edge vs market
- Home page → League-wide best bets card
- API: `/betting/edge`, `/betting/best-bets`

---

## 9. Head-to-Head Cross-Side Matchup

**File:** `backend/app/services/h2h_service.py` → `_cross_side_matchups`

The analyst-style "strength vs weakness" view.

**Paired metrics (offense vs opponent's defensive counterpart)**
| Offense | Opp defense allowed | Label |
|---|---|---|
| `points_per_game` | `points_allowed_per_game` | Scoring |
| `off_epa_per_play` | `def_epa_per_play` | EPA / play |
| `off_success_rate` | `def_success_rate` | Success rate |
| `off_yards_per_play` | `def_yards_per_play` | Yards / play |
| `off_explosive_play_rate` | `def_explosive_play_rate` | Explosive rate |
| `off_red_zone_td_pct` | `def_red_zone_td_pct` | Red-zone TD% |
| `off_third_down_pct` | `def_third_down_pct` | 3rd-down conv% |

**Math (per pairing)**
```
expected = (offense_value + opp_defense_allowed) / 2
delta    = offense_value - opp_defense_allowed
offense_has_edge = delta > 0
```

`expected` is the model's quick answer to "what should we expect for this
stat in this game?" — midpoint of the offense's pace and what the opponent
typically allows. Green delta = offense projects above what the defense
typically allows.

---

## Data sources used

| Source | Use | License/Cost |
|---|---|---|
| `nfl-data-py` (nflverse) | Schedules, scores, play-by-play, weekly player stats, closing spreads/totals | Free, open |
| Sleeper API | Player metadata + injury status + fantasy trending | Free |
| ESPN public JSON | Live scoreboard | Free (unofficial) |
| The Odds API | Current sportsbook lines | Free tier 500 req/mo |
| Open-Meteo | Game-time weather forecast | Free, no key |
| RSS feeds | News headlines | Free |
| Reddit (r/nfl + team subs) | Social discussion | Free public JSON |
| xAI Grok | LLM tool-use + widget generation | Pay-per-token |
