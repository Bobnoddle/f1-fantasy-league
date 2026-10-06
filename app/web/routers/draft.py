"""Draft pages: the hub and the picker.

Two interaction shapes, and they are deliberately different:

* **The hub** is select-and-preview. It answers one question — is it my turn? —
  and shows the board without the player having to commit to anything.
* **The picker** is select-and-close. One tap takes the driver and that's it;
  the page then says so explicitly, because waiting for something that isn't
  coming is the most common way a draft gets abandoned.

Both pages call ``advance_if_expired`` on load. That single call is why there is
no timer anywhere in this codebase: the database holds the deadline and any
request can notice it has passed.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.draft import AvailableView, DraftStatus
from app.models import Driver, League, Roster, Team
from app.repo.postgres import LeagueRepo, PostgresDraftRepo
from app.services.draft import DraftService
from app.web.deps import NotFoundError, get_db, optional_player, require_player
from app.web.view import draft_state, league_context, remaining_seconds, standings_for

router = APIRouter()


@router.get("/l/{code}/draft", response_class=HTMLResponse)
async def draft_hub(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Where every player goes during a draft. Public — spectators welcome."""
    player = await optional_player(request)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    if league is None:
        raise NotFoundError(f"No league called {code!r}")

    draft = await draft_state(db, league)
    teams = (
        await db.scalars(
            select(Team).where(Team.league_id == league.id).order_by(Team.display_name)
        )
    ).all()

    my_team = None
    if player is not None:
        my_team = await db.scalar(
            select(Team).where(Team.league_id == league.id, Team.player_id == player.id)
        )

    # The one call that makes the whole timeout design work.
    service, drepo = _service(db, league, repo)
    rolled = await service.advance_if_expired(
        league.id,
        await _grid_size(db, league),
        league.pick_deadline,
        display_names={t.id: t.display_name for t in teams},
    )
    if rolled is not None:
        draft = await draft_state(db, league)
        teams = (
            await db.scalars(
                select(Team).where(Team.league_id == league.id).order_by(Team.display_name)
            )
        ).all()

    state = await drepo.get_state(league.id)
    standings, _ = await standings_for(db, league, player)

    rosters = await repo.rosters(league.id)

    return request.app.state.templates.TemplateResponse(
        request,
        "draft.html",
        {
            "league": league,
            "state": state,
            "draft": draft,
            "teams": teams,
            "my_team": my_team,
            "standings": standings,
            "rosters": rosters,
            "rolled": rolled,
            "player": player,
            "countdown_seconds": remaining_seconds(draft),
            "csrf_token": request.state.session.csrf_token,
            **await league_context(db, league, player),
        },
    )


@router.get("/l/{code}/draft/pick", response_class=HTMLResponse)
async def pick_page(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The driver grid. Reachable only on your turn."""
    player = await require_player(request)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    if league is None:
        raise NotFoundError(f"No league called {code!r}")

    league = await repo.get(league.id)
    service, drepo = _service(db, league, repo)
    names = {
        t.id: t.display_name
        for t in (await db.scalars(select(Team).where(Team.league_id == league.id))).all()
    }

    # Settle any expired pick before showing the board, so a player who arrives
    # late does not see a stale "your turn".
    await service.advance_if_expired(
        league.id, await _grid_size(db, league), league.pick_deadline, display_names=names
    )

    state = await drepo.get_state(league.id)
    draft = await draft_state(db, league)
    my_team = await db.scalar(
        select(Team).where(Team.league_id == league.id, Team.player_id == player.id)
    )

    if state is None or state.status is not DraftStatus.DRAFTING:
        return RedirectResponse(f"/l/{league.code}/draft", status_code=303)

    if my_team is None:
        return RedirectResponse(f"/l/{league.code}/draft?error=not-in-league", status_code=303)
    if state.on_the_clock != my_team.id:
        return RedirectResponse(f"/l/{league.code}/draft?error=not-your-turn", status_code=303)

    available = await drepo.get_available_drivers(league.id)
    view = AvailableView.build([(did, name, ctor) for did, name, ctor in available], taken=set())

    mine = [
        {"name": d.name, "code": d.code}
        for d in (
            await db.scalars(
                select(Driver)
                .join(Roster, Roster.driver_id == Driver.id)
                .where(Roster.team_id == my_team.id)
                .order_by(Roster.pick_number)
            )
        ).all()
    ]

    return request.app.state.templates.TemplateResponse(
        request,
        "pick.html",
        {
            "league": league,
            "state": state,
            "view": view,
            "my_team": my_team,
            "mine": mine,
            "player": player,
            "countdown_seconds": remaining_seconds(draft),
            "csrf_token": request.state.session.csrf_token,
            **await league_context(db, league, player),
        },
    )


@router.post("/l/{code}/draft/pick")
async def make_pick(
    code: str,
    request: Request,
    driver_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Take a driver. One tap, no confirmation — the tap is the commitment."""
    player = await require_player(request)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    if league is None:
        raise NotFoundError(f"No league called {code!r}")

    league = await repo.get(league.id)
    service, drepo = _service(db, league, repo)
    names = {
        t.id: t.display_name
        for t in (await db.scalars(select(Team).where(Team.league_id == league.id))).all()
    }

    my_team = await db.scalar(
        select(Team).where(Team.league_id == league.id, Team.player_id == player.id)
    )
    if my_team is None:
        return RedirectResponse(f"/l/{league.code}/draft", status_code=303)

    try:
        did = UUID(driver_id)
    except ValueError:
        return RedirectResponse(f"/l/{league.code}/draft?error=bad-driver", status_code=303)

    try:
        await service.pick(league.id, my_team.id, did, league.pick_deadline, display_names=names)
        await db.commit()
    except ValueError as exc:
        return RedirectResponse(f"/l/{league.code}/draft?error={_slug(exc)}", status_code=303)
    except IntegrityError:
        # The roster primary key caught a double pick. Nothing to repair — the
        # other transaction won and this player simply picks again.
        await db.rollback()
        return RedirectResponse(f"/l/{league.code}/draft?error=just-taken", status_code=303)

    return RedirectResponse(f"/l/{league.code}/draft?picked=1", status_code=303)


def _service(db, league, repo):
    drepo = PostgresDraftRepo(db)
    return DraftService(drepo, repo.notifier_for(league)), drepo


async def _grid_size(db: AsyncSession, league: League) -> int:
    return (
        await db.scalar(
            select(func.count(Driver.id)).where(Driver.season_year == league.season_year)
        )
        or 22
    )


def _slug(exc: ValueError) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(exc).lower()).strip("-")[:60]
