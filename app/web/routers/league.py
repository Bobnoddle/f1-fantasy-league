"""League-level pages that are not the draft or admin: signup state refresh.

Kept separate from ``public`` (reads) and ``admin`` (mutations) because these
are read-mostly views an ordinary member needs.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.web.deps import optional_player

router = APIRouter()


@router.get("/pricing", response_class=HTMLResponse)
async def pricing(request: Request):
    """Self-host vs hosted, with a monthly/annual toggle on the hosted price.

    Honest about state: when Stripe is unconfigured the hosted column says so
    rather than offering a checkout that cannot work. Per-league checkout lives
    on /l/{code}/billing after signup — pricing never charges directly.
    """
    settings = request.app.state.settings
    return request.app.state.templates.TemplateResponse(
        request,
        "pricing.html",
        {
            "player": await optional_player(request),
            "hosted_mode": settings.hosted_mode,
            "billing_ready": settings.billing_ready,
        },
    )
