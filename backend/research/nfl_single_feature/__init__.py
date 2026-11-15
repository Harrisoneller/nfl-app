"""Phase-1 scaffolding for three market-blind single-feature tests.

Synthetic inputs only. No network, no play-by-play files, and no shipped
stage-2 coefficients. The procedure is ``docs/prereg/PROTOCOL.md``.
"""

from .evaluate import confirmatory_look, exploratory_screen, minimum_detectable_effect
from .neutral_pace import neutral_pace_feature
from .ol_availability import ol_matchup_feature
from .pressure import filter_dropbacks, pressure_matchup_feature

__all__ = [
    "confirmatory_look",
    "exploratory_screen",
    "filter_dropbacks",
    "minimum_detectable_effect",
    "neutral_pace_feature",
    "ol_matchup_feature",
    "pressure_matchup_feature",
]
