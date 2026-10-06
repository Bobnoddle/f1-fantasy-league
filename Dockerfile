FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

# Dependencies are installed from a stub package so the resolved list comes
# from pyproject.toml rather than a hand-copied list that drifts. The stub is
# removed and replaced by the real source on the next layer, so the image
# carries exactly one copy of the code.
#
# Only pyproject.toml busts this layer, so editing app code does not reinstall.
COPY pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
 && pip install --no-cache-dir . \
 && rm -rf app

COPY app/ ./app/
COPY db/ ./db/

# Fail the build rather than the deploy if a dependency is missing. The stub
# install above resolves pyproject's declared deps; this checks the code agrees.
# Settings are validated at import, so these placeholders only need to satisfy
# that; real values arrive from Railway at runtime.
RUN APP_URL=http://localhost \
    DATABASE_URL=postgresql+asyncpg://user:pw@localhost:5432/db \
    DISCORD_CLIENT_ID=build \
    DISCORD_CLIENT_SECRET=build \
    SESSION_SECRET=build \
    python -c "import app.web.app, app.cli, app.sim.__main__, app.sim.runner"

# Non-root. Railway injects PORT and DATABASE_URL at runtime.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Reads PORT, so it works on whatever port Railway assigns.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
    sys.exit(0) if urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8080\")}/health',timeout=4).status==200 else sys.exit(1)"

# Railway overrides this for the web service. The cron service uses
# `python -m app.cli score`, which must exit when finished.
# Shell form so $PORT expands: Railway does not guarantee 8080.
CMD ["sh", "-c", "exec uvicorn app.web.app:app --host 0.0.0.0 --port ${PORT:-8080}"]