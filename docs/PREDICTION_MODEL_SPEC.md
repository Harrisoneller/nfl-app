# Prediction Model — Assessment & Variance-First Spec

**Status:** Draft v1 (assessment + roadmap; no code changes yet)
**Author:** Principal-developer review
**Date:** 2026-05-21
**Scope:** The predictive stack only — `elo_service`, `predictions_service` (game + Monte Carlo), `ml_predictions_service`, `backtest_service`, and how `betting_service` / `h2h_service` consume them.

---

## 0. How to read this document

This is both an **audit** of what we have and a **design spec** for where we should go. The organizing principle is the goal you set: we are not trying to win every pick. We are trying to **characterize each game and each team's season as a distribution of outcomes** — to understand context, quantify uncertainty, and communicate it better than mainstream products do. In forecasting terms, our objective function is *sharpness subject to calibration*, not accuracy.

Sections 1–3 are the assessment (what exists, what's good, what's missing). Section 4 is the core thesis — the probabilistic framework. Section 5 is how we'll *prove* we're good (evaluation). Section 6 is the prioritized roadmap. Read 0, 1, 4, and 6 if you read nothing else.

---

## 1. What we have today (and what's genuinely good)

The stack is a three-layer ensemble-in-waiting that currently runs as three loosely-coupled pieces:

**Elo rating engine** (`elo_service.py`). A textbook 538/Inpredictable-style implementation: `K=20`, home-field advantage of 55 Elo points (≈ 2.2 pts), a log margin-of-victory multiplier dampened by rating difference (`_mov_multiplier`, lines 60–66), season-to-season regression of 0.75 toward 1500, and `ELO_PER_POINT = 25`. Ratings are persisted weekly to Postgres and rebuilt from six seasons of nflverse schedules. **This is a solid, defensible foundation.** The MOV multiplier and mean reversion are exactly what the reference implementations do.

**Game predictor** (`predictions_service.predict_game`, lines 36–90). Converts the Elo difference into a win probability and a point spread, and produces a total from team scoring tendencies. Surfaces every input for the "explain this" popover — a nice touch for the "understand the context" goal.

**Season Monte Carlo** (`predictions_service._simulate_season_compute`, lines 191–294). Banks completed results, simulates the remaining schedule 10,000 times, and aggregates win distributions (p5 / median / p95), division-winner %, playoff %, and a Super-Bowl heuristic. Persisted to `model_artifacts` and refreshed daily.

**ML layer** (`ml_predictions_service.py`). An XGBoost regression on home margin using rolling-4-week offensive/defensive EPA, Elo, rest days, division flag, and week number.

**Backtest harness** (`backtest_service.py`). This is the most pleasant surprise. It already computes spread MAE/RMSE, straight-up accuracy, **Brier score**, a **decile calibration table** (lines 212–240), high-confidence accuracy, and — critically — **against-the-spread record versus the closing line** (lines 186–194). The ML model is evaluated **out-of-sample** (train on four prior seasons, test on the held-out latest season, lines 279–334), and the code explicitly notes that the shipped in-sample MAE "is not a real performance measure." That instinct is correct and ahead of most amateur projects.

The honest summary: **the foundation is better than 90% of hobbyist NFL models.** What's missing is not correctness of the basics — it's the things that separate a point-estimate engine from a probabilistic forecasting system, and the single biggest in-season signal in football.

---

## 2. The holes, ranked

Each finding notes *what*, *why it matters for the variance goal*, *evidence*, and *direction*. Ordered by impact on the stated objective (understanding and leveraging variance), not by ease.

### 2.1 — The season Monte Carlo structurally understates variance ⚠️ highest priority

**What.** Each simulated game is an independent Bernoulli draw against a *fixed* Elo win probability, with ratings frozen for the whole simulation (`_simulate_season_compute`, lines 228–241; the comment on line 230 even says "Don't actually update Elo within a sim — keeps it light + fast").

**Why it matters.** This is the central issue given your goal. Two structural errors both compress the tails:

- **No team-strength uncertainty.** We treat each team's rating as if we know it exactly. We don't — especially in the preseason and early weeks. A team whose *true* strength is a touchdown better than its rating will beat that rating in *every* game, not in a random half of them.
- **No correlation across a team's games.** Because draws are independent, the simulated win total behaves like a sum of independent coin flips, which concentrates tightly around the mean (variance ≈ Σ p(1−p)). Real win totals are **over-dispersed**: good and bad seasons cluster because the underlying cause (a team is actually good, a QB is actually hurt) persists across the whole slate.

