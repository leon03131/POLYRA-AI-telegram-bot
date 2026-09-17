# syntax=docker/dockerfile:1

###############################################################################
# Stage 1: miniapp build (React + TS + Vite) -> /miniapp/dist
###############################################################################
FROM node:22-alpine AS miniapp-build
WORKDIR /miniapp

# Dependencies layer: cached while package*.json stay unchanged.
# package-lock.json is present in the repo, so `npm ci` is reproducible.
COPY miniapp/package.json miniapp/package-lock.json ./
RUN npm ci

# Sources + build ("tsc && vite build"). miniapp/node_modules and
# miniapp/dist are excluded via .dockerignore, so this COPY does not
# clobber the fresh install and does not bring in a stale build.
COPY miniapp/ ./
RUN npm run build

###############################################################################
# Stage 2: runtime (aiogram long polling + FastAPI/uvicorn + alembic)
###############################################################################
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # FastAPI must listen on all interfaces inside the container.
    API_HOST=0.0.0.0 \
    # Built Mini App location (app.config.Settings.miniapp_dist <- env MINIAPP_DIST).
    MINIAPP_DIST=/app/miniapp/dist

WORKDIR /app

# 1) Dependencies layer: with only pyproject.toml present, `pip install .`
#    resolves and installs runtime deps (no dev extras); setuptools finds no
#    packages yet and builds an empty metadata wheel. Cached while
#    pyproject.toml is unchanged.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# 2) Application code, CLI scripts, alembic config and the built Mini App.
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY alembic.ini ./
COPY --from=miniapp-build /miniapp/dist ./miniapp/dist

# 3) Re-install the real `app` package now that the sources exist
#    (fast: deps already satisfied). Makes `app` importable for
#    `python scripts/*.py` regardless of sys.path quirks.
RUN pip install --no-cache-dir --no-deps .

# Non-root runtime user.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Apply DB migrations (alembic reads DATABASE_URL from env), then start
# the app: bot long polling + Mini App API in a single process.
CMD ["sh", "-c", "alembic upgrade head && python -m app.main"]
