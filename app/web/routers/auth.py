"""Sign-in and league creation.

Two identity paths on purpose:

* **Guest** — a display name and a session cookie. No external account needed.
  This is what makes a league with no Discord integration work at all.
* **Discord** — OAuth with the ``identify`` scope, which yields a numeric user id
  so announcements can actually mention someone. Optional.

Both produce the same ``player`` row, so nothing downstream branches on which was
used.
"""

from __future__ import annotations

import hmac
import random
import re
import secrets
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import League, Team
from app.provider.base import ProviderError
from app.repo.postgres import LeagueRepo
from app.services.scoring import ensure_season_seeded
from app.web.deps import NotFoundError, get_db, optional_player, require_player
from app.web.rejoin import issue_rejoin, rejoin_path, resolve_token, sign_in
from app.web.view import league_context

router = APIRouter()

# Reserved names that would collide in a URL.
_RESERVED = {
    "admin",
    "api",
    "static",
    "l",
    "leagues",
    "login",
    "logout",
    "rules",
    "signup",
    "pricing",
    "me",
    "health",
    "draft",
}

_SLUG = re.compile(r"[^a-z0-9-]+")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/me"):
    """Sign-in page. Kept next to the POST so the two cannot drift."""
    return request.app.state.templates.TemplateResponse(
        request,
        "login.html",
        {
            "player": await optional_player(request),
            "next": next,
            "error": None,
            "csrf_token": request.state.session.csrf_token,
        },
    )


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, db: AsyncSession = Depends(get_db)):
    player = await optional_player(request)
    return request.app.state.templates.TemplateResponse(
        request,
        "signup.html",
        {
            "player": player,
            "seasons": _seasons(),
            "error": None,
            "form": {},
            "csrf_token": request.state.session.csrf_token,
        },
    )


@router.post("/signup")
async def create_league(
    request: Request,
    name: str = Form(...),
    season: int = Form(...),
    team_size: str = Form(""),
    pick_deadline: int = Form(600),
    display_name: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Create a league. The creator becomes its admin and first player."""
    player = await optional_player(request)
    if player is None:
        display = (display_name or name).strip()[:80]
        if not display:
            return _error(request, "Enter a display name so players know who you are.")
        # Always a guest here, never a Discord identity.
        #
        # This route used to accept a discord_id form field and hand it straight
        # to upsert_player, which returns the *existing* player for a matching
        # (provider, external_id). Anyone could therefore POST a victim's
        # snowflake and be signed in as them, inheriting their leagues and any
        # league they administer. Discord identity comes only from the verified
        # OAuth callback.
        player = await LeagueRepo(db).upsert_player(
            provider="guest",
            external_id=None,
            display_name=display,
        )

    name = name.strip()[:120]
    if not name:
        return _error(request, "Give the league a name.")

    # The draft needs a driver grid, and the admin panel shows a computed team
    # size. Seed it now rather than discovering an empty pool at draft start.
    #
    # Upstream may simply not have the season yet — a new year has no published
    # drivers — or be down. Neither is the player's fault, and an unhandled
    # ProviderError here meant every such signup was a 500.
    try:
        await ensure_season_seeded(db, int(season))
    except ProviderError as exc:
        return _error(
            request,
            f"That season isn't available yet. {exc} Try a season that has "
            "started, or create the league and add drivers later.",
            f"/signup?season={int(season)}&name={name}",
        )

    code = await _unique_code(db, name)
    repo = LeagueRepo(db)
    league = await repo.create_league(
        code=code,
        name=name,
        season_year=int(season),
        admin=player,
        pick_deadline=max(30, int(pick_deadline)),
        team_size=int(team_size) if team_size.strip().isdigit() else None,
    )
    await repo.join(league.id, player)
    await repo.set_state(league.id, "created")

    # The creator is the admin, so the session must become theirs. Without this
    # the redirect lands on an admin page that rejects them as anonymous.
    session = request.state.session
    session.player_id = player.id
    session.changed = True

    return RedirectResponse(f"/l/{league.code}/admin", status_code=303)


@router.post("/login")
async def login(
    request: Request,
    display_name: str = Form(...),
    next: str = Form("/me"),
    db: AsyncSession = Depends(get_db),
):
    """Sign in with a display name. No password — this is a friends' league."""
    display = display_name.strip()[:80]
    if not display:
        return _error(request, "Enter a name.", "/login")

    player = await LeagueRepo(db).upsert_player(
        provider="guest", external_id=None, display_name=display
    )

    session = request.state.session
    session.player_id = player.id
    session.changed = True

    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
async def logout(request: Request):
    request.state.session.player_id = None
    request.state.session.changed = True
    return RedirectResponse("/", status_code=303)


@router.get("/auth/discord")
async def discord_start(request: Request):
    """Kick off Discord OAuth. Only meaningful when a client id is configured."""
    settings = request.app.state.settings
    if not settings.discord_client_id:
        return RedirectResponse("/login?discord=unavailable", status_code=303)

    state = secrets.token_urlsafe(16)
    request.state.session.oauth_state = state
    request.state.session.changed = True

    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.discord_redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent",
    }
    return RedirectResponse(f"https://discord.com/oauth2/authorize?{urlencode(params)}")


