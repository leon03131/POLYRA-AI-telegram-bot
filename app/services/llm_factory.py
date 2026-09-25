"""Фабрика стрим-функции LLM: диспетчеризация по ModelDefinition.provider.

gemini → GeminiProjectPool (failover по проектам, ключ через request.metadata);
alibaba → AlibabaProvider с ключом из provider_credentials (env — fallback).

A30: клиенты управляются lifecycle'ом приложения: один shared Gemini client,
кеш Alibaba по ключу с eviction при смене, aclose() закрывает все транспорты
ровно один раз (вызывается из main при shutdown).
"""

import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.llm.base import LLMRequest
from app.llm.errors import AuthError
from app.llm.events import LLMEvent
from app.llm.gemini.store_db import build_gemini_pool
from app.llm.providers.alibaba import AlibabaProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.registry import ModelRegistry
from app.security.crypto import CryptoBox
from app.services.credentials import get_provider_api_key

logger = logging.getLogger(__name__)

LLMStreamFn = Callable[[LLMRequest], AsyncIterator[LLMEvent]]


class ManagedLLMStream:
    """Callable LLM-stream с управляемым lifecycle провайдеров (A30)."""

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        crypto: CryptoBox,
        registry: ModelRegistry,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._crypto = crypto
        self._registry = registry
        self._gemini_provider = GeminiProvider(base_url=settings.gemini_base_url)
        self._pool = build_gemini_pool(session_factory, crypto)
        self._alibaba_providers: dict[str, AlibabaProvider] = {}  # кеш по api_key
        self._graveyard: list[AlibabaProvider] = []  # устаревшие клиенты (до aclose)
        self._closed = False

    def __call__(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        return self._stream(request)

    async def _stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        provider_id = self._registry.get(request.model).provider
        if provider_id == "gemini":
            async for event in self._pool.stream_with_failover(
                self._gemini_provider, request, now_fn=lambda: datetime.now(UTC)
            ):
                yield event
            return
        if provider_id == "alibaba":
            provider = await self._alibaba_provider()
            async for event in provider.stream_chat(request):
                yield event
            return
        raise RuntimeError(f"Неизвестный провайдер: {provider_id}")

    async def _alibaba_provider(self) -> AlibabaProvider:
        """Провайдер по текущему ключу; смена ключа → старый клиент закрывается."""
        async with self._session_factory() as session:
            api_key = await get_provider_api_key(
                session, self._crypto, "alibaba", self._settings.alibaba_api_key
            )
        if api_key is None:
            raise AuthError("Alibaba API key не настроен")
        provider = self._alibaba_providers.get(api_key)
        if provider is not None:
            return provider
        # Новый ключ — старые клиенты НЕ закрываем немедленно (могут идти активные
        # стримы через них — A30): уходят в graveyard, закрываются в aclose().
        if self._alibaba_providers:
            self._graveyard.extend(self._alibaba_providers.values())
            logger.info("alibaba provider: клиент устаревшего ключа ушёл в graveyard")
            self._alibaba_providers.clear()
        provider = AlibabaProvider(api_key=api_key, base_url=self._settings.alibaba_base_url)
        self._alibaba_providers[api_key] = provider
        return provider

    async def aclose(self) -> None:
        """Закрыть все транспорты ровно один раз (идемпотентно)."""
        if self._closed:
            return
        self._closed = True
        await self._gemini_provider.aclose()
        for provider in self._alibaba_providers.values():
            await provider.aclose()
        self._alibaba_providers.clear()
        for provider in self._graveyard:
            await provider.aclose()
        self._graveyard.clear()


def build_llm_stream(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoBox,
    registry: ModelRegistry,
) -> ManagedLLMStream:
    """Собрать stream-функцию по реестру моделей и credentials из БД/env."""
    return ManagedLLMStream(
        settings=settings,
        session_factory=session_factory,
        crypto=crypto,
        registry=registry,
    )
