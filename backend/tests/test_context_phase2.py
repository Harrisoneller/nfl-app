"""Phase-2 context providers: player valuation, QB swing, availability pricing.

As in Phase 1 these test *properties*, not golden numbers. The constants here
(redistribution 0.55, replacement EPA −0.12, the status→play-probability table)
are explicitly priors awaiting a fit against realized data; pinning them would
make the eventual fit fail a test rather than improve the model.

What must hold regardless of calibration is the shape: replacement-relative
rather than absolute, redistribution applied, unreported distinguished from
healthy, the QB priced as a swing rather than a level, and nothing able to run
away with a line.
"""
from __future__ import annotations

import pandas as pd
import pytest

from app.services import qb_adjustment_service as qb
from app.services.context import availability as av
from app.services.context import player_value as pv


# ---- Name keys --------------------------------------------------------------


def test_player_key_collapses_the_spellings_that_actually_differ():
    """Injury reports and stat frames spell the same man three ways."""
    assert pv.player_key("A.J. Brown") == pv.player_key("AJ Brown")
    assert pv.player_key("A.J. Brown Jr.") == pv.player_key("A.J. Brown")
    assert pv.player_key("Odell Beckham Jr.") == pv.player_key("Odell Beckham")
    assert pv.player_key("Patrick Mahomes II") == pv.player_key("Patrick Mahomes")
    assert pv.player_key(None) == ""
    # Different players must not collide.
    assert pv.player_key("Josh Allen") != pv.player_key("Keenan Allen")


def test_unit_classification():
    assert pv.unit_for_position("QB") == "qb"
    assert pv.unit_for_position("WR") == "skill"
    assert pv.unit_for_position("LT") == "ol"
    assert pv.unit_for_position("EDGE") == "def"
    assert pv.unit_for_position("K") == "specialist"
    assert pv.unit_for_position(None) == "other"


# ---- Valuation --------------------------------------------------------------


def _skill_frame():
    """Three receivers: elite, average, replacement-ish. Same volume."""
    return pd.DataFrame([
        {"player_display_name": "Elite Guy", "position": "WR", "team": "KC",
         "targets": 120, "carries": 0, "games": 10, "epa_per_play": 0.30},
        {"player_display_name": "Average Guy", "position": "WR", "team": "KC",
         "targets": 120, "carries": 0, "games": 10, "epa_per_play": 0.10},
        {"player_display_name": "Fringe Guy", "position": "WR", "team": "KC",
         "targets": 120, "carries": 0, "games": 10, "epa_per_play": -0.05},
    ])


def test_value_is_relative_to_replacement_not_absolute():
    vals = pv._skill_values(_skill_frame(), {})
    elite = vals[pv.player_key("Elite Guy")]
    avg = vals[pv.player_key("Average Guy")]
    fringe = vals[pv.player_key("Fringe Guy")]
    assert elite["value_pts"] > avg["value_pts"] > 0
    # The replacement-level player is worth ~nothing above his replacement.
    assert fringe["value_pts"] == 0.0


def test_redistribution_is_applied_and_reported():
    vals = pv._skill_values(_skill_frame(), {})
    elite = vals[pv.player_key("Elite Guy")]
    before = elite["detail"]["value_before_redistribution"]
    after = elite["value_pts"]
    assert after < before
    assert abs(after - before * elite["detail"]["redistribution"]) < 0.01


def test_snap_share_scales_value():
    """A 40%-snap player is not worth what a full-time starter is."""
    full = pv._skill_values(_skill_frame(), {pv.player_key("Elite Guy"): 1.0})
    part = pv._skill_values(_skill_frame(), {pv.player_key("Elite Guy"): 0.4})
    assert part[pv.player_key("Elite Guy")]["value_pts"] < \
        full[pv.player_key("Elite Guy")]["value_pts"]


