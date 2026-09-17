"""Сборка Bot и Dispatcher."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.middleware.access import AccessMiddleware
from app.bot.middleware.errors import on_error
from app.bot.routers import chat, commands, photos, stop
from app.config import Settings

BOT_COMMANDS = [
    BotCommand(command="start", description="Начать работу"),
    BotCommand(command="new", description="Новый чат"),
    BotCommand(command="chats", description="Мои чаты"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Справка"),
]


def create_bot(settings: Settings) -> Bot:
    """Bot с HTML по умолчанию; AiohttpSession с прокси, если он задан."""
    session = AiohttpSession(proxy=settings.telegram_proxy) if settings.telegram_proxy else None
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> Dispatcher:
    """Dispatcher: error handler, access middleware на message/callback_query, роутеры."""
    dp = Dispatcher(settings=settings)  # workflow_data → data["settings"] в хендлерах
    dp.errors.register(on_error)
    dp.message.middleware(AccessMiddleware(session_factory, settings))
    dp.callback_query.middleware(AccessMiddleware(session_factory, settings))
    dp.include_routers(commands.router, chat.router, photos.router, stop.router)
    return dp


async def setup_bot_commands(bot: Bot) -> None:
    """Зарегистрировать команды в меню Telegram."""
    await bot.set_my_commands(BOT_COMMANDS)
