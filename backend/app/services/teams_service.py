"""Team CRUD + seeding from static data + ESPN enrichment."""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models.seed import NFL_TEAMS, logo_url
from ..models.team import Team


def list_teams(db: Session) -> list[Team]:
    return db.query(Team).order_by(Team.conference, Team.division, Team.name).all()


def get_team(db: Session, team_id: str) -> Team | None:
    return db.query(Team).filter(Team.id == team_id.upper()).first()


def teams_by_division(db: Session, conference: str, division: str) -> list[Team]:
    return (
        db.query(Team)
        .filter(Team.conference == conference, Team.division == division)
        .order_by(Team.name)
        .all()
    )


def ensure_seeded(db: Session) -> int:
    """Idempotent — inserts any missing teams. Returns rows added."""
    existing = {t.id for t in db.query(Team.id).all()}
    added = 0
    for row in NFL_TEAMS:
        if row["id"] in existing:
            continue
        team = Team(
            id=row["id"],
            espn_id=row.get("espn_id"),
            market=row.get("market", ""),
            name=row["name"],
            full_name=f"{row.get('market','')} {row['name']}".strip(),
            conference=row.get("conference", ""),
            division=row.get("division", ""),
            primary_color=row.get("primary_color", "#111827"),
            secondary_color=row.get("secondary_color", "#9ca3af"),
            logo_url=logo_url(row["id"]),
        )
        db.add(team)
        added += 1
    if added:
        db.commit()
    return added
