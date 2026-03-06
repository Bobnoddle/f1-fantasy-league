from __future__ import annotations

import asyncio
import json
import random
from typing import Optional

import logging

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import config
from db.connection import get_db
from utils.checks import is_admin, league_exists, draft_active
from utils.embeds import error_embed, success_embed, info_embed

log = logging.getLogger(__name__)

# ── Constructor colour mapping ────────────────────────────────────────────────
_CONSTRUCTOR_EMOJI: dict[str, str] = {
    "ferrari": "🔴",
    "red bull": "🔵",
    "mercedes": "⬛",
    "mclaren": "🟠",
    "aston martin": "🟢",
    "alpine": "💙",
    "williams": "🔵",
    "rb": "🔵",
    "racing bulls": "🔵",
    "haas": "⚪",
    "kick sauber": "🟢",
    "sauber": "🟢",
}


def _team_emoji(team_name: str) -> str:
    return _CONSTRUCTOR_EMOJI.get(team_name.lower(), "🏎️")


# ── Pure helpers ──────────────────────────────────────────────────────────────

def calc_team_size(total_drivers: int, player_count: int, override: int | None) -> int:
    if override:
        return override
    return min(10, total_drivers // player_count)


def generate_snake_order(team_ids: list[int], rounds: int) -> list[int]:
    order: list[int] = []
    for r in range(rounds):
        if r % 2 == 0:
            order.extend(team_ids)
        else:
            order.extend(reversed(team_ids))
    return order


# ── Views ─────────────────────────────────────────────────────────────────────

class JoinView(discord.ui.View):
    """Persistent join / start-draft button view posted by /draft open."""

    def __init__(self, cog: "DraftCog", guild_id: int) -> None:
        super().__init__(timeout=None)  # stays alive until draft starts
        self.cog = cog
        self.guild_id = guild_id

        join_btn = discord.ui.Button(
            label="🏎️ Join",
            style=discord.ButtonStyle.primary,
            custom_id=f"{guild_id}:draft_join",
        )
        start_btn = discord.ui.Button(
            label="🚀 Start (Admin)",
            style=discord.ButtonStyle.success,
            custom_id=f"{guild_id}:draft_start",
        )
        join_btn.callback = self._join_callback
        start_btn.callback = self._start_callback
        self.add_item(join_btn)
        self.add_item(start_btn)

    # ── join ──────────────────────────────────────────────────────────────────

    async def _join_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = await get_db()

        # Check the draft is still open
        async with db.execute(
            "SELECT status FROM draft_state WHERE guild_id = ?", (self.guild_id,)
        ) as cur:
            state = await cur.fetchone()

        if state is None or state["status"] != "open":
            await interaction.followup.send(
                embed=error_embed("The draft is not open for joining right now."),
                ephemeral=True,
            )
            return

        user = interaction.user
        try:
            await db.execute(
                """
                INSERT OR IGNORE INTO team (guild_id, user_id, user_name)
                VALUES (?, ?, ?)
                """,
                (self.guild_id, user.id, user.display_name),
            )
            await db.commit()
        except Exception as exc:
            await interaction.followup.send(
                embed=error_embed(f"Database error: {exc}"), ephemeral=True
            )
            return

        # Re-fetch the player list and update the join embed
        async with db.execute(
            "SELECT user_name FROM team WHERE guild_id = ? ORDER BY id",
            (self.guild_id,),
        ) as cur:
            teams = await cur.fetchall()

        player_count = len(teams)
        names_list = ", ".join(t["user_name"] for t in teams)

        # Compute indicative team size
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM driver WHERE active = 1"
        ) as cur:
            driver_row = await cur.fetchone()
        total_drivers = driver_row["cnt"] if driver_row else 22

        async with db.execute(
            "SELECT team_size, draft_timeout FROM league WHERE guild_id = ?", (self.guild_id,)
        ) as cur:
            league_row = await cur.fetchone()
        ts_override = league_row["team_size"] if league_row else None
        draft_timeout = league_row["draft_timeout"] if league_row else None
        indicative_size = calc_team_size(total_drivers, max(player_count, 1), ts_override)

        # Edit the join message
        new_embed = _build_join_embed(
            config.SEASON_YEAR,
            player_count,
            names_list,
            indicative_size,
            draft_timeout=draft_timeout,
        )
        try:
            await interaction.message.edit(embed=new_embed)  # type: ignore[union-attr]
        except discord.HTTPException:
            pass

        await interaction.followup.send(
            embed=success_embed(
                f"You've joined the draft! (**{player_count}** player{'' if player_count == 1 else 's'} signed up)"
            ),
            ephemeral=True,
        )

    # ── start ─────────────────────────────────────────────────────────────────

    async def _start_callback(self, interaction: discord.Interaction) -> None:
        # Admin check
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=error_embed("Only admins can start the draft."), ephemeral=True
            )
            return

        await interaction.response.defer()

        db = await get_db()

        # Verify status is still 'open'
        async with db.execute(
            "SELECT status FROM draft_state WHERE guild_id = ?", (self.guild_id,)
        ) as cur:
            state = await cur.fetchone()

        if state is None or state["status"] != "open":
            await interaction.followup.send(
                embed=error_embed("Draft is not in the open state."), ephemeral=True
            )
            return

        # Need ≥ 2 players
        async with db.execute(
            "SELECT id, user_id, user_name FROM team WHERE guild_id = ? ORDER BY id",
            (self.guild_id,),
        ) as cur:
            raw_teams = await cur.fetchall()

        if len(raw_teams) < 2:
            await interaction.followup.send(
                embed=error_embed("At least **2 players** must join before the draft can start."),
                ephemeral=True,
            )
            return

        teams = list(raw_teams)
        random.shuffle(teams)

        # Persist randomised draft order
        for idx, t in enumerate(teams):
            await db.execute(
                "UPDATE team SET draft_order = ? WHERE id = ?", (idx + 1, t["id"])
            )

        # Driver / team-size calculations
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM driver WHERE active = 1"
        ) as cur:
            driver_row = await cur.fetchone()
        total_drivers: int = driver_row["cnt"] if driver_row else 22

        async with db.execute(
            "SELECT team_size, draft_timeout FROM league WHERE guild_id = ?",
            (self.guild_id,),
        ) as cur:
            league_row = await cur.fetchone()

        ts_override = league_row["team_size"] if league_row else None
        player_count = len(teams)
        team_size = calc_team_size(total_drivers, player_count, ts_override)
        total_picks = team_size * player_count

        team_ids = [t["id"] for t in teams]
        pick_order = generate_snake_order(team_ids, team_size)

        await db.execute(
            """
            UPDATE draft_state
            SET    status           = 'active',
                   current_pick    = 0,
                   total_picks     = ?,
                   pick_order_json = ?
            WHERE  guild_id = ?
            """,
            (total_picks, json.dumps(pick_order), self.guild_id),
        )
        await db.commit()

        # Disable the Join / Start buttons
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[union-attr]
        try:
            await interaction.message.edit(view=self)  # type: ignore[union-attr]
        except discord.HTTPException:
            pass

        # Build order-reveal embed
        name_map = {t["id"]: t["user_name"] for t in teams}
        rounds_desc_lines: list[str] = []
        for r in range(team_size):
            if r % 2 == 0:
                order_slice = team_ids
            else:
                order_slice = list(reversed(team_ids))
            names = " → ".join(name_map[tid] for tid in order_slice)
            suffix = " ← *reversed*" if r % 2 != 0 else ""
            rounds_desc_lines.append(f"Round {r + 1}: {names}{suffix}")

        reveal_desc = (
            "\n".join(rounds_desc_lines)
            + f"\n\n"
            f"Total picks: **{total_picks}** • "
            f"**{team_size}** rounds • "
            f"**{player_count}** players\n"
            f"Drivers/team: **{team_size}** (floor({total_drivers} ÷ {player_count}))\n\n"
            f"*First pick in 10 seconds…*"
        )
        reveal_embed = info_embed("🎲 DRAFT ORDER (Randomised)", reveal_desc)
        channel = interaction.channel
        await channel.send(embed=reveal_embed)  # type: ignore[union-attr]

        await asyncio.sleep(10)
        await self.cog._advance_turn(self.guild_id, channel)  # type: ignore[arg-type]


