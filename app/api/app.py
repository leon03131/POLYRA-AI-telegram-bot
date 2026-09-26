"""FastAPI application factory для Mini App backend.

Роуты под /api; OpenAPI/docs выключены (приватный API); статика Mini App
(settings.miniapp_dist) монтируется на "/" ПОСЛЕ роутеров, если каталог есть.
CORS не нужен: Mini App ходит same-origin.
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import routes
from app.api.routes import chat_web
from app.config import Settings
from app.llm.registry import ModelRegistry
from app.search.manager import SearchManager
from app.security.crypto import CryptoBox

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoBox,
    registry: ModelRegistry,
    generation_registry: Any | None = None,
    search_manager: SearchManager | None = None,
    generation_service: Any | None = None,
    bot: Any | None = None,
) -> FastAPI:
    """Собрать FastAPI-приложение: state, роутеры /api, /health, /ready, статика.

    web5: generation_service/bot кладутся в app.state (кладёт main.py) и
    используются роутами web-чата; без generation_service они отвечают 503
    (роуты регистрируются всегда, чтобы контракт /api был стабильным)."""
    app = FastAPI(title="aibot Mini App API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.crypto = crypto
    app.state.registry = registry
    app.state.generation_registry = generation_registry
    app.state.generation_service = generation_service
    app.state.bot = bot
    # Один SearchManager на процесс (lifecycle в main) — не плодим инстансы (A30).
    app.state.search_manager = search_manager or SearchManager(
        session_factory=session_factory, crypto=crypto
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    for router in (
        routes.auth.router,
        routes.me.router,
        routes.settings.router,
        routes.chats.router,
        chat_web.router,
        routes.memory.router,
        routes.admin_users.router,
        routes.admin_access.router,
        routes.admin_gemini.router,
        routes.admin_models.router,
        routes.admin_memory.router,
        routes.admin_providers.router,
        routes.admin_search.router,
        routes.admin_system.router,
        routes.admin_stats.router,
    ):
        app.include_router(router, prefix="/api")

    @app.get("/health")
    async def health() -> dict[str, bool]:
        """Liveness: процесс жив."""
        return {"ok": True}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        """Readiness: БД отвечает (A37). 503 при недоступности зависимости."""
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("readiness check failed")
            return JSONResponse(status_code=503, content={"ready": False})
        return JSONResponse(content={"ready": True})

    miniapp_dist = Path(settings.miniapp_dist)
    if miniapp_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(miniapp_dist), html=True), name="miniapp")
    return app
