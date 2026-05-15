"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("hashed_password", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.String(8), nullable=False),
        sa.Column("espn_id", sa.Integer, nullable=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("market", sa.String(64), nullable=False, server_default=""),
        sa.Column("full_name", sa.String(128), nullable=False),
        sa.Column("conference", sa.String(8), nullable=False, server_default=""),
        sa.Column("division", sa.String(16), nullable=False, server_default=""),
        sa.Column("primary_color", sa.String(8), nullable=False, server_default="#111827"),
        sa.Column("secondary_color", sa.String(8), nullable=False, server_default="#9ca3af"),
        sa.Column("logo_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_teams_espn_id", "teams", ["espn_id"])

    op.create_table(
        "players",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("gsis_id", sa.String(32), nullable=True),
        sa.Column("espn_id", sa.Integer, nullable=True),
        sa.Column("full_name", sa.String(128), nullable=False),
        sa.Column("position", sa.String(8), nullable=False, server_default=""),
        sa.Column("team_id", sa.String(8), nullable=True),
        sa.Column("jersey_number", sa.Integer, nullable=True),
        sa.Column("age", sa.Integer, nullable=True),
        sa.Column("height", sa.String(8), nullable=True),
        sa.Column("weight", sa.Integer, nullable=True),
        sa.Column("college", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default=""),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_players_full_name", "players", ["full_name"])
    op.create_index("ix_players_gsis_id", "players", ["gsis_id"])
    op.create_index("ix_players_espn_id", "players", ["espn_id"])
    op.create_index("ix_players_team_id", "players", ["team_id"])

    op.create_table(
        "games",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("season", sa.Integer, nullable=False),
        sa.Column("week", sa.Integer, nullable=True),
        sa.Column("season_type", sa.Integer, nullable=False, server_default="2"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="scheduled"),
        sa.Column("status_detail", sa.String(64), nullable=False, server_default=""),
        sa.Column("home_team_id", sa.String(8), nullable=True),
        sa.Column("away_team_id", sa.String(8), nullable=True),
        sa.Column("home_score", sa.Integer, nullable=True),
        sa.Column("away_score", sa.Integer, nullable=True),
        sa.Column("venue", sa.String(128), nullable=False, server_default=""),
        sa.Column("broadcast", sa.String(64), nullable=False, server_default=""),
        sa.Column("raw", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["home_team_id"], ["teams.id"]),
        sa.ForeignKeyConstraint(["away_team_id"], ["teams.id"]),
    )
    op.create_index("ix_games_season", "games", ["season"])
    op.create_index("ix_games_week", "games", ["week"])
    op.create_index("ix_games_home_team_id", "games", ["home_team_id"])
    op.create_index("ix_games_away_team_id", "games", ["away_team_id"])

    op.create_table(
        "game_stats",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("game_id", sa.String(32), nullable=False),
        sa.Column("team_id", sa.String(8), nullable=False),
        sa.Column("stats", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"]),
        sa.UniqueConstraint("game_id", "team_id", name="uq_game_team"),
    )
    op.create_index("ix_game_stats_game_id", "game_stats", ["game_id"])
    op.create_index("ix_game_stats_team_id", "game_stats", ["team_id"])

    op.create_table(
        "news_items",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("source_label", sa.String(128), nullable=False, server_default=""),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("link", sa.String(1024), nullable=False),
        sa.Column("author", sa.String(255), nullable=False, server_default=""),
        sa.Column("image_url", sa.String(1024), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("team_tags", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_news_published_at", "news_items", ["published_at"])
    op.create_index("ix_news_source", "news_items", ["source"])

    op.create_table(
        "odds_lines",
        sa.Column("id", sa.Integer, autoincrement=True, nullable=False),
        sa.Column("market", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=True),
        sa.Column("home_team", sa.String(128), nullable=True),
        sa.Column("away_team", sa.String(128), nullable=True),
        sa.Column("commence_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bookmaker", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("price", sa.Integer, nullable=True),
        sa.Column("point", sa.Float, nullable=True),
        sa.Column("raw", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_odds_market", "odds_lines", ["market"])
    op.create_index("ix_odds_event", "odds_lines", ["event_id"])

    op.create_table(
        "widgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("spec", postgresql.JSONB, nullable=False),
        sa.Column("pinned", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_rendered_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_widgets_user_id", "widgets", ["user_id"])

    op.create_table(
        "chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default="New chat"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("tool_calls", postgresql.JSONB, nullable=True),
        sa.Column("tool_results", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])

    # Seed system user
    op.execute(
        "INSERT INTO users (id, email, display_name, is_active, is_admin) "
        "VALUES (gen_random_uuid(), 'system@local', 'System', true, true) "
        "ON CONFLICT (email) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("widgets")
    op.drop_table("odds_lines")
    op.drop_table("news_items")
    op.drop_table("game_stats")
    op.drop_table("games")
    op.drop_table("players")
    op.drop_table("teams")
    op.drop_table("users")
