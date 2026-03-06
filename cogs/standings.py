"""
cogs/standings.py — Phase 6: Standings & Info commands.

Commands
--------
/standings          — Season leaderboard for this guild.
/team [@player]     — Roster and per-driver points for one player.
/scores <race_name> — Per-team breakdown for a single scored race.
/rules              — Static embed showing the scoring rules.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from db.connection import get_db
from utils.checks import league_exists
from utils.embeds import error_embed, info_embed

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour constants
# ---------------------------------------------------------------------------

_GOLD  = 0xF1C40F
_BLUE  = 0x3498DB
_GREY  = 0x95A5A6

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
_MEDAL_EMOJIS  = ["🥇", "🥈", "🥉"]


def medal(pos: int) -> str:
    """Return a position emoji/label for 1-based *pos*."""
    if 1 <= pos <= 3:
        return _MEDAL_EMOJIS[pos - 1]
    if 4 <= pos <= 10:
        return _NUMBER_EMOJIS[pos - 1]
    return f"{pos}."


def _fmt_pts(value: float) -> str:
    """Format a season-total points value (no sign, whole number)."""
    return f"{int(value):,} pts"


def _fmt_pts_signed(value: float) -> str:
    """Format a per-race points value with a + sign."""
    return f"+{int(value):,} pts" if value >= 0 else f"{int(value):,} pts"


def _breakdown_str(raw: Optional[str]) -> str:
    """
    Parse a JSON breakdown string and return a compact component summary.

    Expected keys (from ScoreBreakdown.as_dict()):
        finish, quali, completion, gain, fastest_lap, dsq, total
    """
    if not raw:
        return ""
    try:
        bd = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    parts = [
        f"{bd.get('finish', 0)} finish",
        f"{bd.get('completion', 0)} comp",
        f"{bd.get('gain', 0)} gain",
        f"{bd.get('fastest_lap', 0)} FL",
        f"{bd.get('quali', 0)} quali",
    ]
    if bd.get("dsq", 0):
        parts.append(f"{bd['dsq']} dsq")
    return " + ".join(parts)


# ---------------------------------------------------------------------------
# Rules text (built from config constants)
# ---------------------------------------------------------------------------

def _build_rules_embed() -> discord.Embed:
    embed = discord.Embed(
        title="F1 FANTASY — SCORING RULES",
        colour=_GREY,
    )

    # ── Race finish points ──────────────────────────────────────────────────
    race_pts_parts: list[str] = []
    seen: dict[int, list[int]] = {}
    for pos, pts in sorted(config.RACE_POINTS.items()):
        seen.setdefault(pts, []).append(pos)
    for pts, positions in sorted(seen.items(), key=lambda kv: -kv[0]):
        if len(positions) == 1:
            race_pts_parts.append(f"P{positions[0]:<2} = {pts:>2} pts")
        else:
            race_pts_parts.append(f"P{positions[0]}–{positions[-1]:<2} = {pts:>2} pts")
    race_value = "```\n" + "\n".join(race_pts_parts) + "\n```"
    embed.add_field(
        name="RACE FINISH POINTS",
        value=race_value,
        inline=False,
    )

    # ── Qualifying points ───────────────────────────────────────────────────
    quali_pts_parts: list[str] = []
    seen_q: dict[int, list[int]] = {}
    for pos, pts in sorted(config.QUALI_POINTS.items()):
        seen_q.setdefault(pts, []).append(pos)
    for pts, positions in sorted(seen_q.items(), key=lambda kv: -kv[0]):
        if pts == 0:
            continue  # skip zero entries — implied
        if len(positions) == 1:
            quali_pts_parts.append(f"P{positions[0]:<2} = {pts:>2} pts")
        else:
            quali_pts_parts.append(f"P{positions[0]}–{positions[-1]:<2} = {pts:>2} pts")
    quali_pts_parts.append("P16–20 =  0 pts")
    quali_value = "```\n" + "\n".join(quali_pts_parts) + "\n```"
    embed.add_field(
        name="QUALIFYING POINTS (P1–P15)",
        value=quali_value,
        inline=False,
    )

    # ── Bonuses & penalties ─────────────────────────────────────────────────
    bonuses = (
        "```\n"
        f"FINISH       : +{config.COMPLETION_BONUS} pts\n"
        f"POSITION GAIN: +{config.POSITION_GAIN_BONUS} pts / place\n"
        f"FASTEST LAP  : +{config.FASTEST_LAP_BONUS} pts\n"
        f"DNF          :  0 pts (natural floor)\n"
        f"DSQ          : {config.DSQ_PENALTY} pts\n"
        "```"
    )
    embed.add_field(name="BONUSES & PENALTIES", value=bonuses, inline=False)

    # ── Sprint ──────────────────────────────────────────────────────────────
    embed.add_field(
        name="SPRINT RACES",
        value=(
            "```\n"
            "Finish points at 1/2 value (rounded down)\n"
            "Position gain and completion bonus at full value\n"
            "```"
        ),
        inline=False,
    )

    # ── Draft ───────────────────────────────────────────────────────────────
    embed.add_field(
        name="DRAFT",
        value=(
            "```\n"
            f"FORMAT      : Snake\n"
            f"TEAM SIZE   : Auto-scaled (floor(22 / players))\n"
            f"ORDER       : Random\n"
            f"TIMEOUT     : {config.DRAFT_TIMEOUT // 60} min pick timeout\n"
            f"AUTO-PICK   : On timeout\n"
            "```"
        ),
        inline=False,
    )

    return embed


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class StandingsCog(commands.Cog, name="Standings"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -----------------------------------------------------------------------
    # /standings
    # -----------------------------------------------------------------------

    @app_commands.command(name="standings", description="Show the season leaderboard.")
    @league_exists()
    async def standings(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        db = await get_db()
        guild_id = interaction.guild_id

        # Season year
        async with db.execute(
            "SELECT season_year FROM league WHERE guild_id = ?", (guild_id,)
        ) as cur:
            league_row = await cur.fetchone()
        year = league_row["season_year"] if league_row else config.SEASON_YEAR

        # Standings query
        async with db.execute(
            """
            SELECT t.id, t.user_id, t.user_name,
                   COALESCE(SUM(s.points), 0) AS total
            FROM   team t
            LEFT JOIN score s ON s.team_id = t.id
            WHERE  t.guild_id = ?
            GROUP  BY t.id
            ORDER  BY total DESC
            """,
            (guild_id,),
        ) as cur:
            rows = await cur.fetchall()

        # Count scored races
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM race WHERE guild_id = ? AND scored_at IS NOT NULL",
            (guild_id,),
        ) as cur:
            race_row = await cur.fetchone()
        races_scored = race_row["cnt"] if race_row else 0

        title = f"🏆 SEASON STANDINGS — {year}"
        embed = info_embed(title, "")

        if not rows:
            embed.description = "```\nNO TEAMS REGISTERED\n```"
            await interaction.followup.send(embed=embed)
            return

        lines: list[str] = []
        # Header for the table
        lines.append("POS  TEAM            | POINTS")
        lines.append("──── ────────────────|───────")
        
        for idx, row in enumerate(rows, start=1):
            name = (row["user_name"] or "Unknown")[:15]
            pts  = row["total"] or 0
            # Use medals for top 3, numbers for others
            pos_str = medal(idx)
            lines.append(f"{pos_str} {name:<15} | {pts:>5.0f}")

        desc = "```\n" + "\n".join(lines) + "\n```"
        if races_scored == 0:
            footer = f"DRAFT COMPLETED · SEASON {year}"
        else:
            footer = f"{races_scored} RACE{'S' if races_scored != 1 else ''} SCORED · SEASON {year}"

        embed.description = desc
        embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /team
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="team",
        description="Show a player's roster and their points.",
    )
    @app_commands.describe(player="The player to inspect (defaults to you).")
    @league_exists()
    async def team(
        self,
        interaction: discord.Interaction,
        player: Optional[discord.Member] = None,
    ) -> None:
        await interaction.response.defer()

        target = player or interaction.user
        db = await get_db()
        guild_id = interaction.guild_id

        # Season year
        async with db.execute(
            "SELECT season_year FROM league WHERE guild_id = ?", (guild_id,)
        ) as cur:
            league_row = await cur.fetchone()
        year = league_row["season_year"] if league_row else config.SEASON_YEAR

        # Fetch the team record
        async with db.execute(
            "SELECT id, user_name FROM team WHERE guild_id = ? AND user_id = ?",
            (guild_id, target.id),
        ) as cur:
            team_row = await cur.fetchone()

        display_name = (
            team_row["user_name"] if team_row else getattr(target, "display_name", str(target))
        )

        if not team_row:
            await interaction.followup.send(
                embed=error_embed("Player has no team yet."), ephemeral=True
            )
            return

        team_id = team_row["id"]

        # Per-driver points for this team
        async with db.execute(
            """
            SELECT d.name AS driver_name, d.team_name AS car_team,
                   COALESCE(SUM(s.points), 0) AS driver_pts
            FROM   roster r
            JOIN   driver d ON d.id = r.driver_id
            LEFT JOIN score s
                   ON s.team_id = r.team_id AND s.driver_id = r.driver_id
            WHERE  r.team_id = ? AND r.guild_id = ?
            GROUP  BY r.driver_id
            ORDER  BY driver_pts DESC
            """,
            (team_id, guild_id),
        ) as cur:
            driver_rows = await cur.fetchall()

        # Season total
        async with db.execute(
            "SELECT COALESCE(SUM(points), 0) AS total FROM score WHERE team_id = ?",
            (team_id,),
        ) as cur:
            total_row = await cur.fetchone()
        total_pts = total_row["total"] if total_row else 0.0

        embed = discord.Embed(
            title=f"🏎️ {display_name}'s Team — {_fmt_pts(total_pts)}",
            description=f"Season {year}",
            colour=_BLUE,
        )

        if not driver_rows:
            embed.add_field(name="Roster", value="No drivers on roster yet.", inline=False)
        else:
            lines: list[str] = []
            for dr in driver_rows:
                name_col = f"{dr['driver_name']} ({dr['car_team']})"
                pts_col  = _fmt_pts(dr["driver_pts"])
                lines.append(f"`{name_col:<30}` {pts_col}")
            value = "\n".join(lines)
            # Field value limit is 1024 chars
            if len(value) > 1020:
                value = value[:1020] + "…"
            embed.add_field(name="Drivers", value=value, inline=False)

        await interaction.followup.send(embed=embed)

    # -----------------------------------------------------------------------
    # /scores
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="scores",
        description="Show per-team point breakdown for a specific race.",
    )
    @app_commands.describe(race_name="Select a scored Race/Sprint (autocomplete).")
    @league_exists()
    async def scores(self, interaction: discord.Interaction, race_name: str) -> None:
        await interaction.response.defer()

        db = await get_db()
        guild_id = interaction.guild_id

        race_row = None

        race_id_token, _, _race_label_token = race_name.partition(":")
        if race_id_token.isdigit():
            race_id = int(race_id_token)
            async with db.execute(
                """
                SELECT id, name, race_type FROM race
                WHERE  guild_id = ? AND id = ? AND scored_at IS NOT NULL
                LIMIT  1
                """,
                (guild_id, race_id),
            ) as cur:
                race_row = await cur.fetchone()

        if race_row is None:
            # Backward compatibility for legacy/plain-name input.
            async with db.execute(
                """
                SELECT id, name, race_type FROM race
                WHERE  guild_id = ? AND name = ? AND scored_at IS NOT NULL
                LIMIT  1
                """,
                (guild_id, race_name),
            ) as cur:
                race_row = await cur.fetchone()

        if not race_row:
            await interaction.followup.send(
                embed=error_embed(f'Race **{race_name}** not found or not yet scored.'),
                ephemeral=True,
            )
            return

        race_id   = race_row["id"]
        race_label = race_row["name"]
        race_type  = race_row["race_type"]
        type_label = "Sprint" if race_type == "sprint" else "Race"

        # Fetch all scores for this race, grouped by team
        async with db.execute(
            """
            SELECT t.user_name, t.user_id,
                   d.name AS driver_name, d.team_name AS car_team,
                   s.points, s.breakdown
            FROM   score s
            JOIN   team   t ON t.id = s.team_id
            JOIN   driver d ON d.id = s.driver_id
            WHERE  s.race_id = ? AND t.guild_id = ?
            ORDER  BY t.id, s.points DESC
            """,
            (race_id, guild_id),
        ) as cur:
            score_rows = await cur.fetchall()

        if not score_rows:
            await interaction.followup.send(
                embed=error_embed("No scores recorded for this race yet."),
                ephemeral=True,
            )
            return

        # Group by team
        teams: dict[str, dict] = {}
        for row in score_rows:
            key = str(row["user_id"])
            if key not in teams:
                teams[key] = {
                    "name": row["user_name"] or f'<@{row["user_id"]}>',
                    "total": 0.0,
                    "drivers": [],
                }
            teams[key]["total"] += row["points"]
            teams[key]["drivers"].append(row)

        # Sort teams by this-race total descending
        sorted_teams = sorted(teams.values(), key=lambda t: t["total"], reverse=True)

        embed = discord.Embed(
            title=f"🏁 {race_label} — {type_label} Scores",
            colour=_BLUE,
        )

        for tdata in sorted_teams:
            team_header = f"**{tdata['name']}**  {_fmt_pts_signed(tdata['total'])}"
            driver_lines: list[str] = []
            for dr in tdata["drivers"]:
                bd_str = _breakdown_str(dr["breakdown"])
                pts_str = _fmt_pts_signed(dr["points"])
                car = dr["car_team"] or ""
                line = f"  {dr['driver_name']} ({car})  {pts_str}"
                if bd_str:
                    line += f"\n    _{bd_str}_"
                driver_lines.append(line)

            field_value = team_header + "\n" + "\n".join(driver_lines)
            # Truncate to field value limit
            if len(field_value) > 1020:
                field_value = field_value[:1020] + "…"

            embed.add_field(name="\u200b", value=field_value, inline=False)

            # Discord embed has a 25-field limit
            if len(embed.fields) >= 25:
                break

        await interaction.followup.send(embed=embed)

    @scores.autocomplete("race_name")
    async def scores_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        db = await get_db()
        guild_id = interaction.guild_id
        if guild_id is None:
            return []

        async with db.execute(
            """
            SELECT id, name, race_type FROM race
            WHERE  guild_id = ? AND scored_at IS NOT NULL
            ORDER  BY round_number
            """,
            (guild_id,),
        ) as cur:
            rows = await cur.fetchall()

        current_lower = current.lower()
        def _choice_name(row: dict) -> str:
            type_label = "Sprint" if row["race_type"] == "sprint" else "Race"
            return f"{row['name']} ({type_label})"

        choices = [
            app_commands.Choice(
                name=_choice_name(row),
                value=f"{row['id']}:{row['name']}",
            )
            for row in rows
            if current_lower in _choice_name(row).lower()
        ]
        return choices[:25]

    # -----------------------------------------------------------------------
    # /rules
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="rules",
        description="Show the fantasy scoring rules.",
    )
    async def rules(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=_build_rules_embed())


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StandingsCog(bot))
