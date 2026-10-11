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

    The toggle is display-only for now — it switches four numbers in the page.
    Wiring it to real Stripe price ids is a billing task, not a UI one.
    """
    return request.app.state.templates.TemplateResponse(
        request, "pricing.html", {"player": await optional_player(request)}
    )
