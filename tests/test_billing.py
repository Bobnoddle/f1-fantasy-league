"""Billing unit tests. No database, no network, no Stripe account."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime, timedelta

from app import billing as svc
from app.config import Settings
from app.models import Subscription


def _settings(**overrides):
    base = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "session_secret": "x" * 64,
        "app_url": "https://f1.example",
    }
    return Settings(**{**base, **overrides})


def _sub(status="active", paid_until=None):
    return Subscription(
        league_id=uuid.uuid4(),
        status=status,
        paid_until=paid_until,
    )


def test_unpaid_without_row():
    assert svc.is_paid(None) is False


def test_active_with_future_paid_until_is_paid():
    sub = _sub(paid_until=datetime.now(UTC) + timedelta(days=1))
    assert svc.is_paid(sub) is True


def test_expired_is_not_paid():
    sub = _sub(paid_until=datetime.now(UTC) - timedelta(seconds=1))
    assert svc.is_paid(sub) is False


def test_canceled_is_not_paid_even_with_time_left():
    sub = _sub(status="canceled", paid_until=datetime.now(UTC) + timedelta(days=30))
    assert svc.is_paid(sub) is False


def test_price_selection():
    s = _settings(stripe_price_monthly="price_m", stripe_price_annual="price_a")
    assert svc.price_for(s, "monthly") == "price_m"
    assert svc.price_for(s, "annual") == "price_a"
    assert svc.price_for(s, "weekly") is None


def test_billing_ready_needs_secret_and_a_price(monkeypatch=None):
    assert _settings().billing_ready is False
    assert _settings(stripe_secret_key="sk_test").billing_ready is False
    assert (
        _settings(stripe_secret_key="sk_test", stripe_price_annual="price_a").billing_ready is True
    )


def test_webhook_signature_verifies():
    secret = "whsec_test"
    payload = b'{"type":"checkout.session.completed"}'
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    assert svc.verify_webhook_signature(payload, header, secret) is True


def test_webhook_signature_rejects_wrong_secret():
    payload = b"{}"
    ts = str(int(time.time()))
    header = f"t={ts},v1=deadbeef"
    assert svc.verify_webhook_signature(payload, header, "whsec_test") is False


def test_webhook_signature_rejects_stale_timestamp():
    secret = "whsec_test"
    payload = b"{}"
    ts = str(int(time.time()) - 10_000)
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    assert svc.verify_webhook_signature(payload, f"t={ts},v1={sig}", secret) is False