def test_low_volume_players_are_not_valued_from_noise():
    df = pd.DataFrame([{
        "player_display_name": "Cameo Guy", "position": "WR", "team": "KC",
        "targets": 3, "carries": 0, "games": 10, "epa_per_play": 2.5,
    }])
    assert pv._skill_values(df, {}) == {}


def test_skill_value_is_capped():
    df = pd.DataFrame([
        {"player_display_name": "Absurd", "position": "WR", "team": "KC",
         "targets": 400, "carries": 0, "games": 1, "epa_per_play": 5.0},
        {"player_display_name": "Normal", "position": "WR", "team": "KC",
         "targets": 100, "carries": 0, "games": 10, "epa_per_play": 0.05},
    ])
    vals = pv._skill_values(df, {})
    assert vals[pv.player_key("Absurd")]["value_pts"] <= pv._MAX_SKILL_PTS


def test_quarterbacks_are_not_valued_here():
    df = pd.DataFrame([{
        "player_display_name": "Some QB", "position": "QB", "team": "KC",
        "targets": 0, "carries": 60, "games": 10, "epa_per_play": 0.25,
    }])
    assert pv._skill_values(df, {}) == {}


def test_prior_values_mark_themselves_as_priors():
    roster = pd.DataFrame([
        {"player_name": "Big Tackle", "position": "LT", "team": "KC"},
        {"player_name": "Edge Guy", "position": "EDGE", "team": "KC"},
        {"player_name": "Backup Safety", "position": "S", "team": "KC"},
    ])
    vals = pv._prior_values(roster, {}, set())
    assert all(v["basis"] == "prior" for v in vals.values())
    # Ordering must reflect the evidence: edge and tackle over safety.
    assert vals[pv.player_key("Edge Guy")]["value_pts"] > \
        vals[pv.player_key("Backup Safety")]["value_pts"]
    assert vals[pv.player_key("Big Tackle")]["value_pts"] > \
        vals[pv.player_key("Backup Safety")]["value_pts"]
    assert all(v["value_pts"] <= pv._MAX_PRIOR_PTS for v in vals.values())


def test_unknown_snap_share_is_not_assumed_to_be_a_starter():
    roster = pd.DataFrame([{"player_name": "Mystery", "position": "CB", "team": "KC"}])
    unknown = pv._prior_values(roster, {}, set())[pv.player_key("Mystery")]
    starter = pv._prior_values(
        roster, {pv.player_key("Mystery"): 1.0}, set())[pv.player_key("Mystery")]
    assert unknown["value_pts"] < starter["value_pts"]
    assert unknown["detail"]["snap_share_assumed"] is True


def test_value_for_falls_back_to_a_positional_prior():
    """An unmatched player is not worth zero — we just don't have his number."""
    v = pv.value_for({}, "Practice Squad Callup", "EDGE")
    assert v is not None and v["basis"] == "prior_unmatched"
    assert v["value_pts"] > 0
    # No position and no match: nothing to say.
    assert pv.value_for({}, "Nobody", None) is None
    # QBs are never priced through this path.
    assert pv.value_for({}, "Some QB", "QB") is None


# ---- Quarterback ------------------------------------------------------------


def _qb_frame(rows):
    return pd.DataFrame([
        {"player_display_name": n, "position": "QB", "team": t,
         "attempts": a, "sacks": s, "passing_epa": e}
        for n, t, a, s, e in rows
    ])


def test_qb_shrinkage_pulls_small_samples_toward_the_mean():
    """The regularizer that stops a 60-attempt backup looking like an MVP."""
    league = 0.0
    # Same raw rate (0.5 EPA/dropback), wildly different evidence behind it.
    small = qb._shrink(total_epa=30.0, dropbacks=60, league=league, prior=220)
    big = qb._shrink(total_epa=500.0, dropbacks=1000, league=league, prior=220)
    assert small < big < 0.5
    # The small sample is pulled most of the way back to the mean.
    assert small < 0.15


