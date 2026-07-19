# Model Lab

Standalone, dependency-free (stdlib only) ports of the nfl-app prediction models, for testing outside the website. Constants and formulas are exact copies of production (`backend/app/services/`).

| File | Contents |
|---|---|
| `METHODOLOGY.md` | Full extracted methodology for both models, every constant documented |
| `game_model.py` | Elo engine, margin-distribution layer, `predict_game`, season Monte Carlo, backtest scoring rules (CRPS/log-loss/Brier) |
| `player_model.py` | Prior builder, Bayesian update, role/age/weather/injury multipliers, game coupling, prop probabilities, season aggregation, fantasy scoring |

## Run

```bash
python3 game_model.py                 # built-in demo + sanity checks
python3 player_model.py               # built-in demo + sanity checks

# Real data: rebuild Elo + simulate a season from a schedule CSV
python3 game_model.py --csv schedule.csv --season-sims 10000
```

CSV columns: `week,home_team,away_team,home_score,away_score[,neutral]` — leave scores blank for unplayed games. Team ids must match the standard abbreviations (KC, BUF, SF, LA, ...). You can export one from nflverse:

```python
import nfl_data_py as nfl
df = nfl.import_schedules([2026])
df[df.game_type=="REG"][["week","home_team","away_team","home_score","away_score"]] \
  .to_csv("schedule.csv", index=False)
```

## Divergences from production (intentional)

- No DB/cache/network — ratings and schedules are passed in directly.
- `player_model.py` exposes `project_stat_for_game(...)` as a single entry point; production splits that across the orchestration service.
- Positional defense factors are passed as a plain number (production computes them from weekly frames: yards allowed per family ÷ league avg, shrunk 50%, clamped [0.80, 1.25]).
- The uncertainty/calibration layer (`uncertainty_service`) and explainability payloads are omitted — they annotate predictions, they don't change them.
