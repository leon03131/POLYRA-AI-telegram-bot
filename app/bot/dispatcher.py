"""Сборка Bot и Dispatcher."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.middleware.access import AccessMiddleware
from app.bot.middleware.errors import on_error
from app.bot.routers import chat, commands, photos, stop
from app.config import Settings

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.services.generation import GenerationRegistry, GenerationService

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
    *,
    generation_service: GenerationService,
    generation_registry: GenerationRegistry,
) -> Dispatcher:
    """Dispatcher: error handler, access middleware на message/callback_query, роутеры.

    generation_service/generation_registry кладутся в workflow_data и инъектируются
    в хендлеры по имени параметра.
    """
    dp = Dispatcher(
        settings=settings,  # workflow_data → data["settings"] в хендлерах
        generation_service=generation_service,
        generation_registry=generation_registry,
    )
    dp.errors.register(on_error)
    dp.message.middleware(AccessMiddleware(session_factory, settings))
    dp.callback_query.middleware(AccessMiddleware(session_factory, settings))
    dp.include_routers(commands.router, chat.router, photos.router, stop.router)
    return dp


async def setup_bot_commands(bot: Bot) -> None:
    """Зарегистрировать команды в меню Telegram."""
    await bot.set_my_commands(BOT_COMMANDS)


async def setup_menu_button(bot: Bot, settings: Settings) -> None:
    """Глобальная menu button «⚙️ Настройки» → Mini App (A02).

    Вызывается один раз при startup БЕЗ chat_id — действует для всех чатов
    (per-chat кнопка в /start остаётся как best-effort для старых клиентов).
    Ошибка Telegram API не прерывает запуск (warning в лог).
    """
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="⚙️ Настройки",
                web_app=WebAppInfo(url=settings.app_base_url),
            )
        )
    except TelegramAPIError:
        logger.warning("failed to set global menu button", exc_info=True)
