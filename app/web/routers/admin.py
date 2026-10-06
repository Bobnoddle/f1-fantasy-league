"""Admin panel: configuration plus the lifecycle buttons.

Every state transition the old slash commands performed lives here as a plain
button. Actions are idempotent — a double submit must not double-post — and each
one announces to the channel on success.
"""

from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.draft import team_size as calc_team_size
from app.models import (
    Draft,
    Driver,
    Event,
    League,
    Result,
    Roster,
    Score,
    Team,
)
from app.repo.postgres import LeagueRepo, PostgresDraftRepo
from app.services.draft import DraftService
from app.web.deps import get_db, require_admin
from app.web.view import draft_state, league_context, remaining_seconds, standings_for

router = APIRouter()


@router.get("/l/{code}/admin", response_class=HTMLResponse)
async def admin_page(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    player = await require_admin(request, code)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)

    db.info["csrf_token"] = request.state.session.csrf_token

    teams = (
        await db.scalars(select(Team).where(Team.league_id == league.id).order_by(Team.joined_at))
    ).all()
    roster_counts = dict(
        (
            await db.execute(
                select(Roster.team_id, func.count(Roster.driver_id))
                .where(Roster.league_id == league.id)
                .group_by(Roster.team_id)
            )
        ).all()
    )

    standings, _ = await standings_for(db, league, player)
    draft = await draft_state(db, league)
    scored = await db.scalar(select(func.count(Event.id)).where(Event.league_id == league.id))

    return request.app.state.templates.TemplateResponse(
        request,
        "admin.html",
        {
            "league": league,
            "teams": teams,
            "roster_counts": roster_counts,
            "standings": standings,
            "draft": draft,
            "scored": scored or 0,
            "seasons": _seasons(league.season_year),
            "countdown_seconds": remaining_seconds(draft),
            "player": player,
            "csrf_token": request.state.session.csrf_token,
            **await league_context(db, league, player),
        },
    )


@router.post("/l/{code}/admin/settings")
async def save_settings(
    code: str,
    request: Request,
    name: str = Form(...),
    season_year: int = Form(...),
    pick_deadline: int = Form(600),
    team_size: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, code)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)

    league.name = name.strip()[:120] or league.name
    league.season_year = int(season_year)
    league.pick_deadline = max(30, int(pick_deadline))
    league.team_size = int(team_size) if team_size.strip().isdigit() else None
    await db.flush()

    return RedirectResponse(f"/l/{league.code}/admin?saved=settings", status_code=303)


@router.post("/l/{code}/admin/webhook")
async def save_webhook(
    code: str,
    request: Request,
    webhook_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Store or clear the Discord webhook.

    Only the two id/token segments are kept — a pasted URL is a credential, and
    storing it whole means it turns up in logs.
    """
    await require_admin(request, code)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)

    url = webhook_url.strip()
    if not url:
        league.webhook_id = None
        league.webhook_token = None
        await db.flush()
        return RedirectResponse(f"/l/{league.code}/admin?saved=webhook-cleared", status_code=303)

    webhook_id, token = _parse_webhook(url)
    if not (webhook_id and token):
        return RedirectResponse(f"/l/{league.code}/admin?error=bad-webhook", status_code=303)

    league.webhook_id = webhook_id
    league.webhook_token = token
    await db.flush()

    return RedirectResponse(f"/l/{league.code}/admin?saved=webhook", status_code=303)


@router.post("/l/{code}/admin/test-webhook")
async def test_webhook(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Post and delete a test message, so a broken token shows up here."""
    await require_admin(request, code)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)

    notifier = repo.notifier_for(league)
    try:
        await notifier.test()
        status = "ok"
    except Exception as exc:  # noqa: BLE001 — surface any channel failure
        status = f"failed: {str(exc)[:120]}"

    return RedirectResponse(
        f"/l/{league.code}/admin?saved=test-{_slugify(status)}", status_code=303
    )