The result is that our p5–p95 win bands are **too narrow** — we are systematically *overconfident* about how a season will play out, which is the exact opposite of the product we want. A 10-win projection should carry an honest 7-to-12 spread, not 9-to-11.

**Direction.** Move to a **two-level (hierarchical) simulation**: per simulation, first *draw each team's latent strength* from its rating's uncertainty distribution (one offset per team, held constant across that team's whole season — this single change injects the correlation and fattens the tails correctly), then simulate games off those drawn strengths. Optionally evolve ratings within the sim so a hot/cold start propagates. Simulate **margins**, not just win/loss, so totals, point differential, and proper tiebreakers fall out for free.

### 2.2 — No game-level outcome distribution

**What.** `predict_game` returns a point spread, a win probability, and a single total. There is no margin distribution, no standard deviation, no cover probability, no over/under probability, and no credible interval.

**Why it matters.** "Understand the likely outcomes of a game" *is* the distribution. The market itself is a distribution (the spread is the median, the moneyline is the win probability, the total is the median points). To speak the market's language and to beat the average fan's single-number pick, every game should ship its full predictive distribution: P(margin ≥ x) for any x, P(home covers any line), P(total over/under any number), and a 50%/80% credible interval on the score.

NFL margins have two important properties a naive model misses: a standard deviation of roughly **13.5 points**, and **mass concentrated at key numbers** (3 and 7, secondarily 6/10/14). A plain normal curve gets the spread-to-win-prob conversion roughly right but mispredicts cover and push probabilities near the key numbers, which is where most of the actionable signal lives.

**Direction.** Define the game prediction as a distribution: margin ∼ (mean = model spread, σ ≈ 13.5), then derive win/cover/total probabilities *from* that distribution so they are mutually consistent. Upgrade from a plain normal to an empirical or key-number-aware margin kernel (a histogram of historical margins, or a normal with bumps at 3/7) so push and cover probabilities are right.

### 2.3 — No quarterback adjustment 🏈 the biggest missing signal

**What.** Team strength is purely team-level Elo. There is no concept of *who is playing quarterback*.

**Why it matters.** QB is the highest-variance, highest-leverage position in the sport. A starter-to-backup change moves a line **3–7 points** — larger than home field, larger than almost any team-level efficiency swing. 538's signature innovation over vanilla Elo was exactly QB-adjusted Elo. A model blind to QB status will be confidently wrong precisely in the high-information moments (a Week-10 injury) that a sharp observer cares most about — and those moments are a major source of the very variance you want to capture and exploit.

**Direction.** Add a QB value layer: a rolling, EPA/CPOE-based quarterback rating, expressed as an Elo-point adjustment applied when the projected starter differs from the team's baseline. We already ingest injury/active data in `player_predictions_service` (the weather/injury adjustments from task #48) — that's the hook to detect a changed starter. Even a coarse three-tier QB adjustment (elite / starter / backup) would be a large step up.

### 2.4 — ML model: stale features, no uncertainty, no ensemble

**What & why.**

- **Stale Elo feature.** `_build_features_for_season` pulls `current_ratings(db, season=season-1)` (line 121) and uses *last season's final Elo for every week of the current season*. By Week 15 the feature is nine months stale. The backtest's Elo path correctly uses pre-game week-N−1 ratings, so the two layers disagree about what "team strength" means.
- **Possible Week-1 leakage.** Rolling EPA at `target_week = max(week-1, 1)` (line 133) means Week 1 reads Week 1's own rolling value, which includes the game being predicted. Small, but it's leakage.
- **Point estimate only.** XGBoost regresses a single margin. No quantiles, no predictive variance — so the ML layer cannot contribute to the distributional story at all.
- **No ensemble.** Elo and ML are computed independently and shown side-by-side (`include_ml=true`); there is no principled blend, and crucially **no blend with the market**, which is the single most informative feature available.

**Direction.** Fix the Elo feature to use within-season pre-game ratings; close the Week-1 leak; move to a **distributional** learner (quantile regression, or predict mean and variance) so ML produces a margin distribution; and **stack the signals** — Elo, ML, and the closing line — with weights chosen by out-of-sample log loss. Treat the market as the prior and the model as the attempt to find where it's wrong.

### 2.5 — Totals use the naive midpoint (same bug we just fixed in H2H)

**What.** `predict_game` computes `expected_home_pts = (h_off + a_def) / 2` (lines 54–56) — the midpoint of a team's scoring and the opponent's points allowed. This is the identical flaw we corrected in the H2H "Strength vs weakness" widget: a midpoint average is not how an offense and a defense combine.

**Why it matters.** Averaging shrinks toward neither team's reality and ignores the league baseline, so totals for extreme matchups (great offense vs. bad defense, or two elite defenses) are biased toward the middle. Totals are also not pace-aware — a fast, pass-heavy team and a slow, run-heavy team with the same PPG imply very different totals.

**Direction.** Use the matchup-adjusted form `expected_pts = off + (opp_def − league_avg)` (the same correction now live in `h2h_service`), and ideally rebuild totals from **pace × efficiency** (expected plays × points per play) rather than PPG, with its own distribution.

### 2.6 — Calibration is measured but never corrected; scoring is incomplete

**What.** The backtest produces a calibration table but nothing consumes it — win probabilities are shipped raw. Scoring uses Brier and accuracy but not log loss, and there is no single calibration summary statistic.

**Why it matters.** Calibration is the whole game for a variance-first product: if we say 30% we want it to happen ~30% of the time. We measure it but don't act on it, and we don't summarize it (Expected Calibration Error), so "are we calibrated?" has no one-number answer. We also don't yet score the *distribution* (only the win-prob point), so we can't tell whether our margin spread is too wide or too narrow — which is precisely the 2.1 problem we'd be trying to fix.

**Direction.** Add a post-hoc calibration step (Platt or isotonic) fit on out-of-sample data; report **log loss** and **Expected Calibration Error** alongside Brier; and add **CRPS** (continuous ranked probability score) to grade the full margin/total distribution, which directly rewards getting the variance right.

### 2.7 — Lower-priority but real

- **Static, league-wide home-field advantage** (55 Elo for everyone). Real HFA has drifted down toward ~1.5–2.0 pts in recent years and varies by venue (Denver altitude, etc.). Easy, modest win.
- **No preseason prior.** Every team starts at 1500 with six years of history; Week-1 ratings ignore the offseason. Vegas win totals (or a roster/market blend) are an excellent, cheap prior and would sharpen exactly the early-season window where uncertainty — and thus the value of *honest* uncertainty — is highest.
- **Crude playoff/Super-Bowl model.** Random tiebreakers and "best conference record = Super Bowl" (lines 251–273). Fine for v1, but it caps the credibility of the season-context narrative. A real seeding + bracket sim is the eventual fix.
- **Rest/bye/travel/short-week** live only in the ML feature set, not in the Elo or game-prediction path. Surface them consistently.
- **Determinism.** The Monte Carlo seeds `rng = random.Random(42)` (line 226). Reproducible (good), but it means the *same* simulation noise every run; once we add strength sampling we'll want to confirm n_sims is large enough that Monte Carlo error is negligible vs. the signal.

---

## 3. Where we stand vs. the market leaders

The relevant benchmarks, and what each does that bears on our goal:

| Source | Core method | Outputs uncertainty? | QB-aware? | What we can learn |
|---|---|---|---|---|
| **Betting market (Pinnacle / consensus close)** | Crowd + sharp money | Yes — line *is* the distribution | Yes (instantly) | The gold-standard benchmark. ~66–67% straight-up; ATS breakeven is 52.4% at −110. Use it as a prior and as the bar. |
| **FiveThirtyEight NFL Elo** | QB-adjusted Elo + MOV mult + mean reversion | Yes — win-total & playoff distributions | **Yes** (signature feature) | The model we most resemble structurally; the QB adjustment is the gap. |
| **ESPN FPI** | Efficiency-based power index | Yes — projected-win distribution, playoff % | Partial | Distribution-first presentation of season outcomes. |
| **Football Outsiders DVOA** | Opponent-adjusted play/drive efficiency, split O/D/ST | Variance reported (VOA vs DVOA, "Estimated Wins") | Indirect | Opponent adjustment and offense/defense/special-teams splits; "luck vs. skill" framing. |
| **Inpredictable (Burke)** | Bayesian-ish win probability, team tiers | Yes | Indirect | In-game WP and explicit uncertainty tiers. |
| **nflfastR / EPA community (Baldwin et al.)** | EPA/play, success rate, CPOE, PROE | Model-dependent | Via QB EPA/CPOE | The open-source efficiency layer we already use; the right raw ingredients. |

**Honest placement.** Our Elo + EPA foundation and our calibration/ATS-aware backtest put us in the same *methodological family* as 538/FPI and well ahead of the typical fan site. The gaps that keep us out of "best of the best" are specific and addressable: (1) we ship point estimates where the leaders ship distributions; (2) we have no QB adjustment; (3) we don't blend with the market; (4) our season sim under-models variance. None of these require exotic techniques — they require committing to the probabilistic framing.

**What "better than the average joe" actually means here.** The median fan and the median content site give a pick and maybe a confidence adjective. Even matching 538/FPI on point accuracy is *not* the differentiator and isn't realistic to exceed by much. Our edge is **process and honesty about uncertainty**: full per-game distributions, win-total ranges that are actually wide enough to be true, transparent drivers ("this line moved because the backup is starting"), and every number benchmarked against the market and against our own calibration history. That is a defensible, distinctive product even in a world that contains Vegas.

---

## 4. The target: a variance-first probabilistic forecasting system

This is the heart of the spec. The redesign is a shift from *"what is the predicted spread?"* to *"what is the full distribution of outcomes, and how confident should we be?"* — applied consistently at three levels.

### 4.1 — Team strength as a distribution, not a number

Maintain, for every team, not just a rating but an **uncertainty** around it (a Glicko-style ratings deviation, or an ensemble spread). The deviation is **wide in the preseason** (seeded from a market/roster prior), **narrows** as games provide evidence, and **widens** on disruptive events (a QB injury, a trade). This single object — strength ± uncertainty — is what makes everything downstream honest. It is also intuitive to communicate: "we think Detroit is a 1620 team, but we're only ±40 sure this early."

### 4.2 — Game level: ship the whole distribution

For each game, produce a predictive distribution over the margin (and, separately, the total). Mean from the blended model (Elo + ML + market); spread from a base game-to-game σ ≈ 13.5 *inflated by the two teams' rating uncertainty* (early-season games are less certain than Week 17 games — and we should say so). From that one distribution, derive — consistently — the win probability, the probability of covering any spread, the probability of the total going over/under any number, and a 50%/80% credible interval on the final score. Use a key-number-aware kernel so 3 and 7 are handled correctly.

The product surface that falls out of this: "Bengals by 3.5, but it's a coin-flip-plus — 58% to win, 47% to cover −3.5, and a realistic range of Bengals −10 to Ravens −3." That is the "understand the likely outcomes" experience.

### 4.3 — Season level: hierarchical, correlated simulation

Replace the independent-coin-flip sim with the two-level structure from §2.1: per trial, draw each team's true strength from its posterior (held constant across that team's season → correlation, fat tails), then simulate game margins off those strengths, then resolve standings with real tiebreakers and a real playoff bracket. The win-total, division, playoff, and Super-Bowl distributions that come out will be **appropriately wide** — and therefore trustworthy. This is what lets us say "Detroit's range is 8–13 wins" and mean it.

