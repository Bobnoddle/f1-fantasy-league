"""Configuration. Env-driven, validated at construction, never at import."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _optional_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Runtime settings for both the web service and the cron job."""

    database_url: str
    discord_client_id: str
    discord_client_secret: str
    session_secret: str
    app_url: str

    # Optional — absent in self-hosted mode.
    stripe_secret_key: str | None = None
    token_encryption_key: str | None = None

    # Discord OAuth redirect, derived from app_url when unset.
    discord_redirect_uri: str = ""

    # Operational
    cron_lock_name: str = "cron:score"
    fetch_timeout_secs: int = 30
    fetch_retries: int = 3
    post_race_window_hours: int = 36
    log_level: str = field(default="INFO")

    @classmethod
    def from_env(cls) -> Settings:
        app_url = _require("APP_URL").rstrip("/")
        return cls(
            database_url=_require("DATABASE_URL"),
            discord_client_id=_require("DISCORD_CLIENT_ID"),
            discord_client_secret=_require("DISCORD_CLIENT_SECRET"),
            session_secret=_require("SESSION_SECRET"),
            app_url=app_url,
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY") or None,
            token_encryption_key=os.getenv("TOKEN_ENCRYPTION_KEY") or None,
            discord_redirect_uri=os.getenv(
                "DISCORD_REDIRECT_URI", f"{app_url}/auth/discord/callback"
            ),
            cron_lock_name=os.getenv("CRON_LOCK_NAME", "cron:score"),
            fetch_timeout_secs=_optional_int("FETCH_TIMEOUT_SECS", 30),
            fetch_retries=_optional_int("FETCH_RETRIES", 3),
            post_race_window_hours=_optional_int("POST_RACE_WINDOW_HOURS", 36),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    @property
    def hosted_mode(self) -> bool:
        """True when billing is configured.

        Business logic must never branch on this. It gates access only.
        """
        return self.stripe_secret_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
