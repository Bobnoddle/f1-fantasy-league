"""FastAPI application factory.

Server-rendered, one process. The whole app is three concerns: public reads,
league pages, and admin actions. There is no SPA and no client-side router, so
every URL is a real, linkable, shareable page.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.web.deps import NeedsAdminError, NeedsLoginError, NotFoundError
from app.web.session import COOKIE, SessionCodec

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
TEMPLATES = BASE_DIR / "templates"
STATIC = BASE_DIR / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    codec = SessionCodec(settings.session_secret)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = engine
        app.state.db_factory = session_factory
        app.state.codec = codec
        app.state.settings = settings
        yield
        # Must dispose, or uvicorn never exits cleanly and Railway leaves the
        # old container running during a deploy.
        await engine.dispose()

    app = FastAPI(
        title="F1 Fantasy",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.engine = engine
    app.state.db_factory = session_factory
    app.state.codec = codec
    app.state.settings = settings

    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.globals["now"] = lambda: datetime.now(UTC)
    templates.env.filters["pts"] = lambda v: f"{float(v or 0):,.0f}"
    templates.env.filters["signed"] = lambda v: f"{float(v or 0):+,.0f}"
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.middleware("http")
    async def session_middleware(request: Request, call_next):
        """Attach a session and a database connection for every request."""
        request.state.session = codec.decode(request.cookies.get(COOKIE))
        # The middleware owns the transaction rather than a `yield` dependency.
        # Dependency teardown ordering around the response is easy to get wrong,
        # and a lost commit is far worse than a lost abstraction.
        async with session_factory() as db:
            request.state.db = db
            try:
                response = await call_next(request)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        if request.state.session.changed:  # pragma: no cover - set on login
            response.set_cookie(
                COOKIE,
                codec.encode(request.state.session),
                max_age=7 * 24 * 3600,
                httponly=True,
                samesite="lax",
                secure=settings.app_url.startswith("https://"),
            )
        return response

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError):
        return templates.TemplateResponse(
            request, "error.html", {"code": 404, "message": exc.message}, status_code=404
        )

    @app.exception_handler(NeedsLoginError)
    async def needs_login(request: Request, exc: NeedsLoginError):
        """Send anonymous visitors to sign in, remembering where they were."""
        return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)

    @app.exception_handler(NeedsAdminError)
    async def needs_admin(request: Request, exc: NeedsAdminError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 403, "message": "Only the league admin can do that."},
            status_code=403,
        )

    from app.web.routers import admin, auth, draft, league, public

    app.include_router(public.router)
    app.include_router(auth.router)
    app.include_router(league.router)
    app.include_router(draft.router)
    app.include_router(admin.router)

    @app.get("/health")
    async def health() -> JSONResponse:
        """Railway polls this. Must stay cheap — no database round trip."""
        return JSONResponse({"status": "ok"})

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        """Readiness. Does touch the database, so it can report a bad DSN."""
        from sqlalchemy import text

        try:
            async with session_factory() as db:
                await db.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover - depends on infra
            return JSONResponse({"status": "error", "detail": str(exc)}, status_code=503)
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
