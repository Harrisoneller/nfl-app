# v6-blind priors

Built by `backend/scripts/backtest_v6_blind.py` from the nflverse mirror in
`NFLVERSE_DIR` (play-by-play, games, depth charts, injuries, weekly rosters,
snap counts, players). No spread, total, moneyline, or other price entered
the design.

`week_features.csv` is one row per scheduled game, 2015–2026. Ratings use only
earlier weeks. Garbage time (win probability outside 10–90%), kneels and spikes
are dropped. Team units are opponent-adjusted ridge ratings of pass EPA/play,
rush EPA/play and success rate, shrunk by k games toward a preseason prior
(last season × 2/3 × continuity). Defense is weighted at 0.55. The pass
component adds the expected starter's QB EPA/dropback minus the QB value
already in the team pass rating (shrinkage k = 400.0 dropbacks toward a
draft-round × experience prior fit on earlier seasons only).

Expected starter: weekly depth-chart QB, replaced when an injury row with
`date_modified` ≤ kickoff−24h says Out. Depth charts have no timestamp.
`home_qb_id` is not used. If the depth chart is missing, the QB delta is 0.

Coefficients in `../coefs.json`: `by_season` for 2016–2023 is fit on earlier
seasons only. 2024–2026 use the single 2015–2023 fit.

- week_features.csv SHA-256: `51d626d41a94184e375111b3244dab675ae0870e7c2d45f13556112d77dff854`
- coefs.json SHA-256: `881deb1705a945654f6344e04dba258953048fc171d99db5371861ef64d21142`
- input data SHA-256: `539b6fb9a848cbf25c8efceb9b8777caf87a7b8db519a226de166c153138eefc`
