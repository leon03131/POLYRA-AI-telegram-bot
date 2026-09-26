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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import User
from app.db.repositories import ChatRepository
from app.services.chats import ChatService

logger = logging.getLogger(__name__)

router = Router(name="commands")

_OPEN_CHAT_PREFIX = "chat:open:"

# round4-P1: Bot API требует InlineKeyboardButton.text из 1-64 символов.
_CHAT_BUTTON_TEXT_LIMIT = 64
_NO_TITLE_LABEL = "Без названия"


def build_admin_url(app_base_url: str) -> str:
    """URL админ-панели Mini App. Frontend — HashRouter → hash route /#/admin (A02)."""
    return f"{app_base_url.rstrip('/')}/#/admin"


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


def _chat_button_label(title: str | None, date_str: str, marker: str = "") -> str:
    """Текст кнопки чата в /chats: «маркер + название · дата», целиком не длиннее 64 символов.

    round4-P1: chat.title приходит из Mini App (Field(max_length=256), без
    санитизации) — «сырой» title превышал лимит InlineKeyboardButton.text и
    ломал команду /chats целиком (TelegramBadRequest). Длинное название
    режется с «…», дата и маркер текущего чата сохраняются; лимит считается
    по символам (len), как в Bot API.
    """
    name = title or _NO_TITLE_LABEL
    suffix = f" · {date_str}"
    budget = _CHAT_BUTTON_TEXT_LIMIT - len(marker) - len(suffix)
    if len(name) <= budget:
        return f"{marker}{name}{suffix}"
    return f"{marker}{name[: max(budget - 1, 0)].rstrip()}…{suffix}"


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
async def cmd_new(
    message: Message,
    user: User,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await ChatService(session).create_chat(user.id)
        await session.commit()
    await message.answer("🆕 Новый чат создан. Пишите сообщение — начнём диалог.")


@router.message(Command("chats"))
async def cmd_chats(
    message: Message,
    user: User,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = ChatService(session)
        await _send_chats_list(message, service, user)


async def _send_chats_list(message: Message, service: ChatService, user: User) -> None:
    chats = await service.list_chats(user.id)
    if not chats:
        await message.answer("У вас пока нет чатов. /new — создать.")
        return
    current_id = await service.get_current_chat_id(user.id)
    buttons = []
    for chat in chats:
        marker = "✅ " if chat.id == current_id else ""
        # round4-P1: label с гарантией ≤64 символов (раньше TelegramBadRequest).
        label = _chat_button_label(chat.title, f"{chat.created_at:%d.%m.%Y}", marker)
        buttons.append(
            [InlineKeyboardButton(text=label, callback_data=build_open_chat_callback(chat.id))]
        )
    await message.answer("Ваши чаты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith(_OPEN_CHAT_PREFIX))
async def open_chat(
    callback: CallbackQuery,
    user: User,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = parse_open_chat_callback(callback.data or "")
    if chat_id is None:
        await callback.answer("Некорректные данные кнопки", show_alert=True)
        return
    async with session_factory() as session:
        chat = await ChatRepository(session).get(chat_id)
        if chat is None or chat.owner_user_id != user.id:
            await callback.answer("Чат не найден", show_alert=True)
            return
        await ChatService(session).set_current_chat(user.id, chat.id)
        await session.commit()
    await callback.answer("Чат выбран")
    if isinstance(callback.message, Message):
        # round4-P2: parse_mode=None — у бота default HTML (dispatcher), а title
        # из Mini App не экранирован: '<' или незакрытый тег давали BadRequest.
        await callback.message.answer(
            f"Открыт чат: {chat.title or _NO_TITLE_LABEL}", parse_mode=None
        )


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
    # A04: owner identity — ТОЛЬКО numeric telegram id (флаг БД не даёт прав).
    if message.from_user is None or message.from_user.id != settings.owner_telegram_id:
        await message.answer("⛔ Только для владельца.")
        return
    await message.answer(
        "Панель администратора:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🛠 Админ-панель",
                        web_app=WebAppInfo(url=build_admin_url(settings.app_base_url)),
                    )
                ]
            ]
        ),
    )
