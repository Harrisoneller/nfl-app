"""Admin projection overrides — the manual-adjustment layer.

An ``AdminOverride`` is one hand-set value that supersedes a model output at
read time. The model keeps producing its own numbers; overrides are applied
on top when responses are built, so a model re-run never wipes an adjustment
and deleting the row instantly reverts to pure model output.

Scope semantics
---------------
* ``entity_type='game'``  — ``entity_id`` is the nflverse game_id.
  Fields: ``predicted_spread``, ``predicted_total``, ``home_win_prob``.
* ``entity_type='player'`` — ``entity_id`` is the Player.id.
  Week-scoped fields: any projected stat key (``passing_yards``,
  ``receiving_tds``, …) or ``fantasy_points_<fmt>`` (ppr/half_ppr/standard).
  Week-scoped rank pin: ``pos_rank`` (start/sit board position rank).
  Season-scoped (``week IS NULL``) rank pin: ``rank`` (season leaderboard).
  Season-scoped INPUT fields (``week IS NULL``, PLAYER_INPUT_FIELDS): usage
  levers — ``target_share``, ``rush_share``, ``yards_per_target``,
  ``yards_per_carry``, ``snap_rate`` — that scale the projection *inputs*
  (posterior rates) rather than pinning outputs. For a role change the input
  lever is the right tool: every downstream stat, prop probability, and
  fantasy number moves consistently.
* ``entity_type='team'`` — ``entity_id`` is the team id (e.g. "KC").
  Season-scoped input levers (TEAM_INPUT_FIELDS): ``pace`` (offensive plays
  per game), ``yards_per_play``, ``pass_rate`` (neutral-situation), and
  ``points_per_game`` (direct scoring level). Set these for a coaching or
  scheme change; they adjust the scoring model's inputs and flow through to
  game totals, spreads, and every player on the roster.

``original_value`` snapshots what the model said at the moment the override
was created — purely informational, shown in the admin UI as "model" vs
"yours".
"""
from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ._mixins import TimestampMixin

ENTITY_TYPES = ("game", "player", "team")

GAME_FIELDS = ("predicted_spread", "predicted_total", "home_win_prob")

# Team-level model-input levers (season/rest-of-season scoped, week IS NULL).
TEAM_INPUT_FIELDS = ("pace", "yards_per_play", "pass_rate", "points_per_game")

# Player-level usage levers (season scoped, week IS NULL). Distinct from stat
# output overrides: these scale the posterior rates feeding every projection.
PLAYER_INPUT_FIELDS = (
    "target_share", "rush_share", "yards_per_target", "yards_per_carry",
    "snap_rate",
)


class AdminOverride(Base, TimestampMixin):
    __tablename__ = "admin_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field: Mapped[str] = mapped_column(String(48), nullable=False)

    value: Mapped[float] = mapped_column(Float, nullable=False)
    original_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    __table_args__ = (
        Index("ix_admin_overrides_scope", "entity_type", "season", "week"),
        Index("ix_admin_overrides_entity", "entity_type", "entity_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "season": self.season,
            "week": self.week,
            "field": self.field,
            "value": self.value,
            "original_value": self.original_value,
            "note": self.note,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
