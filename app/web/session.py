"""Signed cookie sessions.

The cookie holds only a player id plus a CSRF token. It is signed with
``SESSION_SECRET`` so it cannot be forged, and nothing about league state lives
in it — a league always reads its current state from the database, never from
whatever the browser happened to be holding.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field
from uuid import UUID

from itsdangerous import BadSignature, URLSafeSerializer

COOKIE = "f1_session"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"

#: A week. Long enough that nobody re-logs-in mid-draft, short enough that a
#: shared machine does not stay signed in forever.
MAX_AGE = 7 * 24 * 3600


@dataclass(slots=True)
class Session:
    player_id: UUID | None = None
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))

    #: Set when the session is mutated, so the middleware re-issues the cookie.
    changed: bool = False
    oauth_state: str | None = None

    @property
    def signed_in(self) -> bool:
        return self.player_id is not None


class SessionCodec:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeSerializer(secret, salt="f1-fantasy-session")

    def encode(self, session: Session) -> str:
        return self._serializer.dumps(
            {
                "pid": str(session.player_id) if session.player_id else None,
                "csrf": session.csrf_token,
            }
        )

    def decode(self, raw: str | None) -> Session:
        """Never raises. A bad or missing cookie becomes an anonymous session."""
        if not raw:
            return Session()
        try:
            data = self._serializer.loads(raw, max_age=MAX_AGE)
        except BadSignature:
            return Session()

        raw_id = data.get("pid")
        try:
            player_id = UUID(raw_id) if raw_id else None
        except (ValueError, TypeError):
            player_id = None

        token = data.get("csrf") or secrets.token_urlsafe(24)
        return Session(player_id=player_id, csrf_token=token)

    @staticmethod
    def verify_csrf(session: Session, supplied: str | None) -> bool:
        """Compare in constant time — a CSRF token is still a secret."""
        if not supplied:
            return False
        return hmac.compare_digest(session.csrf_token, supplied)