@router.post("/l/{code}/admin/action")
async def lifecycle_action(
    code: str,
    request: Request,
    action: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """The progress buttons. Each branch is idempotent."""
    await require_admin(request, code)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    notifier = repo.notifier_for(league)

    teams = await db.scalar(select(func.count(Team.id)).where(Team.league_id == league.id)) or 0
    grid = (
        await db.scalar(
            select(func.count(Driver.id)).where(Driver.season_year == league.season_year)
        )
        or 22
    )
    size = calc_team_size(teams, grid, league.team_size)

    if action == "open-signup":
        if league.state == "created":
            await repo.set_state(league.id, "signup_open")
        await notifier.signup_updated(repo.context(league), teams, size)

    elif action == "close-signup":
        if league.state in ("created", "signup_open"):
            await repo.set_state(league.id, "draft_ready")

    elif action == "start-draft":
        if teams < 2:
            return RedirectResponse(f"/l/{league.code}/admin?error=need-2-players", status_code=303)
        drepo = PostgresDraftRepo(db)
        service = DraftService(drepo, notifier)
        await repo.set_state(league.id, "drafting")
        try:
            state = await service.start(league.id, grid, league.team_size, league.pick_deadline)
            # The seed order lives in draft_state, but the admin panel and the
            # hub read team.draft_order. Persist it or that column stays blank
            # for the whole draft.
            await repo.set_draft_order(league.id, list(state.team_ids))
        except ValueError as exc:
            await db.rollback()
            return RedirectResponse(
                f"/l/{league.code}/admin?error={_slugify(str(exc))}", status_code=303
            )

    elif action == "archive":
        await _archive(db, league)

    elif action == "reset":
        await _reset(db, league)
        return RedirectResponse(f"/l/{league.code}/admin?saved=reset", status_code=303)

    elif action == "rescore":
        # Clear this league's scoring and let the next cron pass redo it.
        await db.execute(
            delete(Score).where(
                Score.event_id.in_(select(Event.id).where(Event.league_id == league.id))
            )
        )
        await db.execute(
            delete(Result).where(
                Result.event_id.in_(select(Event.id).where(Event.league_id == league.id))
            )
        )
        await db.execute(delete(Event).where(Event.league_id == league.id))
        return RedirectResponse(f"/l/{league.code}/admin?saved=rescore", status_code=303)

    else:
        return RedirectResponse(f"/l/{league.code}/admin?error=unknown-action", status_code=303)

    await db.flush()
    return RedirectResponse(f"/l/{league.code}/admin?saved={action}", status_code=303)


async def _archive(db: AsyncSession, league: League) -> None:
    """Snapshot the season, then clear race data but keep the league playable."""
    from app.models import SeasonArchive
    from app.web.view import standings_for

    standings, _ = await standings_for(db, league, None)
    champion = standings[0] if standings else None

    await db.execute(
        SeasonArchive.__table__.insert().values(
            league_id=league.id,
            season_year=league.season_year,
            champion_team_id=UUID(champion.team_id) if champion else None,
            final_standings=json.dumps(
                [
                    {"team": r.display_name, "points": r.points, "position": r.position}
                    for r in standings
                ]
            ),
        )
    )
    await _clear_season(db, league)
    await db.execute(
        League.__table__.update()
        .where(League.id == league.id)
        .values(state="archived", archived_at=func.now())
    )


async def _reset(db: AsyncSession, league: League) -> None:
    """Wipe the league entirely. Destructive and explicit."""
    await _clear_season(db, league)
    await db.execute(delete(Roster).where(Roster.league_id == league.id))
    await db.execute(delete(Team).where(Team.league_id == league.id))
    await db.execute(delete(Draft).where(Draft.league_id == league.id))
    await db.execute(Draft.__table__.insert().values(league_id=league.id, status="pending"))
    await db.execute(
        League.__table__.update()
        .where(League.id == league.id)
        .values(state="created", season_year=league.season_year + 1)
    )


async def _clear_season(db: AsyncSession, league: League) -> None:
    event_ids = select(Event.id).where(Event.league_id == league.id)
    await db.execute(delete(Score).where(Score.event_id.in_(event_ids)))
    await db.execute(delete(Result).where(Result.event_id.in_(event_ids)))
    await db.execute(delete(Event).where(Event.league_id == league.id))


def _parse_webhook(url: str) -> tuple[str | None, str | None]:
    """Accept either a full URL or a bare ``id/token`` pair."""
    tail = url.rstrip("/").split("/")
    if len(tail) >= 2:
        token, webhook_id = tail[-1], tail[-2]
        if webhook_id and token:
            return webhook_id, token
    return None, None


def _slugify(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:60]


def _seasons(current: int | None = None) -> list[int]:
    """Options for the season select.

    Always includes the league's current season. Without that, a league on a
    season that has rolled past shows a different year as selected, and an admin
    who saves settings changes the season by accident.
    """
    from datetime import UTC, datetime

    year = datetime.now(UTC).year
    options = {year, year + 1}
    if current is not None:
        options.add(current)
    return sorted(options)