### 4.4 — Blend, with the market as the anchor

Combine Elo, ML, and the market line into one number per game via a weighted blend whose weights are chosen by out-of-sample log loss. The default posture: the market is the prior; our model's job is to find the specific spots where we have information the market is slow to price (a QB situation, a pace mismatch, weather), and to be *calibrated* everywhere else. This both improves accuracy and gives us the honest "edge vs. market" readout that the betting/H2H surfaces want.

---

## 5. How we'll prove it — the evaluation framework

A variance-first model must be graded on variance-aware metrics. The guiding principle (Gneiting & Raftery): **maximize sharpness subject to calibration.** Be as decisive as the data allow, but never more confident than you're right.

**Calibration (are our probabilities honest?)** — reliability diagrams (we have the table), summarized by **Expected Calibration Error**; **log loss** and **Brier score** for win probabilities. A 70% pick should win ~70% of the time across the bin.

**Distributional accuracy (is our variance right?)** — **CRPS** on the margin and total distributions. CRPS punishes both a wrong center *and* a wrong spread, so it is the metric that tells us whether §2.1/§4 actually fixed the over-confidence. Add **PIT histograms** (probability integral transform) — if our distributions are well-specified, the PIT values are uniform; a U-shape means we're too narrow (the current likely failure mode), a hump means too wide.

