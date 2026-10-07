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

    def verify_csrf(self, supplied: str | None) -> bool:
        """Constant-time comparison — a CSRF token is still a secret.

        Lives on Session rather than the codec because it compares against the
        session's own token; as a codec staticmethod it was awkward to call and
        easy to reach for wrongly.
        """
        if not supplied:
            return False
        return hmac.compare_digest(self.csrf_token, supplied)


class SessionCodec:
    def __init__(self, secret: str) -> None:
        self._serializer = URLSafeSerializer(secret, salt="f1-fantasy-session")

    def encode(self, session: Session) -> str:
        return self._serializer.dumps(
            {
                "pid": str(session.player_id) if session.player_id else None,
                "csrf": session.csrf_token,
                # Carried in the cookie so it survives the redirect to Discord.
                # It used to be dropped here, which made the callback's state
                # check always fail — every Discord sign-in ended at "that link
                # expired", and once it was fixed this becomes the CSRF guard on
                # the OAuth flow.
                "oauth": session.oauth_state,
            }
        )

    def decode(self, raw: str | None) -> Session:
        """Never raises. A bad or missing cookie becomes an anonymous session."""
        if not raw:
            # No cookie at all, so this visitor has no CSRF token they can
            # present. Minting one and marking the session changed makes the
            # middleware issue a cookie, which is what lets anonymous forms —
            # /login and /signup — be protected at all. Without it their token
            # would differ on every request and every anonymous POST would fail.
            session = Session()
            session.changed = True
            return session
        try:
            data = self._serializer.loads(raw, max_age=MAX_AGE)
        except BadSignature:
            session = Session()
            session.changed = True
            return session

        raw_id = data.get("pid")
        try:
            player_id = UUID(raw_id) if raw_id else None
        except (ValueError, TypeError):
            player_id = None

        token = data.get("csrf") or secrets.token_urlsafe(24)
        oauth_state = data.get("oauth") or None
        return Session(
            player_id=player_id,
            csrf_token=token,
            oauth_state=oauth_state,
            # A cookie minted before this field existed carries no token, so
            # mint one and ask for the cookie to be reissued.
            changed=not data.get("csrf"),
        )
