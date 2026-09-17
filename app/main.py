"""Точка входа Telegram-бота (long polling)."""

import asyncio
import logging

from app.bot.dispatcher import create_bot, create_dispatcher, setup_bot_commands
from app.config import get_settings
from app.db.session import create_engine_from_url, make_session_factory
from app.observability.logging import setup_logging

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
    bot = create_bot(settings)
    dp = create_dispatcher(settings, session_factory)
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
