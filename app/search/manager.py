"""SearchManager: конфиги из БД, fallback-цепочка по приоритету, режимы, health.

Lifecycle (A30): инстансы бэкендов переиспользуются (реестр backend_id → instance
с подписью конфига); смена enabled/ключа в БД → aclose старого + пересоздание при
следующем вызове; aclose() закрывает все инстансы (shutdown).
Playwright: in-memory cooldown 15 мин после BackendUnavailableError (A33).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.search_configs import SearchConfigRepository
from app.search.base import (
    BackendUnavailableError,
    SearchBackend,
    SearchBackendError,
    SearchOptions,
    SearchOutcome,
    SearchResult,
    SupportsAIOverview,
)
from app.search.brave import BraveSearchBackend
from app.search.jina import JinaReaderBackend, JinaSearchBackend
from app.search.playwright_google import PlaywrightGoogleBackend
from app.search.serpapi_ai_overview import SerpApiAIOverviewBackend
from app.search.serper import SerperBackend
from app.security.crypto import CryptoBox

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_BACKEND_FACTORIES: dict[str, Callable[[str | None], SearchBackend]] = {
    "serper": SerperBackend,
    "brave": BraveSearchBackend,
    "serpapi_aio": SerpApiAIOverviewBackend,
    "jina_search": JinaSearchBackend,
    "playwright_google": PlaywrightGoogleBackend,
}

_PLAYWRIGHT_BACKEND_ID = "playwright_google"
_PLAYWRIGHT_COOLDOWN_SECONDS = 15 * 60  # in-memory, на процесс (experimental)


@dataclass
class _BackendEntry:
    """Кэшированный инстанс бэкенда + подпись конфига, из которой он построен."""

    instance: SearchBackend
    signature: tuple[bool, str | None]  # (enabled, encrypted_api_key)


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
        self._entries: dict[str, _BackendEntry] = {}
        self._reader: JinaReaderBackend | None = None
        self._reader_key: str | None = None
        self._playwright_cooldown_until: float = 0.0  # time.monotonic() deadline
        self._lifecycle_lock = asyncio.Lock()

    # ---------------------------------------------------------- lifecycle (A30)

    def _decrypt_key(self, backend_id: str, encrypted: str | None) -> str | None:
        if not encrypted:
            return None
        try:
            return self._crypto.decrypt(encrypted)
        except ValueError:
            logger.warning("search backend %r: key decryption failed", backend_id)
            return None

    async def _get_backend(
        self, backend_id: str, *, enabled: bool, encrypted_key: str | None
    ) -> SearchBackend | None:
        """Инстанс из кэша; смена enabled/ключа → aclose старого + пересоздание."""
        factory = _BACKEND_FACTORIES.get(backend_id)
        if factory is None:
            logger.warning("unknown search backend_id %r — skipped", backend_id)
            return None
        signature = (enabled, encrypted_key)
        async with self._lifecycle_lock:
            entry = self._entries.get(backend_id)
            if entry is not None and entry.signature != signature:
                logger.info("search backend %r config changed — recreating", backend_id)
                await entry.instance.aclose()
                entry = None
            if entry is None:
                entry = _BackendEntry(
                    instance=factory(self._decrypt_key(backend_id, encrypted_key)),
                    signature=signature,
                )
                self._entries[backend_id] = entry
            return entry.instance

    async def _load_backends(self, *, include_disabled: bool = False) -> list[SearchBackend]:
        """Инстансы бэкендов из конфигов БД: enabled + priority asc, ключи decrypt.

        Инстансы кэшируются (A30): один клиент на backend_id до смены конфига.
        """
        async with self._session_factory() as session:
            configs = await SearchConfigRepository(session).list_all()
        backends: list[SearchBackend] = []
        for config in configs:
            if not config.enabled and not include_disabled:
                continue
            backend = await self._get_backend(
                config.backend_id,
                enabled=config.enabled,
                encrypted_key=config.encrypted_api_key,
            )
            if backend is not None:
                backends.append(backend)
        return backends

    async def get_reader(self) -> JinaReaderBackend | None:
        """JinaReaderBackend по ключу jina_search (reader и search делят ключ jina).

        None — если jina_search не enabled или без ключа. Инстанс кэшируется;
        смена ключа → aclose старого + пересоздание.
        """
        async with self._session_factory() as session:
            config = await SearchConfigRepository(session).get("jina_search")
        if config is None or not config.enabled:
            return None
        api_key = self._decrypt_key("jina_search", config.encrypted_api_key)
        if not api_key:
            return None
        async with self._lifecycle_lock:
            if self._reader is not None and self._reader_key != api_key:
                logger.info("jina reader key changed — recreating")
                await self._reader.aclose()
                self._reader = None
            if self._reader is None:
                self._reader = JinaReaderBackend(api_key=api_key)
                self._reader_key = api_key
            return self._reader

    async def aclose(self) -> None:
        """Закрыть все кэшированные инстансы (shutdown). Идемпотентно."""
        async with self._lifecycle_lock:
            entries = list(self._entries.values())
            self._entries.clear()
            reader = self._reader
            self._reader = None
            self._reader_key = None
        for entry in entries:
            try:
                await entry.instance.aclose()
            except Exception:
                logger.warning("search backend aclose failed", exc_info=True)
        if reader is not None:
            try:
                await reader.aclose()
            except Exception:
                logger.warning("jina reader aclose failed", exc_info=True)

    # --------------------------------------------------------------- поиск

    async def search(self, query: str, options: SearchOptions) -> SearchOutcome:
        """Режимы: normal → цепочка; ai_overview → SerpApi AIO, сбой → normal.

        auto (A32): СНАЧАЛА normal-цепочка; AI Overview только если normal вернул
        0 результатов ИЛИ упал (SearchBackendError). AI Overview без references
        не считается авторитетным (обрабатывается в бэкенде: деградация в organic).
        Пустой ответ normal-цепочки — валиден, НЕ повод для fallback на другой
        normal-бэкенд (но для auto — повод попробовать AIO).
        """
        backends = await self._load_backends()
        if not backends:
            raise SearchBackendError("all backends failed: none configured/enabled")

        mode = options.mode
        if mode == "ai_overview":
            outcome = await self._try_ai_overview(backends, query, options)
            if outcome is not None:
                return outcome
            return await self._normal_chain(backends, query, options)

        # normal | auto: сначала normal-цепочка
        normal_error: SearchBackendError | None = None
        try:
            outcome = await self._normal_chain(backends, query, options)
        except SearchBackendError as exc:
            if mode != "auto":
                raise
            normal_error = exc
            outcome = None

        if mode == "auto" and (outcome is None or not outcome.results):
            aio_outcome = await self._try_ai_overview(backends, query, options)
            if aio_outcome is not None:
                return aio_outcome
        if outcome is None:
            assert normal_error is not None
            raise normal_error
        return outcome

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
        if outcome.ai_overview_text and not outcome.results:
            # A32: overview без references не авторитетен — не выдаём, fallback.
            logger.info("ai_overview without references — fallback to normal")
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
            if self._in_cooldown(backend.backend_id):
                logger.info("backend %r in cooldown — skipped", backend.backend_id)
                continue
            try:
                results = await backend.search(query, options)
            except SearchBackendError as exc:
                self._maybe_cooldown(backend.backend_id, exc)
                errors.append(f"{backend.backend_id}: {exc}")
                await self._record_health(backend.backend_id, False, str(exc))
                continue
            await self._record_health(backend.backend_id, True, None)
            return SearchOutcome(
                results=_dedupe(results)[: options.max_results],
                backend=backend.backend_id,
            )
        raise SearchBackendError("all backends failed: " + "; ".join(errors))

    # ------------------------------------------------------ playwright cooldown

    def _in_cooldown(self, backend_id: str) -> bool:
        """In-memory cooldown (на процесс) — только playwright (A33, experimental)."""
        return (
            backend_id == _PLAYWRIGHT_BACKEND_ID
            and time.monotonic() < self._playwright_cooldown_until
        )

    def _maybe_cooldown(self, backend_id: str, exc: SearchBackendError) -> None:
        """BackendUnavailableError от playwright → cooldown 15 мин (CAPTCHA и т.п.)."""
        if backend_id != _PLAYWRIGHT_BACKEND_ID:
            return
        if isinstance(exc, BackendUnavailableError):
            self._playwright_cooldown_until = time.monotonic() + _PLAYWRIGHT_COOLDOWN_SECONDS
            logger.info(
                "playwright_google cooldown %ds after: %s",
                _PLAYWRIGHT_COOLDOWN_SECONDS,
                exc,
            )

    # --------------------------------------------------------------- health

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
