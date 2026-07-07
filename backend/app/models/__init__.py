"""SQLAlchemy models package.

Importing this module registers every model with `Base.metadata` for Alembic
autogenerate.
"""
from .bet import Bet, BetLeg  # noqa: F401
from .chat import ChatMessage, ChatSession  # noqa: F401
from .data_sync_run import DataSyncRun  # noqa: F401
from .elo import TeamEloRating  # noqa: F401
from .endpoint_slo_snapshot import EndpointSloSnapshot  # noqa: F401
from .experiment_event import ExperimentEvent  # noqa: F401
from .feature_snapshot import FeatureSnapshot  # noqa: F401
from .game import Game, GameStat  # noqa: F401
from .model_artifact import ModelArtifact  # noqa: F401
from .model_lifecycle_run import ModelLifecycleRun  # noqa: F401
from .news import NewsItem  # noqa: F401
from .odds import OddsLine  # noqa: F401
from .odds_snapshot import OddsSnapshot  # noqa: F401
from .player import Player  # noqa: F401
from .player_metric_value import PlayerMetricValue  # noqa: F401
from .player_prop_snapshot import PlayerPropSnapshot  # noqa: F401
from .player_season_stat import PlayerSeasonStat  # noqa: F401
from .sparky import (  # noqa: F401
    SparkyGamePrediction,
    SparkyHistoricalResult,
    SparkyParlayRanking,
    SparkyParlayResult,
)
from .team_metric_value import TeamMetricValue  # noqa: F401
from .team_season_aggregate import TeamSeasonAggregate  # noqa: F401
from .team import Team  # noqa: F401
from .user import User  # noqa: F401
from .widget import Widget  # noqa: F401