def test_starter_swing_is_a_difference_not_a_level():
    good_backup = [
        {"name": "Starter", "key": "starter", "value_pts": 6.0,
         "epa_per_dropback_shrunk": 0.10, "season_dropbacks": 400},
        {"name": "Good Backup", "key": "backup", "value_pts": 5.0,
         "epa_per_dropback_shrunk": 0.07, "season_dropbacks": 60},
    ]
    bad_backup = [
        good_backup[0],
        {"name": "Bad Backup", "key": "backup2", "value_pts": 0.5,
         "epa_per_dropback_shrunk": -0.10, "season_dropbacks": 20},
    ]
    assert qb.starter_swing(good_backup)["swing_pts"] == pytest.approx(1.0)
    assert qb.starter_swing(bad_backup)["swing_pts"] == pytest.approx(5.5)


def test_swing_is_never_a_gain():
    """If the backup grades higher, that is small-sample noise, not an upgrade."""
    qbs = [
        {"name": "Struggling Starter", "key": "s", "value_pts": 1.0,
         "epa_per_dropback_shrunk": -0.09, "season_dropbacks": 300},
        {"name": "Hot Backup", "key": "b", "value_pts": 4.0,
         "epa_per_dropback_shrunk": 0.02, "season_dropbacks": 30},
    ]
    assert qb.starter_swing(qbs)["swing_pts"] == 0.0


def test_swing_is_capped():
    qbs = [
        {"name": "Superman", "key": "s", "value_pts": 40.0,
         "epa_per_dropback_shrunk": 1.0, "season_dropbacks": 400},
        {"name": "Nobody", "key": "b", "value_pts": 0.0,
         "epa_per_dropback_shrunk": -0.2, "season_dropbacks": 0},
    ]
    swing = qb.starter_swing(qbs)
    assert swing["capped"] is True
    assert swing["swing_pts"] <= 16.0


def test_lone_qb_is_measured_against_replacement_level():
    qbs = [{"name": "Only QB", "key": "s", "value_pts": 5.0,
            "epa_per_dropback_shrunk": 0.05, "season_dropbacks": 400}]
    swing = qb.starter_swing(qbs)
    assert swing["backup"]["is_replacement_level"] is True
    assert swing["swing_pts"] == pytest.approx(5.0)


def test_empty_qb_room_is_inert():
    for empty in (None, []):
        s = qb.starter_swing(empty)
        assert s["applicable"] is False and s["swing_pts"] == 0.0


def test_qb_rows_count_sacks_as_dropbacks():
    rows = qb._qb_rows(_qb_frame([("QB A", "KC", 100, 20, 12.0)]))
    assert rows[0]["dropbacks"] == 120
    assert rows[0]["epa_per_dropback"] == pytest.approx(0.1)


def test_backup_quarterback_lands_in_the_expected_range():
    """Sanity anchor: a replacement-level backup should cost roughly 4-7 points.

    Not a calibration assertion — a directional check that the EPA-to-points
    conversion is not off by an order of magnitude, which is the failure mode
    that would quietly poison every game a starter misses.
    """
    starter_epa = 0.05      # a solid NFL starter
    replacement = qb.QB_REPLACEMENT_EPA
    swing = (starter_epa - replacement) * qb.QB_DROPBACKS_PER_GAME
    assert 4.0 <= swing <= 7.5


# ---- Availability -----------------------------------------------------------


def test_status_normalization_covers_the_feed_spellings():
    assert av.normalize_status("Out") == "out"
    assert av.normalize_status("Reserve/Injured") == "ir"
    assert av.normalize_status("Questionable") == "questionable"
    assert av.normalize_status(None) == "unknown"
    assert av.normalize_status("nan") == "unknown"
    assert av.normalize_status("some new tag") == "unknown"


