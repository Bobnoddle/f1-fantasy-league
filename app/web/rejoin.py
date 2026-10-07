"""Guest identity: magic links.

Guests have no external account, so a display name is not an identity — anyone
can claim anyone else's name. The honest fix is a bearer link: on joining, the
player is handed a private URL, and that URL is the only way back in.

One token per league, in ``rejoin_token``, not one per player. A guest can be in
several leagues and needs a way back into each; a single hash on ``player``
meant joining a second league permanently destroyed the first league's link.

Tokens are stored hashed, so a database leak does not hand over every guest's
account, and they expire, so a link sitting in browser history is not a
permanent credential.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Player, RejoinToken
from app.web.session import Session

TOKEN_BYTES = 24

#: Long enough that a guest who loses their link mid-season can get back in,
#: short enough that a leaked one is not a permanent account credential.
TOKEN_TTL = timedelta(days=180)


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
    league_id: UUID
    token: str  # only populated when it was just issued


async def issue_rejoin(session: AsyncSession, player: Player, league_id: UUID) -> Rejoin | None:
    """Mint a fresh magic link for a guest into one league.

    Re-issuing rotates that league's token, invalidating the previous link. That
    is the behaviour we want if someone shares a screen or loses a device, and
    it is scoped to this league so joining another one is unaffected.

    Returns None for Discord players, who can always sign back in through OAuth.
    """
    if player.provider != "guest":
        return None

    token = generate_token()
    now = datetime.now(UTC)
    digest = hash_token(token)

    # One statement, so two concurrent joins cannot both land an INSERT and
    # collide on the primary key.
    await session.execute(
        pg_insert(RejoinToken)
        .values(
            league_id=league_id,
            player_id=player.id,
            token_hash=digest,
            expires_at=now + TOKEN_TTL,
        )
        .on_conflict_do_update(
            index_elements=["league_id", "player_id"],
            set_={"token_hash": digest, "created_at": now, "expires_at": now + TOKEN_TTL},
        )
        .execution_options(synchronize_session=False)
    )

    return Rejoin(player=player, league_id=league_id, token=token)


async def resolve_token(session: AsyncSession, token: str) -> Player | None:
    """Look a token up by hash. Returns None for anything unrecognised.

    Expired tokens do not resolve. Redemption is recorded rather than consumed:
    a guest who clicks the link on a shared machine should still be able to use
    it on their own, and the expiry is what bounds the damage if it leaks.
    """
    if not token or len(token) < 16:
        return None

    candidate = hash_token(token)
    row = (
        (
            await session.execute(
                select(RejoinToken).where(RejoinToken.token_hash == candidate).limit(1)
            )
        )
        .scalars()
        .first()
    )

    if row is None:
        return None

    now = datetime.now(UTC)
    if row.expires_at is not None and row.expires_at <= now:
        return None

    if row.used_at is None:
        row.used_at = now
        await session.flush()

    return await session.get(Player, row.player_id)


def sign_in(session: Session, player: Player) -> None:
    session.player_id = player.id
    session.changed = True


def rejoin_path(token: str) -> str:
    return f"/rejoin/{token}"
