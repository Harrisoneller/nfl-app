# NFL Context Layer — Roadmap to a Top-Tier Edge

Status as of 2026-08-29. Current model: `epa-elo-v3` (Elo prior × ridge
opponent-adjusted EPA fundamentals, market blend on top). Sibling `cfb-app`
is at `epa-elo-spi-qb-cal-so-ctx-v12` with a full context layer, game-conditional
sigma, walk-forward backtest, and a model-CLV proof loop. **The NFL app is
roughly four model generations behind its own sibling.** Much of what follows
is a port, not a build.

---

## 1. Why the current model has a ceiling

`predict_game` is a *difference of two backward-looking team means*. That
structure has four blind spots, and no amount of better EPA fixes them:

| Blind spot | What it means | Example |
|---|---|---|
| **Personnel identity** | History assumes the roster that produced it is the roster playing Sunday | Starting QB out; model has no idea |
| **Interaction** | A difference of means cannot express "this weakness meets that strength" | Elite pass rush vs. slow-processing QB behind a bad OL |
| **Regime change** | Team history is assumed drawn from one distribution | New OC, new scheme, new personnel usage |
| **Sample size** | 17 games/season. By Week 6 you have 6 | Defensive EPA barely stabilizes before Week 10 |

The fourth is the one that kills most "add more features" projects. NFL is the
sharpest market in sports precisely because everyone has the same 6-game
samples. Edge comes from **better priors, better shrinkage, and pricing what
history structurally cannot see** — not from more regressors.

---

## 2. The non-negotiable discipline

Every nuanced/subjective input must be:

1. **Priced in points** (a mean shift) and/or **in sigma** (a variance
   multiplier). Never a vague "confidence" score. If you cannot say
   "this is worth −2.4 points," you have not modeled it.
2. **Stored append-only with `as_of`**. A mutable `current_injuries` table
   leaks the future into every backtest. This is the single most common way
   a sports model fools its author.
3. **Validated against closing-line movement**, per component, with an
   automatic weight → 0 below a floor. Keeping a fraction of a noise signal
   is how a model drifts. `cfb-app/backend/app/services/context_validation_service.py`
   already implements exactly this; port it.
4. **Separately visible in the explainability panel.** If a component can't
   be shown to the user as its own line item, it can't be audited by you either.

An LLM may **extract** structured claims from unstructured news. An LLM may
never **emit points**. Extraction → deterministic valuation → CLV gate.

---

## 3. Priority order

Ranked by expected points of edge per unit of work.

### P0 — QB adjustment (`qb_adjustment_service.py`)

Highest-value single item in NFL modeling, full stop. A backup QB is worth
roughly 4–7 points of spread; Elo and season-aggregate EPA are blind to it.

- QB value = shrunk blend of career and current-season EPA/play + CPOE,
  with an explicit rookie/small-sample prior (draft capital + college adj-EPA).
- Depth chart from `nfl.import_depth_charts()`; starter minus next-man-up
  is the swing, not starter minus replacement-level.
- **Offseason carryover**: when a team changes starting QB, its prior-season
  offensive rating must be de-attributed. Carrying Team X's 2025 offensive
  EPA into 2026 with a different QB is a guaranteed mispricing. This is the
  FiveThirtyEight QB-adjusted-Elo idea and it is still underused publicly.
- QB uncertainty (rookie, new team, injury return) feeds sigma, not just mean.

### P0 — Game-conditional sigma

`GAME_SIGMA` is currently a constant 13.5. Every downstream number — win prob,
cover prob, total prob, every parlay leg, every EV calculation — inherits that
error in the same direction. Sigma should be a function of expected total,
absolute spread, wind, pace, QB uncertainty, and divisional status.
`cfb-app/backend/app/services/dist_model.py` already does this (v11).

For a betting product this is the highest ROI-per-line-of-code item on the list.

### P1 — Availability layer (`services/context/availability.py`)

Port the CFB module; NFL data is strictly better.

- Source: `nfl.import_injuries()` — the official report, with Wed/Thu/Fri
  practice participation (DNP / LP / FP) *and* game status
  (Out / Doubtful / Questionable). **Fit status → play probability empirically**
  from historical report rows joined to actual snap counts. Do not guess params.
  Practice-participation trajectory (DNP→DNP→LP vs. LP→LP→FP) is far more
  informative than the game-status tag alone, and most public models ignore it.
- Player value = `(EPA/play above positional replacement) × snap share ×
  redistribution factor`. NFL has real snap counts (`import_snap_counts`),
  which CFB does not — use them.
- **The redistribution factor (~0.55) is the key correction.** An injured WR1's
  targets go to WR2, not to a replacement-level body. Charging full value is the
  classic injury-model error and it is why most public injury adjustments are
  2× too large.
