# Data sources

| Domain | Source | Cost | Notes |
|---|---|---|---|
| Live scores, schedules | ESPN public JSON (`site.api.espn.com`) | Free | Unofficial. Reverse-engineered from ESPN's web app. Stable for years but no SLA. |
| Player/team stats, PBP, rosters | `nfl-data-py` (PyPI) | Free | Sources nflverse data — gold standard for historical NFL. Updated weekly during season. |
| Player metadata, trending players, injuries | Sleeper public API | Free, no key | Excellent for fantasy-grade player info. |
| Sportsbook odds (futures, awards, games) | The Odds API | Free (500 req/mo) | Cached every 15 min by scheduler to stay within limits. |
| News headlines | RSS — ESPN, ProFootballTalk, CBS, Yahoo | Free | Fast and reliable. |
| Social discussion | Reddit `r/nfl` JSON endpoint | Free | Top/hot posts and titles only. |
| Reporter tweets | Twitter / X API v2 | $200+/mo | Adapter exists; gated by `ENABLE_TWITTER=true`. |

## Adapter contract

Every adapter implements its domain's base interface (e.g. `ScoreboardAdapter`, `NewsAdapter`, `LLMProvider`). This means swapping ESPN for SportsData.io or Grok for Claude is a config change, not a code change.

```python
class ScoreboardAdapter(Protocol):
    async def fetch_current_scoreboard(self) -> list[Game]: ...
    async def fetch_team_schedule(self, team_id: str, season: int) -> list[Game]: ...
```

## Caching strategy

In-process TTL cache (no Redis to keep deps light). The scheduler does the heavy lifting — most read endpoints serve from the DB, which is refreshed on a cron-like schedule.

| Data | Refresh interval | Why |
|---|---|---|
| Scoreboard (live) | 30s | In-game score movement |
| News feed | 5 min | New posts trickle in |
| Odds | 15 min | API rate limit |
| Team rosters / stats | 1 day | Updates weekly |
| Player metadata | 1 day | Sleeper updates daily |