**Market-relative (are we good by the only external standard?)** — straight-up and ATS records *versus the closing line*, and log loss *versus market-implied probabilities*. We already do the ATS-vs-close comparison; extend it to probability scoring. The realistic, stated target: **match the market on calibration, lose to it only slightly on sharpness, and beat it in identifiable niches** (early season, QB-change games). Sustained ATS profit is explicitly *not* a success criterion — almost no one clears the vig long-term, and promising it would be dishonest.

**Baselines (did the complexity earn its keep?)** — always report against (a) pick-the-home-team, (b) Elo-only, (c) market-only. Every added component must beat the baseline it complicates out-of-sample, evaluated with **walk-forward** validation (train through week *t*, predict *t+1*) — never a random split, which leaks future information.

---

## 6. Prioritized roadmap

Sequenced by impact-per-effort toward the variance goal. Each phase is independently shippable and independently measurable against §5.

**Phase 1 — Make the existing numbers honest (highest impact, low/medium effort).**
1. Hierarchical, correlated season Monte Carlo (§2.1/§4.3) — draw team strength per trial. *This is the single highest-value change for your stated goal.*
2. Game-level margin distribution + derived win/cover/total probabilities + credible intervals (§2.2/§4.2), with a key-number-aware kernel.
3. Matchup-adjusted, pace-aware totals (§2.5) — reuse the H2H correction.
4. Add CRPS, log loss, ECE, and PIT histograms to the backtest (§5); keep walk-forward discipline.

