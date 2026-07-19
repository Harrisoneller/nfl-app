# NFL App — Exact Model Methodologies

Extracted from the backend source (`backend/app/services/`). Two models: the **game model** (Elo → margin distribution → Monte Carlo) and the **player model** (Bayesian posterior → game coupling → distribution outputs). Every constant below is the live production value.

---

## 1. Game Projections

**Source files:** `elo_service.py`, `prediction_dist.py`, `predictions_service.py`
**Model version:** `elo-v2`

### 1.1 Elo rating engine (`elo_service.py`)

Classic 538/Inpredictable-style NFL Elo.

| Constant | Value | Meaning |
|---|---|---|
| `K_FACTOR` | 20.0 | Update rate per game |
| `HOME_FIELD_ADVANTAGE` | 55.0 Elo | ≈ +2.2 spread points |
| `SEASON_REGRESSION` | 0.75 | Offseason carry-over toward 1500 |
| `INITIAL_RATING` | 1500.0 | Expansion/default rating |
| `ELO_PER_POINT` | 25.0 | Elo diff per point of spread |

**Win probability (Elo logistic — used only inside rating updates):**

```
diff = home_elo − away_elo + (0 if neutral else 55)
P(home) = 1 / (1 + 10^(−diff/400))
```

**Predicted spread** (sportsbook convention, negative = home favored):

```
spread = −diff / 25
```

**Margin-of-victory multiplier** (dampens blowout updates by favorites):

```
mov = ln(max(|margin|,1) + 1) × 2.2 / (|elo_diff| × 0.001 + 2.2)
```
where `elo_diff` is taken from the *winning* team's perspective (sign-flipped if the away team won).

**Rating update after a game:**

```
delta = K × mov × (actual_home − expected_home)     # actual: 1 win / 0 loss / 0.5 tie
home' = home + delta;  away' = away − delta
```

**Season rollover:** `rating ← 0.75·rating + 0.25·1500`, persisted as Week 0.

### 1.2 Distribution layer (`prediction_dist.py`)

The variance-first contract: every game probability derives from **one** Normal margin distribution, so win prob, spread, and score ranges are mutually consistent.

| Constant | Value |
|---|---|
| `NFL_MARGIN_SIGMA` | 13.5 pts (SD of final margin around expectation) |
| `NFL_TOTAL_SIGMA` | 10.0 pts (SD of total around expectation) |

- `P(home win) = Φ(expected_margin / 13.5)` — NOT the Elo logistic.
- `P(home covers line L) = Φ((expected_margin + L) / 13.5)` (L in sportsbook convention).
- `P(over T) = Φ((expected_total − T) / 10)`.
- Credible intervals: `expected_margin ± z·13.5`.
- Push probability at key numbers from an empirical table: {3: 9.4%, 7: 5.8%, 6: 3.7%, 10: 3.4%, 4: 3.4%, 14: 2.5%, 1: 2.7%, 17: 1.3%, 8: 2.4%, 13: 1.2%}.
- Backtest scoring rules: closed-form Normal CRPS, log loss, Brier. Calibration checked via PIT histogram (U-shaped ⇒ sigma too small; domed ⇒ too large).

### 1.3 Single-game prediction (`predictions_service.predict_game`)

Inputs: home/away Elo, each team's offensive PPG and defensive PPG-allowed (from play-by-play aggregates; league average 22.0 substituted when missing).

```
spread          = elo predicted_spread(...)
expected_margin = −spread
win_p           = Φ(expected_margin / 13.5)

expected_home_pts = home_off_ppg + (away_def_ppg_allowed − 22.0)
expected_away_pts = away_off_ppg + (home_def_ppg_allowed − 22.0)
total             = expected_home_pts + expected_away_pts

# Reconcile the (well-calibrated) Elo margin with the total:
predicted_home_score = (total + expected_margin) / 2
predicted_away_score = (total − expected_margin) / 2
```

Score ranges come from the margin's 50%/80% intervals with the total held at its expectation. A game-script label is assigned: total ≥ 48 "Shootout"; total ≤ 40 "Defensive grind"; |spread| ≥ 7 "Blowout potential"; |spread| ≤ 2.5 "Toss-up"; else "Methodical".

Explainability contributions (heuristic v1) are scaled directional impacts: Elo+HFA gap ÷ 28, offense-vs-defense edge ÷ 2.8, defensive-resistance edge ÷ 2.2, pace environment ÷ 6. An uncertainty layer (`uncertainty_service`) attaches a confidence tier and 80% band on win prob using expected calibration error from the Elo backtest.

