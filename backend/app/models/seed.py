"""Static team seed data with NFL team colors.

Loaded on startup if the teams table is empty.
"""
from __future__ import annotations

NFL_TEAMS: list[dict] = [
    # AFC East
    {"id": "BUF", "espn_id": 2, "market": "Buffalo", "name": "Bills", "conference": "AFC", "division": "East", "primary_color": "#00338D", "secondary_color": "#C60C30"},
    {"id": "MIA", "espn_id": 15, "market": "Miami", "name": "Dolphins", "conference": "AFC", "division": "East", "primary_color": "#008E97", "secondary_color": "#FC4C02"},
    {"id": "NE", "espn_id": 17, "market": "New England", "name": "Patriots", "conference": "AFC", "division": "East", "primary_color": "#002244", "secondary_color": "#C60C30"},
    {"id": "NYJ", "espn_id": 20, "market": "New York", "name": "Jets", "conference": "AFC", "division": "East", "primary_color": "#125740", "secondary_color": "#000000"},
    # AFC North
    {"id": "BAL", "espn_id": 33, "market": "Baltimore", "name": "Ravens", "conference": "AFC", "division": "North", "primary_color": "#241773", "secondary_color": "#9E7C0C"},
    {"id": "CIN", "espn_id": 4, "market": "Cincinnati", "name": "Bengals", "conference": "AFC", "division": "North", "primary_color": "#FB4F14", "secondary_color": "#000000"},
    {"id": "CLE", "espn_id": 5, "market": "Cleveland", "name": "Browns", "conference": "AFC", "division": "North", "primary_color": "#311D00", "secondary_color": "#FF3C00"},
    {"id": "PIT", "espn_id": 23, "market": "Pittsburgh", "name": "Steelers", "conference": "AFC", "division": "North", "primary_color": "#FFB612", "secondary_color": "#101820"},
    # AFC South
    {"id": "HOU", "espn_id": 34, "market": "Houston", "name": "Texans", "conference": "AFC", "division": "South", "primary_color": "#03202F", "secondary_color": "#A71930"},
    {"id": "IND", "espn_id": 11, "market": "Indianapolis", "name": "Colts", "conference": "AFC", "division": "South", "primary_color": "#002C5F", "secondary_color": "#A2AAAD"},
    {"id": "JAX", "espn_id": 30, "market": "Jacksonville", "name": "Jaguars", "conference": "AFC", "division": "South", "primary_color": "#101820", "secondary_color": "#D7A22A"},
    {"id": "TEN", "espn_id": 10, "market": "Tennessee", "name": "Titans", "conference": "AFC", "division": "South", "primary_color": "#0C2340", "secondary_color": "#4B92DB"},
    # AFC West
    {"id": "DEN", "espn_id": 7, "market": "Denver", "name": "Broncos", "conference": "AFC", "division": "West", "primary_color": "#FB4F14", "secondary_color": "#002244"},
    {"id": "KC", "espn_id": 12, "market": "Kansas City", "name": "Chiefs", "conference": "AFC", "division": "West", "primary_color": "#E31837", "secondary_color": "#FFB81C"},
    {"id": "LV", "espn_id": 13, "market": "Las Vegas", "name": "Raiders", "conference": "AFC", "division": "West", "primary_color": "#000000", "secondary_color": "#A5ACAF"},
    {"id": "LAC", "espn_id": 24, "market": "Los Angeles", "name": "Chargers", "conference": "AFC", "division": "West", "primary_color": "#0080C6", "secondary_color": "#FFC20E"},
    # NFC East
    {"id": "DAL", "espn_id": 6, "market": "Dallas", "name": "Cowboys", "conference": "NFC", "division": "East", "primary_color": "#003594", "secondary_color": "#869397"},
    {"id": "NYG", "espn_id": 19, "market": "New York", "name": "Giants", "conference": "NFC", "division": "East", "primary_color": "#0B2265", "secondary_color": "#A71930"},
    {"id": "PHI", "espn_id": 21, "market": "Philadelphia", "name": "Eagles", "conference": "NFC", "division": "East", "primary_color": "#004C54", "secondary_color": "#A5ACAF"},
    {"id": "WAS", "espn_id": 28, "market": "Washington", "name": "Commanders", "conference": "NFC", "division": "East", "primary_color": "#5A1414", "secondary_color": "#FFB612"},
    # NFC North
    {"id": "CHI", "espn_id": 3, "market": "Chicago", "name": "Bears", "conference": "NFC", "division": "North", "primary_color": "#0B162A", "secondary_color": "#C83803"},
    {"id": "DET", "espn_id": 8, "market": "Detroit", "name": "Lions", "conference": "NFC", "division": "North", "primary_color": "#0076B6", "secondary_color": "#B0B7BC"},
    {"id": "GB", "espn_id": 9, "market": "Green Bay", "name": "Packers", "conference": "NFC", "division": "North", "primary_color": "#203731", "secondary_color": "#FFB612"},
    {"id": "MIN", "espn_id": 16, "market": "Minnesota", "name": "Vikings", "conference": "NFC", "division": "North", "primary_color": "#4F2683", "secondary_color": "#FFC62F"},
    # NFC South
    {"id": "ATL", "espn_id": 1, "market": "Atlanta", "name": "Falcons", "conference": "NFC", "division": "South", "primary_color": "#A71930", "secondary_color": "#000000"},
    {"id": "CAR", "espn_id": 29, "market": "Carolina", "name": "Panthers", "conference": "NFC", "division": "South", "primary_color": "#0085CA", "secondary_color": "#101820"},
    {"id": "NO", "espn_id": 18, "market": "New Orleans", "name": "Saints", "conference": "NFC", "division": "South", "primary_color": "#D3BC8D", "secondary_color": "#101820"},
    {"id": "TB", "espn_id": 27, "market": "Tampa Bay", "name": "Buccaneers", "conference": "NFC", "division": "South", "primary_color": "#D50A0A", "secondary_color": "#34302B"},
    # NFC West
    {"id": "ARI", "espn_id": 22, "market": "Arizona", "name": "Cardinals", "conference": "NFC", "division": "West", "primary_color": "#97233F", "secondary_color": "#000000"},
    {"id": "LAR", "espn_id": 14, "market": "Los Angeles", "name": "Rams", "conference": "NFC", "division": "West", "primary_color": "#003594", "secondary_color": "#FFA300"},
    {"id": "SF", "espn_id": 25, "market": "San Francisco", "name": "49ers", "conference": "NFC", "division": "West", "primary_color": "#AA0000", "secondary_color": "#B3995D"},
    {"id": "SEA", "espn_id": 26, "market": "Seattle", "name": "Seahawks", "conference": "NFC", "division": "West", "primary_color": "#002244", "secondary_color": "#69BE28"},
]


def logo_url(team_id: str) -> str:
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{team_id.lower()}.png"
