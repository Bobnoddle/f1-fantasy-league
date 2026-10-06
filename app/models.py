"""SQLAlchemy ORM models.

One-to-one with ``db/schema.sql``. Multi-tenant throughout: every league-scoped
row carries league_id, so a self-hosted database is just one containing a single
league. No tier concept appears anywhere.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Player(Base):
    """Identity, independent of Discord.

    ``provider='discord'`` rows carry a snowflake. ``provider='guest'`` rows
    have no external id at all, which is what makes a league with no Discord
    integration possible without special-casing anywhere downstream.
    """

    __tablename__ = "player"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="guest")
    external_id: Mapped[str | None] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("provider IN ('discord','guest')", name="player_provider_check"),
        CheckConstraint(
            "provider <> 'discord' OR external_id IS NOT NULL", name="player_external_check"
        ),
        Index(
            "player_discord_key",
            "provider",
            "external_id",
            unique=True,
            postgresql_where=text("provider = 'discord'"),
        ),
    )


class League(Base):
    __tablename__ = "league"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="created")
    team_size: Mapped[int | None] = mapped_column(Integer)
    pick_deadline: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    admin_player_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("player.id")
    )
    webhook_id: Mapped[str | None] = mapped_column(String(32))
    webhook_token: Mapped[str | None] = mapped_column(Text)  # encrypted at rest
    signup_message_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "state IN ('created','signup_open','draft_ready','drafting','active','archived')",
            name="league_state_check",
        ),
        Index("league_state_idx", "state"),
        Index("league_season_idx", "season_year"),
    )

    teams: Mapped[list[Team]] = relationship(back_populates="league", cascade="all, delete-orphan")
    draft: Mapped[Draft | None] = relationship(
        back_populates="league", cascade="all, delete-orphan"
    )


class Driver(Base):
    """Season-scoped. Keyed on (season_year, code) so seasons never bleed."""

    __tablename__ = "driver"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    season_year: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    constructor: Mapped[str] = mapped_column(String(80), nullable=False, default="Unknown")

    __table_args__ = (UniqueConstraint("season_year", "code", name="driver_season_code_key"),)


class Team(Base):
    __tablename__ = "team"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    league_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("league.id", ondelete="CASCADE"), nullable=False
    )
    player_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("player.id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    draft_order: Mapped[int | None] = mapped_column(Integer)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("league_id", "player_id", name="team_league_player_key"),
        Index("team_league_idx", "league_id"),
        Index("team_player_idx", "player_id"),
    )

    league: Mapped[League] = relationship(back_populates="teams")
    player: Mapped[Player] = relationship()
    roster: Mapped[list[Roster]] = relationship(back_populates="team", cascade="all, delete-orphan")


class Roster(Base):
    """The double-pick guard lives here, not in application code.

    Two players racing for the same driver resolve at the storage layer: one
    transaction commits, the other gets an IntegrityError.
    """

    __tablename__ = "roster"

    league_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("league.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team.id", ondelete="CASCADE"), primary_key=True
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver.id"), nullable=False
    )
    pick_number: Mapped[int] = mapped_column(Integer, nullable=False)
    picked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    auto_picked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    team: Mapped[Team] = relationship(back_populates="roster")
    driver: Mapped[Driver] = relationship()


class Draft(Base):
    """Draft state. ``pick_expires_at`` is the clock — nothing runs in memory."""

    __tablename__ = "draft"

    league_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("league.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    current_pick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_picks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pick_order: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    pick_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    league: Mapped[League] = relationship(back_populates="draft")


class Event(Base):
    """Unique on identity, never on name — a renamed calendar entry must not
    create a duplicate event."""

    __tablename__ = "event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    league_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("league.id", ondelete="CASCADE"), nullable=False
    )
    season_year: Mapped[int] = mapped_column(Integer, nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("league_id", "season_year", "round", "kind", name="event_identity_key"),
        CheckConstraint("kind IN ('race','sprint')", name="event_kind_check"),
        Index("event_league_idx", "league_id", "round"),
    )


class Result(Base):
    __tablename__ = "result"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event.id", ondelete="CASCADE"), primary_key=True
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver.id"), primary_key=True
    )
    position: Mapped[int | None] = mapped_column(Integer)
    grid: Mapped[int | None] = mapped_column(Integer)
    quali: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    fastest_lap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    driver: Mapped[Driver] = relationship()


class Score(Base):
    __tablename__ = "score"

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("event.id", ondelete="CASCADE"), primary_key=True
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("team.id", ondelete="CASCADE"), primary_key=True
    )
    driver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("driver.id"), primary_key=True
    )
    points: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    breakdown: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (Index("score_team_idx", "team_id"),)


class SeasonArchive(Base):
    __tablename__ = "season_archive"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    league_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("league.id", ondelete="CASCADE"), nullable=False
    )
    season_year: Mapped[int] = mapped_column(Integer, nullable=False)
    champion_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    final_standings: Mapped[dict | None] = mapped_column(JSONB)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("league_id", "season_year", name="archive_league_season_key"),
    )


class Subscription(Base):
    """Hosted tier only. Present and empty in self-hosted databases, so there is
    one schema and one set of migrations regardless of tier."""

    __tablename__ = "subscription"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    league_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("league.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    plan: Mapped[str] = mapped_column(String(24), nullable=False, default="free")
    cadence: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    paid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "cadence IS NULL OR cadence IN ('monthly','annual')", name="sub_cadence_check"
        ),
    )


class JobLock(Base):
    """Makes a hung cron run visible.

    Railway skips a scheduled run when the previous one is still going, so a
    non-exiting scorer would leave a league silently unscored with nothing to
    alert on. This row gives us something to check.
    """

    __tablename__ = "job_lock"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
