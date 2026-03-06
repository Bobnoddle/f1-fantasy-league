from __future__ import annotations

import json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from db.connection import get_db
from utils.checks import is_admin, league_exists
from utils.embeds import error_embed, success_embed, info_embed


class ConfirmResetView(discord.ui.View):
    """Ephemeral confirm / cancel view for /league reset."""

    def __init__(self, guild_id: int, bot: commands.Bot) -> None:
        super().__init__(timeout=60)
        self.guild_id = guild_id
        self.bot = bot

    @discord.ui.button(label="✅ Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()

        try:
            # ── snapshot current standings ──────────────────────────────────
            async with db.execute(
                """
                SELECT t.user_id, t.user_name, COALESCE(SUM(s.points), 0) AS total
                FROM   team t
                LEFT JOIN score s ON s.team_id = t.id
                WHERE  t.guild_id = ?
                GROUP  BY t.id
                ORDER  BY total DESC
                """,
                (self.guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()

            standings = [
                {
                    "user_id": r["user_id"],
                    "user_name": r["user_name"],
                    "points": r["total"],
                }
                for r in rows
            ]

            champion_user: int | None = rows[0]["user_id"] if rows else None
            champion_pts: float = rows[0]["total"] if rows else 0

            async with db.execute(
                "SELECT season_year FROM league WHERE guild_id = ?", (self.guild_id,)
            ) as cursor:
                league_row = await cursor.fetchone()

            old_year: int = league_row["season_year"] if league_row else config.SEASON_YEAR
            new_year: int = old_year + 1

            # ── archive the season ──────────────────────────────────────────
            await db.execute(
                """
                INSERT INTO season_archive
                    (guild_id, season_year, champion_user, final_standings)
                VALUES (?, ?, ?, ?)
                """,
                (self.guild_id, old_year, champion_user, json.dumps(standings)),
            )

            # ── clear season data (FK order) ────────────────────────────────
            await db.execute(
                "DELETE FROM score  WHERE race_id IN (SELECT id FROM race WHERE guild_id = ?)",
                (self.guild_id,),
            )
            await db.execute(
                "DELETE FROM result WHERE race_id IN (SELECT id FROM race WHERE guild_id = ?)",
                (self.guild_id,),
            )
            await db.execute("DELETE FROM race        WHERE guild_id = ?", (self.guild_id,))
            await db.execute("DELETE FROM roster      WHERE guild_id = ?", (self.guild_id,))
            await db.execute("DELETE FROM team        WHERE guild_id = ?", (self.guild_id,))
            await db.execute("DELETE FROM draft_state WHERE guild_id = ?", (self.guild_id,))

            # ── re-seed draft_state as pending ──────────────────────────────
            await db.execute(
                "INSERT INTO draft_state (guild_id, status) VALUES (?, 'pending')",
                (self.guild_id,),
            )

            # ── bump season year ────────────────────────────────────────────
            await db.execute(
                "UPDATE league SET season_year = ? WHERE guild_id = ?",
                (new_year, self.guild_id),
            )

            await db.commit()

            # ── re-seed calendar + driver list for the new season ───────────
            if self.bot.jolpica is not None:
                from api.jolpica import seed_calendar, seed_drivers

                await seed_calendar(self.bot.jolpica, db, new_year)
                await seed_drivers(self.bot.jolpica, db, new_year)

        except Exception as exc:
            await db.rollback()
            self.stop()
            await interaction.followup.send(
                embed=error_embed(
                    f"Reset failed — no changes were saved.\n```\n{exc}\n```"
                ),
                ephemeral=True,
            )
            return

        self.stop()

        if champion_user:
            champ_line = f"Champion: <@{champion_user}> ({champion_pts:.0f} pts)"
        else:
            champ_line = "No races were scored this season."

        await interaction.followup.send(
            embed=success_embed(
                f"**🏆 Season {old_year} archived!**\n"
                f"{champ_line}\n\n"
                f"Season **{new_year}** is ready. "
                f"Run `/draft open` to begin the new draft."
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await interaction.response.send_message(
            embed=info_embed("Reset Cancelled", "No changes were made."),
            ephemeral=True,
        )


class LeagueCog(commands.Cog, name="League"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    league_group = app_commands.Group(
        name="league", description="League management commands"
    )

    # ──────────────────────────────────────────────────────────────────────────
    # /league setup
    # ──────────────────────────────────────────────────────────────────────────

    @league_group.command(
        name="setup",
        description="Configure the F1 Fantasy league for this server",
    )
    @is_admin()
    @app_commands.describe(
        team_size="Drivers per team (omit for auto-scaling: floor(drivers ÷ players), max 10)",
        timeout="Seconds allowed per draft pick (default 600)",
        channel="Channel for auto-posting race results (defaults to this channel)",
    )
    async def league_setup(
        self,
        interaction: discord.Interaction,
        team_size: Optional[int] = None,
        timeout: int = 600,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        results_channel = channel or interaction.channel
        results_channel_id: int = (
            results_channel.id if results_channel else interaction.channel_id  # type: ignore[union-attr]
        )

        db = await get_db()
        await db.execute(
            """
            INSERT INTO league (guild_id, team_size, draft_timeout, season_year, results_channel_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                team_size          = excluded.team_size,
                draft_timeout      = excluded.draft_timeout,
                season_year        = excluded.season_year,
                results_channel_id = excluded.results_channel_id
            """,
            (
                interaction.guild_id,
                team_size,
                timeout,
                config.SEASON_YEAR,
                results_channel_id,
            ),
        )
        # Seed draft_state only if it doesn't already exist — preserves an
        # in-progress draft when an admin re-runs /league setup to tweak settings.
        await db.execute(
            "INSERT OR IGNORE INTO draft_state (guild_id, status) VALUES (?, 'pending')",
            (interaction.guild_id,),
        )
        await db.commit()

        size_str = str(team_size) if team_size else "auto (floor(drivers ÷ players), max 10)"
        minutes = timeout // 60
        seconds_extra = timeout % 60
        timeout_label = (
            f"{minutes} min"
            if seconds_extra == 0
            else f"{minutes}m {seconds_extra}s"
        )

        # Check whether a draft is currently in progress
        draft_warning = ""
        async with db.execute(
            "SELECT status FROM draft_state WHERE guild_id = ?", (interaction.guild_id,)
        ) as cur:
            ds_row = await cur.fetchone()
        if ds_row is not None and ds_row["status"] == "active":
            draft_warning = (
                "\n\n⚠️ A draft is currently in progress. "
                "Settings will apply to the next season."
            )

        embed = success_embed(
            f"**✅ League configured!**\n"
            f"Season: **{config.SEASON_YEAR}**\n"
            f"Team size: **{size_str}**\n"
            f"Pick timeout: **{timeout}s ({timeout_label})**\n"
            f"Results channel: <#{results_channel_id}>"
            f"{draft_warning}"
        )
        await interaction.response.send_message(embed=embed)

    # ──────────────────────────────────────────────────────────────────────────
    # /league reset
    # ──────────────────────────────────────────────────────────────────────────

    @league_group.command(
        name="reset",
        description="Archive the current season and wipe the league (destructive)",
    )
    @is_admin()
    @league_exists()
    async def league_reset(self, interaction: discord.Interaction) -> None:
        view = ConfirmResetView(interaction.guild_id, self.bot)  # type: ignore[arg-type]
        await interaction.response.send_message(
            embed=info_embed(
                "⚠️ Reset League?",
                (
                    "This will **archive the current season** and permanently clear "
                    "all teams, rosters, races and scores.\n\n"
                    "**This action cannot be undone.** Are you sure?"
                ),
            ),
            view=view,
            ephemeral=True,
        )


    # ──────────────────────────────────────────────────────────────────────────
    # /league archive
    # ──────────────────────────────────────────────────────────────────────────

    @league_group.command(
        name="archive",
        description="Show the hall-of-fame: past season champions",
    )
    async def league_archive(self, interaction: discord.Interaction) -> None:
        db = await get_db()
        async with db.execute(
            """
            SELECT season_year, champion_user, final_standings
            FROM   season_archive
            WHERE  guild_id = ?
            ORDER  BY season_year DESC
            """,
            (interaction.guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await interaction.response.send_message(
                embed=info_embed("📚 Season History", "No completed seasons yet."),
                ephemeral=True,
            )
            return

        lines: list[str] = []
        for row in rows:
            year = row["season_year"]
            champ_id = row["champion_user"]
            standings_raw = row["final_standings"]

            if champ_id:
                # Try to pull points from the stored standings JSON
                champ_pts: float | None = None
                if standings_raw:
                    try:
                        s = json.loads(standings_raw)
                        if s:
                            champ_pts = s[0]["points"]
                    except (ValueError, KeyError):
                        pass

                pts_str = f" ({champ_pts:.0f} pts)" if champ_pts is not None else ""
                lines.append(f"**{year}** — Champion: <@{champ_id}>{pts_str}")
            else:
                lines.append(f"**{year}** — No champion recorded")

        await interaction.response.send_message(
            embed=info_embed("📚 Season History", "\n".join(lines)),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeagueCog(bot))
