"""Фабрика стрим-функции LLM: диспетчеризация по ModelDefinition.provider.

gemini → GeminiProjectPool (failover по проектам, ключ через request.metadata);
alibaba → AlibabaProvider с ключом из provider_credentials (env — fallback),
провайдер кешируется по ключу, чтобы переиспользовать httpx-клиент.
"""

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

LLMStreamFn = Callable[[LLMRequest], AsyncIterator[LLMEvent]]


def build_llm_stream(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    crypto: CryptoBox,
    registry: ModelRegistry,
) -> LLMStreamFn:
    """Собрать stream-функцию по реестру моделей и credentials из БД/env."""
    gemini_provider = GeminiProvider(base_url=settings.gemini_base_url)
    pool = build_gemini_pool(session_factory, crypto)
    alibaba_providers: dict[str, AlibabaProvider] = {}  # кеш по api_key

    async def stream(request: LLMRequest) -> AsyncIterator[LLMEvent]:
        provider_id = registry.get(request.model).provider
        if provider_id == "gemini":
            async for event in pool.stream_with_failover(
                gemini_provider, request, now_fn=lambda: datetime.now(UTC)
            ):
                yield event
            return
        if provider_id == "alibaba":
            async with session_factory() as session:
                api_key = await get_provider_api_key(
                    session, crypto, "alibaba", settings.alibaba_api_key
                )
            if api_key is None:
                raise AuthError("Alibaba API key не настроен")
            provider = alibaba_providers.get(api_key)
            if provider is None:
                provider = AlibabaProvider(api_key=api_key, base_url=settings.alibaba_base_url)
                alibaba_providers[api_key] = provider
            async for event in provider.stream_chat(request):
                yield event
            return
        raise RuntimeError(f"Неизвестный провайдер: {provider_id}")

    return stream
