"""Приём фото в текущий чат."""

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.chats import ChatService

router = Router(name="photos")

MAX_PHOTO_SIZE_BYTES = 15 * 1024 * 1024  # 15 МБ


@router.message(F.photo)
async def on_photo_message(message: Message, user: User, db_session: AsyncSession) -> None:
    if not message.photo:
        return
    photo = message.photo[-1]  # максимальное разрешение из вариантов
    if photo.file_size is not None and photo.file_size > MAX_PHOTO_SIZE_BYTES:
        await message.answer("⚠️ Фото слишком большое: максимум 15 МБ.")
        return
    service = ChatService(db_session)
    chat = await service.get_or_create_current_chat(user.id)
    await service.save_user_photo_message(
        chat.id,
        caption=message.caption,
        photo_file_id=photo.file_id,
        photo_file_size=photo.file_size,
        width=photo.width,
        height=photo.height,
    )
    # TODO(M3): заменить заглушку на запуск генерации ответа модели с учётом фото.
    await message.answer("💾 Принято. Подключение моделей — на следующем этапе (M3).")
