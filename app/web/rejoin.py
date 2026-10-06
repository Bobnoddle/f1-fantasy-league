"""Guest identity: magic links.

Guests have no external account, so a display name is not an identity — anyone
can claim anyone else's name. The honest fix is a bearer link: on joining, the
player is handed a private URL, and that URL is the only way back in.

The token is stored hashed. It is a credential, so it must never be logged, and a
database leak must not hand over every guest's account.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Player
from app.web.session import Session

TOKEN_BYTES = 24


def generate_token() -> str:
    """A URL-safe bearer token. Shown once, never recoverable after that."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 hex. A guest token is high-entropy, so a plain hash is fine —
    there is nothing to brute-force and no password to slow down."""
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Rejoin:
    player: Player
    token: str  # only populated when it was just issued


async def issue_rejoin(session: AsyncSession, player: Player) -> Rejoin | None:
    """Mint a fresh magic link for a guest.

    Re-issuing rotates the token, which signs the previous link out. That is the
    behaviour we want if someone shares a screen or loses a device.
    """
    if player.provider != "guest":
        return None

    token = generate_token()
    player.rejoin_hash = hash_token(token)
    await session.flush()
    return Rejoin(player=player, token=token)


async def resolve_token(session: AsyncSession, token: str) -> Player | None:
    """Look a token up by hash. Returns None for anything unrecognised."""
    if not token or len(token) < 16:
        return None

    candidate = hash_token(token)
    return await session.scalar(
        select(Player).where(Player.provider == "guest", Player.rejoin_hash == candidate)
    )


def sign_in(session: Session, player: Player) -> None:
    session.player_id = player.id
    session.changed = True


def rejoin_path(token: str) -> str:
    return f"/rejoin/{token}"