# ── Available-driver Select view ──────────────────────────────────────────────

class DriverSelectView(discord.ui.View):
    """Dropdown of available drivers attached to the live draft embed."""

    def __init__(
        self,
        cog: "DraftCog",
        guild_id: int,
        team_id: int,
        available_drivers: list,
        pick_index: int,
        draft_channel_id: int | None,
        draft_timeout: int,
    ) -> None:
        super().__init__(timeout=float(draft_timeout))
        self.cog = cog
        self.guild_id = guild_id
        self.team_id = team_id
        self.pick_index = pick_index
        self.draft_channel_id = draft_channel_id
        # Store first driver in pool for auto-pick on timeout
        self._available = available_drivers

        # Discord hard-limits Select menus to 25 options
        options = [
            discord.SelectOption(
                label=f"{d['name']} ({d['code']})",
                value=str(d["id"]),
                description=d["team_name"],
            )
            for d in available_drivers[:25]
        ]
        if not options:
            return  # nothing to add — edge case

        placeholder = "Select a driver to pick…"
        if len(available_drivers) > 25:
            placeholder = "Select a driver (use /draft pick for others)…"

        select = discord.ui.Select(
            placeholder=placeholder,
            options=options,
            custom_id=f"{guild_id}:driver_select",
        )
        select.callback = self._select_callback
        self.add_item(select)

    async def _select_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        select = self.children[0]  # type: ignore[index]
        driver_id = int(select.values[0])  # type: ignore[union-attr]

        # Validate it's still this team's turn
        db = await get_db()
        async with db.execute(
            "SELECT current_pick, pick_order_json FROM draft_state WHERE guild_id = ?",
            (self.guild_id,),
        ) as cur:
            state = await cur.fetchone()

        if state is None or state["current_pick"] != self.pick_index:
            await interaction.followup.send(
                embed=error_embed("It's no longer your turn."), ephemeral=True
            )
            return
        pick_order = json.loads(state["pick_order_json"])
        expected_team = pick_order[self.pick_index]
        if expected_team != self.team_id:
            await interaction.followup.send(
                embed=error_embed("It's not your turn to pick."), ephemeral=True
            )
            return

        # Verify the interacting user actually owns this team
        async with db.execute(
            "SELECT user_id FROM team WHERE id = ?", (self.team_id,)
        ) as cur:
            team_owner = await cur.fetchone()
        if team_owner is None or interaction.user.id != team_owner["user_id"]:
            await interaction.followup.send(
                embed=error_embed("It's not your turn."), ephemeral=True
            )
            return

        self.stop()
        self.cog._cancel_timeout(self.guild_id)
        await self.cog._do_pick(
            self.guild_id, self.team_id, driver_id, interaction.channel  # type: ignore[arg-type]
        )

    async def on_timeout(self) -> None:
        """Auto-pick a random remaining driver when time expires."""
        db = await get_db()
        # Re-confirm the pick hasn't already been made
        async with db.execute(
            "SELECT current_pick FROM draft_state WHERE guild_id = ?",
            (self.guild_id,),
        ) as cur:
            state = await cur.fetchone()
        if state is None or state["current_pick"] != self.pick_index:
            return

        if not self._available:
            return
        auto_driver = random.choice(self._available)

        # Post timeout notice — we need a channel; fetch via bot
        guild = self.cog.bot.get_guild(self.guild_id)
        channel = None
        if guild:
            async with db.execute(
                "SELECT results_channel_id FROM league WHERE guild_id = ?",
                (self.guild_id,),
            ) as cur:
                row = await cur.fetchone()
            if row and row["results_channel_id"]:
                channel = guild.get_channel(row["results_channel_id"])

        if channel is None:
            if self.draft_channel_id is not None:
                channel = self.cog.bot.get_channel(self.draft_channel_id)
        if channel is None:
            return

        # Look up user for the team
        async with db.execute(
            "SELECT user_id FROM team WHERE id = ?", (self.team_id,)
        ) as cur:
            team_row = await cur.fetchone()
        ping = f"<@{team_row['user_id']}>" if team_row else "Unknown player"

        await channel.send(  # type: ignore[union-attr]
            f"⏰ {ping} ran out of time! Auto-picked: **{auto_driver['name']}** ({auto_driver['team_name']})"
        )
        self.cog._cancel_timeout(self.guild_id)
        await self.cog._do_pick(
            self.guild_id, self.team_id, auto_driver["id"], channel  # type: ignore[arg-type]
        )


