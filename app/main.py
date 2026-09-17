"""Точка входа Telegram-бота (long polling)."""

import asyncio
import logging

from app.bot.dispatcher import create_bot, create_dispatcher, setup_bot_commands
from app.config import get_settings
from app.context import ContextBuilder, ContextCompactor, TitleGenerator, TokenBudgetManager
from app.db.session import create_engine_from_url, make_session_factory
from app.llm.registry import default_registry
from app.llm.tools.builtin import build_default_registry
from app.memory.extractor import MemoryExtractor
from app.memory.retriever import PostgresFtsRetriever
from app.observability.logging import setup_logging
from app.search.manager import SearchManager
from app.security.crypto import CryptoBox
from app.services.generation import GenerationRegistry, GenerationService
from app.services.llm_factory import build_llm_stream

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    missing = settings.validate_for_runtime()
    if missing:
        logger.error("missing required env vars: %s", ", ".join(missing))
        raise SystemExit(1)

    engine = create_engine_from_url(settings.database_url)
    session_factory = make_session_factory(engine)
    crypto = CryptoBox(settings.master_encryption_key)
    registry = default_registry()
    llm_stream = build_llm_stream(
        settings=settings,
        session_factory=session_factory,
        crypto=crypto,
        registry=registry,
    )
    generation_registry = GenerationRegistry()
    context_builder = ContextBuilder(
        TokenBudgetManager(),
        keep_recent=settings.context_keep_recent,
        trigger_ratio=settings.context_trigger_ratio,
    )
    compactor = ContextCompactor(
        session_factory=session_factory,
        llm_stream=llm_stream,
        summary_model=settings.summary_model,
        summary_thinking=settings.summary_thinking,
        keep_recent=settings.context_keep_recent,
        min_segment=settings.compaction_min_segment,
    )
    title_generator = TitleGenerator(
        session_factory=session_factory,
        llm_stream=llm_stream,
        title_model=settings.title_model,
        title_thinking=settings.title_thinking,
    )
    memory_retriever = PostgresFtsRetriever(session_factory)
    memory_extractor = MemoryExtractor(
        session_factory=session_factory,
        llm_stream=llm_stream,
        memory_model=settings.memory_model,
        memory_thinking=settings.memory_thinking,
        dedup_threshold=settings.memory_dedup_threshold,
    )
    search_manager = SearchManager(session_factory=session_factory, crypto=crypto)
    tool_registry = build_default_registry()
    generation_service = GenerationService(
        session_factory=session_factory,
        registry=registry,
        llm_stream=llm_stream,
        settings=settings,
        generation_registry=generation_registry,
        context_builder=context_builder,
        compactor=compactor,
        title_generator=title_generator,
        memory_retriever=memory_retriever,
        memory_extractor=memory_extractor,
        tool_registry=tool_registry,
        search_manager=search_manager,
    )
    bot = create_bot(settings)
    dp = create_dispatcher(
        settings,
        session_factory,
        generation_service=generation_service,
        generation_registry=generation_registry,
    )
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await setup_bot_commands(bot)
        logger.info("bot started (long polling)")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("bot stopped")
