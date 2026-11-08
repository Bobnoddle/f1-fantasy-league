"""Per-league billing pages. Admin only, except the Stripe webhook.

When Stripe is unconfigured (self-host) these routes explain that billing is
off rather than 500ing — an unset secret is a valid deployment, not an error.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import billing as billing_svc
from app.repo.postgres import LeagueRepo
from app.web.deps import get_db, require_admin
from app.web.view import league_context

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/l/{code}/billing", response_class=HTMLResponse)
async def billing_page(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    player = await require_admin(request, code)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    settings = request.app.state.settings
    sub = await billing_svc.get_subscription(db, league.id)

    return request.app.state.templates.TemplateResponse(
        request,
        "billing.html",
        {
            "league": league,
            "player": player,
            "subscription": sub,
            "is_paid": billing_svc.is_paid(sub),
            "hosted_mode": settings.hosted_mode,
            "billing_ready": settings.billing_ready,
            "has_monthly": bool(settings.stripe_price_monthly),
            "has_annual": bool(settings.stripe_price_annual),
            "csrf_token": request.state.session.csrf_token,
            **await league_context(db, league, player, csrf_token=request.state.session.csrf_token),
        },
    )


@router.post("/l/{code}/billing/checkout")
async def checkout(
    code: str,
    request: Request,
    cadence: str = Form("annual"),
    db: AsyncSession = Depends(get_db),
):
    await require_admin(request, code)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    settings = request.app.state.settings

    if not settings.hosted_mode:
        return RedirectResponse(f"/l/{league.code}/billing?error=self-host", status_code=303)
    if cadence not in ("monthly", "annual"):
        return RedirectResponse(f"/l/{league.code}/billing?error=bad-cadence", status_code=303)
    price = billing_svc.price_for(settings, cadence)
    if not price:
        return RedirectResponse(f"/l/{league.code}/billing?error=unconfigured", status_code=303)

    base = settings.app_url.rstrip("/")
    try:
        url = await billing_svc.create_checkout_session(
            secret_key=settings.stripe_secret_key or "",
            price_id=price,
            league_id=league.id,
            success_url=f"{base}/l/{league.code}/billing?saved=subscribed",
            cancel_url=f"{base}/l/{league.code}/billing?error=cancelled",
        )
    except Exception as exc:  # noqa: BLE001 — surface as a redirect, not a 500
        log.warning("stripe checkout failed for %s: %s", league.code, exc)
        return RedirectResponse(f"/l/{league.code}/billing?error=stripe", status_code=303)
    return RedirectResponse(url, status_code=303)


@router.post("/l/{code}/billing/portal")
async def portal(code: str, request: Request, db: AsyncSession = Depends(get_db)):
    await require_admin(request, code)
    repo = LeagueRepo(db)
    league = await repo.by_code(code)
    settings = request.app.state.settings
    sub = await billing_svc.get_subscription(db, league.id)

    if not settings.hosted_mode or sub is None or not sub.stripe_customer_id:
        return RedirectResponse(f"/l/{league.code}/billing?error=no-subscription", status_code=303)
    try:
        url = await billing_svc.create_portal_session(
            secret_key=settings.stripe_secret_key or "",
            customer_id=sub.stripe_customer_id,
            return_url=f"{settings.app_url.rstrip('/')}/l/{league.code}/billing",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("stripe portal failed for %s: %s", league.code, exc)
        return RedirectResponse(f"/l/{league.code}/billing?error=stripe", status_code=303)
    return RedirectResponse(url, status_code=303)


@router.post("/billing/webhook")
async def stripe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Stripe event ingress. CSRF-exempt (Stripe has no session cookie).

    Verifies the signature when a secret is configured. Without a secret the
    route refuses rather than accepting unsigned events.
    """
    settings = request.app.state.settings
    if not settings.stripe_webhook_secret:
        return JSONResponse({"error": "webhook not configured"}, status_code=503)

    payload = await request.body()
    header = request.headers.get("stripe-signature", "")
    if not billing_svc.verify_webhook_signature(payload, header, settings.stripe_webhook_secret):
        return JSONResponse({"error": "bad signature"}, status_code=400)

    try:
        event = billing_svc.webhook_event(payload)
    except ValueError:
        return JSONResponse({"error": "bad payload"}, status_code=400)

    await billing_svc.upsert_from_stripe_event(db, event)
    return JSONResponse({"received": True})
