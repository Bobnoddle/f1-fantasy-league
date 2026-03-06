from __future__ import annotations

import discord
from discord import app_commands

from db.connection import get_db


def is_admin():
    """Check that the invoking user has the Manage Guild permission."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            raise app_commands.CheckFailure("This command can only be used inside a server.")
        member = interaction.user
        if not isinstance(member, discord.Member):
            raise app_commands.CheckFailure("Could not resolve your server membership.")
        if not member.guild_permissions.manage_guild:
            raise app_commands.CheckFailure(
                "You need the **Manage Server** permission to use this command."
            )
        return True

    return app_commands.check(predicate)


def league_exists():
    """Check that a league has been configured for this guild."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            raise app_commands.CheckFailure("This command can only be used inside a server.")
        db = await get_db()
        async with db.execute(
            "SELECT guild_id FROM league WHERE guild_id = ?", (interaction.guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise app_commands.CheckFailure(
                "No league set up in this server. An admin needs to run `/league setup` first."
            )
        return True

    return app_commands.check(predicate)


def draft_active():
    """Check that a draft is currently active for this guild."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            raise app_commands.CheckFailure("This command can only be used inside a server.")
        db = await get_db()
        async with db.execute(
            "SELECT status FROM draft_state WHERE guild_id = ?", (interaction.guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or row["status"] != "active":
            raise app_commands.CheckFailure(
                "No draft is currently active in this server."
            )
        return True

    return app_commands.check(predicate)
