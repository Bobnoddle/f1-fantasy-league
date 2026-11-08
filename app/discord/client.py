"""Discord output. Inbound webhook only — post, edit, delete. Nothing else.

No gateway, no bot token, no intents. The channel is a read surface for
standings and a notification surface for draft turns; every action happens on
the web app.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any

import httpx

log = logging.getLogger(__name__)

API = "https://discord.com/api/v10/webhooks"

#: Webhooks share a per-channel rate limit. Draft pings burst, so sends are
#: spaced rather than fired in a tight loop.
_MIN_SEND_GAP = 1.1

#: One pool for the process, closed by the app lifespan.
#:
#: A notifier is built per request, so constructing one httpx.AsyncClient per
#: notifier meant a new socket pool on every draft page load, never closed —
#: unbounded fd growth, and the app falling over mid-draft.
_shared: httpx.AsyncClient | None = None


def shared_client(timeout: float = 10.0) -> httpx.AsyncClient:
    """The process-wide HTTP client used by every webhook."""
    global _shared  # noqa: PLW0603 - one pool per process is the point
    if _shared is None:
        _shared = httpx.AsyncClient(timeout=timeout)
    return _shared


async def close_shared_client() -> None:
    """Dispose the pool. Called from the app lifespan on shutdown."""
    global _shared  # noqa: PLW0603
    if _shared is not None:
        await _shared.aclose()
        _shared = None


class DiscordError(RuntimeError):
    pass


class WebhookClient:
    """Thin, queueing client for a single webhook."""

    def __init__(
        self,
        webhook_id: str,
        webhook_token: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not webhook_id or not webhook_token:
            raise DiscordError("webhook_id and webhook_token are required")

        self._id = webhook_id
        self._token = webhook_token
        # Default to the shared pool. A client passed in is still owned by the
        # caller and closed with it.
        self._owns_client = client is None
        self._client = client or shared_client(timeout)
        self._gate = asyncio.Lock()
        self._last_send = 0.0

    async def aclose(self) -> None:
        """Close the client, but only if this instance made it.

        With the shared pool there is nothing per-instance to close, and closing
        the shared pool would break every other request in flight.
        """
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> WebhookClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ── Operations ───────────────────────────────────────────────────────────

    async def send(
        self,
        *,
        content: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
        mention_ids: Iterable[str] = (),
    ) -> str | None:
        """Post a message. Returns the message id, or None if it failed."""
        payload: dict[str, Any] = {
            "allowed_mentions": {"parse": ["users"], "users": list(mention_ids)},
        }
        if content:
            payload["content"] = content
        if embeds:
            payload["embeds"] = embeds[:10]  # Discord hard limit

        if not content and not embeds:
            raise DiscordError("a message needs content or embeds")

        return await self._spaced_request("POST", self._url(), json=payload)

    async def edit(
        self,
        message_id: str,
        *,
        content: str | None = None,
        embeds: list[dict[str, Any]] | None = None,
    ) -> None:
        """Edit in place. Signup state updates depend on this."""
        payload: dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if embeds is not None:
            payload["embeds"] = embeds[:10]
        if not payload:
            return
        await self._spaced_request("PATCH", f"{self._url()}/messages/{message_id}", json=payload)

    async def delete(self, message_id: str) -> None:
        await self._spaced_request("DELETE", f"{self._url()}/messages/{message_id}")

    # ── Internals ────────────────────────────────────────────────────────────

    def _url(self) -> str:
        return f"{API}/{self._id}/{self._token}"

    async def _spaced_request(self, method: str, url: str, **kwargs: Any) -> str | None:
        """Rate-limit ourselves and retry once on a 429."""
        async with self._gate:
            gap = asyncio.get_running_loop().time() - self._last_send
            if gap < _MIN_SEND_GAP:
                await asyncio.sleep(_MIN_SEND_GAP - gap)
            try:
                resp = await self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                log.warning("discord %s %s failed: %s", method, url.rsplit("/", 1)[0], exc)
                return None

            if resp.status_code == 429:
                retry_after = float(
                    resp.json().get("retry_after", _MIN_SEND_GAP)
                    if resp.headers.get("content-type", "").startswith("application/json")
                    else _MIN_SEND_GAP
                )
                log.info("discord rate limited — waiting %.2fs", retry_after)
                await asyncio.sleep(min(retry_after, 10.0))
                try:
                    resp = await self._client.request(method, url, **kwargs)
                except httpx.HTTPError:
                    return None

            self._last_send = asyncio.get_running_loop().time()

            if resp.status_code >= 400:
                log.warning("discord %s returned %s: %s", method, resp.status_code, resp.text[:200])
                return None

            if method == "POST":
                try:
                    return resp.json().get("id")
                except ValueError:
                    return None
            return None


# ── Embed helpers ───────────────────────────────────────────────────────────

#: Constructor colours. Keyed on the substrings Jolpica actually returns, which
#: for 2026 include "Alpine F1 Team", "RB F1 Team", and "Haas F1 Team" — all of
#: which a naive exact-match map misses.
CONSTRUCTOR_COLOURS: dict[str, int] = {
    "ferrari": 0xDC0000,
    "red bull": 0x1E41FF,
    "mercedes": 0x00D2BE,
    "mclaren": 0xFF8000,
    "aston martin": 0x229971,
    "alpine": 0xFF87BC,
    "williams": 0x64C4FF,
    "rb": 0x6C7BFF,
    "racing bulls": 0x6C7BFF,
    "haas": 0xB6BABD,
    "sauber": 0x52E252,
    "kick sauber": 0x52E252,
    "audi": 0xF50537,
    "cadillac": 0xB0B7BC,
}

DEFAULT_COLOUR = 0x1E293B


def constructor_colour(name: str) -> int:
    """Longest-substring match so 'RB F1 Team' resolves to Red Bull's blue."""
    lowered = name.lower()
    best: tuple[int, int] = (0, DEFAULT_COLOUR)
    for needle, colour in CONSTRUCTOR_COLOURS.items():
        if needle in lowered and len(needle) > best[0]:
            best = (len(needle), colour)
    return best[1]


def simple_embed(title: str, description: str, colour: int = DEFAULT_COLOUR) -> dict[str, Any]:
    return {
        "title": title.upper(),
        "description": description,
        "color": colour,
    }
