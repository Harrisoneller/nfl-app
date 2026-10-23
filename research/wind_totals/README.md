# T−24h wind forecasts

Market-free cache of the wind forecast used by the totals ablation. One row
per regular-season or playoff game from 2016 through 2026. The file has no
scores, spreads, totals, moneylines, temperature, precipitation, or observed
wind.

## Source

* Endpoint: `https://historical-forecast-api.open-meteo.com/v1/forecast`
* Model: `jma_gsm` (JMA GSM), the same model for every season
* Variable: `wind_speed_10m_previous_day1`
* Units: miles per hour (`wind_speed_unit=mph`)
* Time zone of the hourly stamps: UTC
* Window: 1 August of the season through 28 February of the next year, cut
  at the fetch date when that end is still in the future

`previous_day1` is the archived forecast whose valid time is the hour in the
file and whose issue time is 24 hours earlier. That issue time is at or before
kickoff minus 24 hours. This is not the Historical Weather reanalysis, and it
is not the stitched `wind_speed_10m` series (the first hours of each run,
which sit close to the analysis). GFS `previous_day1` wind is empty for
2016–2023 on this API, so it is not used. Switching to GFS only for 2024
would give the holdout a sharper model than the selection window had.

Checked against the previous-runs host for the Arrowhead hour of 2016-09-11:
the same `previous_day1` values. On 2018-09-09 the stitched analysis and
`previous_day1` differ (about 1 mph on average that day).

## Kickoff

`games.csv` `gametime` is Eastern. It is converted from `America/New_York` to
UTC. A missing clock would default to 13:00 ET; none of the 2016–2026 games
in this file were missing one. The valid hour is the UTC hour containing
kickoff. If that hour is null, the latest filled hour at or before kickoff is
used, and its issue time is still at or before T−24h. A later hour is never
used.

## What the number in `wind_mph` is

The raw forecast at the home franchise's coordinates in
`backend/app/adapters/data/weather.py`. The roof mask is **not** baked into
this file. Domes, closed roofs, and neutral sites are set to 0 when the
feature is built, by re-reading `roof` and `location` from the schedule.
Open retractable roofs keep the forecast. A null roof uses the `is_indoor`
flag only as a fallback. A null forecast becomes a zero feature and does not
move the v6 total.

Neutral games (London, Germany, Mexico, Brazil, and the rest) stay at 0.
`weather.py` is keyed by team, not by stadium name, and it has no coordinates
for those sites. Some neutral games were played in an NFL building; the home
team's coordinate is still the wrong coordinate, so those rows are not used.

## Relocation limit

Historical clubs are looked up under the current franchise id (`LA`→`LAR`,
`OAK`→`LV`, `SD`→`LAC`, `STL`→`LAR`). The coordinate is therefore the current
stadium in `weather.py`: SoFi for the Rams and Chargers, Allegiant for Las
Vegas. That is wrong for the Los Angeles Coliseum (2016–2019), the Oakland
Coliseum, and Qualcomm Stadium. The task uses the coordinates already in
`weather.py` and does not add sites that are not in that file. Shared
coordinates: NYG and NYJ (MetLife), LAR and LAC (SoFi).

## File

* Rows: 3033
* Rows with a forecast value: 3033
* SHA-256: `1e9e1dc859011979fbe641a6d90e9fccb3eb9d02dd0fa73724441613d97905fa`

Rebuild with `python3 backend/scripts/wind_totals_ablation.py fetch`.
The hourly responses are cached under `/tmp/wind_jma_prev1` and are not
committed.
