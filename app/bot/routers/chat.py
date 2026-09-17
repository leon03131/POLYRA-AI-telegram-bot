"""Приём текстовых сообщений в текущий чат."""

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.chats import ChatService

router = Router(name="chat")


@router.message(F.text, ~F.text.startswith("/"))
async def on_text_message(message: Message, user: User, db_session: AsyncSession) -> None:
    text = message.text
    if not text:
        return
    service = ChatService(db_session)
    chat = await service.get_or_create_current_chat(user.id)
    await service.save_user_text_message(chat.id, text)
    # TODO(M3): заменить заглушку на запуск генерации ответа модели.
    await message.answer("💾 Принято. Подключение моделей — на следующем этапе (M3).")
