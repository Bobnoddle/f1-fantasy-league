"""Configuration tests.

Config is validated at construction and lru_cached, so these call
``Settings.from_env`` directly rather than going through ``get_settings``.
"""

from __future__ import annotations

import pytest

from app.config import ConfigError, Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Start from a known-empty slate so a real .env cannot affect the result."""
    for name in (
        "APP_URL",
        "DATABASE_URL",
        "SESSION_SECRET",
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "DISCORD_REDIRECT_URI",
        "STRIPE_SECRET_KEY",
        "TOKEN_ENCRYPTION_KEY",
        "FETCH_TIMEOUT_SECS",
        "FETCH_RETRIES",
        "POST_RACE_WINDOW_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("SESSION_SECRET", "x" * 64)
    monkeypatch.setenv("APP_URL", "https://f1.example/")


# ── Discord is optional. This is the project's premise. ─────────────────────


def test_it_loads_with_no_discord_configured_at_all():
    """A self-hoster with no Discord must be able to start.

    These were required, which meant the app could not run without Discord —
    the exact opposite of Discord being optional. Found by trying to run the
    simulator on a machine with no Discord credentials.
    """
    settings = Settings.from_env()

    assert settings.discord_client_id == ""
    assert settings.discord_client_secret == ""


def test_half_configured_discord_is_refused(monkeypatch):
    """Silent half-configuration would produce a confusing OAuth failure later."""
    monkeypatch.setenv("DISCORD_CLIENT_ID", "only-the-id")

    with pytest.raises(ConfigError, match="both"):
        Settings.from_env()


def test_paired_discord_config_is_accepted(monkeypatch):
    monkeypatch.setenv("DISCORD_CLIENT_ID", "id")
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "secret")

    settings = Settings.from_env()
    assert settings.discord_client_id == "id"
    assert settings.discord_redirect_uri == "https://f1.example/auth/discord/callback"


# ── Required settings ───────────────────────────────────────────────────────


def test_app_url_is_required(monkeypatch):
    monkeypatch.delenv("APP_URL")
    with pytest.raises(ConfigError, match="APP_URL"):
        Settings.from_env()


def test_session_secret_is_required(monkeypatch):
    monkeypatch.delenv("SESSION_SECRET")
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        Settings.from_env()


def test_database_url_is_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        Settings.from_env()


# ── Normalisation and defaults ──────────────────────────────────────────────


def test_app_url_loses_its_trailing_slash(monkeypatch):
    """A trailing slash would double up in every absolute URL built from it."""
    settings = Settings.from_env()
    assert settings.app_url == "https://f1.example"


def test_redirect_uri_can_be_overridden(monkeypatch):
    monkeypatch.setenv("DISCORD_REDIRECT_URI", "https://other.example/cb")
    assert Settings.from_env().discord_redirect_uri == "https://other.example/cb"


def test_a_non_numeric_timeout_is_reported_clearly(monkeypatch):
    monkeypatch.setenv("FETCH_TIMEOUT_SECS", "soon")
    with pytest.raises(ConfigError, match="must be an integer"):
        Settings.from_env()


def test_hosted_mode_is_false_without_stripe():
    """Billing gates access only. It must never appear in business logic."""
    assert Settings.from_env().hosted_mode is False


def test_hosted_mode_is_true_with_stripe(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    assert Settings.from_env().hosted_mode is True


def test_empty_optional_strings_become_none(monkeypatch):
    """An empty env var means unset, not an empty secret."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    settings = Settings.from_env()

    assert settings.stripe_secret_key is None
    assert settings.token_encryption_key is None
