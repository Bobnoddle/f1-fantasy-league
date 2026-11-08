"""Discord as a Notifier.

Adapter over the low-level webhook client so nothing else in the app imports
Discord. A league without a webhook never constructs one of these.
"""

from __future__ import annotations

from uuid import UUID

from app.discord.client import WebhookClient, constructor_colour, simple_embed
from app.notifier import LeagueContext


class DiscordNotifier:
    """Lifecycle announcements to a league's Discord channel.

    Mentions use ``<@id>`` and are gated on an external id being present. A guest
    player has none, so they get a plain name instead — the game works fully
    without any Discord identity.
    """

    def __init__(self, league: LeagueContext, client: WebhookClient | None = None) -> None:
        self.league = league
        self._client = client or WebhookClient(league.webhook_id, league.webhook_token)
        self._signup_message_id: str | None = None

    def _mention(self, team_id: UUID, mention: str | None) -> str:
        return mention or f"`{str(team_id)[:8]}`"

    async def signup_updated(self, league: LeagueContext, players: int, size: int) -> None:
        """Edit the call in place rather than posting a new message each join."""
        description = f"**{players}** player{'s' if players != 1 else ''} joined" + (
            f"\n**{size}** drivers each" if players else "\nBe the first to join"
        )
        embed = simple_embed(f"🏎 {league.name} {league.season_year}", description)

        if self._signup_message_id:
            await self._client.edit(self._signup_message_id, embeds=[embed])
        else:
            self._signup_message_id = await self._client.send(embeds=[embed])

    async def turn_started(
        self, league: LeagueContext, team_id: UUID, mention: str | None, deadline_secs: int
    ) -> None:
        await self._client.send(
            content=(
                f"{self._mention(team_id, mention)} you're on the clock — "
                f"{deadline_secs // 60} minutes. Auto-picks on timeout."
            )
        )

    async def pick_recorded(
        self,
        league: LeagueContext,
        team_id: UUID,
        actor: str,
        driver_name: str,
        remaining: int,
        auto: bool,
    ) -> None:
        verb = "auto-picked" if auto else "picked"
        await self._client.send(content=f"**{actor}** {verb} **{driver_name}** · {remaining} left")

    async def draft_complete(self, league: LeagueContext, rosters: dict[str, list[str]]) -> None:
        lines = [
            f"**{name}**\n" + "\n".join(f"  • {d}" for d in drivers)
            for name, drivers in rosters.items()
        ]
        await self._client.send(
            embeds=[simple_embed(f"🏆 {league.name} — draft complete", "\n".join(lines))]
        )

    async def results_posted(
        self,
        league: LeagueContext,
        event_name: str,
        kind: str,
        standings: list[tuple[str, float]],
    ) -> None:
        if not standings:
            return
        leader_points = standings[0][1]
        lines = [
            f"`{pos:>2}. {'🥇' if pos == 1 else '  '} {name:<16} {points:>5.0f}`"
            for pos, (name, points) in enumerate(standings, start=1)
        ]
        embed = simple_embed(
            f"🏁 {event_name} — {kind}",
            "```\n" + "\n".join(lines) + f"\n```\nLeader: {leader_points:.0f} pts",
            colour=constructor_colour("ferrari"),
        )
        await self._client.send(embeds=[embed])

    async def test(self) -> None:
        message_id = await self._client.send(
            content="🏎 **F1 Fantasy** connected. Results will post here."
        )
        if message_id is None:
            raise RuntimeError("Discord webhook rejected the test message")
        await self._client.delete(message_id)