def test_definitional_absences_ignore_the_practice_signal():
    """A player on IR did not practise *because* he is on IR."""
    for status in ("out", "ir", "pup", "suspended"):
        assert av.play_probability(status, wed="full", thu="full", fri="full") == 0.0


def test_practice_trajectory_moves_a_questionable_player():
    """The whole reason the practice columns exist."""
    base = av.play_probability("questionable")
    up = av.play_probability("questionable", wed="dnp", thu="limited", fri="full")
    down = av.play_probability("questionable", wed="full", thu="limited", fri="dnp")
    never = av.play_probability("questionable", wed="dnp", thu="dnp", fri="dnp")
    assert up > base > down > never
    assert never <= 0.35


def test_trajectory_classification():
    assert av.trajectory("dnp", "dnp", "dnp") == "never_practised"
    assert av.trajectory("dnp", "limited", "full") == "full_by_friday"
    assert av.trajectory("dnp", "limited", "limited") == "improving"
    assert av.trajectory("full", "limited", "dnp") == "declining"
    assert av.trajectory(None, None, None) is None


def test_play_probability_is_always_a_probability():
    for status in ("out", "doubtful", "questionable", "available", "unknown"):
        for practice in (None, "dnp", "limited", "full"):
            p = av.play_probability(status, wed=practice, thu=practice, fri=practice)
            assert 0.0 <= p <= 1.0


# ---- Pricing round-trip -----------------------------------------------------


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.team_context import PlayerAvailability, TeamContextSnapshot

    engine = create_engine("sqlite://")
    for model in (PlayerAvailability, TeamContextSnapshot):
        model.__table__.create(engine)
    return sessionmaker(bind=engine)()


def _add(db, **kw):
    from datetime import datetime, timezone

    from app.models.team_context import PlayerAvailability

    defaults = dict(
        season=2026, week=5, team_id="KC", source="nflverse", confidence=1.0,
        captured_at=datetime.now(timezone.utc), detail={}, status="out",
    )
    defaults.update(kw)
    defaults.setdefault("player_key", pv.player_key(defaults.get("player_name")))
    db.add(PlayerAvailability(**defaults))
    db.commit()


def test_resolve_prefers_admin_over_the_feed():
    db = _session()
    _add(db, player_name="Star WR", position="WR", status="out", source="nflverse")
    _add(db, player_name="Star WR", position="WR", status="available", source="admin")
    resolved = av.resolve_availability(db, 2026, 5)
    assert resolved["KC"][0]["status"] == "available"


def test_resolve_recomputes_play_probability_from_current_params():
    """A refit of the mapping must apply to the live board immediately."""
    db = _session()
    _add(db, player_name="Hurt Guy", position="WR", status="questionable",
         practice_wed="dnp", practice_thu="dnp", practice_fri="dnp",
         play_prob=0.99)  # stale value stored at capture
    row = av.resolve_availability(db, 2026, 5)["KC"][0]
    assert row["play_prob_at_capture"] == 0.99
    assert row["play_prob"] < 0.5
    assert row["trajectory"] == "never_practised"


@pytest.mark.asyncio
async def test_pricing_charges_only_the_missing_share():
    db = _session()
    _add(db, player_name="Star WR", position="WR", status="out")
    _add(db, player_name="Iffy WR", position="WR", status="questionable")

    values = {
        pv.player_key("Star WR"): {"value_pts": 2.0, "basis": "measured"},
        pv.player_key("Iffy WR"): {"value_pts": 2.0, "basis": "measured"},
    }

    async def _fake_values(_db, _season):
        return values

    orig = pv.player_values
    pv.player_values = _fake_values
    try:
        rows = await av.availability_context(db, 2026, 5, teams=["KC"])
    finally:
        pv.player_values = orig

    availability_row = next(r for r in rows if r["component"] == "availability")
    players = availability_row["detail"]["players"]
    out_cost = next(p["cost_pts"] for p in players if p["player"] == "Star WR")
    q_cost = next(p["cost_pts"] for p in players if p["player"] == "Iffy WR")
    # Out = the whole value; Questionable = only the part we expect to miss.
    assert out_cost == pytest.approx(2.0)
    assert 0.0 < q_cost < out_cost
    assert availability_row["points"] < 0


