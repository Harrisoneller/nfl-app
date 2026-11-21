"""Abbreviations that changed city but are the same franchise.

Play-by-play uses the current code. Older schedule rows still say STL, SD,
or OAK. The crosswalk is only for joining a side to a game. The drive table
keeps the abbreviation that was on the play.
"""

from __future__ import annotations

CANON = {
    "STL": "LA",
    "LAR": "LA",
    "SD": "LAC",
    "OAK": "LV",
    "JAC": "JAX",
    "WSH": "WAS",
}


def canon(team: object) -> str:
    text = "" if team is None else str(team).strip()
    if text in ("", "nan", "None", "NaN"):
        return ""
    return CANON.get(text, text)


def same_team(left: object, right: object) -> bool:
    a = canon(left)
    b = canon(right)
    return a != "" and a == b