**Phase 2 — Add the biggest missing signal (high impact, medium effort).**
5. QB-adjusted ratings (§2.3), wired to the existing injury/active-status ingestion.
6. Preseason prior from market win totals (§2.7) and rating uncertainty that starts wide and narrows (§4.1).

**Phase 3 — Sharpen and blend (medium impact, medium effort).**
7. Fix the ML feature staleness + Week-1 leak; move ML to distributional output (§2.4).
8. Market-anchored ensemble of Elo + ML + line, weighted by out-of-sample log loss (§4.4).
9. Post-hoc probability calibration (Platt/isotonic) (§2.6).

**Phase 4 — Polish the season story (lower urgency).**
10. Real playoff seeding + bracket simulation; dynamic/venue HFA; consistent rest/travel handling.

A reasonable definition of "done / best-of-the-best": Phases 1–3 shipped, with the backtest showing **ECE within a couple of points of the market**, **PIT histograms flat** (variance well-specified), and **CRPS/log loss beating the Elo-only and home-team baselines** out-of-sample — all visible on the Model Performance page so the rigor is part of the product, not hidden.

---

## 7. Appendix — reference points

- **NFL margin standard deviation:** ≈ 13.5 points (game-to-game), the basis for spread→win-probability conversion.
- **Key numbers:** 3 and 7 dominate the NFL margin distribution; 6, 10, 14 secondary. Any margin model that ignores them misprices cover/push probabilities.
- **Market difficulty:** closing line ≈ 66–67% straight-up; ATS breakeven 52.4% at −110 odds. Beating the close ATS sustainably is the rare exception, not the target.
- **Scoring rules:** Brier and log loss (win prob), CRPS (full distribution), ECE + reliability diagrams + PIT histograms (calibration). Proper scoring rules reward honesty; optimize sharpness *subject to* calibration.
- **Validation:** walk-forward / time-series only. A random train/test split leaks the future and will flatter the model.
- **Code touchpoints for implementation:** `elo_service` (HFA, QB layer, rating uncertainty), `predictions_service.predict_game` (distribution + totals), `predictions_service._simulate_season_compute` (hierarchical sim), `ml_predictions_service` (feature fix + distributional output + blend), `backtest_service` (CRPS/log loss/ECE/PIT + baselines).

*End of v1. This document is the living source of truth for the predictive redesign; update it as phases land and as the backtest tells us what actually moved the needle.*
