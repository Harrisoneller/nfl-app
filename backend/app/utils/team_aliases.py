"""Team aliases for news tagging + subreddit map.

When news arrives, we lower-case the title + summary and look for any of
these phrases. Order matters: longer phrases first so "kansas city chiefs"
matches before "chiefs" (which might be ambiguous in some contexts, but
within an NFL feed it's effectively unique).
"""
from __future__ import annotations

# id → list of aliases (longest first within each list)
TEAM_ALIASES: dict[str, list[str]] = {
    "ARI": ["arizona cardinals", "cardinals", "cards"],
    "ATL": ["atlanta falcons", "falcons"],
    "BAL": ["baltimore ravens", "ravens"],
    "BUF": ["buffalo bills", "bills"],
    "CAR": ["carolina panthers", "panthers"],
    "CHI": ["chicago bears", "bears"],
    "CIN": ["cincinnati bengals", "bengals"],
    "CLE": ["cleveland browns", "browns"],
    "DAL": ["dallas cowboys", "cowboys", "boys"],
    "DEN": ["denver broncos", "broncos"],
    "DET": ["detroit lions", "lions"],
    "GB": ["green bay packers", "packers", "pack"],
    "HOU": ["houston texans", "texans"],
    "IND": ["indianapolis colts", "colts"],
    "JAX": ["jacksonville jaguars", "jaguars", "jags"],
    "KC": ["kansas city chiefs", "chiefs"],
    "LAC": ["los angeles chargers", "la chargers", "chargers", "bolts"],
    "LAR": ["los angeles rams", "la rams", "rams"],
    "LV": ["las vegas raiders", "raiders"],
    "MIA": ["miami dolphins", "dolphins", "fins"],
    "MIN": ["minnesota vikings", "vikings", "vikes"],
    "NE": ["new england patriots", "patriots", "pats"],
    "NO": ["new orleans saints", "saints"],
    "NYG": ["new york giants", "ny giants", "giants", "g-men"],
    "NYJ": ["new york jets", "ny jets", "jets"],
    "PHI": ["philadelphia eagles", "eagles", "philly"],
    "PIT": ["pittsburgh steelers", "steelers"],
    "SEA": ["seattle seahawks", "seahawks", "hawks"],
    "SF": ["san francisco 49ers", "49ers", "niners", "9ers"],
    "TB": ["tampa bay buccaneers", "buccaneers", "bucs"],
    "TEN": ["tennessee titans", "titans"],
    "WAS": ["washington commanders", "commanders", "washington football team"],
}

# id → primary team subreddit slug (without leading 'r/')
TEAM_SUBREDDITS: dict[str, str] = {
    "ARI": "azcardinals",
    "ATL": "falcons",
    "BAL": "ravens",
    "BUF": "buffalobills",
    "CAR": "panthers",
    "CHI": "chibears",
    "CIN": "bengals",
    "CLE": "browns",
    "DAL": "cowboys",
    "DEN": "denverbroncos",
    "DET": "detroitlions",
    "GB": "greenbaypackers",
    "HOU": "texans",
    "IND": "colts",
    "JAX": "jaguars",
    "KC": "kansascitychiefs",
    "LAC": "chargers",
    "LAR": "losangelesrams",
    "LV": "raiders",
    "MIA": "miamidolphins",
    "MIN": "minnesotavikings",
    "NE": "patriots",
    "NO": "saints",
    "NYG": "nygiants",
    "NYJ": "nyjets",
    "PHI": "eagles",
    "PIT": "steelers",
    "SEA": "seahawks",
    "SF": "49ers",
    "TB": "buccaneers",
    "TEN": "tennesseetitans",
    "WAS": "commanders",
}


def tags_for_text(text: str) -> list[str]:
    """Return team_ids whose aliases appear in `text` (case-insensitive)."""
    if not text:
        return []
    t = text.lower()
    found: set[str] = set()
    for team_id, aliases in TEAM_ALIASES.items():
        for alias in aliases:
            # Use word-boundary-ish check by padding spaces; cheaper than regex.
            if f" {alias} " in f" {t} " or t.startswith(alias + " ") or t.endswith(" " + alias):
                found.add(team_id)
                break
    return sorted(found)
