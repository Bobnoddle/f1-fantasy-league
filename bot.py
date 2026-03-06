from __future__ import annotations

import logging
import traceback
from typing import TYPE_CHECKING

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
from config import TOKEN
from db.connection import get_db, init_db, close_db
from utils.embeds import error_embed

if TYPE_CHECKING:
    from api.jolpica import JolpicaClient

log = logging.getLogger(__name__)


class F1Bot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession | None = None
        self.jolpica: JolpicaClient | None = None  # set in setup_hook

    async def setup_hook(self) -> None:
        # Initialise database (creates tables on first run)
        await init_db()

        # Create persistent HTTP session and seed startup cache
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        from api.jolpica import JolpicaClient, seed_calendar, seed_drivers

        self.jolpica = JolpicaClient(self.http_session)
        db = await get_db()
        await seed_calendar(self.jolpica, db, config.SEASON_YEAR)
        await seed_drivers(self.jolpica, db, config.SEASON_YEAR)
        print(f"Startup cache seeded for {config.SEASON_YEAR}")

        # Load all feature cogs
        cog_modules = [
            "cogs.league",
            "cogs.draft",
            "cogs.results",
            "cogs.standings",
        ]
        for module in cog_modules:
            await self.load_extension(module)

        # Sync slash commands globally
        synced = await self.tree.sync()
        print(f"Synced {len(synced)} slash command(s) globally.")

        # ── Global slash-command error handler ──────────────────────────────
        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction,
            error: app_commands.AppCommandError,
        ) -> None:
            if isinstance(error, app_commands.CheckFailure):
                msg = str(error) or "You do not have permission to use this command."
                try:
                    await interaction.response.send_message(
                        embed=error_embed(msg), ephemeral=True
                    )
                except discord.InteractionResponded:
                    await interaction.followup.send(
                        embed=error_embed(msg), ephemeral=True
                    )
            else:
                log.error("Unhandled app command error:\n%s", traceback.format_exc())
                generic = "An unexpected error occurred. Please try again."
                try:
                    await interaction.response.send_message(
                        embed=error_embed(generic), ephemeral=True
                    )
                except discord.InteractionResponded:
                    await interaction.followup.send(
                        embed=error_embed(generic), ephemeral=True
                    )

    async def on_ready(self) -> None:
        if self.user is None:
            log.warning("on_ready: bot user not available yet")
            return
        print(f"Logged in as {self.user} ({self.user.id})")

    async def close(self) -> None:
        if self.http_session is not None:
            await self.http_session.close()
        await close_db()
        await super().close()


bot = F1Bot()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    bot.run(TOKEN)