- **Teams with no rows are *unreported*, not healthy** — zero points, zero confidence.
- OL is its own model: continuity (games with the same five starters) and
  projected pressure rate allowed, not a sum of individual grades.
- Defense: weakest-link, capped tight. Individual defenders are worth much less
  than fans believe; CB1 out is the main exception worth pricing.

### P1 — Weather into the model

`weather_service.py` computes forecasts and the model never reads them. Free win.

- **Wind is the whole story.** >15 mph cuts totals ~2–4 points, suppresses deep
  passing and FG rate, and *reduces* margin variance. Temperature and light
  precipitation are heavily overrated by the public — which is exactly why the
  total moves too far on a cold-weather narrative.
- Feeds total and pass/run mix. Should barely touch margin.
- Dome/retractable handling; wind is meaningless indoors.

### P1 — Situational layer (`services/situational_service.py`)

Cheap, well-established, currently absent entirely:

- Rest differential: short week (TNF), post-bye, mini-bye, 3rd straight road game.
- Travel: distance, time-zone crossings, west→east 1:00 ET kickoffs, altitude (DEN).
- International games (neutral-ish, both teams travelling, London body clock).
- Divisional games: compressed spreads, reduced HFA, lower variance.
- Referee crew (`nfl.import_officials()`): penalty-rate tendency → total.
- **Late-season leverage/motivation**: clinched teams resting starters in
  Weeks 17–18. This is one of the few remaining large public edges and it is
  a pure context problem — history says nothing about it.

### P2 — Coach & scheme (`services/context/coach_scheme.py`)

Handle this correctly or it will hurt you. The honest empirical finding:
**new-coach mean effects are small and near-unpredictable; the reliable effects
are on variance and on how much prior-season history you should trust.**

So a coaching change should do three things, in this order of confidence:

1. **Widen sigma** (first-year HC/OC/DC → ~1.05–1.08× margin sigma).
2. **Cut prior-season carryover weight** for the affected unit — the team's own
   last-season EPA gets shrunk harder toward league mean.
3. **Substitute the coordinator's own fingerprint** from his previous stop:
   PROE, neutral pace, personnel usage, motion rate, play-action rate, blitz
   rate, coverage-shell mix. This is the actionable piece and almost nobody
   consumer-facing does it. It converts "new OC, who knows" into a concrete
   prior on tendencies, which drives totals and pass/run mix before a single
   snap is played.

Only after those should a small mean adjustment (±0.3 pts) be entertained,
and only if the CLV gate keeps it.

### P2 — Scheme matchup interaction terms

Products of two teams' features. A difference-of-means model *structurally
cannot* express these, which is why they are where the residual edge lives.

Candidates, strongest first:

- **Pass rush win rate × OL pass block win rate / time-to-throw** (NGS). Pressure
  is the most predictive single matchup in football.
- Man/zone coverage rate × offense's man-vs-zone EPA split.
- Light-box rate × offensive run tendency and rush EPA (NGS box counts).
- Explosive-play rate × explosive-play rate allowed.
- Play-action rate × defensive play-action EPA allowed (FTN charting,
  `nfl.import_ftn_data()` — motion, play action, no-huddle, blitzers, pass rushers).
- Neutral pace as a *joint* function, not additive — game script drives plays,
  and plays drive totals.

Fit with ridge against closing-line residuals. Gate every coefficient by CLV.
Caveat carried over from the CFB implementation: fitting on residuals from
week ≥ 8 has a known lookahead caveat — document it in the module.

### P2 — Unit-specific shrinkage and half-lives

Currently one `epa.ridge_lambda` for everything. NFL stabilization rates differ
sharply by unit: pass offense stabilizes fastest (~4–6 games), rush offense and
especially defensive EPA much slower (~8–10+). Using one lambda over-trusts
defense early and under-trusts pass offense late. Separate lambdas and separate
prior-season blend weights per unit is a small change with a real accuracy gain,
and it is exactly the kind of thing the market prices better than a naive model.

### P3 — LLM news extraction (off by default)

The genuinely "subjective" input, done safely. Beat writers, practice reports,
press conferences → an LLM emits **structured claims** into an append-only
snapshot table: `{player, status, confidence, source, as_of}`, or
`{team, tendency_claim, confidence}`. Points come from the deterministic
valuation layer. Source priority: admin > official report > beat writer > LLM.
Ship behind a param, default off, and turn it on only when the CLV gate says it earns.

### P3 — Model-CLV proof loop (`/proof`)

The NFL app tracks user-bet CLV but has **no model-CLV loop**. The CFB app does.