@router.get("/auth/discord/callback")
async def discord_callback(
    request: Request,
    code: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Exchange the OAuth code for an identity and sign in as that player."""
    import httpx

    settings = request.app.state.settings
    session = request.state.session

    # Constant-time: the comparison is against a secret the browser was just
    # handed, and a mismatch means a replayed or forged callback.
    expected = session.oauth_state
    if not code or not expected or not hmac.compare_digest(state, expected):
        return _error(request, "That sign-in link expired. Try again.", "/login")

    # Single use. Without this, one captured callback URL could be replayed to
    # keep signing in as whoever it names.
    session.oauth_state = None
    session.changed = True

    async with httpx.AsyncClient(timeout=15.0) as client:
        token_resp = await client.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
        )
        if token_resp.status_code != 200:
            return _error(request, "Discord refused the sign-in. Try again.", "/login")

        access = token_resp.json().get("access_token", "")
        me_resp = await client.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {access}"},
        )
        if me_resp.status_code != 200:
            return _error(request, "Could not read your Discord profile.", "/login")

    profile = me_resp.json()
    player = await LeagueRepo(db).upsert_player(
        provider="discord",
        external_id=str(profile["id"]),
        display_name=(profile.get("global_name") or profile.get("username", "player"))[:80],
    )
    session.player_id = player.id
    session.changed = True

    return RedirectResponse("/me", status_code=303)


@router.get("/rejoin/{token}")
async def rejoin(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Sign back in from a guest's magic link.

    This is the only way a guest can return to their team. Signing in by display
    name would just mint a new player and abandon the old one.
    """
    player = await resolve_token(db, token)
    if player is None:
        return _error(request, "That link is not valid any more.", "/login")

    sign_in(request.state.session, player)
    return RedirectResponse("/me", status_code=303)


@router.get("/me", response_class=HTMLResponse)
async def my_leagues(request: Request, db: AsyncSession = Depends(get_db)):
    """Every league this player belongs to, in one place."""
    player = await require_player(request)

    # scalars(), not execute(): an entity select still comes back wrapped in Row
    # objects unless you unwrap it, and the template then has no .state.
    leagues = (
        (
            await db.scalars(
                select(League)
                .where(League.id.in_(select(Team.league_id).where(Team.player_id == player.id)))
                .order_by(League.created_at.desc())
            )
        )
        .unique()
        .all()
    )

    return request.app.state.templates.TemplateResponse(
        request,
        "me.html",
        {"player": player, "leagues": leagues, "csrf_token": request.state.session.csrf_token},
    )


@router.get("/join/{code}", response_class=HTMLResponse)
async def join_page(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    player = await require_player(request)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    if league is None:
        raise NotFoundError(f"No league called {code!r}")

    return request.app.state.templates.TemplateResponse(
        request,
        "join.html",
        {
            "league": league,
            "player": player,
            "csrf_token": request.state.session.csrf_token,
            "already": await _already_joined(db, league.id, player.id),
            "state": league.state,
            **await league_context(db, league, player, csrf_token=request.state.session.csrf_token),
        },
    )


@router.post("/join/{code}")
async def do_join(
    code: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Claim a spot. Idempotent — joining twice is a no-op, not an error."""
    player = await require_player(request)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    if league is None:
        raise NotFoundError(f"No league called {code!r}")

    # Enforced here, not just by hiding the button. A team added mid-draft has no
    # slot in the pick order, so the round and pick counters the hub shows go
    # wrong and the new team can never pick. Joining an archived league is
    # equally meaningless.
    if league.state in ("drafting", "active", "archived"):
        return RedirectResponse(f"/l/{league.code}?error=signup-closed", status_code=303)

    added = await repo.join(league.id, player)
    if added is not None:
        count = await db.scalar(select(func.count(Team.id)).where(Team.league_id == league.id))
        await repo.notifier_for(league).signup_updated(repo.context(league), count or 0, 0)
        if league.state == "created":
            await repo.set_state(league.id, "signup_open")

        # Guests get a private way back in. Without one, a guest who clears their
        # cookie has lost their team: signing in by name mints a *new* player and
        # the old team is stranded. Scoped to this league, so joining a second one
        # leaves the first link working.
        reissued = await issue_rejoin(db, player, league.id)
        if reissued is not None:
            return RedirectResponse(
                f"/welcome/{league.code}?token={reissued.token}", status_code=303
            )

    return RedirectResponse(f"/l/{league.code}", status_code=303)


@router.get("/welcome/{code}", response_class=HTMLResponse)
async def welcome(code: str, request: Request, token: str = "", db: AsyncSession = Depends(get_db)):
    """Shown once, right after a guest joins. The link is the receipt."""
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    if league is None:
        raise NotFoundError(f"No league called {code!r}")

    player = await optional_player(request)
    return request.app.state.templates.TemplateResponse(
        request,
        "welcome.html",
        {
            "league": league,
            "player": player,
            "rejoin_url": (
                f"{request.app.state.settings.app_url}{rejoin_path(token)}" if token else ""
            ),
            **await league_context(db, league, player, csrf_token=request.state.session.csrf_token),
        },
    )


async def _already_joined(db: AsyncSession, league_id, player_id) -> bool:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Team)
            .where(Team.league_id == league_id, Team.player_id == player_id)
        )
    ) > 0


async def _unique_code(db: AsyncSession, name: str) -> str:
    """Short, readable, collision-checked league code."""
    base = _SLUG.sub("-", name.lower()).strip("-")[:20] or "league"
    base = "-".join(filter(None, base.split("-")[:3]))

    for attempt in range(50):
        candidate = base if attempt == 0 else f"{base}-{random.randint(100, 999)}"
        if candidate in _RESERVED:
            continue
        exists = await db.scalar(select(League.id).where(League.code == candidate))
        if exists is None:
            return candidate
    return f"league-{secrets.token_hex(3)}"


def _safe_next(target: str) -> str:
    """Only ever redirect within this site."""
    if not target.startswith("/") or target.startswith("//"):
        return "/me"
    return target


def _seasons() -> list[int]:
    year = datetime.now(UTC).year
    return [year, year + 1]


def _error(request: Request, message: str, path: str = "/signup"):
    return request.app.state.templates.TemplateResponse(
        request,
        "error.html",
        {"code": 400, "message": message, "back": path},
        status_code=400,
    )
