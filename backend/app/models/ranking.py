"""Custom fantasy ranking sets — admin-authored boards independent of the model.

Projections produce one ordering; fantasy formats produce many. A
``RankingSet`` is a named, format-tagged big board ("PPR Redraft",
"Superflex", "Dynasty") whose order the admin controls entirely. Entries
reference players but their ranks/tiers are hand-set — the projection engine
never rewrites them.

Draft → publish semantics
-------------------------
The ``ranking_entries`` rows are the *working draft*: every admin edit
(drag, tier break, note) mutates them. Publishing snapshots the current
entries into ``published_json`` on the set, bumps ``version``, and stamps
``published_at``. Public fantasy endpoints serve ONLY the snapshot, so the
admin can keep reshuffling privately and readers never see a half-edited
board. A set with ``published_at IS NULL`` has never been visible publicly.

The snapshot embeds name/position/team at publish time so public reads don't
depend on roster churn; live injury status is re-joined at read time.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ._mixins import TimestampMixin

# Format tags are advisory metadata (drives default scoring for model-rank
# comparison + display chips). "custom" is the escape hatch.
RANKING_FORMATS = (
    "ppr", "half_ppr", "standard", "superflex", "two_qb",
    "dynasty", "best_ball", "custom",
)

RANKING_STATUSES = ("draft", "published")


class RankingSet(Base, TimestampMixin):
    __tablename__ = "ranking_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")

    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    entries: Mapped[list["RankingEntry"]] = relationship(
        back_populates="ranking_set",
        cascade="all, delete-orphan",
        order_by="RankingEntry.rank",
    )

    __table_args__ = (
        UniqueConstraint("season", "name", name="uq_ranking_sets_season_name"),
        Index("ix_ranking_sets_season_status", "season", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "season": self.season,
            "format": self.format,
            "description": self.description,
            "status": self.status,
            "version": self.version,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class RankingEntry(Base, TimestampMixin):
    """One player's slot in one set's working draft. ``rank`` is 1-based and
    dense within a set; ``tier`` is 1-based and non-decreasing down the board."""

    __tablename__ = "ranking_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey("ranking_sets.id", ondelete="CASCADE"), nullable=False,
    )
    player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    note: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    ranking_set: Mapped[RankingSet] = relationship(back_populates="entries")

    __table_args__ = (
        UniqueConstraint("set_id", "player_id", name="uq_ranking_entries_set_player"),
        Index("ix_ranking_entries_set_rank", "set_id", "rank"),
    )

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "rank": self.rank,
            "tier": self.tier,
            "note": self.note,
        }