### 1.4 Season Monte Carlo (`predictions_service._simulate_season_compute`)

10,000 trials of the remaining schedule, seeded RNG (`random.Random(42)`), cached 24h as a model artifact.

1. **Bank** completed games (wins + point differential).
2. **Correlated team strength** — the key variance fix. Per trial, draw one latent offset per team: `offset ~ N(0, RATING_SIGMA_ELO = 55 Elo)`, held across the team's entire remaining slate. Mean-zero, so central projections are unchanged; only the win-total distribution widens realistically (instead of an over-tight sum of independent coin flips).
3. **Per game:** `expected_margin = (elo_h + off_h − elo_a − off_a + HFA·(not neutral)) / 25`; simulate `margin ~ N(expected_margin, 13.5)`. Margin ≥ 0 → home win. Margin accumulates into simulated point differential (for tiebreakers).
4. **Division winners:** max by (wins, point diff, random). **Wildcards:** top 3 non-winners per conference by (wins, PD, random). **SB appearance heuristic:** best seed per conference.
5. **Aggregate** across trials: mean/std/p5/median/p95 wins, division %, playoff %, SB appearance %.

Season win-total for a team = banked wins + Σ win probabilities (deterministic view) or the simulated distribution (probabilistic view).

---

## 2. Player Projections

**Source files:** `player_projection_engine.py` (pure math), `player_predictions_service.py` (orchestration)
**Model version:** `player-proj-v2`

Every stat is a distribution (mean + SD), and every product number — prop over-probability, anytime-TD, fantasy bands — derives from that one distribution.

### 2.1 Stat kits by position

- QB: attempts, completions, passing_yards, passing_tds, interceptions, carries, rushing_yards, rushing_tds
- RB: carries, rushing_yards, rushing_tds, targets, receptions, receiving_yards, receiving_tds
- WR/TE: targets, receptions, receiving_yards, receiving_tds
- Fantasy points are **derived**, not modeled.

### 2.2 Multi-year prior (`build_prior`)

Up to 3 prior seasons, most recent first, weighted by recency **and** games played:

```
season weights: (1.0, 0.55, 0.30)
weight_i = recency_i × min(games_i, 17)
prior_mean = Σ w_i·mean_i / Σ w_i
```

Game SD blends within-season SDs the same way, plus half the cross-season disagreement (role changes widen the prior):

```
game_sd = sqrt(sd_within² + 0.5·sd_between²)
```

Then:
1. **Aging curve** multiplies the mean (not variance). Peaks: RB 23–26 (decline 5%/yr), WR 24–28 (3.5%), TE 25–29 (3%), QB 26–34 (2%); pre-peak growth −3%/yr short of peak start; clamped [0.72, 1.10].
2. **Marcel-style shrinkage** toward the positional starter mean by `POSITION_SHRINK_K = 3` pseudo-games: `mean = (G·mean + 3·pos_mean)/(G+3)`. Positional means come from the top fantasy-usage pool per position (QB 32, RB 50, WR 70, TE 32 players, ≥8 games).
3. **Prior strength** `n0` by stat class — volume 5.0, yardage 8.0, scoring 12.0 pseudo-games — scaled by `min(1, total_games/12)`, floored at 1.

Rookies with no history get position × draft-capital archetype priors (day1/day2/day3 tables of (mean, sd) per stat) with `n0 = 3`.

### 2.3 Bayesian in-season update (`bayesian_update`)

Conjugate-style shrinkage; the prior is worth `n0` pseudo-games:

```
posterior_mean = (n0·prior_mean + n·obs_mean) / (n0 + n)
posterior_sd²  = (n0·prior_sd² + n·obs_sd²) / (n0 + n)
posterior_sd   = max(posterior_sd, 0.35·sqrt(mean))          # noise floor
talent_sd      = posterior_sd / sqrt(n0 + n)                  # uncertainty about the mean
```

Week 0 the posterior IS the prior; as the season progresses it converges on the actual current-season rate — no hand-tuned switch week.

### 2.4 Role / depth-chart share

