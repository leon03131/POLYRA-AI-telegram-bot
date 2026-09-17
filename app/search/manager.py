"""SearchManager: конфиги из БД, fallback-цепочка по приоритету, режимы, health."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.search_configs import SearchConfigRepository
from app.search.base import (
    SearchBackend,
    SearchBackendError,
    SearchOptions,
    SearchOutcome,
    SearchResult,
    SupportsAIOverview,
)
from app.search.brave import BraveSearchBackend
from app.search.jina import JinaSearchBackend
from app.search.playwright_google import PlaywrightGoogleBackend
from app.search.serpapi_ai_overview import SerpApiAIOverviewBackend
from app.search.serper import SerperBackend
from app.security.crypto import CryptoBox

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_QUESTION_STARTERS = frozenset(
    {
        "почему",
        "как",
        "что",
        "зачем",
        "когда",
        "где",
        "кто",
        "чей",
        "сколько",
        "какой",
        "какая",
        "какие",
        "ли",
        "what",
        "how",
        "why",
        "when",
        "where",
        "who",
        "whom",
        "which",
        "explain",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "can",
        "could",
        "should",
    }
)

_BACKEND_FACTORIES: dict[str, Callable[[str | None], SearchBackend]] = {
    "serper": SerperBackend,
    "brave": BraveSearchBackend,
    "serpapi_aio": SerpApiAIOverviewBackend,
    "jina_search": JinaSearchBackend,
    "playwright_google": PlaywrightGoogleBackend,
}


def _looks_like_question(query: str) -> bool:
    """Эвристика auto-режима: вопросительное слово в начале или '?' в запросе."""
    q = query.strip().lower()
    if "?" in q:
        return True
    words = q.split()
    return bool(words) and words[0] in _QUESTION_STARTERS


def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
    """Дедуп по url (первый выигрывает), порядок сохраняется."""
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        if result.url in seen:
            continue
        seen.add(result.url)
        unique.append(result)
    return unique


class SearchManager:
    """Fallback-цепочка поисковых бэкендов; конфиги и ключи — из search_backend_configs."""

    def __init__(self, *, session_factory: SessionFactory, crypto: CryptoBox) -> None:
        self._session_factory = session_factory
        self._crypto = crypto

    async def _load_backends(self, *, include_disabled: bool = False) -> list[SearchBackend]:
        """Инстансы бэкендов из конфигов БД: enabled + priority asc, ключи decrypt."""
        async with self._session_factory() as session:
            configs = await SearchConfigRepository(session).list_all()
        backends: list[SearchBackend] = []
        for config in configs:
            if not config.enabled and not include_disabled:
                continue
            factory = _BACKEND_FACTORIES.get(config.backend_id)
            if factory is None:
                logger.warning("unknown search backend_id %r — skipped", config.backend_id)
                continue
            api_key: str | None = None
            if config.encrypted_api_key:
                try:
                    api_key = self._crypto.decrypt(config.encrypted_api_key)
                except ValueError:
                    logger.warning("search backend %r: key decryption failed", config.backend_id)
            backends.append(factory(api_key))
        return backends

    async def search(self, query: str, options: SearchOptions) -> SearchOutcome:
        """Режимы: ai_overview (или auto-эвристика) → SerpApi AIO, иначе normal-цепочка.

        normal: по приоритету; BackendUnavailable/SearchBackendError → следующий;
        все упали → SearchBackendError. Пустой ответ — валиден, НЕ повод для fallback.
        """
        backends = await self._load_backends()
        if not backends:
            raise SearchBackendError("all backends failed: none configured/enabled")

        mode = options.mode
        if mode == "auto":
            mode = "ai_overview" if _looks_like_question(query) else "normal"

        if mode == "ai_overview":
            outcome = await self._try_ai_overview(backends, query, options)
            if outcome is not None:
                return outcome
        return await self._normal_chain(backends, query, options)

    async def _try_ai_overview(
        self, backends: list[SearchBackend], query: str, options: SearchOptions
    ) -> SearchOutcome | None:
        """Первый настроенный AIO-бэкенд; при сбое — None (fallback на normal)."""
        aio: SupportsAIOverview | None = None
        for candidate in backends:
            if isinstance(candidate, SupportsAIOverview) and candidate.is_configured():
                aio = candidate
                break
        if aio is None:
            return None
        try:
            outcome = await aio.ai_overview(query, options)
        except SearchBackendError as exc:
            await self._record_health(aio.backend_id, False, str(exc))
            logger.info("ai_overview failed (%s), fallback to normal", exc)
            return None
        await self._record_health(aio.backend_id, True, None)
        return SearchOutcome(
            results=_dedupe(outcome.results)[: options.max_results],
            backend=outcome.backend,
            ai_overview_text=outcome.ai_overview_text,
        )

    async def _normal_chain(
        self, backends: list[SearchBackend], query: str, options: SearchOptions
    ) -> SearchOutcome:
        """Цепочка по приоритету; все упали → SearchBackendError."""
        errors: list[str] = []
        for backend in backends:
            # SerpApi AIO — отдельный режим, НЕ обычный поиск (см. ТЗ §28).
            if isinstance(backend, SupportsAIOverview):
                continue
            if not backend.is_configured():
                continue
            try:
                results = await backend.search(query, options)
            except SearchBackendError as exc:
                errors.append(f"{backend.backend_id}: {exc}")
                await self._record_health(backend.backend_id, False, str(exc))
                continue
            await self._record_health(backend.backend_id, True, None)
            return SearchOutcome(
                results=_dedupe(results)[: options.max_results],
                backend=backend.backend_id,
            )
        raise SearchBackendError("all backends failed: " + "; ".join(errors))

    async def health_check(self, backend_id: str) -> bool:
        """Минимальный запрос 'test' (limit 1); обновляет health в конфиге (commit)."""
        backends = await self._load_backends(include_disabled=True)
        backend = next((b for b in backends if b.backend_id == backend_id), None)
        if backend is None or not backend.is_configured():
            await self._record_health(backend_id, False, "not configured")
            return False
        try:
            await backend.search("test", SearchOptions(max_results=1))
        except SearchBackendError as exc:
            await self._record_health(backend_id, False, str(exc))
            return False
        await self._record_health(backend_id, True, None)
        return True

    async def _record_health(self, backend_id: str, ok: bool, error: str | None) -> None:
        """Зафиксировать health/last_error (commit); ошибки записи не роняют поиск."""
        try:
            async with self._session_factory() as session:
                repo = SearchConfigRepository(session)
                await repo.set_health(backend_id, "ok" if ok else "error", error)
                await session.commit()
        except Exception:
            logger.warning("failed to record health for %r", backend_id, exc_info=True)
