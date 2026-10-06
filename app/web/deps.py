"""Web dependencies: sessions, current player, access guards.

Reads never require a login. Login exists to *act* — draft, join, administer —
not to look at standings.
"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import League, Player
from app.web.session import Session


class NeedsLoginError(Exception):
    """Raised when an action requires a signed-in player."""


class NeedsAdminError(Exception):
    """Raised when a non-admin tries to mutate a league."""


class NotFoundError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


async def get_db(request: Request) -> AsyncSession:
    """The request's session.

    Commit and rollback belong to the middleware, which brackets the whole
    request. Nothing here commits — a route that flushed and returned must be
    able to rely on that flush being durable.
    """
    return request.state.db


def session_of(request: Request) -> Session:
    return request.state.session


async def optional_player(request: Request) -> Player | None:
    """The signed-in player, or None. Every read path uses this."""
    session: Session = request.state.session
    if session.player_id is None:
        return None
    return await request.state.db.get(Player, session.player_id)


async def require_player(request: Request) -> Player:
    player = await optional_player(request)
    if player is None:
        raise NeedsLoginError()
    return player


async def load_league(request: Request, code: str) -> League:
    """Resolve a league code to its row, or raise NotFound."""
    league = await request.state.db.scalar(select(League).where(League.code == code))
    if league is None:
        raise NotFoundError(f"No league called {code!r}")
    return league


async def require_admin(request: Request, code: str) -> Player:
    """Every mutating route goes through this.

    The check is on the league's admin player id, not on Discord roles, so a
    self-hosted league with no Discord at all is still correctly gated.
    """
    player = await require_player(request)
    league = await load_league(request, code)
    if league.admin_player_id != player.id:
        raise NeedsAdminError()
    return player


# Convenience aliases for route signatures.
DbSession = Annotated = Depends(get_db)
