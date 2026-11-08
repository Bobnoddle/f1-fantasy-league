"""Hosted billing. Kept in the open repo on purpose.

Stripe price IDs are public, the secret key lives in env, and the same image
serves both tiers. Self-hosters leave Stripe unset and billing disappears;
hosted sets four vars and checkout appears. No tier branching in domain logic —
this module gates access only, via ``paid_until > now()``.

Flow is per league (``subscription.league_id`` unique), not per user:
Checkout → webhook → ``subscription`` row → admin pages read it.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Subscription

log = logging.getLogger(__name__)

STRIPE_API = "https://api.stripe.com/v1"


def is_paid(sub: Subscription | None, now: datetime | None = None) -> bool:
    """Active subscription with time left. Expired → read-only, never deleted."""
    if sub is None:
        return False
    if sub.status not in ("active", "trialing"):
        return False
    if sub.paid_until is None:
        return sub.status == "active"
    check = now or datetime.now(UTC)
    paid = sub.paid_until.replace(tzinfo=UTC) if sub.paid_until.tzinfo is None else sub.paid_until
    return paid > check


def price_for(settings, cadence: str) -> str | None:
    """Price ID for a cadence. Returns None when that cadence is unconfigured."""
    if cadence == "monthly":
        return settings.stripe_price_monthly
    if cadence == "annual":
        return settings.stripe_price_annual
    return None


def verify_webhook_signature(payload: bytes, header: str, secret: str) -> bool:
    """Verify a Stripe webhook signature. Pure — no network, easily tested."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        timestamp = parts.get("t", "")
        signatures = [v for k, v in (p.split("=", 1) for p in header.split(",")) if k == "v1"]
        if not timestamp or not signatures:
            return False
        # Reject replays older than 5 minutes.
        if abs(time.time() - int(timestamp)) > 300:
            return False
        expected = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
        ).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in signatures)
    except (ValueError, TypeError):
        return False


async def get_subscription(session: AsyncSession, league_id: UUID) -> Subscription | None:
    return await session.scalar(select(Subscription).where(Subscription.league_id == league_id))


async def ensure_paid_for_admin(session: AsyncSession, league_id: UUID, settings) -> bool:
    """True when admin mutations are allowed.

    Self-host (no Stripe secret) is always allowed. Hosted requires an active
    subscription. Reads are never gated — callers only call this on POSTs.
    """
    if not settings.stripe_secret_key:
        return True
    return is_paid(await get_subscription(session, league_id))


async def upsert_from_stripe_event(
    session: AsyncSession,
    event: dict,
) -> Subscription | None:
    """Apply a Stripe event to the subscription table. Idempotent.

    Handles ``checkout.session.completed`` (links customer/subscription to the
    league) and ``customer.subscription.{updated,deleted}`` plus
    ``invoice.paid`` (refreshes status/paid_until). Unknown types are ignored
    so a new Stripe event cannot 500 the webhook.
    """
    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        league_id = (obj.get("metadata") or {}).get("league_id")
        if not league_id:
            return None
        try:
            lid = UUID(str(league_id))
        except ValueError:
            return None
        sub = await get_subscription(session, lid)
        if sub is None:
            sub = Subscription(
                league_id=lid,
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("subscription"),
                plan="hosted",
                status="active",
            )
            session.add(sub)
        else:
            if obj.get("customer"):
                sub.stripe_customer_id = obj["customer"]
            if obj.get("subscription"):
                sub.stripe_subscription_id = obj["subscription"]
            sub.status = "active"
        await session.flush()
        return sub

    if event_type in (
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
    ):
        sub_id = obj.get("id") or obj.get("subscription")
        if event_type == "invoice.paid" and not sub_id:
            return None
        sub = None
        if sub_id:
            sub = await session.scalar(
                select(Subscription).where(Subscription.stripe_subscription_id == sub_id)
            )
        if sub is None:
            return None
        if event_type == "customer.subscription.deleted":
            sub.status = "canceled"
        else:
            sub.status = obj.get("status", sub.status)
            period_end = obj.get("current_period_end")
            if period_end is None:
                lines = obj.get("lines", {}).get("data", [{}])
                period_end = (lines[0].get("period", {}) if lines else {}).get("end")
            if period_end:
                with contextlib.suppress(ValueError, TypeError):
                    sub.paid_until = datetime.fromtimestamp(int(period_end), tz=UTC)
        await session.flush()
        return sub

    return None


async def create_checkout_session(
    *,
    secret_key: str,
    price_id: str,
    league_id: UUID,
    success_url: str,
    cancel_url: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Create a Stripe Checkout session. Returns the redirect URL."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(
            f"{STRIPE_API}/checkout/sessions",
            auth=("", secret_key),
            data={
                "mode": "subscription",
                "success_url": success_url,
                "cancel_url": cancel_url,
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "client_reference_id": str(league_id),
                "metadata[league_id]": str(league_id),
            },
            headers={"Idempotency-Key": f"checkout-{league_id}-{price_id}"},
        )
        resp.raise_for_status()
        url = resp.json().get("url", "")
        if not url:
            raise RuntimeError("Stripe did not return a checkout URL")
        return url
    finally:
        if owns:
            await client.aclose()


async def create_portal_session(
    *,
    secret_key: str,
    customer_id: str,
    return_url: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Create a Stripe Customer Portal session. Returns the redirect URL."""
    owns = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(
            f"{STRIPE_API}/billing_portal/sessions",
            auth=("", secret_key),
            data={"customer": customer_id, "return_url": return_url},
        )
        resp.raise_for_status()
        url = resp.json().get("url", "")
        if not url:
            raise RuntimeError("Stripe did not return a portal URL")
        return url
    finally:
        if owns:
            await client.aclose()


def webhook_event(payload: bytes) -> dict:
    """Parse the webhook body. Signature is verified separately."""
    return json.loads(payload.decode("utf-8"))
