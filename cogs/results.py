"""
cogs/results.py — Phase 5: Auto-fetch loop + /race fetch override.

Background task
---------------
An hourly calendar-aware loop that detects recently completed races (main race
and sprint), fetches results from Jolpica, scores every guild's fantasy teams,
persists everything to the database, and posts a formatted embed to each
guild's configured results channel.

Admin override
--------------
/race fetch  — manually trigger scoring for any unscored round (with
               autocomplete limited to unscored rounds).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

from api.jolpica import JolpicaClient
from db.connection import get_db
from utils.checks import is_admin, league_exists
from utils.embeds import error_embed, info_embed, success_embed
from utils.scoring import DriverResult, score_event

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WINDOW_HOURS = 36  # how far back we look for unscored races

_POSITION_EMOJIS = [
    "🥇", "🥈", "🥉",
    "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟",
]

_RACE_TYPE_LABEL: dict[str, str] = {
    "race": "Race Results",
    "sprint": "Sprint Results",
}


def _pos_emoji(rank: int) -> str:
    """Return a position emoji for 0-based *rank* (0 = 1st)."""
    if rank < len(_POSITION_EMOJIS):
        return _POSITION_EMOJIS[rank]
    return f"{rank + 1}."


def _parse_dt(iso_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 UTC datetime string, returning None on failure."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.rstrip("Z")).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class ResultsCog(commands.Cog, name="Results"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.auto_fetch_loop.start()

    def cog_unload(self) -> None:
        self.auto_fetch_loop.cancel()

    # -----------------------------------------------------------------------
    # Background task
    # -----------------------------------------------------------------------

    @tasks.loop(hours=1)
    async def auto_fetch_loop(self) -> None:
        """Hourly task: detect and score recently completed races/sprints."""
        try:
            await self._run_auto_fetch()
        except Exception:
            log.exception("auto_fetch_loop: unhandled error")

    @auto_fetch_loop.before_loop
    async def _before_auto_fetch(self) -> None:
        await self.bot.wait_until_ready()

    async def _run_auto_fetch(self) -> None:
        db = await get_db()
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=_WINDOW_HOURS)

        async with db.execute("SELECT guild_id FROM league") as cur:
            league_rows = await cur.fetchall()
        guild_ids: set[int] = {row["guild_id"] for row in league_rows}
        if not guild_ids:
            return

        async with db.execute(
            "SELECT round_number, name, race_date, sprint_date FROM calendar"
        ) as cur:
            rows = await cur.fetchall()

        for row in rows:
            round_number: int = row["round_number"]
            round_name: str = row["name"]

            # ── Main race ──────────────────────────────────────────────────
            race_dt = _parse_dt(row["race_date"])
            if race_dt and window_start <= race_dt <= now:
                async with db.execute(
                    "SELECT guild_id FROM race WHERE round_number = ? AND race_type = 'race'",
                    (round_number,),
                ) as cur:
                    scored_rows = await cur.fetchall()
                scored_guild_ids = {r["guild_id"] for r in scored_rows}
                if any(gid not in scored_guild_ids for gid in guild_ids):
                    log.info(
                        "auto_fetch: scoring round %d (%s) — race",
                        round_number, round_name,
                    )
                    await self._score_round(round_number, round_name, is_sprint=False)

            # ── Sprint ─────────────────────────────────────────────────────
            sprint_dt = _parse_dt(row["sprint_date"])
            if sprint_dt and window_start <= sprint_dt <= now:
                async with db.execute(
                    "SELECT guild_id FROM race WHERE round_number = ? AND race_type = 'sprint'",
                    (round_number,),
                ) as cur:
                    scored_rows = await cur.fetchall()
                scored_guild_ids = {r["guild_id"] for r in scored_rows}
                if any(gid not in scored_guild_ids for gid in guild_ids):
                    log.info(
                        "auto_fetch: scoring round %d (%s) — sprint",
                        round_number, round_name,
                    )
                    await self._score_round(round_number, round_name, is_sprint=True)

    # -----------------------------------------------------------------------
    # Core scoring
    # -----------------------------------------------------------------------

    async def _score_round(
        self,
        round_number: int,
        round_name: str,
        is_sprint: bool,
        target_guild_id: int | None = None,
    ) -> bool:
        """Fetch Jolpica results, persist, and post embeds for every guild.

        Returns
        -------
        bool
            True  — results were available and processed for at least one guild.
            False — results not yet published (will auto-retry next hour).
        """
        jolpica: JolpicaClient = self.bot.jolpica
        race_type = "sprint" if is_sprint else "race"

        if jolpica is None:
            log.error("_score_round: Jolpica client not initialised")
            return False

        # 1. Load leagues to score ──────────────────────────────────────────
        db = await get_db()

        if target_guild_id is None:
            async with db.execute(
                "SELECT guild_id, results_channel_id, season_year FROM league"
            ) as cur:
                league_rows = await cur.fetchall()
        else:
            async with db.execute(
                """
                SELECT guild_id, results_channel_id, season_year
                FROM league
                WHERE guild_id = ?
                """,
                (target_guild_id,),
            ) as cur:
                league_rows = await cur.fetchall()

        if not league_rows:
            return False

        async with db.execute("SELECT id, code, name FROM driver") as cur:
            driver_rows = await cur.fetchall()
        driver_id_map: dict[str, int] = {r["code"]: r["id"] for r in driver_rows}
        driver_name_map: dict[str, str] = {r["code"]: r["name"] for r in driver_rows}

        # 2. Group leagues by season and fetch each season once ────────────
        leagues_by_year: dict[int, list[aiosqlite.Row]] = {}
        for league_row in league_rows:
            leagues_by_year.setdefault(league_row["season_year"], []).append(league_row)

        any_success = False
        for season_year, season_leagues in leagues_by_year.items():
            if is_sprint:
                finish_results = await jolpica.get_sprint_results(season_year, round_number)
            else:
                finish_results = await jolpica.get_race_results(season_year, round_number)

            if not finish_results:
                log.info(
                    "_score_round(%d, %s, year=%d): results not yet available, will retry later",
                    round_number,
                    race_type,
                    season_year,
                )
                continue

            quali_results = await jolpica.get_qualifying_results(season_year, round_number)
            quali_map: dict[str, int] = {
                r["driver_code"]: r["quali_position"] for r in quali_results
            }

            driver_pts_map: dict[str, float] = {}
            for res in finish_results:
                code = res["driver_code"]
                dr_preview = DriverResult(
                    driver_id=0,
                    grid_position=res["grid_position"] or None,
                    finish_position=res["finish_position"] or None,
                    dnf=res["dnf"],
                    dsq=res["dsq"],
                    fastest_lap=res["fastest_lap"],
                    quali_position=quali_map.get(code) if not is_sprint else None,
                )
                driver_pts_map[code] = score_event(dr_preview, is_sprint=is_sprint).total

            fastest_lap_code: Optional[str] = next(
                (r["driver_code"] for r in finish_results if r.get("fastest_lap")),
                None,
            )

            for league_row in season_leagues:
                guild_id: int = league_row["guild_id"]
                results_channel_id: Optional[int] = league_row["results_channel_id"]
                try:
                    ok = await self._score_for_guild(
                        db=db,
                        guild_id=guild_id,
                        results_channel_id=results_channel_id,
                        round_number=round_number,
                        round_name=round_name,
                        race_type=race_type,
                        is_sprint=is_sprint,
                        finish_results=finish_results,
                        quali_map=quali_map,
                        driver_id_map=driver_id_map,
                        driver_name_map=driver_name_map,
                        driver_pts_map=driver_pts_map,
                        fastest_lap_code=fastest_lap_code,
                    )
                    if ok:
                        any_success = True
                except Exception:
                    log.exception(
                        "_score_round: failed for guild %d round %d %s (year=%d)",
                        guild_id,
                        round_number,
                        race_type,
                        season_year,
                    )

        return any_success

    async def _score_for_guild(  # noqa: PLR0913  (many params, all essential)
        self,
        *,
        db,
        guild_id: int,
        results_channel_id: Optional[int],
        round_number: int,
        round_name: str,
        race_type: str,
        is_sprint: bool,
        finish_results: list[dict],
        quali_map: dict[str, int],
        driver_id_map: dict[str, int],
        driver_name_map: dict[str, str],
        driver_pts_map: dict[str, float],
        fastest_lap_code: Optional[str],
    ) -> bool:
        """Persist results + scores for a single guild.

        Returns True if the race was newly scored; False if already present.
        """
        try:
            race_cursor = await db.execute(
                """
                INSERT INTO race (guild_id, name, round_number, race_type)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, round_name, round_number, race_type),
            )
            race_id: int = race_cursor.lastrowid
        except aiosqlite.IntegrityError:
            log.debug(
                "_score_for_guild: guild %d round %d %s already scored — skipping",
                guild_id, round_number, race_type,
            )
            return False

        try:
            if race_id is None:
                async with db.execute(
                    "SELECT id FROM race WHERE guild_id = ? AND name = ? AND race_type = ?",
                    (guild_id, round_name, race_type),
                ) as cur:
                    race_row = await cur.fetchone()
                if not race_row:
                    log.error(
                        "_score_for_guild: could not retrieve race_id for guild %d after insert",
                        guild_id,
                    )
                    await db.rollback()
                    return False
                race_id = race_row["id"]

            # ── Guild roster: driver_id → [team_id, …] ────────────────────
            async with db.execute(
                """
                SELECT t.id AS team_id, r.driver_id
                FROM   team t
                JOIN   roster r ON r.team_id = t.id
                WHERE  t.guild_id = ?
                """,
                (guild_id,),
            ) as cur:
                roster_rows = await cur.fetchall()

            driver_to_teams: dict[int, list[int]] = {}
            for rr in roster_rows:
                driver_to_teams.setdefault(rr["driver_id"], []).append(rr["team_id"])

            # ── Score each driver result ───────────────────────────────────
            team_race_points: dict[int, float] = {}
            team_driver_contrib: dict[int, list[tuple[str, float]]] = {}

            for res in finish_results:
                code: str = res["driver_code"]
                driver_id = driver_id_map.get(code)
                if driver_id is None:
                    log.debug("_score_for_guild: unknown driver code %r — skipping", code)
                    continue

                dr = DriverResult(
                    driver_id=driver_id,
                    grid_position=res["grid_position"] or None,
                    finish_position=res["finish_position"] or None,
                    dnf=res["dnf"],
                    dsq=res["dsq"],
                    fastest_lap=res["fastest_lap"],
                    quali_position=quali_map.get(code) if not is_sprint else None,
                )
                breakdown = score_event(dr, is_sprint=is_sprint)

                await db.execute(
                    """
                    INSERT OR IGNORE INTO result
                      (race_id, driver_id, grid_position, finish_position,
                       dnf, dsq, fastest_lap, quali_position)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        race_id,
                        driver_id,
                        dr.grid_position,
                        dr.finish_position,
                        int(dr.dnf),
                        int(dr.dsq),
                        int(dr.fastest_lap),
                        dr.quali_position,
                    ),
                )

                for team_id in driver_to_teams.get(driver_id, []):
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO score
                          (race_id, team_id, driver_id, points, breakdown)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            race_id,
                            team_id,
                            driver_id,
                            breakdown.total,
                            json.dumps(breakdown.as_dict()),
                        ),
                    )
                    team_race_points[team_id] = (
                        team_race_points.get(team_id, 0.0) + breakdown.total
                    )
                    full_name = driver_name_map.get(code, code)
                    surname = full_name.split()[-1] if full_name else code
                    team_driver_contrib.setdefault(team_id, []).append(
                        (surname, breakdown.total)
                    )

            await db.commit()
        except Exception:
            await db.rollback()
            raise

        # ── Post embed ─────────────────────────────────────────────────────
        await self._post_results_embed(
            db=db,
            guild_id=guild_id,
            results_channel_id=results_channel_id,
            round_name=round_name,
            race_type=race_type,
            team_race_points=team_race_points,
            team_driver_contrib=team_driver_contrib,
            driver_pts_map=driver_pts_map,
            driver_name_map=driver_name_map,
            fastest_lap_code=fastest_lap_code,
        )

        return True

    async def _post_results_embed(
        self,
        *,
        db,
        guild_id: int,
        results_channel_id: Optional[int],
        round_name: str,
        race_type: str,
        team_race_points: dict[int, float],
        team_driver_contrib: dict[int, list[tuple[str, float]]],
        driver_pts_map: dict[str, float],
        driver_name_map: dict[str, str],
        fastest_lap_code: Optional[str],
    ) -> None:
        """Build and send the results embed to the guild's results channel."""
        if not results_channel_id:
            return

        channel = self.bot.get_channel(results_channel_id)
        if channel is None:
            log.warning(
                "_post_results_embed: channel %d not found for guild %d",
                results_channel_id, guild_id,
            )
            return

        # Team display names
        async with db.execute(
            "SELECT id, user_name FROM team WHERE guild_id = ?", (guild_id,)
        ) as cur:
            team_name_map: dict[int, str] = {
                r["id"]: r["user_name"] for r in await cur.fetchall()
            }

        sorted_teams = sorted(
            team_race_points.items(), key=lambda kv: kv[1], reverse=True
        )

        label = _RACE_TYPE_LABEL.get(race_type, race_type.upper() + " RESULTS")
        title = f"🏁 {round_name.upper()} — {label.upper()}"

        lines: list[str] = []
        for rank, (team_id, total_pts) in enumerate(sorted_teams):
            user_name = team_name_map.get(team_id, f"Team {team_id}")
            
            # Formatted leaderboard line
            # Rank 1-3 get medals, otherwise plain text
            rank_display = _pos_emoji(rank) if rank < 3 else f"{rank+1:02}"
            line = f"{rank_display} **{user_name:<15}** | {total_pts:>3.0f} PTS"
            
            contribs = sorted(
                team_driver_contrib.get(team_id, []),
                key=lambda x: x[1], reverse=True,
            )
            # Filter non-zero contributors for cleaner view
            driving_text = ", ".join(
                f"{name}:+{pts:.0f}" for name, pts in contribs if pts != 0
            )
            if driving_text:
                line += f"\n   `{driving_text}`"
            lines.append(line)

        description = (
            "\n".join(lines) if lines else "```\nNO SCORES RECORDED\n```"
        )
        embed = info_embed(title, description)

        # Top stats as focused fields
        if driver_pts_map:
            top_code = max(driver_pts_map, key=lambda c: driver_pts_map[c])
            top_pts = driver_pts_map[top_code]
            top_name = driver_name_map.get(top_code, top_code)
            embed.add_field(
                name="TOP DRIVER",
                value=f"```\n{top_name}\n{top_pts:.0f} PTS\n```",
                inline=True,
            )

        if fastest_lap_code:
            fl_name = driver_name_map.get(fastest_lap_code, fastest_lap_code)
            embed.add_field(
                name="FASTEST LAP", 
                value=f"```\n{fl_name}\n```", 
                inline=True
            )

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.exception(
                "_post_results_embed: failed to post to channel %d (guild %d)",
                results_channel_id, guild_id,
            )

    # -----------------------------------------------------------------------
    # /race command group
    # -----------------------------------------------------------------------

    race_group = app_commands.Group(
        name="race",
        description="Race result commands",
    )

    @race_group.command(name="fetch", description="Manually score a specific round")
    @app_commands.describe(round_name="Race to score (unscored rounds only)")
    @is_admin()
    @league_exists()
    async def race_fetch(
        self, interaction: discord.Interaction, round_name: str
    ) -> None:
        """Admin override: immediately score the chosen round for this guild."""
        await interaction.response.defer(ephemeral=True)

        db = await get_db()

        async with db.execute(
            "SELECT round_number, name, sprint_date FROM calendar WHERE name = ?",
            (round_name,),
        ) as cur:
            cal_row = await cur.fetchone()

        if not cal_row:
            await interaction.followup.send(
                embed=error_embed(f"No calendar entry found for **{round_name}**."),
                ephemeral=True,
            )
            return

        rn: int = cal_row["round_number"]
        name: str = cal_row["name"]
        has_sprint: bool = cal_row["sprint_date"] is not None

        async with db.execute(
            "SELECT race_type FROM race WHERE guild_id = ? AND round_number = ?",
            (interaction.guild_id, rn),
        ) as cur:
            scored_types: set[str] = {r["race_type"] for r in await cur.fetchall()}

        messages: list[str] = []
        any_scored = False

        # Score sprint first (runs earlier on the race weekend)
        if has_sprint and "sprint" not in scored_types:
            ok = await self._score_round(
                rn,
                name,
                is_sprint=True,
                target_guild_id=interaction.guild_id,
            )
            if ok:
                messages.append(f"✅ Sprint scored for **{name}**.")
                any_scored = True
            else:
                messages.append(
                    f"⏳ Sprint results not yet available for **{name}**."
                )

        if "race" not in scored_types:
            ok = await self._score_round(
                rn,
                name,
                is_sprint=False,
                target_guild_id=interaction.guild_id,
            )
            if ok:
                messages.append(f"✅ Race scored for **{name}**.")
                any_scored = True
            else:
                messages.append(
                    f"⏳ Race results not yet available for **{name}**. "
                    "Try again later."
                )

        if not messages:
            messages.append(
                f"**{name}** has already been fully scored for this server."
            )

        body = "\n".join(messages)
        if any_scored:
            await interaction.followup.send(
                embed=success_embed(body), ephemeral=True
            )
        else:
            await interaction.followup.send(
                embed=info_embed("Fetch Status", body), ephemeral=True
            )

    @race_fetch.autocomplete("round_name")
    async def _round_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Offer only rounds that have at least one unscored event for this guild."""
        db = await get_db()

        async with db.execute(
            "SELECT round_number, name, sprint_date FROM calendar ORDER BY round_number"
        ) as cur:
            cal_rows = await cur.fetchall()

        async with db.execute(
            "SELECT round_number, race_type FROM race WHERE guild_id = ?",
            (interaction.guild_id,),
        ) as cur:
            scored_set: set[tuple[int, str]] = {
                (r["round_number"], r["race_type"]) for r in await cur.fetchall()
            }

        choices: list[app_commands.Choice[str]] = []
        for row in cal_rows:
            rn: int = row["round_number"]
            name: str = row["name"]
            has_sprint: bool = row["sprint_date"] is not None

            race_done = (rn, "race") in scored_set
            sprint_done = (rn, "sprint") in scored_set
            fully_scored = race_done and (not has_sprint or sprint_done)
            if fully_scored:
                continue

            if current and current.lower() not in name.lower():
                continue

            choices.append(app_commands.Choice(name=name, value=name))
            if len(choices) >= 25:
                break

        return choices


# ---------------------------------------------------------------------------
# Extension entry point
# ---------------------------------------------------------------------------


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ResultsCog(bot))