# ── Embed builders ────────────────────────────────────────────────────────────

def _build_join_embed(
    season_year: int,
    player_count: int,
    names_str: str,
    indicative_size: int,
    draft_timeout: Optional[int] = None,
) -> discord.Embed:
    timeout = draft_timeout or config.DRAFT_TIMEOUT
    if player_count == 0:
        player_text = "*No one yet — be the first!*"
    else:
        player_text = names_str
    desc = (
        f"The draft is open! Click the button below to join.\n"
        f"**Players signed up: {player_count}**\n\n"
        f"{player_text}\n\n"
        f"__Settings (calculated at start):__\n"
        f"• Drivers/team: auto (floor(drivers ÷ players), max 10)\n"
        f"• {timeout}s pick timeout ({timeout // 60} minutes)\n"
        f"• Snake order (randomised at start)"
    )
    return info_embed(f"🏁 F1 FANTASY DRAFT {season_year}", desc)


def _build_board_embed(
    season_year: int,
    current_pick: int,
    total_picks: int,
    team_size: int,
    player_count: int,
    current_user_id: int,
    current_user_name: str,
    available_drivers: list,
    draft_timeout: Optional[int] = None,
    extra_note: str = "",
) -> discord.Embed:
    timeout = draft_timeout or config.DRAFT_TIMEOUT
    round_num = current_pick // player_count + 1
    pick_in_round = current_pick % player_count + 1

    # Group available drivers by constructor
    by_team: dict[str, list[str]] = {}
    for d in available_drivers:
        by_team.setdefault(d["team_name"], []).append(d["code"])

    driver_lines: list[str] = []
    for constructor, codes in sorted(by_team.items()):
        emoji = _team_emoji(constructor)
        driver_lines.append(f"{emoji} **{constructor}:** {' · '.join(codes)}")

    available_block = "\n".join(driver_lines) if driver_lines else "*No drivers remaining*"
    if len(available_drivers) > 25:
        available_block += "\n*(use `/draft pick` to pick a driver not shown)*"

    desc = (
        f"**🏎️ Round {round_num} — Pick {pick_in_round} of {player_count}**\n"
        f"<@{current_user_id}>, it's your turn!\n"
        f"⏱️ Time remaining: {timeout // 60}:{timeout % 60:02d}\n\n"
        f"── Available Drivers ──\n{available_block}\n\n"
        f"*Or type: `/draft pick <driver_name>`*"
    )
    if extra_note:
        desc += f"\n\n{extra_note}"
    return info_embed(f"🏁 F1 FANTASY DRAFT {season_year}", desc)


