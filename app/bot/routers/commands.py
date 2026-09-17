"""Команды бота: /start /help /new /chats /settings /admin и выбор чата."""

import logging
import uuid

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import User
from app.db.repositories import ChatRepository
from app.services.chats import ChatService

logger = logging.getLogger(__name__)

router = Router(name="commands")

_OPEN_CHAT_PREFIX = "chat:open:"


def build_open_chat_callback(chat_id: uuid.UUID) -> str:
    """callback_data кнопки «открыть чат»."""
    return f"{_OPEN_CHAT_PREFIX}{chat_id}"


def parse_open_chat_callback(data: str) -> uuid.UUID | None:
    """Извлечь chat_id из callback_data; None для чужого префикса или мусора."""
    if not data.startswith(_OPEN_CHAT_PREFIX):
        return None
    try:
        return uuid.UUID(data.removeprefix(_OPEN_CHAT_PREFIX))
    except ValueError:
        return None


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot, settings: Settings) -> None:
    """Приветствие + кнопка меню с Mini App (best effort)."""
    await message.answer(
        "👋 Привет! Я AI-ассистент: отвечаю на вопросы, работаю с текстом и фото.\n"
        "Просто напишите сообщение, чтобы начать диалог.\n"
        "Настройки — в Mini App по кнопке меню."
    )
    try:
        await bot.set_chat_menu_button(
            chat_id=message.chat.id,
            menu_button=MenuButtonWebApp(
                text="⚙️ Настройки",
                web_app=WebAppInfo(url=settings.app_base_url),
            ),
        )
    except TelegramAPIError:
        logger.warning("failed to set chat menu button", exc_info=True)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Команды:\n"
        "/new — новый чат\n"
        "/chats — список чатов\n"
        "/settings — настройки\n"
        "/help — эта справка"
    )


@router.message(Command("new"))
async def cmd_new(message: Message, user: User, db_session: AsyncSession) -> None:
    await ChatService(db_session).create_chat(user.id)
    await message.answer("🆕 Новый чат создан. Пишите сообщение — начнём диалог.")


@router.message(Command("chats"))
async def cmd_chats(message: Message, user: User, db_session: AsyncSession) -> None:
    service = ChatService(db_session)
    chats = await service.list_chats(user.id)
    if not chats:
        await message.answer("У вас пока нет чатов. /new — создать.")
        return
    current_id = await service.get_current_chat_id(user.id)
    buttons = []
    for chat in chats:
        marker = "✅ " if chat.id == current_id else ""
        label = f"{marker}{chat.title or 'Без названия'} · {chat.created_at:%d.%m.%Y}"
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=build_open_chat_callback(chat.id))]
        )
    await message.answer("Ваши чаты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith(_OPEN_CHAT_PREFIX))
async def open_chat(callback: CallbackQuery, user: User, db_session: AsyncSession) -> None:
    chat_id = parse_open_chat_callback(callback.data or "")
    if chat_id is None:
        await callback.answer("Некорректные данные кнопки", show_alert=True)
        return
    chat = await ChatRepository(db_session).get(chat_id)
    if chat is None or chat.owner_user_id != user.id:
        await callback.answer("Чат не найден", show_alert=True)
        return
    await ChatService(db_session).set_current_chat(user.id, chat.id)
    await callback.answer("Чат выбран")
    if isinstance(callback.message, Message):
        await callback.message.answer(f"Открыт чат: {chat.title or 'Без названия'}")


@router.message(Command("settings"))
async def cmd_settings(message: Message, settings: Settings) -> None:
    await message.answer(
        "Настройки открываются в Mini App:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Открыть настройки",
                        web_app=WebAppInfo(url=settings.app_base_url),
                    )
                ]
            ]
        ),
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, user: User, settings: Settings) -> None:
    if not user.is_owner:
        await message.answer("⛔ Только для владельца.")
        return
    await message.answer(
        "Панель администратора:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛠 Админ-панель",
                        web_app=WebAppInfo(url=f"{settings.app_base_url}/admin"),
                    )
                ]
            ]
        ),
    )
