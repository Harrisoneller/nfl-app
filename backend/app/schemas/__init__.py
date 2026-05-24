"""Pydantic schemas (DTOs)."""
from .ai import ChatRequest, ChatResponse, WidgetBuildRequest  # noqa: F401
from .auth import (  # noqa: F401
    ChangePasswordRequest,
    LoginRequest,
    SignupRequest,
    TokenResponse,
    UpdateProfileRequest,
    UserProfile,
)
from .common import Paginated  # noqa: F401
from .game import GameOut  # noqa: F401
from .news import NewsItemOut  # noqa: F401
from .odds import OddsLineOut  # noqa: F401
from .player import PlayerOut  # noqa: F401
from .team import TeamOut, TeamWithStats  # noqa: F401
from .widget import WidgetOut, WidgetSpec  # noqa: F401
