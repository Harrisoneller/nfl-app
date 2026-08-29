"""Context layer storage — append-only snapshots of what is true *now*.

Why this exists
---------------
Every other input to the NFL model is derived from what already happened: Elo
from results, opponent-adjusted EPA from plays, the market blend from lines
that themselves price history. All of it is backward-looking, and all of it
silently assumes the team that plays Sunday is the team that produced the
history.

Often it is not. A starting quarterback goes down Wednesday, three offensive
linemen miss practice, a coordinator is fired in November, a clinched team
rests half its starters in Week 18. Those are the single largest sources of
prediction error a purely historical model can have, they are knowable before
kickoff, and the market prices them within minutes of the news breaking.

Two tables, both **append-only**:

``player_availability_snapshots``
    One row per (player, week, source, capture). The official NFL injury
    report, ESPN's feed, LLM-extracted beat-writer candidates and manual admin
    entries all land here in the same shape, tagged by source. Nothing is ever
    updated in place: the resolver reads the latest row per player per source
    and applies SOURCE_PRIORITY.

``team_context_snapshots``
    One row per (team, week, component, source, capture) carrying the *points*
    that component is worth to that team, plus a confidence and a sigma
    multiplier. This is the model-facing table — ``context_service`` reduces it
    to a per-team delta and ``predict_game`` applies it.

Append-only matters for far more than auditability. A backtest that reads a
mutable "current injuries" table leaks the future into every historical game,
and will report an edge that does not exist. With capture timestamps you can
always ask the honest question — *what did we know at the time the line was
available?* — which is also exactly what the CLV validation harness needs.

See docs/CONTEXT_LAYER_ROADMAP.md for how the components are meant to grow.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin

# JSONB on Postgres, plain JSON on SQLite. The rest of the app uses bare JSONB,
# which cannot render on SQLite and is why ``Base.metadata.create_all()`` fails
# under the SQLite test fixture. Doing it properly here keeps the Phase-2
# context providers unit-testable without standing up a Postgres.
_JSON = JSONB().with_variant(JSON(), "sqlite")

# ---- Availability vocabulary ------------------------------------------------
# The NFL's mandated report uses Out / Doubtful / Questionable plus the roster
# designations (IR, PUP, NFI, suspended). Everything from every source is
# normalized into this vocabulary so downstream code never branches on source.
AVAILABILITY_STATUSES = (
    "out",
    "doubtful",
    "questionable",
    "ir",            # injured reserve — season or 4-game window
    "pup",           # physically unable to perform
    "nfi",           # non-football injury
    "suspended",
    "available",
    "unknown",
)

# Practice participation, the NFL's real signal. The game-status tag is a
# coarse, strategically-reported summary; the Wed/Thu/Fri participation
# *trajectory* (DNP → DNP → LP is a very different player from LP → LP → FP)
# carries materially more information and is what the availability provider
# should key on once it is built. Stored raw here so that provider can.
PRACTICE_STATUSES = ("dnp", "limited", "full", "unknown")

# Default probability the player takes the field, by game status. These are
# code defaults only — they are deliberately *placeholders*. The roadmap calls
# for fitting this mapping empirically from historical report rows joined to
# actual snap counts, because the league-wide Questionable→play rate has
# drifted a long way from the folk number and varies by position and by team's
# reporting habits. Tunable meanwhile via ``context.play_prob_*``.
DEFAULT_PLAY_PROB: dict[str, float] = {
    "out": 0.0,
    "ir": 0.0,
    "pup": 0.0,
    "nfi": 0.0,
    "suspended": 0.0,
    "doubtful": 0.06,
    "questionable": 0.72,
    "available": 1.0,
    "unknown": 0.90,
}

# Source precedence when feeds disagree about the same player. The league's own
# mandated report outranks a scraped aggregator, which outranks a language
# model reading a beat reporter, and an admin outranks everything because a
# human looked at it.
SOURCE_PRIORITY: dict[str, int] = {
    "admin": 100,
    "nflverse": 80,     # official injury report via nfl_data_py
    "espn": 60,
    "news_llm": 40,
    "derived": 20,
}

# Model-facing context components. Each is computed by its own provider and
# stays separately visible all the way to the explainability panel — a reader
# who sees "-2.8 availability" learns something a lumped number cannot tell,
# and a component that cannot be shown on its own cannot be audited by us
# either. Adding one here is the first step of adding a provider.
CONTEXT_COMPONENTS = (
    "availability",      # injuries / suspensions, valued in points
    "qb",                # starter vs. next-man-up delta (largest single term)
    "staff_change",      # in-season firing, interim staff, coordinator change
    "scheme_matchup",    # style-vs-style interaction for this specific opponent
    "situational",       # rest, travel, time zone, divisional, motivation
    "weather",           # wind-driven total and variance effects
    "manual",            # admin-entered context note with a point value
)


class PlayerAvailability(Base, TimestampMixin):
    """One observation of one player's game availability, from one source."""

    __tablename__ = "player_availability_snapshots"
    __table_args__ = (
        Index("ix_pavail_week_team", "season", "week", "team_id"),
        Index("ix_pavail_week_player", "season", "week", "player_key"),
        Index("ix_pavail_captured", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    season: Mapped[int] = mapped_column(Integer, nullable=False)
    # NULL = season-scoped (season-ending injury, offseason suspension).
    week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)

    # Normalized name key ("first.last", lowercased, punctuation stripped).
    # Injury reports carry names, not ids, and gsis_id is absent from most feeds.
    player_key: Mapped[str] = mapped_column(String(128), nullable=False)
    player_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # nflverse gsis_id when the resolver matched one; often NULL.
    player_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    position: Mapped[str | None] = mapped_column(String(16), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    # Practice participation for the three report days. NFL-specific and the
    # more predictive signal — kept as its own columns rather than buried in
    # ``detail`` so the provider can query on the trajectory.
    practice_wed: Mapped[str | None] = mapped_column(String(16), nullable=True)
    practice_thu: Mapped[str | None] = mapped_column(String(16), nullable=True)
    practice_fri: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Resolved probability the player appears. Stored rather than recomputed so
    # a historical row keeps the mapping that was in force when it was captured
    # — otherwise refitting the status→prob model silently rewrites the past.
    play_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_detail: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0-1. The official report is 1.0; an LLM reading a tweet is not.
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)


class TeamContextSnapshot(Base, TimestampMixin):
    """One component's point value for one team at one capture time."""

    __tablename__ = "team_context_snapshots"
    __table_args__ = (
        Index("ix_tctx_week_team", "season", "week", "team_id"),
        Index("ix_tctx_week_component", "season", "week", "component"),
        Index("ix_tctx_captured", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # Components that only apply to a specific matchup (scheme_matchup) carry
    # the opponent so they are never applied league-wide.
    opponent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    component: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="derived")

    # Signed points, team's own perspective: positive = this team is stronger
    # than its history implies. predict_game differences the two sides.
    value_pts: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Multiplier on the game's margin sigma. Missing information should widen
    # the distribution, not only shift it — a team we know nothing about this
    # week is not the same as a team we know is healthy.
    sigma_mult: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    # Everything a reader needs to see *why*: which players, which report,
    # which scheme features fired. This is what the explainability panel renders.
    detail: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    evidence_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