Per-game rates are availability-biased (backups' history comes from games they started), so the whole distribution is scaled by expected opportunity share:

- QB {1: 1.0, 2: 0.05, 3: 0.02} — winner-take-all (unknown-depth QB rooms are ranked by projected passing volume; top arm = starter)
- RB {1: 1.0, 2: 0.85, 3: 0.45, 4: 0.15}
- WR {1: 1.0, 2: 1.0, 3: 0.85, 4: 0.50, 5: 0.20}
- TE {1: 1.0, 2: 0.60, 3: 0.25}

Scaling multiplies mean, game_sd, AND talent_sd (a 5%-snap backup shouldn't carry a starter's ±60-yard band). Leaderboards require role ≥ 0.30; weekly boards ≥ 0.15.

### 2.5 Game coupling (`game_environment_multiplier`)

Each per-game mean is conditioned on the game model's output for that matchup (team implied points = `predicted_home/away_score` from `predict_game`):

```
ratio = clamp(team_exp_pts / 22.0, 0.60, 1.45)
env   = ratio ^ elasticity        # elasticity: volume 0.30, yardage 0.55, scoring 1.00

margin = team_exp_pts − opp_exp_pts
tilt   = −0.008·margin  (pass/receiving stats)   |   +0.012·margin  (rush stats)
env   *= 1 + clamp(tilt, ±0.12)

env   *= clamp(defense_factor, 0.75, 1.30)
env    = clamp(env, 0.75, 1.30)                   # overall cap ≈ ±25–30%
```

**Positional defense factors:** per team, families {pass, rush, recv_WR, recv_RB, recv_TE} = yards allowed per game to that family ÷ league average, shrunk 50% toward 1.0, clamped [0.80, 1.25]. >1 = leaky defense = good matchup. Matchup grade: ≥1.10 A, ≥1.03 B, ≥0.97 C, ≥0.90 D, else F.

**Weather multipliers** (outdoor only): passing ×0.92 at 15+ mph wind, ×0.85 at 25+; ×0.93/0.85 by precipitation 0.15/0.4 in; ×0.95 at ≤25°F. Receiving slightly milder (0.94/0.88). Rushing ×1.04 in bad weather.

**Injury multipliers** (Sleeper status): OUT/IR/PUP/NFI/SUSPENDED → 0.0; Doubtful → 0.3; Questionable → 0.85; else 1.0.

**Market anchor** (next game only, volume/yardage stats, ≥2 books): pull toward consensus prop line by `k = min(0.40, 0.12 × books)`: `mean ← mean + k·(line − mean)`. TD/INT lines are thresholds, not medians — never anchored.

Multipliers shift the **mean**; week-to-week game_sd stays put (except role scaling).

### 2.6 Distribution outputs

- **Prop over-probability:** Normal truncated at 0 — `P(X > line) = [1 − Φ((line−μ)/σ)] / [1 − Φ(−μ/σ)]`. The truncation matters for small means (TDs, low-volume receptions).
- **Anytime TD:** Poisson — `P(≥1) = 1 − e^(−λ)`, λ = expected TDs (rush+recv for skill players).
- **Intervals:** central credible intervals floored at 0.

### 2.7 Season aggregation — two-component variance

Talent error is perfectly correlated across games; game noise is independent (the player-level analog of the Monte Carlo's latent-strength draw):

```
season_mean = Σ per-game means (each with its own env multiplier)
season_sd   = sqrt(G²·talent_sd² + G·game_sd²)
```

Quantiles reported at p10/p25/p50/p75/p90. Final projection = YTD actuals + remaining-schedule distribution.

### 2.8 Fantasy scoring

```
FP = 0.04·pass_yds + 4·pass_TD − 2·INT + 0.1·rush_yds + 6·rush_TD
   + 0.1·rec_yds + 6·rec_TD + rec_bonus·receptions
rec_bonus: PPR 1.0 / half 0.5 / standard 0.0
FP_sd = sqrt(Σ (weight·stat_sd)²)      # independence assumption — conservative
```

---

## 3. Pipeline summary

```
schedules + results ──► Elo engine ──► ratings ─┐
team PBP aggregates (off/def PPG) ──────────────┼──► predict_game ──► margin N(μ, 13.5) ──► win/cover/total/intervals
                                                └──► Monte Carlo (10k, correlated N(0,55 Elo) offsets) ──► season odds

player weekly frames (3 prior seasons) ──► prior (recency+games weighted, aged, shrunk to pos mean)
current-season weeks ─────────────────────► Bayesian update ──► posterior (mean, game_sd, talent_sd)
depth chart ──► role share × distribution
predict_game outputs ──► env multiplier (scoring env × script tilt × pos defense)
weather + injuries + market anchor ──► final per-game mean
                                     ──► props / anytime TD / fantasy / season quantiles
```
