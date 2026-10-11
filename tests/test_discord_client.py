"""Discord webhook client tests.

Uses httpx's MockTransport — no network, no live channel.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.discord.client import (
    DiscordError,
    WebhookClient,
    constructor_colour,
    simple_embed,
)


def client(handler) -> WebhookClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return WebhookClient("123", "tok", client=http)


def ok_handler(calls: list) -> callable:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"id": "999"})

    return handler


# ── Send ────────────────────────────────────────────────────────────────────


async def test_send_posts_and_returns_message_id() -> None:
    calls: list = []
    async with client(ok_handler(calls)) as wh:
        message_id = await wh.send(content="hello", mention_ids=["42"])

    assert message_id == "999"
    method, url, body = calls[0]
    assert method == "POST"
    assert url.startswith("https://discord.com/api/v10/webhooks/123/tok")
    assert body["content"] == "hello"
    assert body["allowed_mentions"] == {"parse": ["users"], "users": ["42"]}


async def test_send_requires_content_or_embeds() -> None:
    async with client(ok_handler([])) as wh:
        with pytest.raises(DiscordError, match="content or embeds"):
            await wh.send()


async def test_send_caps_embeds_at_ten() -> None:
    calls: list = []
    async with client(ok_handler(calls)) as wh:
        await wh.send(embeds=[{"title": f"e{i}"} for i in range(25)])

    assert len(calls[0][2]["embeds"]) == 10


async def test_send_defaults_to_user_mentions_only() -> None:
    """Webhook default. No @everyone is reachable from this client."""
    calls: list = []
    async with client(ok_handler(calls)) as wh:
        await wh.send(content="no mentions")

    assert calls[0][2]["allowed_mentions"]["parse"] == ["users"]


# ── Edit ────────────────────────────────────────────────────────────────────


async def test_edit_targets_message_path() -> None:
    calls: list = []
    async with client(ok_handler(calls)) as wh:
        await wh.edit("555", embeds=[simple_embed("title", "body")])

    method, url, body = calls[0]
    assert method == "PATCH"
    assert url.endswith("/messages/555")
    assert body["embeds"][0]["title"] == "TITLE"


async def test_edit_with_nothing_is_noop() -> None:
    calls: list = []
    async with client(ok_handler(calls)) as wh:
        await wh.edit("555")

    assert calls == []


# ── Delete ──────────────────────────────────────────────────────────────────


async def test_delete_targets_message_path() -> None:
    calls: list = []
    async with client(ok_handler(calls)) as wh:
        await wh.delete("555")

    assert calls[0][0] == "DELETE"
    assert calls[0][1].endswith("/messages/555")


# ── Failure handling ────────────────────────────────────────────────────────


async def test_send_returns_none_on_server_error() -> None:
    def handler(request):
        return httpx.Response(500, text="boom")

    async with client(handler) as wh:
        assert await wh.send(content="x") is None


async def test_send_returns_none_on_network_failure() -> None:
    def handler(request):
        raise httpx.ConnectError("down")

    async with client(handler) as wh:
        assert await wh.send(content="x") is None


async def test_rate_limit_is_retried() -> None:
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if len(calls) == 1:
            return httpx.Response(
                429, json={"retry_after": 0.01}, headers={"content-type": "application/json"}
            )
        return httpx.Response(200, json={"id": "1"})

    async with client(handler) as wh:
        message_id = await wh.send(content="x")

    assert message_id == "1"
    assert len(calls) == 2


# ── Constructor colours ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ferrari", 0xDC0000),
        ("Red Bull", 0x1E41FF),
        ("Mercedes", 0x00D2BE),
        ("McLaren", 0xFF8000),
    ],
)
def test_constructor_colour_known_teams(name: str, expected: int) -> None:
    assert constructor_colour(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        # The five names a naive exact-match map misses. Verified against the
        # live 2026 grid: these all returned the fallback before.
        "Alpine F1 Team",
        "RB F1 Team",
        "Haas F1 Team",
        "Audi",
        "Cadillac F1 Team",
    ],
)
def test_constructor_colour_handles_suffixed_names(name: str) -> None:
    assert constructor_colour(name) != 0x1E293B  # not the fallback


def test_constructor_colour_prefers_longest_match() -> None:
    """'RB' must not beat 'Red Bull' when both appear."""
    assert constructor_colour("Red Bull Racing") == 0x1E41FF


def test_constructor_colour_unknown_falls_back() -> None:
    assert constructor_colour("Some New Team") == 0x1E293B


# ── Construction ────────────────────────────────────────────────────────────


def test_webhook_requires_credentials() -> None:
    with pytest.raises(DiscordError, match="required"):
        WebhookClient("", "tok")


def test_simple_embed_uppercases_title() -> None:
    assert simple_embed("race results", "body")["title"] == "RACE RESULTS"
