"""Market-blind NFL drive table for a later Monte Carlo.

P1 loads nflverse data, builds one row per offensive drive, and checks that
those points reproduce the final score. It does not simulate a game, fit a
model, or read the served rating coefficients.
"""

from .calendar import SCREEN_CUTOFF, before_screen_cutoff, eastern_kickoff
from .drives import DRIVE_COLUMNS, OUTCOMES, build_drive_table

__all__ = [
    "DRIVE_COLUMNS",
    "OUTCOMES",
    "SCREEN_CUTOFF",
    "before_screen_cutoff",
    "build_drive_table",
    "eastern_kickoff",
]