def _build_final_embed(
    season_year: int, rosters: dict[str, list[str]]
) -> discord.Embed:
    lines: list[str] = []
    for user_name, drivers in rosters.items():
        lines.append(f"**{user_name}**")
        for d in drivers:
            lines.append(f"  • {d}")
    desc = "\n".join(lines) if lines else "No rosters found."
    return info_embed(f"🏆 DRAFT COMPLETE! — {season_year}", desc)


# ── Cog ───────────────────────────────────────────────────────────────────────

class DraftCog(commands.Cog, name="Draft"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Tracks pending pick-timeout tasks keyed by guild_id
        self._timeout_tasks: dict[int, asyncio.Task] = {}

    async def cog_load(self) -> None:
        """Re-register persistent views for open/active drafts after a restart.

        Without this, any button or select posted before a bot restart will
        produce "This interaction failed" because discord.py has no handler
        for the interaction — the view object was lost from memory.
        """
        async def _restore() -> None:
            await self.bot.wait_until_ready()
            db = await get_db()

            # ── Re-register JoinViews for 'open' drafts ───────────────────
            async with db.execute(
                "SELECT guild_id FROM draft_state WHERE status = 'open'"
            ) as cur:
                open_rows = await cur.fetchall()
            for row in open_rows:
                self.bot.add_view(JoinView(self, row["guild_id"]))
                log.info(
                    "cog_load: restored JoinView for guild %d", row["guild_id"]
                )

            # ── Re-register DriverSelectViews for 'active' drafts ─────────
            async with db.execute(
                """
                SELECT guild_id, current_pick, pick_order_json
                FROM   draft_state
                WHERE  status = 'active'
                """
            ) as cur:
                active_rows = await cur.fetchall()

            for row in active_rows:
                guild_id: int = row["guild_id"]
                current_pick: int = row["current_pick"]
                pick_order: list[int] = json.loads(row["pick_order_json"] or "[]")
                if not pick_order or current_pick >= len(pick_order):
                    continue
                team_id: int = pick_order[current_pick]

                async with db.execute(
                    "SELECT draft_timeout FROM league WHERE guild_id = ?",
                    (guild_id,),
                ) as cur2:
                    league_row = await cur2.fetchone()
                draft_timeout: int = (
                    league_row["draft_timeout"]
                    if league_row and league_row["draft_timeout"]
                    else config.DRAFT_TIMEOUT
                )

                available = await self._fetch_available_drivers(guild_id)
                view = DriverSelectView(
                    cog=self,
                    guild_id=guild_id,
                    team_id=team_id,
                    available_drivers=available,
                    pick_index=current_pick,
                    draft_channel_id=None,
                    draft_timeout=draft_timeout,
                )
                self.bot.add_view(view)
                log.info(
                    "cog_load: restored DriverSelectView for guild %d (pick %d)",
                    guild_id,
                    current_pick,
                )

        asyncio.create_task(_restore())

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _cancel_timeout(self, guild_id: int) -> None:
        task = self._timeout_tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()

    async def _send_with_retry(
        self,
        channel: discord.abc.Messageable,
        **kwargs,
    ) -> discord.Message | None:
        """Send a message with retry logic for transient Discord API errors.
        
        Handles 503, 504, and connection errors with exponential backoff.
        Returns message if successful, None if all retries exhausted.
        """
        max_retries = 3
        base_delay = 1
        
        for attempt in range(max_retries):
            try:
                return await channel.send(**kwargs)  # type: ignore[return-value]
            except (discord.DiscordServerError, discord.HTTPException) as e:
                # Transient errors: 503 Service Unavailable, 504 Gateway Timeout
                is_transient = (
                    (isinstance(e, discord.DiscordServerError) and e.status in (503, 504))
                    or (isinstance(e, discord.HTTPException) and e.status in (503, 504, 429))
                )
                
                if not is_transient or attempt >= max_retries - 1:
                    log.exception(
                        "Failed to send message (attempt %d/%d): %s",
                        attempt + 1,
                        max_retries,
                        e,
                    )
                    return None
                
                delay = base_delay * (2 ** attempt)
                log.warning(
                    "Transient error on send (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as e:
                log.exception("Unexpected error on send: %s", e)
                return None
        
        return None

    async def _fetch_available_drivers(self, guild_id: int) -> list:
        db = await get_db()
        async with db.execute(
            """
            SELECT d.id, d.code, d.name, d.team_name
            FROM   driver d
            WHERE  d.active = 1
              AND  d.id NOT IN (
                  SELECT driver_id FROM roster WHERE guild_id = ?
              )
            ORDER BY d.team_name, d.name
            """,
            (guild_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _do_pick(
        self,
        guild_id: int,
        team_id: int,
        driver_id: int,
        channel: discord.abc.Messageable,
    ) -> None:
        """Record a pick, advance the cursor, then trigger next turn or finish."""
        db = await get_db()

        async with db.execute(
            "SELECT current_pick, total_picks FROM draft_state WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            state = await cur.fetchone()
        if state is None:
            return
        pick_number: int = state["current_pick"]
        total_picks: int = state["total_picks"]

        try:
            await db.execute(
                """
                INSERT INTO roster (guild_id, team_id, driver_id, pick_number)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, team_id, driver_id, pick_number),
            )
        except aiosqlite.IntegrityError:
            # Race condition — driver was just claimed by a concurrent pick
            await channel.send(  # type: ignore[union-attr]
                embed=error_embed(
                    "⚡ That driver was just picked by another player! Please select again."
                )
            )
            await self._advance_turn(guild_id, channel)
            return
        await db.execute(
            "UPDATE draft_state SET current_pick = current_pick + 1 WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()

        if pick_number + 1 >= total_picks:
            await self._finish_draft(guild_id, channel)
        else:
            await self._advance_turn(guild_id, channel)

    async def _advance_turn(
        self, guild_id: int, channel: discord.abc.Messageable
    ) -> None:
        """Post the next-player ping, edit the draft embed, attach a select view."""
        db = await get_db()

        async with db.execute(
            """
            SELECT ds.current_pick, ds.total_picks, ds.pick_order_json, ds.message_id,
                   l.team_size, l.draft_timeout, l.season_year
            FROM   draft_state ds
            JOIN   league l ON l.guild_id = ds.guild_id
            WHERE  ds.guild_id = ?
            """,
            (guild_id,),
        ) as cur:
            state = await cur.fetchone()
        if state is None:
            return

        current_pick: int = state["current_pick"]
        total_picks: int = state["total_picks"]
        pick_order: list[int] = json.loads(state["pick_order_json"])
        team_size: int = state["team_size"]
        season_year: int = state["season_year"]
        draft_timeout: int = state["draft_timeout"] or config.DRAFT_TIMEOUT

        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM team WHERE guild_id = ?", (guild_id,)
        ) as cur:
            count_row = await cur.fetchone()
        player_count: int = count_row["cnt"] if count_row else 1

        current_team_id = pick_order[current_pick]

        async with db.execute(
            "SELECT user_id, user_name FROM team WHERE id = ?", (current_team_id,)
        ) as cur:
            team_row = await cur.fetchone()
        if team_row is None:
            return

        current_user_id: int = team_row["user_id"]
        current_user_name: str = team_row["user_name"]

        available = await self._fetch_available_drivers(guild_id)

        embed = _build_board_embed(
            season_year=season_year,
            current_pick=current_pick,
            total_picks=total_picks,
            team_size=team_size,
            player_count=player_count,
            current_user_id=current_user_id,
            current_user_name=current_user_name,
            available_drivers=available,
            draft_timeout=draft_timeout,
        )
        view = DriverSelectView(
            cog=self,
            guild_id=guild_id,
            team_id=current_team_id,
            available_drivers=available,
            pick_index=current_pick,
            draft_channel_id=getattr(channel, "id", None),
            draft_timeout=draft_timeout,
        )

        # Attempt to edit the original draft message if we know its ID
        message_id: Optional[int] = state["message_id"]
        if message_id and hasattr(channel, "fetch_message"):
            try:
                msg = await channel.fetch_message(message_id)  # type: ignore[union-attr]
                await msg.edit(embed=embed, view=view)
            except (discord.NotFound, discord.HTTPException):
                msg = await channel.send(embed=embed, view=view)  # type: ignore[union-attr]
                await db.execute(
                    "UPDATE draft_state SET message_id = ? WHERE guild_id = ?",
                    (msg.id, guild_id),
                )
                await db.commit()
        else:
            msg = await channel.send(embed=embed, view=view)  # type: ignore[union-attr]
            await db.execute(
                "UPDATE draft_state SET message_id = ? WHERE guild_id = ?",
                (msg.id, guild_id),
            )
            await db.commit()

        # Separate ping so the user gets a notification
        await self._send_with_retry(
            channel,  # type: ignore[arg-type]
            content=f"<@{current_user_id}>, it's your turn to pick!"
        )

        # Schedule timeout task — cancels itself if _do_pick is called first
        self._cancel_timeout(guild_id)  # cancel any previous (safety)
        task = asyncio.create_task(
            self._pick_timeout_background(guild_id, current_pick, view, draft_timeout)
        )
        self._timeout_tasks[guild_id] = task

    async def _pick_timeout_background(
        self,
        guild_id: int,
        pick_index: int,
        view: DriverSelectView,
        draft_timeout: int,
    ) -> None:
        """Background task — fires view.on_timeout if pick hasn't been made."""
        try:
            await asyncio.sleep(draft_timeout)
        except asyncio.CancelledError:
            return
        # Check whether pick index is still the same (not already picked)
        db = await get_db()
        async with db.execute(
            "SELECT current_pick FROM draft_state WHERE guild_id = ?", (guild_id,)
        ) as cur:
            state = await cur.fetchone()
        if state is None or state["current_pick"] != pick_index:
            return
        # Trigger the view's timeout handler
        view.stop()
        await view.on_timeout()

    async def _finish_draft(self, guild_id: int, channel: discord.abc.Messageable) -> None:
        """Mark draft complete and post the full rosters embed."""
        db = await get_db()
        await db.execute(
            "UPDATE draft_state SET status = 'complete' WHERE guild_id = ?",
            (guild_id,),
        )
        await db.commit()

        # Build rosters dict: user_name → list of 'Name (CODE)' strings
        async with db.execute(
            """
            SELECT t.user_name,
                   d.name   AS driver_name,
                   d.code   AS driver_code,
                   d.team_name,
                   r.pick_number
            FROM   roster r
            JOIN   team   t ON t.id = r.team_id
            JOIN   driver d ON d.id = r.driver_id
            WHERE  r.guild_id = ?
            ORDER  BY t.draft_order, r.pick_number
            """,
            (guild_id,),
        ) as cur:
            rows = await cur.fetchall()

        rosters: dict[str, list[str]] = {}
        for row in rows:
            entry = f"{row['driver_name']} ({row['driver_code']}) — {row['team_name']}"
            rosters.setdefault(row["user_name"], []).append(entry)

        async with db.execute(
            "SELECT season_year FROM league WHERE guild_id = ?", (guild_id,)
        ) as cur:
            league_row = await cur.fetchone()
        season_year = league_row["season_year"] if league_row else config.SEASON_YEAR

        embed = _build_final_embed(season_year, rosters)
        await channel.send(embed=embed)  # type: ignore[union-attr]

    # ── Slash commands ────────────────────────────────────────────────────────

    draft_group = app_commands.Group(
        name="draft", description="F1 Fantasy snake draft commands"
    )

    # /draft open ──────────────────────────────────────────────────────────────

    @draft_group.command(name="open", description="Open the draft for players to join")
    @is_admin()
    @league_exists()
    async def draft_open(self, interaction: discord.Interaction) -> None:
        db = await get_db()
        async with db.execute(
            "SELECT status FROM draft_state WHERE guild_id = ?",
            (interaction.guild_id,),
        ) as cur:
            state = await cur.fetchone()

        if state is None or state["status"] != "pending":
            if state is not None and state["status"] == "complete":
                msg = "Season already drafted. Run `/league reset` to start a new season."
            else:
                msg = (
                    "The draft is already open or active. "
                    "Run `/league reset` to start fresh."
                )
            await interaction.response.send_message(
                embed=error_embed(msg),
                ephemeral=True,
            )
            return

        await db.execute(
            "UPDATE draft_state SET status = 'open' WHERE guild_id = ?",
            (interaction.guild_id,),
        )
        await db.commit()

        async with db.execute(
            "SELECT season_year, draft_timeout FROM league WHERE guild_id = ?",
            (interaction.guild_id,),
        ) as cur:
            league_row = await cur.fetchone()

        season_year = league_row["season_year"] if league_row else config.SEASON_YEAR
        draft_timeout = league_row["draft_timeout"] if league_row else None

        join_embed = _build_join_embed(
            season_year,
            0,
            "",
            0,
            draft_timeout=draft_timeout,
        )
        view = JoinView(self, interaction.guild_id)  # type: ignore[arg-type]

        # Send the join message to the channel (visible to all)
        msg = await interaction.channel.send(embed=join_embed, view=view)  # type: ignore[union-attr]
        await db.execute(
            "UPDATE draft_state SET message_id = ? WHERE guild_id = ?",
            (msg.id, interaction.guild_id),
        )
        await db.commit()

        await interaction.response.send_message(
            embed=success_embed("Draft opened! Players can now join."),
            ephemeral=True,
        )

    # /draft pick ──────────────────────────────────────────────────────────────

    @draft_group.command(
        name="pick", description="Pick a driver during the active draft"
    )
    @league_exists()
    @draft_active()
    @app_commands.describe(driver="Driver name or code to pick")
    async def draft_pick(
        self, interaction: discord.Interaction, driver: str
    ) -> None:
        await interaction.response.defer()
        db = await get_db()
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        async with db.execute(
            "SELECT current_pick, total_picks, pick_order_json FROM draft_state WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            state = await cur.fetchone()
        if state is None:
            await interaction.followup.send(
                embed=error_embed("Draft state not found."), ephemeral=True
            )
            return

        pick_order: list[int] = json.loads(state["pick_order_json"])
        current_pick: int = state["current_pick"]
        current_team_id = pick_order[current_pick]

        # Check it's the user's turn
        async with db.execute(
            "SELECT id FROM team WHERE guild_id = ? AND user_id = ? AND id = ?",
            (guild_id, user_id, current_team_id),
        ) as cur:
            team_row = await cur.fetchone()

        if team_row is None:
            await interaction.followup.send(
                embed=error_embed("It's not your turn to pick."), ephemeral=True
            )
            return

        # Find the driver (by name or code, case-insensitive)
        token = driver.strip()
        async with db.execute(
            """
            SELECT d.id, d.code, d.name, d.team_name
            FROM   driver d
            WHERE  d.active = 1
              AND  d.id NOT IN (
                  SELECT driver_id FROM roster WHERE guild_id = ?
              )
              AND  (LOWER(d.name) LIKE LOWER(?) OR LOWER(d.code) = LOWER(?))
            LIMIT  1
            """,
            (guild_id, f"%{token}%", token),
        ) as cur:
            driver_row = await cur.fetchone()

        if driver_row is None:
            await interaction.followup.send(
                embed=error_embed(
                    f"Driver **{token}** not found or already picked. "
                    "Use autocomplete to see available drivers."
                ),
                ephemeral=True,
            )
            return

        self._cancel_timeout(guild_id)  # type: ignore[arg-type]
        await self._do_pick(
            guild_id,  # type: ignore[arg-type]
            current_team_id,
            driver_row["id"],
            interaction.channel,  # type: ignore[arg-type]
        )
        await interaction.followup.send(
            embed=success_embed(
                f"✅ **{interaction.user.display_name}** picked "
                f"**{driver_row['name']}** ({driver_row['code']})!"
            )
        )

    @draft_pick.autocomplete("driver")
    async def draft_pick_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild_id = interaction.guild_id
        if guild_id is None:
            return []
        db = await get_db()
        token = current.strip()
        async with db.execute(
            """
            SELECT d.code, d.name, d.team_name
            FROM   driver d
            WHERE  d.active = 1
              AND  d.id NOT IN (
                  SELECT driver_id FROM roster WHERE guild_id = ?
              )
              AND  (? = '' OR LOWER(d.name) LIKE LOWER(?) OR LOWER(d.code) LIKE LOWER(?))
            ORDER  BY d.team_name, d.name
            LIMIT  25
            """,
            (guild_id, token, f"%{token}%", f"%{token}%"),
        ) as cur:
            rows = await cur.fetchall()
        return [
            app_commands.Choice(
                name=f"{r['name']} ({r['code']}) — {r['team_name']}",
                value=r["name"],
            )
            for r in rows
        ]

    # /draft status ────────────────────────────────────────────────────────────

    @draft_group.command(
        name="status", description="Show the current state of the draft"
    )
    @league_exists()
    async def draft_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        db = await get_db()
        guild_id = interaction.guild_id

        async with db.execute(
            """
            SELECT ds.status, ds.current_pick, ds.total_picks,
                   ds.pick_order_json, l.team_size, l.season_year
            FROM   draft_state ds
            JOIN   league l ON l.guild_id = ds.guild_id
            WHERE  ds.guild_id = ?
            """,
            (guild_id,),
        ) as cur:
            state = await cur.fetchone()

        if state is None:
            await interaction.followup.send(
                embed=error_embed("Run `/league setup` first."), ephemeral=True
            )
            return

        status: str = state["status"]
        season_year: int = state["season_year"]

        if status == "pending":
            await interaction.followup.send(
                embed=info_embed(
                    f"🏁 Draft — {season_year}",
                    "Draft hasn't started yet. An admin can run `/draft open` to begin.",
                )
            )
            return

        if status == "open":
            async with db.execute(
                "SELECT user_name FROM team WHERE guild_id = ? ORDER BY id",
                (guild_id,),
            ) as cur:
                teams = await cur.fetchall()
            names = ", ".join(t["user_name"] for t in teams) or "*None yet*"
            await interaction.followup.send(
                embed=info_embed(
                    f"🏁 Draft — {season_year}",
                    f"Draft is **open** for joining.\n\n"
                    f"**Players signed up ({len(teams)}):** {names}\n\n"
                    f"Wait for an admin to click 🚀 **Start** on the join message.",
                )
            )
            return

        # active or complete — build board
        async with db.execute(
            """
            SELECT t.user_name, d.name AS driver_name, d.code,
                   d.team_name, r.pick_number
            FROM   roster r
            JOIN   team   t ON t.id = r.team_id
            JOIN   driver d ON d.id = r.driver_id
            WHERE  r.guild_id = ?
            ORDER  BY t.draft_order, r.pick_number
            """,
            (guild_id,),
        ) as cur:
            roster_rows = await cur.fetchall()

        rosters: dict[str, list[str]] = {}
        for row in roster_rows:
            entry = f"{row['driver_name']} ({row['code']})"
            rosters.setdefault(row["user_name"], []).append(entry)

        if status == "complete":
            embed = _build_final_embed(season_year, rosters)
            await interaction.followup.send(embed=embed)
            return

        # active
        pick_order: list[int] = json.loads(state["pick_order_json"])
        current_pick: int = state["current_pick"]
        total_picks: int = state["total_picks"]
        current_team_id = pick_order[current_pick]

        async with db.execute(
            "SELECT user_id, user_name FROM team WHERE id = ?", (current_team_id,)
        ) as cur:
            cur_team = await cur.fetchone()

        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM team WHERE guild_id = ?", (guild_id,)
        ) as cur:
            cnt_row = await cur.fetchone()
        player_count = cnt_row["cnt"] if cnt_row else 1

        available = await self._fetch_available_drivers(guild_id)
        ts = state["team_size"] or calc_team_size(len(available) + current_pick, player_count, None)

        board_lines: list[str] = []
        for user_name, drivers in rosters.items():
            board_lines.append(f"**{user_name}:** {', '.join(drivers)}")
        board_text = "\n".join(board_lines) if board_lines else "*No picks yet*"

        embed = _build_board_embed(
            season_year=season_year,
            current_pick=current_pick,
            total_picks=total_picks,
            team_size=ts,
            player_count=player_count,
            current_user_id=cur_team["user_id"] if cur_team else 0,
            current_user_name=cur_team["user_name"] if cur_team else "?",
            available_drivers=available,
            extra_note=f"__Current rosters:__\n{board_text}",
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DraftCog(bot))
