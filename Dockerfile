FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so edits to app code do not bust the layer cache.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "jinja2>=3.1" \
      "httpx>=0.27" "sqlalchemy[asyncio]>=2.0" "asyncpg>=0.30" \
      "alembic>=1.14" "itsdangerous>=2.2" "python-multipart>=0.0.12"

COPY app/ ./app/
COPY db/ ./db/

# Non-root. Railway injects PORT and DATABASE_URL at runtime.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0) if urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8080\")}/health',timeout=4).status==200 else sys.exit(1)"

# Railway overrides this for the web service. The cron service uses
# `python -m app.cli score`, which must exit when finished.
CMD ["uvicorn", "app.web.app:app", "--host", "0.0.0.0", "--port", "8080"]