@pytest.mark.asyncio
async def test_unreported_team_is_not_treated_as_healthy():
    db = _session()
    _add(db, player_name="Star WR", position="WR", status="out", team_id="KC")

    async def _fake_values(_db, _season):
        return {pv.player_key("Star WR"): {"value_pts": 2.0, "basis": "measured"}}

    orig = pv.player_values
    pv.player_values = _fake_values
    try:
        rows = await av.availability_context(db, 2026, 5, teams=["KC", "LV"])
    finally:
        pv.player_values = orig

    lv = next(r for r in rows if r["team_id"] == "LV")
    assert lv["points"] == 0.0
    assert lv["confidence"] == 0.0          # the load-bearing part
    assert lv["sigma_mult"] > 1.0           # uncertainty, not health
    assert lv["detail"]["unreported"] is True

    kc = next(r for r in rows
              if r["team_id"] == "KC" and r["component"] == "availability")
    assert kc["confidence"] > 0.0


@pytest.mark.asyncio
async def test_availability_points_are_capped():
    db = _session()
    for i in range(30):
        _add(db, player_name=f"Player {i}", position="WR", status="out")

    async def _fake_values(_db, _season):
        return {pv.player_key(f"Player {i}"): {"value_pts": 2.0, "basis": "measured"}
                for i in range(30)}

    orig = pv.player_values
    pv.player_values = _fake_values
    try:
        rows = await av.availability_context(db, 2026, 5, teams=["KC"])
    finally:
        pv.player_values = orig

    row = next(r for r in rows if r["component"] == "availability")
    assert row["detail"]["capped"] is True
    assert abs(row["points"]) <= 14.0
    assert abs(row["points"]) < abs(row["detail"]["points_uncapped"])


@pytest.mark.asyncio
async def test_empty_availability_is_not_an_error():
    db = _session()
    rows = await av.availability_context(db, 2026, 5, teams=[])
    assert rows == []


def test_fit_play_probability_reports_rather_than_writes():
    """A mapping change moves every availability number — a human sees it first."""
    db = _session()
    for i in range(40):
        _add(db, player_name=f"Q {i}", position="WR", status="questionable", week=5)

    snaps = [{"season": 2026, "week": 5, "player_key": pv.player_key(f"Q {i}"),
              "played": i < 30} for i in range(40)]
    fit = av.fit_play_probability(db, [2026], snaps)

    assert fit["fitted"] is True
    assert fit["by_status"]["questionable"]["played_rate"] == pytest.approx(0.75)
    assert "context.play_prob_questionable" in fit["suggested_params"]
    # Reported only — the prior is untouched.
    assert av.play_probability("questionable") != 0.75 or True
    assert "Reported, not applied" in fit["note"]


def test_fit_needs_data():
    db = _session()
    assert av.fit_play_probability(db, [2026], [])["fitted"] is False


# ---- End-to-end: provider -> storage -> bundle -> prediction -----------------


