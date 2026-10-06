"""Public pages. Reads require no login — that is the point.

A player who never signs in can still follow a league, see the standings, and
look up the scoring rules. Login exists to *act*, not to read.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import draft as domain_draft
from app.domain.constants import rules_markdown
from app.models import Driver, Event, League, Player, Roster, Score, Team
from app.web.deps import get_db, optional_player
from app.web.view import league_context, standings_for

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def landing(
    request: Request,
    db: AsyncSession = Depends(get_db),
    player: Player | None = Depends(optional_player),
):
    leagues = (
        await db.execute(
            select(
                League,
                func.count(func.distinct(Team.id)).label("team_count"),
                func.count(func.distinct(Event.id)).label("scored"),
            )
            .outerjoin(Team, Team.league_id == League.id)
            .outerjoin(Event, Event.league_id == League.id)
            .where(League.state != "created")
            .group_by(League.id)
            .order_by(League.created_at.desc())
            .limit(6)
        )
    ).all()

    total = len((await db.scalars(select(League.id))).all())

    return request.app.state.templates.TemplateResponse(
        request,
        "landing.html",
        {
            "leagues": [
                {
                    "code": lg.code,
                    "name": lg.name,
                    "season_year": lg.season_year,
                    "state": lg.state,
                    "team_count": tc,
                    "scored": sc,
                }
                for lg, tc, sc in leagues
            ],
            "leagues_total": total,
            "player": player,
        },
    )


@router.get("/leagues", response_class=HTMLResponse)
async def leagues(request: Request, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(League, func.count(func.distinct(Team.id)))
            .outerjoin(Team, Team.league_id == League.id)
            .where(League.state != "created")
            .group_by(League.id)
            .order_by(League.created_at.desc())
        )
    ).all()

    return request.app.state.templates.TemplateResponse(
        request,
        "leagues.html",
        {
            "leagues": [
                {
                    "code": lg.code,
                    "name": lg.name,
                    "season_year": lg.season_year,
                    "state": lg.state,
                    "team_count": c,
                }
                for lg, c in rows
            ]
        },
    )


@router.get("/l/{code}", response_class=HTMLResponse)
async def standings(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    player: Player | None = Depends(optional_player),
):
    """Season leaderboard. Public — this is the page players screenshot."""
    league = await db.scalar(select(League).where(League.code == code))
    if league is None:
        return _not_found(request, f"No league called {code!r}")

    standings, my_team = await standings_for(db, league, player)
    teams = await db.scalars(
        select(Team).where(Team.league_id == league.id).order_by(Team.display_name)
    )
    events = (
        await db.scalars(
            select(Event)
            .where(Event.league_id == league.id, Event.kind == "race")
            .order_by(Event.round.desc())
        )
    ).all()

    return request.app.state.templates.TemplateResponse(
        request,
        "standings.html",
        {
            "league": league,
            "standings": standings,
            "my_team": my_team,
            "teams": teams.all(),
            "events": events,
            "player": player,
            **await league_context(db, league, player),
        },
    )


@router.get("/l/{code}/team/{team_id}", response_class=HTMLResponse)
async def team_page(
    code: str,
    team_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    player: Player | None = Depends(optional_player),
):
    from uuid import UUID

    league = await db.scalar(select(League).where(League.code == code))
    if league is None:
        return _not_found(request, f"No league called {code!r}")

    try:
        tid = UUID(team_id)
    except ValueError:
        return _not_found(request, "That is not a valid team id")

    team = await db.scalar(select(Team).where(Team.id == tid, Team.league_id == league.id))
    if team is None:
        return _not_found(request, "No such team in this league")

    rows = (
        await db.execute(
            select(Driver, func.coalesce(func.sum(Score.points), 0))
            .join(Roster, Roster.driver_id == Driver.id)
            .outerjoin(
                Score,
                (Score.driver_id == Driver.id) & (Score.team_id == team.id),
            )
            .where(Roster.team_id == team.id)
            .group_by(Driver.id)
            .order_by(func.coalesce(func.sum(Score.points), 0).desc())
        )
    ).all()

    total = float(sum(float(p or 0) for _, p in rows))

    return request.app.state.templates.TemplateResponse(
        request,
        "team.html",
        {
            "league": league,
            "team": team,
            "drivers": [
                {
                    "name": d.name,
                    "code": d.code,
                    "constructor": d.constructor,
                    "points": float(p or 0),
                }
                for d, p in rows
            ],
            "total": total,
            "player": player,
            **await league_context(db, league, player),
        },
    )


@router.get("/l/{code}/event/{event_id}", response_class=HTMLResponse)
async def event_page(
    code: str,
    event_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    player: Player | None = Depends(optional_player),
):
    from uuid import UUID

    league = await db.scalar(select(League).where(League.code == code))
    if league is None:
        return _not_found(request, f"No league called {code!r}")

    try:
        eid = UUID(event_id)
    except ValueError:
        return _not_found(request, "That is not a valid event id")

    event = await db.scalar(select(Event).where(Event.id == eid, Event.league_id == league.id))
    if event is None:
        return _not_found(request, "No such race in this league")

    rows = (
        await db.execute(
            select(Team.display_name, Driver.name, Driver.constructor, Score.points)
            .join(Score, Score.team_id == Team.id)
            .join(Driver, Driver.id == Score.driver_id)
            .where(Score.event_id == event.id)
            .order_by(Team.display_name, Score.points.desc())
        )
    ).all()

    by_team: dict[str, list] = {}
    for team_name, driver, ctor, points in rows:
        by_team.setdefault(team_name, []).append(
            {"driver": driver, "constructor": ctor, "points": float(points or 0)}
        )

    totals = sorted(
        ((name, sum(d["points"] for d in ds)) for name, ds in by_team.items()),
        key=lambda kv: kv[1],
        reverse=True,
    )

    return request.app.state.templates.TemplateResponse(
        request,
        "event.html",
        {
            "league": league,
            "event": event,
            "by_team": by_team,
            "totals": totals,
            "player": player,
            **await league_context(db, league, player),
        },
    )


@router.get("/rules", response_class=HTMLResponse)
async def rules(request: Request):
    return request.app.state.templates.TemplateResponse(
        request,
        "rules.html",
        {"rules": rules_markdown(), "max_team_size": domain_draft.MAX_TEAM_SIZE},
    )


def _not_found(request: Request, message: str):
    return request.app.state.templates.TemplateResponse(
        request, "error.html", {"code": 404, "message": message}, status_code=404
    )
