"""SQLAlchemy models package.

Importing this module registers every model with `Base.metadata` for Alembic
autogenerate.
"""
from .chat import ChatMessage, ChatSession  # noqa: F401
from .data_sync_run import DataSyncRun  # noqa: F401
from .elo import TeamEloRating  # noqa: F401
from .game import Game, GameStat  # noqa: F401
from .model_artifact import ModelArtifact  # noqa: F401
from .news import NewsItem  # noqa: F401
from .odds import OddsLine  # noqa: F401
from .player import Player  # noqa: F401
from .team import Team  # noqa: F401
from .user import User  # noqa: F401
from .widget import Widget  # noqa: F401