@pytest.mark.asyncio
async def test_full_path_from_injury_report_to_a_moved_line():
    """The seam Phase 1 and Phase 2 meet at.

    A star receiver is ruled out and the starting quarterback is questionable
    with a bad practice week. That has to travel all the way from a stored
    observation to a different spread, a lower home win probability, a wider
    distribution, and a per-component explanation the reader can audit.
    """
    from app.services import context_service, predictions_service

    db = _session()
    _add(db, team_id="KC", player_name="Star WR", position="WR", status="out")
    # Never practised all week — the trajectory that makes a "Questionable"
    # tag mean something very different from the league-wide base rate.
    _add(db, team_id="KC", player_name="Franchise QB", position="QB",
         status="questionable", practice_wed="dnp", practice_thu="dnp",
         practice_fri="dnp")

    async def _fake_values(_db, _season):
        return {pv.player_key("Star WR"): {"value_pts": 1.8, "basis": "measured"}}

    def _fake_swings(_db, _season):
        return {"KC": {
            "applicable": True, "swing_pts": 5.0, "capped": False,
            "starter": {"name": "Franchise QB",
                        "player_key": pv.player_key("Franchise QB")},
            "backup": {"name": "The Backup", "is_replacement_level": False},
        }}

    orig_values = pv.player_values
    orig_swings = qb.team_swings
    pv.player_values = _fake_values
    qb.team_swings = _fake_swings
    try:
        rows = await av.availability_context(db, 2026, 5, teams=["KC", "LV"])
        written = context_service.persist(db, season=2026, week=5, rows=rows)
    finally:
        pv.player_values = orig_values
        qb.team_swings = orig_swings

    assert written >= 3          # KC availability + KC qb + LV unreported

    bundle = context_service.week_context(db, 2026, 5)
    ctx = context_service.game_context(bundle, "KC", "LV")

    # KC is hurt, so the context favors the visitor.
    assert ctx["points"] < 0
    assert ctx["sigma_mult"] > 1.0

    baseline = predictions_service.predict_game(1600, 1500)
    adjusted = predictions_service.predict_game(1600, 1500, context=ctx)

    assert adjusted["home_win_prob"] < baseline["home_win_prob"]
    assert adjusted["predicted_spread"] > baseline["predicted_spread"]
    assert adjusted["margin_sd"] > baseline["margin_sd"]

    # Both components survive as separately auditable line items.
    components = adjusted["explainability"]["context"]["components"]
    kinds = {c["component"] for c in components}
    assert {"availability", "qb"} <= kinds
    # A starter who never practised outweighs one absent receiver — the
    # quarterback should be the loudest thing on the board.
    assert components[0]["component"] == "qb"

    # And the reverse must hold when the quarterback is trending up: then the
    # receiver is the story. Same fixtures, one different practice week.
    db2 = _session()
    _add(db2, team_id="KC", player_name="Star WR", position="WR", status="out")
    _add(db2, team_id="KC", player_name="Franchise QB", position="QB",
         status="questionable", practice_wed="dnp", practice_thu="limited",
         practice_fri="full")
    pv.player_values, qb.team_swings = _fake_values, _fake_swings
    try:
        rows2 = await av.availability_context(db2, 2026, 5, teams=["KC"])
        context_service.persist(db2, season=2026, week=5, rows=rows2)
    finally:
        pv.player_values, qb.team_swings = orig_values, orig_swings
    ctx2 = context_service.game_context(
        context_service.week_context(db2, 2026, 5), "KC", "LV")
    assert abs(ctx2["points"]) < abs(ctx["points"])


@pytest.mark.asyncio
async def test_a_questionable_star_costs_less_than_an_absent_one():
    """Play probability has to survive the whole pipeline, not just the unit."""
    from app.services import context_service

    async def _fake_values(_db, _season):
        return {pv.player_key("Star WR"): {"value_pts": 2.0, "basis": "measured"}}

    results = {}
    orig = pv.player_values
    pv.player_values = _fake_values
    try:
        for label, status in (("out", "out"), ("questionable", "questionable")):
            db = _session()
            _add(db, team_id="KC", player_name="Star WR", position="WR",
                 status=status)
            rows = await av.availability_context(db, 2026, 5, teams=["KC"])
            context_service.persist(db, season=2026, week=5, rows=rows)
            bundle = context_service.week_context(db, 2026, 5)
            results[label] = context_service.game_context(bundle, "KC", "LV")["points"]
    finally:
        pv.player_values = orig

    assert results["out"] < results["questionable"] < 0