This is both a validation tool and the product answer to "why come here."
An append-only, public record of the model beating the closing line is a harder
moat than any individual feature — and it is the only honest way to claim the
edge you're claiming.

### P3 — Walk-forward backtest with layer attribution

Port `cfb-app` `backtest-v1`: point-in-time replay of `predict_game` with a rung
ladder (Elo only → +EPA → +QB → +availability → +situational → +scheme) via param
overlays. Without this you cannot tell which layer is earning and which is noise,
and you will end up defending features on narrative rather than measurement.

---

## 4. Where the edge actually is

Being direct: the NFL sides market is close to efficient, and no context layer
is going to yield a persistent 3-point edge on spreads. Realistic edge lives in:

- **Player props and derivatives** — thinner markets, slower to move on
  availability news. This is where the availability layer pays for itself.
- **Totals in weather** — the public over-reacts to cold and under-reacts to wind.
- **Timing / CLV capture** — being right about an injury's price before the market is.
- **Correlation in SGPs** — parlay infrastructure already exists; correct joint
  modelling of (margin, total) with conditional sigma is a real pricing edge
  against books' naive leg-independence assumptions.
- **Late-season motivation** — Weeks 17–18 resting decisions.

Design the context layer so its output flows into props and SGP pricing, not
just the game-side number. That is where it converts into money.

---

## 5. Suggested sequencing

| Phase | Contents | Rationale |
|---|---|---|
| 1 | **DONE 2026-08-29** — Storage (append-only snapshots, `as_of`), context bundle plumbing into `predict_game`, game-conditional sigma wired + registered | Foundation; sigma pays off immediately across every market |
| 2 | QB adjustment + availability layer | The two largest point-swings in the NFL |
| 3 | Weather-into-model + situational layer | Cheap, well-established, currently zero |
| 4 | CLV validation gate + walk-forward backtest with layer attribution | Proves phases 2–3 and prevents drift |
| 5 | Coach/scheme priors + interaction terms | Highest-nuance, needs the gate from phase 4 to be safe |
| 6 | LLM extraction, `/proof` page | Product surface and the last mile of "subjective" |

Model version target: `epa-elo-qb-ctx-v4`.

Params land under a new registry category `context`; per
[[project-param-registry]], never read them at import time.

---

## 6. Phase 1 as built (2026-08-29)

Model version `epa-elo-v3` → **`epa-elo-ctx-v4`**.

**New files**
- `app/models/team_context.py` — `player_availability_snapshots` (with NFL
  Wed/Thu/Fri practice columns) + `team_context_snapshots`. Append-only by
  contract. `DEFAULT_PLAY_PROB` is a placeholder to be fit empirically.
- `alembic/versions/0019_context_layer.py`
- `app/services/context_service.py` — resolves snapshots into one bundle per
  slate (`week_context`) and one home-signed adjustment per game
  (`game_context`); `persist` / `record_manual` on the write side.
- `tests/test_context_layer.py` — 33 tests.

**Changed**
- `dist_model.py` — added `context_sigma_mult`, `wind_mph` / `indoor`,
  `wind_total_points()`, `fit_key_number_excess()`. Purely additive; all new
  arguments default to a no-op.
- `predictions_service.predict_game()` — takes `context=` and `weather=`;
  applies context points to the margin *before* the quoted spread, builds the
  game-conditional joint distribution, subtracts wind from the total, and
  emits a per-component breakdown under `explainability.context`.
- `predict_week()` — builds one context bundle and one forecast map per slate.
- `param_registry.py` — new `context` category, plus the 15 `dist.*` params
  that dist_model was already reading but which had never been registered
  (the parlay/value pricing surface was running on unaudited constants).

**Switches**
- `context.enabled` — kills the whole layer.
- `dist.conditional_sigma_enabled` — reverts to flat sigma for every game.
Both are admin-tunable with no deploy.

**Deliberately not done**
- The team-schedule projector (`/teams/{id}/schedule`) stays uncontexted:
  this week's injury report says nothing about Week 14. Per-week context there
  needs the durability model from the season-context work.
- No providers yet — the table is fed only by the admin manual path until
  Phase 2 (`services/context/`).

**Known issue found along the way (pre-existing, not introduced here)**
`Base.metadata.create_all()` fails on SQLite because ~7 models declare bare
`JSONB` (`experiment_events.payload` is the first to blow up). That is why
`tests/test_sparky_service.py` (6 tests) and `test_seasons_util.py` (1) fail
under the SQLite fixture the suite is designed to use. The new context models
use `JSONB().with_variant(JSON(), "sqlite")` and create cleanly; applying that
same one-line change to the other JSONB models would restore the SQLite test
path for the whole suite.
